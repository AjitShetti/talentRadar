"""
domain/job_boards.py
~~~~~~~~~~~~~~~~~~~~
The rules behind the job-board roster: which applicant-tracking-system boards
exist, how to recognise one in a link, what to guess when a company's board is
not linked anywhere, and when to read a board again.

A board is *named*, never fetched, here. A page we crawled (a careers page, a
YC listing) can contain any URL on the internet, so a link is matched against
strict, exact host patterns - the same discipline as ``domain/posting_urls.py``
- and only the captured pieces (ATS, board id, region) ever reach a request. A
host that merely contains an ATS's name (``boards.greenhouse.io.evil.example``)
matches nothing.

Nothing here is a score. A board's status moves by fixed rules from the outcome
of its last read, so the same history always gives the same schedule.

Pure functions and constants, no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal, get_args
from urllib.parse import parse_qs, urlsplit

from domain.company_identity import normalize_domain, normalize_name
from domain.geo import is_india

AtsName = Literal[
    "greenhouse",
    "ashby",
    "lever",
    "workable",
    "recruitee",
    "keka",
    "rippling",
    "personio",
    "breezy",
]

#: The nine ATSes, in the order the sweep and the docs list them.
ATS: tuple[AtsName, ...] = get_args(AtsName)

#: Minimum seconds between two requests to one ATS's host. Lever's is its
#: published ``Crawl-delay``; Workable's is deliberately slow (it answers a
#: faster client with an HTML 429 page).
PACE_SECONDS: Mapping[AtsName, float] = MappingProxyType(
    {
        "greenhouse": 0.5,
        "ashby": 0.5,
        "lever": 1.0,
        "workable": 10.0,
        "recruitee": 1.0,
        "keka": 2.0,
        "rippling": 1.0,
        "personio": 1.0,
        "breezy": 1.0,
    }
)

#: The two ATSes live search scrapes on demand (the rest are swept on a schedule).
LIVE_SEARCH_ATS: frozenset[AtsName] = frozenset({"greenhouse", "ashby"})

#: ATSes whose board id can be guessed from a company name: an unknown slug
#: answers 404 there. Rippling, Personio and Breezy are found by link only.
PROBE_ATS: tuple[AtsName, ...] = ("greenhouse", "ashby", "lever", "workable", "recruitee", "keka")

BoardStatus = Literal["candidate", "active", "no_india", "gone", "blocked"]

#: Statuses the scheduled sweep reads. ``gone`` and ``blocked`` are not read.
SWEPT_STATUSES: frozenset[BoardStatus] = frozenset({"candidate", "active", "no_india"})

#: What one read of a board came to. A 3xx, a body over the cap and a 200 that
#: is not the ATS's JSON are all ``error``: never "no India roles".
ReadOutcome = Literal["ok", "not_found", "rate_limited", "error"]


@dataclass(frozen=True)
class BoardRef:
    """One board on one ATS: enough to ask that ATS for its postings."""

    ats: AtsName
    board: str
    region: str | None = None

    @property
    def source(self) -> str:
        """The ``jobs.source`` value for a role read from this board."""
        return f"{self.ats}:{self.board}"


@dataclass(frozen=True)
class Schedule:
    """The part of a board's row that decides when it is read next."""

    status: BoardStatus
    failures: int
    zero_india_reads: int
    not_found_reads: int
    next_read_at: datetime


# ---------------------------------------------------------------------------
# Recognising a board in a link
# ---------------------------------------------------------------------------

_MAX_URL_LENGTH = 2048

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")

#: ATSes whose slug keeps the case it was written in (the rest are lower-cased).
_CASE_KEPT: frozenset[AtsName] = frozenset({"lever", "ashby"})

#: Hosts where the board is the first path segment. Matched exactly.
_PATH_HOSTS: dict[str, AtsName] = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "ats.rippling.com": "rippling",
}

#: First path segments that are never a board: an ATS's own API prefix, and
#: Workable's short posting link (``apply.workable.com/j/<id>``).
_NOT_A_PATH_BOARD: dict[AtsName, frozenset[str]] = {
    "greenhouse": frozenset({"api"}),
    "lever": frozenset({"api"}),
    "ashby": frozenset({"api"}),
    "workable": frozenset({"api", "j"}),
    "rippling": frozenset({"api"}),
}

#: Hosts where the board is one label in front of a fixed suffix:
#: ``(suffix, ats, region, required path prefix)``.
_SUBDOMAIN_HOSTS: tuple[tuple[str, AtsName, str | None, str | None], ...] = (
    (".recruitee.com", "recruitee", None, None),
    (".keka.com", "keka", None, "/careers"),
    (".breezy.hr", "breezy", None, None),
    (".jobs.personio.de", "personio", "de", None),
    (".jobs.personio.com", "personio", "com", None),
)

#: A label that is the ATS's own site, not a customer's board.
_NOT_A_SUBDOMAIN_BOARD = frozenset({"www", "app", "api", "careers", "jobs", "help", "support"})


def _first_segment(path: str) -> str | None:
    if not path.startswith("/"):
        return None
    return path[1:].split("/", 1)[0] or None


