"""
domain/practice_repo.py
~~~~~~~~~~~~~~~~~~~~~~~
What Practice may trust about a candidate's GitHub repository, and how it reads it.

Pure rules over payloads the caller already fetched. There is no network and no
clock: GitHub's own REST payloads come in, typed values go out. Every function
that parses a payload accepts anything and returns what it could use; a row it
cannot read is skipped, never guessed.

Two kinds of time appear here, and only one of them decides anything:

* **Push times** (the Activity API ``timestamp``) and the repo's ``created_at``
  are stamped by GitHub's servers. The snapshot is chosen from those.
* **Commit dates** are written by the author's own machine. They are kept as
  ``claimed_at`` for display and nothing in this module, or in
  ``domain.practice_signals``, reads them to decide anything.

Every timestamp is parsed to an aware UTC ``datetime``.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, TypeGuard
from urllib.parse import urlsplit

#: ``https://github.com/<owner>/<repo>``, matched strictly (spec 4.9). Matched with
#: ``fullmatch``: ``$`` would also accept a trailing newline.
_REPO_URL = re.compile(r"https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})/?")

#: Hosts a write-up's screen recording may live on. The link is shown, never fetched.
VIDEO_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "loom.com",
    "www.loom.com",
    "drive.google.com",
)

#: A repo made up to this long before Start is still "created in the window": the person
#: may create the empty repo while the Start dialog is open.
REPO_CREATION_GRACE = timedelta(minutes=15)

#: Activity types that move a branch to a new tip, which is ``after``. GitHub reports the
#: first push to a new branch as ``branch_creation`` and a merge as ``pr_merge`` or
#: ``merge_queue_merge``. ``branch_deletion`` removes the tip and is never counted.
PUSH_KINDS = ("push", "force_push", "branch_creation", "pr_merge", "merge_queue_merge")

#: First line of a commit message, cut to this many characters.
COMMIT_MESSAGE_CHARS = 80
COMMIT_LIMIT = 60

#: The file a candidate commits, holding the code shown at Start, to prove the repo is theirs.
PROOF_FILE = ".talentradar"
PROOF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
PROOF_LENGTH = 10

KEY_FILE_LIMIT = 4
#: A file bigger than this is not shown to the judge.
MAX_KEY_FILE_BYTES = 200 * 1024
_MAX_PATH_CHARS = 500
#: In a repo path these end it or rewrite the request built from it (see ``_is_safe_path``).
_UNSAFE_PATH_CHARS = frozenset("?#%\\")

_SHA = re.compile(r"[0-9a-f]{40}")

#: Directories that hold vendored, installed, built or generated code, never the candidate's own.
_VENDORED_DIRS = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        "venv",
        ".venv",
        "env",
        "site-packages",
        "__pycache__",
        ".git",
        "target",
        ".next",
        "coverage",
    }
)
_LOCKFILES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
    }
)
#: Only a file with one of these extensions can be a key file. That alone keeps out
#: binaries, images, data and documents.
# fmt: off
_SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts",
    ".scala", ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".ex", ".exs",
    ".clj", ".dart", ".lua", ".sh", ".sql", ".vue", ".svelte", ".zig",
})
# fmt: on
_SOURCE_DIRS = frozenset({"src", "app", "lib", "api"})
#: Entry-point stems in rank order (``main.*`` first). ``manage.py`` is matched whole.
_ENTRY_STEMS = ("main", "app", "server", "index", "cli")
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs"})


@dataclass(frozen=True)
class RepoRef:
    """A repository named by its owner and name. Every GitHub request is built from these."""

    owner: str
    name: str

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.name}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass(frozen=True)
class Push:
    """One push to a branch, as GitHub's Activity API recorded it.

    ``sha`` is the entry's ``after`` (the branch tip once the push landed) and
    ``before`` the tip it replaced (forty zeros for a new branch). ``at`` is GitHub's
    own timestamp. ``kind`` is the activity type, one of ``PUSH_KINDS``.
    """

    sha: str
    before: str
    at: datetime
    kind: str


@dataclass(frozen=True)
class TreeEntry:
    """A file in the repo at the snapshot. ``size`` is bytes, 0 when GitHub gave none."""

    path: str
    size: int


@dataclass(frozen=True)
class CommitLine:
    """One commit as the judge sees it: id, first message line, and the date the author
    *claims*. That date is client-set and forgeable; it is evidence to show, not to decide on."""

    sha: str
    message: str
    claimed_at: datetime | None


def _is_sha(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def parse_timestamp(value: object) -> datetime | None:
    """An ISO-8601 instant as aware UTC, or ``None``.

    A value with no UTC offset is not an instant and is refused: GitHub always sends one,
    so a naive value means the payload is not what we think it is.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        # An instant at the edge of the supported years can overflow when moved to UTC.
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


