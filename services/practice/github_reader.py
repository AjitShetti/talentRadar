"""
services/practice/github_reader.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A bounded, read-only window onto one candidate's GitHub repository.

Practice reads a submitted repo through GitHub's REST API and nothing else: it never clones,
never runs what it reads, and never follows a link a candidate wrote. The repo is the
candidate's, so everything about it is untrusted input; these rules keep that input from
steering the request or filling the server.

* **Every URL is built from a ``RepoRef`` and fixed path pieces.** The owner and name are
  re-checked against the strict repo pattern, every path piece is percent-encoded, query
  values are passed as data, and the finished URL is asserted to be ``https://api.github.com``
  with exactly the intended path. A ``Link`` header, a ``Location`` header or a redirect never
  supplies a URL; the only thing taken from a ``Link`` is a pagination cursor, and it goes
  back out as an encoded query value.
* **No redirects are followed.** GitHub's own API redirects a renamed repo; that is refused as
  :class:`RepoGone` (the person pastes the current link) rather than followed, so the token
  and the request can only ever reach ``api.github.com``.
* **Every body is read in a bounded way.** The reply is streamed and reading stops at the
  limit; a body is never read whole and then cut. A JSON document over its cap is
  :class:`ReplyTooLarge` (a :class:`GitHubUnavailable`, so a caller that does not care still
  backs off, and one that does can tell "too big, ever" from "try later"); a text file is
  simply cut at ``limit`` bytes (a character split by the cut is dropped, not turned into a
  replacement character).
* **Failures are two kinds.** ``RepoGone`` is GitHub saying the thing is not there (404, 410,
  451, or a redirect). ``GitHubUnavailable`` is anything that may pass: a rate limit, a 5xx, a
  network error, a timeout, an unreadable reply. Callers back off on the second and reject on
  the first. A missing *file* is not an error: ``fetch_text`` returns ``None``.
* **Timestamps and ids that decide anything are GitHub's own** (the Activity API ``timestamp``,
  the repo ``created_at``); the parsing is in ``domain/practice_repo.py``.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from domain.practice_repo import (
    CommitLine,
    Push,
    RepoRef,
    TreeEntry,
    parse_activity,
    parse_commits,
    parse_repo_url,
    parse_tree,
)
from services.github import _headers

API_HOST = "api.github.com"
API_ORIGIN = f"https://{API_HOST}"

#: Per-phase timeout of the client (connect, each read), and a ceiling on one whole request.
REQUEST_TIMEOUT = 6.0
REQUEST_DEADLINE = 10.0

#: Caps on a JSON reply, in bytes. A tree of a repo that commits ``node_modules`` is big.
JSON_LIMIT = 1_000_000
TREE_LIMIT = 3_000_000

JSON_ACCEPT = "application/vnd.github+json"
RAW_ACCEPT = "application/vnd.github.raw+json"

ACTIVITY_PAGE_SIZE = 100
COMMITS_PAGE_SIZE = 100

_SHA = re.compile(r"[0-9a-f]{40}")
_GONE = frozenset({404, 410, 451})
_MAX_BRANCH_CHARS = 255
_MAX_PATH_CHARS = 500
_MAX_CURSOR_CHARS = 500


class GitHubUnavailable(Exception):  # noqa: N818 - names the condition, as RateLimited does
    """GitHub could not answer right now (rate limit, 5xx, network, timeout, unreadable reply)."""


class ReplyTooLarge(GitHubUnavailable):
    """A JSON reply is over its size cap. Asking again will not shrink it: the repo is too big."""


class RepoGone(Exception):  # noqa: N818 - names the condition, as RateLimited does
    """GitHub says the repo, or the thing asked of it, is not there (404, 410, 451, a redirect)."""


class _NotFound(RepoGone):
    """Exactly a 404. ``fetch_text`` reads this as "no such file"; every other caller as gone."""


@dataclass(frozen=True)
class _Reply:
    """A 200 reply read up to a limit."""

    body: bytes
    truncated: bool
    links: Mapping[str | None, Mapping[str, str]]


def github_client() -> httpx.AsyncClient:
    """A client for ``api.github.com`` with the token (when configured) and a 6 s timeout.

    It does not follow redirects, and every request built by this module is an absolute
    ``https://api.github.com`` URL, so the token cannot be sent anywhere else.
    """
    return httpx.AsyncClient(
        base_url=API_ORIGIN,
        headers=_headers(),
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        follow_redirects=False,
    )


# -- building requests -------------------------------------------------------------------------


def _checked(ref: RepoRef) -> RepoRef:
    """``ref`` if it is exactly what the strict repo-URL pattern would have produced."""
    if parse_repo_url(ref.html_url) != ref:
        raise ValueError(f"not a GitHub repo reference: {ref!r}")
    return ref


def _check_sha(sha: str) -> str:
    if _SHA.fullmatch(sha) is None:
        raise ValueError("sha must be forty lowercase hex characters")
    return sha


def _check_path(path: str) -> str:
    """A clean relative path inside a repo: no empty, ``.`` or ``..`` segment."""
    clean = (
        0 < len(path) <= _MAX_PATH_CHARS
        and path.isprintable()
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )
    if not clean:
        raise ValueError(f"not a clean repo path: {path!r}")
    return path


def _check_branch(branch: str) -> str:
    if not 0 < len(branch) <= _MAX_BRANCH_CHARS or not branch.isprintable():
        raise ValueError("branch is not a usable branch name")
    return branch


def _url(ref: RepoRef, tail: str = "", query: Mapping[str, str] | None = None) -> httpx.URL:
    """``https://api.github.com/repos/<owner>/<name><tail>`` with ``query`` as encoded data.

    ``tail`` is the decoded path below the repo (``/contents/src/app.py``). It is
    percent-encoded here, and the URL that results is asserted to be the HTTPS API host with
    exactly ``/repos/<owner>/<name><tail>`` as its decoded path, so nothing in ``tail`` or
    ``query`` can move the request to another host, path or fragment.
    """
    wanted = f"{_checked(ref).api_path}{tail}"
    base = f"{API_ORIGIN}{quote(wanted, safe='/')}"
    url = httpx.URL(base, params=dict(query)) if query else httpx.URL(base)
    if (
        url.scheme != "https"
        or url.host != API_HOST
        or url.port is not None
        or url.userinfo
        or url.fragment
        or url.path != wanted
    ):
        raise ValueError(f"refusing a request that is not {API_HOST}{wanted}")
    return url


# -- sending and reading -----------------------------------------------------------------------


async def _read(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    """At most ``limit`` bytes of the body, and whether more was on offer.

    Reading stops as soon as more than ``limit`` bytes have arrived, so the most that is ever
    pulled is ``limit`` plus one network chunk.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            break
    return b"".join(chunks)[:limit], size > limit


