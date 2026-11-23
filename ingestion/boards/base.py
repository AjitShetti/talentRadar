"""
ingestion/boards/base.py
~~~~~~~~~~~~~~~~~~~~~~~~
What every board reader shares: one bounded, never-raising JSON request, the
shape of a read, and the one place a posting becomes a :class:`domain.entities.Job`.

A reader makes the request(s) for one board and returns **all** its roles. It
never decides which are in India (the caller does, on ``location_raw``) and never
raises for an HTTP or parse problem: every failure is a :class:`BoardRead` whose
``outcome`` says what happened, with ``roles == []`` and ``complete is False``.

Three failures look alike from outside and must not be told apart wrongly:

* a **200 that is not the ATS's JSON** (a captive portal, a rate-limit page served
  with a 200) is ``error``, never "this board has no India roles" - the latter
  would demote a live board;
* a **3xx** is ``error``: redirects are never followed, so a moved or hijacked
  board cannot send the sweep somewhere we did not choose;
* a **body over the cap** is ``error`` and is never parsed truncated: the read stops
  at the cap rather than downloading the rest.

Only a 404 or 410 means the board is gone, and only a 429 means "slow down".
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from domain.geo import INDIA_COUNTRY_CODE, is_india, resolve_city
from domain.job_boards import BoardRef, ReadOutcome
from ingestion.scrapers._parsing import parse_html_sync
from services.job_persistence import MAX_DESCRIPTION_CHARS

USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"

#: A board whose response is larger than this is an ``error`` read, never a truncated one.
MAX_BODY_BYTES = 2 * 1024 * 1024

_GONE_STATUSES = frozenset({404, 410})
_REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


class _OverCap:
    """The data of an ``error`` read that stopped because the body passed the size cap."""

    def __repr__(self) -> str:
        return "OVER_CAP"


#: What :func:`get_json` returns in place of data when its ``error`` is the size cap
#: and nothing else (not a 5xx, a timeout or a body that is not JSON). A reader that
#: can ask for less (Greenhouse without ``content``) tells that case apart with it.
OVER_CAP = _OverCap()


@dataclass(frozen=True)
class BoardRead:
    """The result of reading one board once.

    ``roles`` holds every role the ATS listed that could be read, India or not.
    ``total`` is how many postings the ATS listed, so ``complete`` is true only when
    the whole board was read: no error, no page cap hit, no posting that could not
    be parsed. A role missing from an incomplete read is not evidence it closed.
    ``company_name`` is the name the ATS states for the board, where it states one.
    """

    outcome: ReadOutcome
    roles: list[Job]
    total: int
    complete: bool
    company_name: str | None = None


#: What every ATS module's ``read_board`` looks like.
Reader = Callable[[httpx.AsyncClient, BoardRef], Awaitable[BoardRead]]


def failed(outcome: ReadOutcome) -> BoardRead:
    """A read that produced no roles: the board was not read, so nothing is known about it."""
    return BoardRead(outcome=outcome, roles=[], total=0, complete=False)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> tuple[ReadOutcome, Any]:
    """
    One request, answered as ``(outcome, parsed JSON)``; the data is ``None`` unless ``ok``.

    The project's User-Agent is sent, redirects are not followed whatever the client
    is set to, and the body is streamed and abandoned the moment it passes
    :data:`MAX_BODY_BYTES` (the count is of decoded bytes, so a compressed body that
    inflates past the cap is caught too). 404 and 410 are ``not_found``, 429 is
    ``rate_limited``; any other status, a timeout, a transport error, a body over
    the cap and a body that is not JSON (an HTML page at 200 included) are ``error``.
    A body over the cap is the one ``error`` whose data is :data:`OVER_CAP` rather than
    ``None``. Never raises, other than cancellation.
    """
    body = bytearray()
    try:
        async with client.stream(
            method,
            url,
            params=params,
            json=json_body,
            headers=_REQUEST_HEADERS,
            follow_redirects=False,
        ) as response:
            status = response.status_code
            if status in _GONE_STATUSES:
                return "not_found", None
            if status == 429:
                return "rate_limited", None
            if status != 200:
                return "error", None
            declared = response.headers.get("content-length", "").strip()
            if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                return "error", OVER_CAP
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    return "error", OVER_CAP
    except (httpx.HTTPError, httpx.InvalidURL):
        return "error", None

    try:
        # A body near the cap takes tens of milliseconds to parse: not on the loop.
        return "ok", await asyncio.to_thread(json.loads, bytes(body))
    except (ValueError, RecursionError):
        return "error", None


async def read_items(
    items: list[Any],
    ref: BoardRef,
    parse: Callable[[Any, BoardRef], Job | None],
    *,
    exhaustive: bool = True,
) -> BoardRead:
    """
    Turn a board's list of postings into an ``ok`` read, off the event loop.

    ``parse`` returns ``None`` for a posting it cannot use (no title, no URL); that
    posting still counts in ``total``, so the read is then ``complete=False``.
    ``exhaustive`` is false when the reader stopped before the end of the board (a
    page cap hit), which also makes the read incomplete.
    """

    def work() -> BoardRead:
        roles = [role for item in items if (role := parse(item, ref)) is not None]
        return BoardRead(
            outcome="ok",
            roles=roles,
            total=len(items),
            complete=exhaustive and len(roles) == len(items),
        )

    return await asyncio.to_thread(work)


# ── turning a posting's fields into a Job ────────────────────────────────────

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_URL_MAX_LENGTH = 2048
_BLOCK_TAGS = (
    "p", "div", "li", "ul", "ol", "tr", "table", "blockquote", "pre", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6",
)  # fmt: skip
_REMOTE_WORDS = ("remote", "anywhere", "worldwide", "global", "wfh")


def text_value(value: object) -> str:
    """``value`` when it is a string, else ``""``: ATS JSON is not trusted to have the type it documents."""
    return value if isinstance(value, str) else ""


def posting_id(value: object) -> str | None:
    """The ATS's own id for a posting as text (Greenhouse's are integers), or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return str(value).strip() or None