# -- repo URL and video link ------------------------------------------------------------------


def parse_repo_url(url: str) -> RepoRef | None:
    """The repo a ``https://github.com/<owner>/<repo>`` URL names, or ``None``.

    Strict: nothing but those two segments, an optional trailing slash and an optional
    ``.git``. Anything else (a branch path, a query, another host, ``.``/``..``) is not
    a repo URL. Requests are built from the returned pieces, never from the string.
    """
    match = _REPO_URL.fullmatch(url)
    if match is None:
        return None
    owner, name = match.groups()
    name = name.removesuffix(".git")
    if name in ("", ".", ".."):
        return None
    return RepoRef(owner, name)


def valid_video_url(url: str) -> bool:
    """Whether ``url`` is an https link to one of ``VIDEO_HOSTS``. It is never fetched.

    Whitespace and control characters are refused, and so is any ``user@`` part: a browser
    reads ``https://evil.example\\@youtube.com`` as ``evil.example`` where Python reads
    ``youtube.com``, and refusing every ``@`` removes the difference.
    """
    if any(ch.isspace() or not ch.isprintable() for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname in VIDEO_HOSTS
        and "@" not in parts.netloc
        and port in (None, 443)
    )


# -- pushes and the snapshot ------------------------------------------------------------------


def parse_activity(payload: Any, ref: str) -> list[Push]:
    """The entries on ``ref`` of an Activity API response that moved it to a new tip.

    That is every type in ``PUSH_KINDS``: ``push``, ``force_push``, ``branch_creation``,
    ``pr_merge`` and ``merge_queue_merge``. ``branch_deletion`` is not one.

    The order of the payload is kept (GitHub lists newest first). Entries on other refs,
    of other types, or with an unreadable id or timestamp are dropped.
    """
    if not isinstance(payload, list):
        return []
    pushes: list[Push] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("activity_type")
        if kind not in PUSH_KINDS or entry.get("ref") != ref:
            continue
        after, before = entry.get("after"), entry.get("before")
        at = parse_timestamp(entry.get("timestamp"))
        if _is_sha(after) and _is_sha(before) and at is not None:
            pushes.append(Push(sha=after, before=before, at=at, kind=kind))
    return pushes


def snapshot(pushes: Sequence[Push], deadline_at: datetime) -> tuple[Push | None, int]:
    """The push to judge, and how many pushes came after the deadline.

    The push is the latest one with ``at <= deadline_at``: a push at exactly the deadline
    counts. Pushes and merges after it, force pushes included, are ignored and only counted.
    When two share a timestamp the one listed first wins (GitHub lists newest first).
    ``None`` when nothing was pushed in time.

    Only push times are read. Commit dates play no part.
    """
    on_time = [push for push in pushes if push.at <= deadline_at]
    late = len(pushes) - len(on_time)
    if not on_time:
        return None, late
    return max(on_time, key=lambda push: push.at), late


# -- file tree and key files ------------------------------------------------------------------