def _board(ats: AtsName, slug: str | None, region: str | None = None) -> BoardRef | None:
    if not slug or _SLUG.fullmatch(slug) is None:
        return None
    return BoardRef(ats=ats, board=slug if ats in _CASE_KEPT else slug.lower(), region=region)


#: The two hosts a Personio board lives on: ``{board}.jobs.personio.de`` and ``.com``.
PERSONIO_REGIONS: tuple[str, ...] = ("de", "com")


def board_ref(ats: str, board: str, region: str | None = None) -> BoardRef | None:
    """
    A :class:`BoardRef` for an ATS name and board id taken from a list, or ``None``.

    The same rules a board found in a link follows: ``ats`` must be one of the nine, ``board``
    must be a valid id (it can come from a list we did not write, and it ends up in a request),
    and the case is the one :func:`board_from_url` would keep (Lever's and Ashby's as written,
    the rest lower-cased). ``region`` is kept only for Personio, where it must be ``de`` or
    ``com`` (anything else is ``de``, the reader's own default); every other ATS has none.
    """
    if ats not in ATS:
        return None
    chosen = None
    if ats == "personio":
        chosen = region if region in PERSONIO_REGIONS else "de"
    return _board(ats, (board or "").strip(), chosen)


def board_from_url(url: str) -> BoardRef | None:
    """
    The ATS board a URL names, or ``None``.

    The URL is never requested. It must be ``http(s)``, carry no login or port,
    and its host must be exactly one of the known hosts (or a single label in
    front of a known suffix); the board id is then read from the path, the
    subdomain or, for a Greenhouse embed, the ``for`` parameter.
    """
    text = (url or "").strip()
    if not text or len(text) > _MAX_URL_LENGTH:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or port is not None:
        return None
    host = parts.hostname or ""

    ats = _PATH_HOSTS.get(host)
    if ats is not None:
        first = _first_segment(parts.path)
        if ats == "greenhouse" and first is not None and first.lower() == "embed":
            if parts.path.rstrip("/").lower() != "/embed/job_board":
                return None
            values = parse_qs(parts.query).get("for", [])
            return _board(ats, values[0] if values else None)
        if first is None or first.lower() in _NOT_A_PATH_BOARD.get(ats, frozenset()):
            return None
        return _board(ats, first)

    for suffix, sub_ats, region, path_prefix in _SUBDOMAIN_HOSTS:
        if not host.endswith(suffix):
            continue
        label = host[: -len(suffix)]
        if "." in label or label in _NOT_A_SUBDOMAIN_BOARD:
            return None
        if path_prefix is not None and not (
            parts.path == path_prefix or parts.path.startswith(f"{path_prefix}/")
        ):
            return None
        return _board(sub_ats, label, region)
    return None


def board_for_posting(ats: str, board: str, url: str | None) -> BoardRef | None:
    """
    The board a stored posting is read from, when its own URL names that same board; else ``None``.

    The posting's URL is the proof of where it lives. A URL on another board, or on
    another ATS, proves nothing about this one, so it is never checked against it.
    A Personio posting's region is the host its URL names (the reader built that URL
    from the board's region, so it is the board's own region).
    """
    expected = board_ref(ats, board)
    named = board_from_url(url or "")
    if expected is None or named is None or (named.ats, named.board) != (expected.ats, expected.board):
        return None
    return board_ref(expected.ats, expected.board, named.region)


# A URL anywhere in the text: an attribute value, a script string or a bare
# link. ``//host`` is a protocol-relative link; the lookbehind keeps ``ftp://``
# and ``x-https://`` from being read as one.
_URL_IN_TEXT = re.compile(r"(?<![A-Za-z0-9+.\-:])(?:https?:)?//[^\s\"'<>`\\]+", re.IGNORECASE)
_ENTITY_STOP = re.compile(r"&(?:quot|apos|lt|gt|#0*3[49]|#x0*2[27]);", re.IGNORECASE)
_ENTITY_AMP = re.compile(r"&(?:amp|#0*38|#x0*26);", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}"


def boards_in_html(html: str) -> list[BoardRef]:
    """
    Every distinct ATS board a page links to, in the order it first appears.

    Reads each ``href``/``src`` value and each bare URL in the text with
    :func:`board_from_url`. Only ``&amp;`` is decoded: the rest of the HTML
    entity table (``&reg`` and friends) would corrupt a query string.
    """
    if not html:
        return []
    found: dict[tuple[str, str, str | None], BoardRef] = {}
    for match in _URL_IN_TEXT.finditer(html.replace("\\/", "/")):
        raw = _ENTITY_STOP.split(match.group(0), maxsplit=1)[0]
        url = _ENTITY_AMP.sub("&", raw).rstrip(_TRAILING_PUNCTUATION)
        if url.startswith("//"):
            url = f"https:{url}"
        ref = board_from_url(url)
        if ref is not None:
            found.setdefault((ref.ats, ref.board, ref.region), ref)
    return list(found.values())


# ---------------------------------------------------------------------------
# Guessing a board
# ---------------------------------------------------------------------------