async def _answer(response: httpx.Response, *, limit: int, hard_limit: bool) -> _Reply:
    code = response.status_code
    if 300 <= code < 400:
        raise RepoGone("GitHub says the repo has moved")
    if code == 404:
        raise _NotFound("GitHub has no such repo or file")
    if code in _GONE:
        raise RepoGone(f"GitHub answered {code}")
    if code != 200:
        raise GitHubUnavailable(f"GitHub answered {code}")
    declared = response.headers.get("Content-Length", "")
    if hard_limit and declared.isdigit() and int(declared) > limit:
        raise ReplyTooLarge("GitHub's reply is too large to read")
    body, truncated = await _read(response, limit)
    if hard_limit and truncated:
        raise ReplyTooLarge("GitHub's reply is too large to read")
    return _Reply(body=body, truncated=truncated, links=response.links)


async def _get(
    client: httpx.AsyncClient,
    url: httpx.URL,
    *,
    accept: str,
    limit: int,
    hard_limit: bool,
) -> _Reply:
    """One GET, streamed and bounded, with redirects refused and every failure classified."""
    request = client.build_request(
        "GET", url, headers={"Accept": accept, "Accept-Encoding": "identity"}
    )
    try:
        async with asyncio.timeout(REQUEST_DEADLINE):
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                return await _answer(response, limit=limit, hard_limit=hard_limit)
            finally:
                await response.aclose()
    except TimeoutError as exc:
        raise GitHubUnavailable("GitHub did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise GitHubUnavailable(f"GitHub could not be reached ({type(exc).__name__})") from exc


def _json(reply: _Reply) -> Any:
    try:
        return json.loads(reply.body)
    except ValueError as exc:  # includes UnicodeDecodeError
        raise GitHubUnavailable("GitHub's reply was not JSON") from exc


async def _get_json(
    client: httpx.AsyncClient, url: httpx.URL, *, limit: int = JSON_LIMIT
) -> tuple[Any, _Reply]:
    reply = await _get(client, url, accept=JSON_ACCEPT, limit=limit, hard_limit=True)
    return _json(reply), reply


# -- the reads ---------------------------------------------------------------------------------


async def fetch_repo(client: httpx.AsyncClient, ref: RepoRef) -> dict[str, Any]:
    """``GET /repos/{owner}/{name}``: ``private``, ``fork``, ``created_at``, ``default_branch``."""
    payload, _ = await _get_json(client, _url(ref))
    if not isinstance(payload, dict):
        raise GitHubUnavailable("GitHub's repo reply was not an object")
    return payload


def _next_cursor(links: Mapping[str | None, Mapping[str, str]]) -> str | None:
    """The ``after`` cursor of a ``rel="next"`` link, or ``None``.

    Only the cursor is taken. The link's own URL is never requested.
    """
    target = links.get("next", {}).get("url")
    if not target:
        return None
    try:
        cursor: str | None = httpx.URL(target).params.get("after")
    except httpx.InvalidURL:
        return None
    if not cursor or len(cursor) > _MAX_CURSOR_CHARS or not cursor.isprintable():
        return None
    return cursor


async def fetch_pushes(
    client: httpx.AsyncClient, ref: RepoRef, branch: str, *, pages: int = 3
) -> list[Push]:
    """The pushes GitHub recorded on ``branch``, newest first, from up to ``pages`` pages.

    ``GET /repos/{owner}/{name}/activity?ref=refs/heads/<branch>&per_page=100``. Paging follows
    the cursor GitHub hands back and stops at the last page or at ``pages``.
    """
    ref_name = f"refs/heads/{_check_branch(branch)}"
    pushes: list[Push] = []
    cursor: str | None = None
    for _page in range(max(pages, 0)):
        query = {"ref": ref_name, "per_page": str(ACTIVITY_PAGE_SIZE)}
        if cursor is not None:
            query["after"] = cursor
        payload, reply = await _get_json(client, _url(ref, "/activity", query))
        pushes.extend(parse_activity(payload, ref_name))
        cursor = _next_cursor(reply.links)
        if cursor is None:
            break
    return pushes


async def fetch_commits(client: httpx.AsyncClient, ref: RepoRef, sha: str) -> list[CommitLine]:
    """Up to sixty commits reachable from ``sha``: id, first message line, claimed date."""
    query = {"sha": _check_sha(sha), "per_page": str(COMMITS_PAGE_SIZE)}
    payload, _ = await _get_json(client, _url(ref, "/commits", query))
    return parse_commits(payload)


async def fetch_tree(client: httpx.AsyncClient, ref: RepoRef, sha: str) -> list[TreeEntry]:
    """Every file path (with size, where GitHub gave one) in the tree at ``sha``."""
    payload, _ = await _get_json(
        client,
        _url(ref, f"/git/trees/{_check_sha(sha)}", {"recursive": "1"}),
        limit=TREE_LIMIT,
    )
    return parse_tree(payload)


async def fetch_text(
    client: httpx.AsyncClient, ref: RepoRef, path: str | None, sha: str, *, limit: int
) -> str | None:
    """A text file of the repo at ``sha``, at most ``limit`` characters, or ``None``.

    ``path=None`` reads the README. The raw media type is requested, so the body is the file
    itself. At most ``limit`` bytes are read; a character the cut splits is dropped, and a
    byte that is not UTF-8 is replaced. ``None`` means GitHub has no such file at that commit
    (a 404). A redirect is :class:`RepoGone`; a rate limit or server error is
    :class:`GitHubUnavailable`.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    tail = "/readme" if path is None else f"/contents/{_check_path(path)}"
    url = _url(ref, tail, {"ref": _check_sha(sha)})
    try:
        reply = await _get(client, url, accept=RAW_ACCEPT, limit=limit, hard_limit=False)
    except _NotFound:
        return None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    return decoder.decode(reply.body, final=not reply.truncated)