def _is_safe_path(path: str) -> bool:
    """Whether a repo path can be put into a request URL without changing the request.

    The candidate chooses these names, and a key file's path ends up in
    ``.../contents/<path>?ref=<snapshot>``. So a path is refused if it is empty or long,
    holds a control character or any whitespace but a plain space, holds one of ``?``,
    ``#``, ``%`` or a backslash (they end the path, drop or replace ``ref``, or smuggle in
    escapes), or has an empty, ``.`` or ``..`` segment.
    """
    return (
        0 < len(path) <= _MAX_PATH_CHARS
        and path.isprintable()
        and _UNSAFE_PATH_CHARS.isdisjoint(path)
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def parse_tree(payload: Any) -> list[TreeEntry]:
    """The files (blobs) of a ``git/trees?recursive=1`` response. A missing size is 0.

    A blob whose path is not safe to put in a request URL (see ``_is_safe_path``) is skipped.
    """
    rows = payload.get("tree") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    entries: list[TreeEntry] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "blob":
            continue
        path, size = row.get("path"), row.get("size")
        if not isinstance(path, str) or not _is_safe_path(path):
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            size = 0
        entries.append(TreeEntry(path=path, size=size))
    return entries


def _directories(path: str) -> list[str]:
    return path.lower().split("/")[:-1]


def is_test_path(path: str) -> bool:
    """Whether ``path`` looks like a test or spec file. Vendored code does not count."""
    directories = _directories(path)
    if any(part in _VENDORED_DIRS for part in directories):
        return False
    if any(part in _TEST_DIRS for part in directories):
        return True
    name = path.lower().rsplit("/", 1)[-1]
    stem = name.split(".", 1)[0]
    return (
        stem in _TEST_DIRS
        or stem.startswith("test_")
        or stem.endswith(("_test", "_tests", "_spec"))
        or ".test." in name
        or ".spec." in name
    )


def _is_candidate(entry: TreeEntry) -> bool:
    """A source file the judge could be shown: a safe path, not vendored, generated, locked,
    hidden by a ``.git*`` name, minified or oversize. Binaries and images fall out on
    extension."""
    parts = entry.path.lower().split("/")
    name = parts[-1]
    return (
        _is_safe_path(entry.path)
        and not any(part in _VENDORED_DIRS for part in parts[:-1])
        and not any(part.startswith(".git") for part in parts)
        and name not in _LOCKFILES
        and ".min." not in name
        and entry.size <= MAX_KEY_FILE_BYTES
        and PurePosixPath(name).suffix in _SOURCE_EXTENSIONS
    )


def _entry_rank(path: str) -> int | None:
    """Where ``path`` ranks among entry points (``main.*`` first), or ``None``."""
    name = path.lower().rsplit("/", 1)[-1]
    if name == "manage.py":
        return len(_ENTRY_STEMS)
    stem = name.split(".", 1)[0]
    return _ENTRY_STEMS.index(stem) if stem in _ENTRY_STEMS else None


def pick_key_files(tree: Sequence[TreeEntry], limit: int = KEY_FILE_LIMIT) -> list[str]:
    """The files worth showing the judge, best first, at most ``limit``.

    Entry points come first (``main.*``, ``app.*``, ``server.*``, ``index.*``, ``cli.*``,
    ``manage.py``; shallowest first), then the largest source files under ``src/``,
    ``app/``, ``lib/`` or ``api/``. If the tree has a test file, the best (largest) one
    takes the last slot, even when entry points or sources would have filled it, so a
    reader can see that tests exist; with a single slot the entry point wins. Vendored,
    generated, locked, minified, hidden, binary and oversize files never qualify.
    """
    candidates = [entry for entry in tree if _is_candidate(entry)]
    entry_points: list[tuple[int, TreeEntry]] = []
    sources: list[TreeEntry] = []
    tests: list[TreeEntry] = []
    for entry in candidates:
        rank = _entry_rank(entry.path)
        if is_test_path(entry.path):
            tests.append(entry)
        elif rank is not None:
            entry_points.append((rank, entry))
        elif any(part in _SOURCE_DIRS for part in _directories(entry.path)):
            sources.append(entry)
    entry_points.sort(key=lambda pair: (pair[1].path.count("/"), pair[0], pair[1].path))
    sources.sort(key=lambda entry: (-entry.size, entry.path))
    tests.sort(key=lambda entry: (-entry.size, entry.path))
    ranked = [entry for _, entry in entry_points] + sources
    reserve_test = bool(tests) and limit > 1
    picked = ranked[: limit - 1] + tests[:1] if reserve_test else ranked[: max(limit, 0)]
    return [entry.path for entry in picked]


# -- commits ----------------------------------------------------------------------------------


def parse_commits(payload: Any, limit: int = COMMIT_LIMIT) -> list[CommitLine]:
    """Up to ``limit`` commits of a ``/commits`` response, newest first as GitHub sends them.

    Each keeps its id, the first line of its message (cut to 80 characters) and the
    author's *claimed* date. A row without a readable id is skipped.
    """
    if not isinstance(payload, list):
        return []
    lines: list[CommitLine] = []
    for item in payload:
        if len(lines) >= limit:
            break
        if not isinstance(item, dict) or not _is_sha(item.get("sha")):
            continue
        commit = item.get("commit")
        commit = commit if isinstance(commit, dict) else {}
        author = commit.get("author")
        claimed = parse_timestamp(author.get("date")) if isinstance(author, dict) else None
        message = commit.get("message")
        first_line = (message.strip().splitlines() or [""])[0] if isinstance(message, str) else ""
        lines.append(
            CommitLine(
                sha=item["sha"],
                message=first_line.strip()[:COMMIT_MESSAGE_CHARS],
                claimed_at=claimed,
            )
        )
    return lines


# -- proof of ownership -----------------------------------------------------------------------


def proof_matches(content: str | None, code: str) -> bool:
    """Whether the proof file holds exactly ``code``.

    Surrounding whitespace (a trailing newline, ``\\r\\n``) and a leading byte-order mark are
    ignored; the comparison is otherwise exact and case-sensitive. A missing file, or an
    empty ``code``, never matches.
    """
    if content is None or not code:
        return False
    return content.lstrip("\N{BYTE ORDER MARK}").strip() == code


def new_proof_code() -> str:
    """A fresh proof code: ten characters from an alphabet without look-alikes."""
    return "".join(secrets.choice(PROOF_ALPHABET) for _ in range(PROOF_LENGTH))