def one_line(value: object) -> str:
    """A title or a location: control characters dropped, whitespace collapsed to single spaces."""
    return " ".join(_CONTROL_RE.sub("", text_value(value)).split())


def clean_url(value: object) -> str | None:
    """``value`` when it is an absolute http(s) URL with no whitespace, else ``None``."""
    text = text_value(value).strip()
    if not text or len(text) > _URL_MAX_LENGTH or _CONTROL_RE.search(text) or " " in text:
        return None
    return text if text.lower().startswith(("https://", "http://")) else None


def plain_text(text: str) -> str:
    """Plain text with control characters dropped, lines trimmed and blank runs squeezed to one."""
    cleaned = _CONTROL_RE.sub(
        "", text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    )
    lines: list[str] = []
    for line in cleaned.split("\n"):
        line = _SPACE_RE.sub(" ", line).strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def html_to_text(html: str) -> str:
    """
    An HTML fragment as plain text, one line per block, parsed with lxml.

    Call it from a worker thread (``read_items`` does); parsing is synchronous.
    """
    if not html.strip():
        return ""
    soup = parse_html_sync(html)
    for tag in soup(["script", "style"]):
        tag.decompose()
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    # One line per block, no blank lines: nested blocks would otherwise leave a gap
    # after every list, and a description is cut to a few KB, so the space counts.
    lines = plain_text(soup.get_text()).split("\n")
    return "\n".join(line for line in lines if line)


def describe(*parts: str) -> str | None:
    """The non-empty ``parts`` as one description, cut to ``MAX_DESCRIPTION_CHARS``; ``None`` when empty."""
    text = "\n\n".join(part for part in parts if part)[:MAX_DESCRIPTION_CHARS].rstrip()
    return text or None


def looks_remote(location: str | None) -> bool:
    """Whether a location's own words say remote. Only for an ATS that states no remote flag."""
    lowered = (location or "").lower()
    return any(word in lowered for word in _REMOTE_WORDS)


def build_job(
    ref: BoardRef,
    *,
    external_id: str | None,
    title: str,
    url: str,
    location: str | None,
    description: str | None,
    is_remote: bool = False,
    posted_at: datetime | None = None,
    updated_at: datetime | None = None,
    company_name: str | None = None,
) -> Job:
    """
    The one place a board's posting becomes a :class:`Job`.

    ``posted_at`` is the date the ATS states, or ``None``; nothing here reads the
    clock. ``country`` is filled only when the location text itself says India (the
    ATS's own country field is never trusted), and ``company_id`` is a placeholder:
    whoever persists the role files it under the board's company.
    """
    return Job(
        company_id=uuid.uuid4(),
        external_id=external_id,
        source=ref.source,
        source_url=url,
        title=title,
        description_clean=description,
        status=JobStatus.ACTIVE,
        employment_type=EmploymentType.FULL_TIME,
        seniority=SeniorityLevel.MID,
        location_raw=location or None,
        country=INDIA_COUNTRY_CODE if is_india(location) else None,
        city=resolve_city(location),
        is_remote=is_remote,
        skills=[],
        tags=["ats", ref.ats, ref.board],
        posted_at=posted_at,
        source_posted_at=posted_at,
        source_updated_at=updated_at,
        extra_metadata={"company_name": company_name or ref.board.capitalize(), "ats": ref.ats},
    )