_MAX_GUESSES = 4
_ALNUM_RUN = re.compile(r"[a-z0-9]+")


def domain_label(domain: str | None) -> str | None:
    """
    The registrable domain's first label (``https://www.groww.in/careers`` -> ``groww``), or ``None``.

    The one guess that rests on something the company itself published, its own site: a
    board id equal to it is the company's, where an id guessed from the name alone may
    belong to anyone with a similar name.
    """
    registrable = normalize_domain(domain)
    return registrable.split(".", 1)[0] if registrable else None


def slug_candidates(name: str, domain: str | None) -> list[str]:
    """
    Up to four board ids to try for a company, most likely first.

    The registrable domain's label (``groww.in`` -> ``groww``, :func:`domain_label`), then
    the name with everything but letters and digits removed, then the name's words
    joined by ``-``. A legal suffix is kept on purpose: Razorpay's board is
    ``razorpaysoftwareprivatelimited``. A guess that is not a valid slug is
    dropped, and so is a repeat.
    """
    guesses: list[str] = []
    label = domain_label(domain)
    if label:
        guesses.append(label)
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    words = _ALNUM_RUN.findall(folded.lower())
    if words:
        guesses.append("".join(words))
        guesses.append("-".join(words))

    out: list[str] = []
    for guess in guesses:
        if _SLUG.fullmatch(guess) is not None and guess not in out:
            out.append(guess)
    return out[:_MAX_GUESSES]


# ---------------------------------------------------------------------------
# When to read a board again
# ---------------------------------------------------------------------------

_ACTIVE_EVERY = timedelta(hours=24)
_ZERO_INDIA_BACK_OFF = (timedelta(days=7), timedelta(days=14))
_NO_INDIA_EVERY = timedelta(days=30)
_NO_INDIA_AFTER_ZEROS = 3
_NOT_FOUND_RETRY = timedelta(hours=24)
_GONE_AFTER_NOT_FOUNDS = 2
_GONE_RECHECK = timedelta(days=30)
_MAX_BACK_OFF = timedelta(days=7)
_BLOCKED_AFTER_FAILURES = 6


def next_read(prev: Schedule, outcome: ReadOutcome, india_roles: int, *, now: datetime) -> Schedule:
    """
    The schedule after one read of a board.

    * ``ok`` with India roles: ``active``, read again in 24 h; every counter
      resets.
    * ``ok`` with none: ``zero_india_reads`` grows and the board is read again
      in 7, then 14 days; the third zero makes it ``no_india``, read monthly.
    * ``not_found`` (404/410): read once more a day later; the second in a row
      makes it ``gone``. Any other outcome in between breaks the run.
    * ``rate_limited`` / ``error``: ``failures`` grows and the board waits
      ``2 ** failures`` hours (at most 7 days); the sixth failure makes it
      ``blocked``. The 429 or the timeout says nothing about the board's roles.

    ``blocked`` and ``gone`` boards are not swept, so their date only records
    when they would have been read.
    """
    if outcome == "ok":
        if india_roles > 0:
            return Schedule("active", 0, 0, 0, now + _ACTIVE_EVERY)
        zeros = prev.zero_india_reads + 1
        if zeros >= _NO_INDIA_AFTER_ZEROS:
            return Schedule("no_india", 0, zeros, 0, now + _NO_INDIA_EVERY)
        status: BoardStatus = prev.status if prev.status in ("candidate", "active") else "candidate"
        return Schedule(status, 0, zeros, 0, now + _ZERO_INDIA_BACK_OFF[zeros - 1])

    if outcome == "not_found":
        misses = prev.not_found_reads + 1
        if misses >= _GONE_AFTER_NOT_FOUNDS:
            return replace(
                prev,
                status="gone",
                failures=0,
                not_found_reads=misses,
                next_read_at=now + _GONE_RECHECK,
            )
        return replace(
            prev, failures=0, not_found_reads=misses, next_read_at=now + _NOT_FOUND_RETRY
        )

    failures = prev.failures + 1
    wait = min(timedelta(hours=2 ** min(failures, 10)), _MAX_BACK_OFF)
    return replace(
        prev,
        status="blocked" if failures >= _BLOCKED_AFTER_FAILURES else prev.status,
        failures=failures,
        not_found_reads=0,
        next_read_at=now + wait,
    )


# ---------------------------------------------------------------------------
# Roles and names
# ---------------------------------------------------------------------------


def is_india_role(location_text: str | None) -> bool:
    """
    A role is kept only when its own location text says India. The ``country`` column is never trusted.

    A reader joins a role's several locations with ``;`` ("United Kingdom; Bengaluru"), and
    :func:`domain.geo.is_india` lets a foreign country beat a city within one string, so the
    text is read one location at a time: any one in India is enough.
    """
    return any(is_india(part.strip()) for part in (location_text or "").split(";") if part.strip())


def names_match(a: str, b: str) -> bool:
    """True when two company names are the same under ``normalize_name``. Two empty names are not a match."""
    left = normalize_name(a)
    return bool(left) and left == normalize_name(b)
