"""
services/company_map/github_orgs.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Bengaluru companies found through their GitHub organisation.

Two stages, both on GitHub's public API:

1. **Search**: ``GET /search/users?q=type:org location:Bengaluru`` (and the
   ``Bangalore`` spelling), sorted by followers so the most established
   organisations come first and the order stays put between runs. A query
   returns at most 1,000 results, 100 to a page. Search is allowed 10
   requests a minute unauthenticated (30 with ``GITHUB_TOKEN``), so a walk
   pauses between pages.
2. **Lookup**: ``GET /orgs/{login}`` for each candidate: name, website
   (``blog``), description, location, repository and follower counts. The
   search result carries none of these.

**The filter** keeps real companies and drops communities, student clubs and
personal projects (the filter is deliberately cheap; some foundations and
communities still pass): a ``blog`` that is a company's own domain (not a profile
page, a hosting-platform subdomain, a link shortener or a school), a name or a
description, at least one public repository, and a location that still says
Bengaluru. Nobody is named and no email is read.

**The walk is a cursor** (:class:`Cursor`: which query, how far into it). One
run takes the next ``limit`` candidates after it, so daily runs cover every
result in ``total / limit`` days without searching twice for the same page.
:func:`fetch_candidates` hands each candidate the cursor that follows it, so a
run that stops early (rate limit, errors) resumes exactly after the last
organisation it finished.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from config.settings import get_settings
from domain.company_identity import normalize_domain
from domain.company_taxonomy import sector_from_text
from domain.geo import mentions_city
from services.company_map.sourced import SourcedCompany, clean_website
from services.github import _headers

GITHUB_API = "https://api.github.com"
SEARCH_URL = f"{GITHUB_API}/search/users"
LOCATIONS: tuple[str, ...] = ("Bengaluru", "Bangalore")
PER_PAGE = 100
#: GitHub serves the first 1,000 results of any search and nothing beyond.
RESULT_CAP = 1000

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_SEARCH_GAP_ANONYMOUS_S = 6.5  # 10 requests a minute
_SEARCH_GAP_TOKEN_S = 2.2  # 30 requests a minute

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_DESCRIPTION_MAX = 500

#: Link shorteners and chat invites: an address, not a company.
_NOT_A_COMPANY_SITE = frozenset(
    {
        "discord.gg",
        "discord.com",
        "bit.ly",
        "tinyurl.com",
        "gitter.im",
        "reddit.com",
        "dev.to",
        "wa.me",
        "forms.gle",
    }
)
#: Schools, universities and government: their GitHub organisations are
#: clubs and course pages.
_INSTITUTION_SUFFIXES = (".edu", ".ac.in", ".edu.in", ".gov", ".gov.in", ".nic.in", ".ac.uk")

#: Student clubs and college chapters keep real websites and real repositories
#: and are not employers. Whole words in the name or description, or a login
#: that starts with a club's usual prefix: "Clubhouse" and "dscout" stay.
_CLUB_WORDS = re.compile(
    r"\b(?:clubs?|chapters?|college|university|campus|gdsc|dsc|glug)\b", re.IGNORECASE
)
_CLUB_LOGIN = re.compile(r"^(?:gdsc|glug|dsc[-_])", re.IGNORECASE)

_sleep = asyncio.sleep


class RateLimited(Exception):  # noqa: N818 - reads as the condition, like StopIteration
    """GitHub says this client has used its allowance. ``reset_at`` is epoch seconds."""

    def __init__(self, reset_at: int | None = None) -> None:
        super().__init__("GitHub rate limit reached")
        self.reset_at = reset_at


@dataclass(frozen=True)
class Cursor:
    """A place in the walk: ``query`` indexes :data:`LOCATIONS`, ``offset`` counts results taken."""

    query: int = 0
    offset: int = 0

    def encode(self) -> str:
        return f"{self.query}:{self.offset}"

    @classmethod
    def decode(cls, value: object) -> Cursor:
        """The cursor ``value`` encodes; anything unreadable starts a fresh walk."""
        if not isinstance(value, str):
            return cls()
        left, _, right = value.partition(":")
        if not (left.isdigit() and right.isdigit()):
            return cls()
        query, offset = int(left), int(right)
        return cls(query, offset) if query < len(LOCATIONS) else cls()


@dataclass(frozen=True)
class Candidate:
    """An organisation the search listed, and where the walk stands once it is done."""

    login: str
    next: Cursor


@dataclass
class Walk:
    """
    What one walk found.

    ``cursor`` is where the next run starts if every candidate is finished;
    ``wrapped`` says the walk reached the end of the last query (``cursor`` is
    then the start of a fresh cycle); ``rate_limited`` says the search was cut
    short, with whatever came before it still in ``candidates``.
    """

    candidates: list[Candidate] = field(default_factory=list)
    cursor: Cursor = field(default_factory=Cursor)
    rate_limited: bool = False
    wrapped: bool = False


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def _has_token() -> bool:
    return bool(get_settings().github_token)


def _search_gap() -> float:
    return _SEARCH_GAP_TOKEN_S if _has_token() else _SEARCH_GAP_ANONYMOUS_S


def _reset_at(response: httpx.Response) -> int | None:
    value = response.headers.get("x-ratelimit-reset")
    return int(value) if value and value.isdigit() else None


def _check_rate_limit(response: httpx.Response) -> None:
    """Raise :class:`RateLimited` for GitHub's two ways of saying "slow down"."""
    if response.status_code == 429:
        raise RateLimited(_reset_at(response))
    if response.status_code == 403 and (
        response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
    ):
        raise RateLimited(_reset_at(response))


async def search_page(client: httpx.AsyncClient, location: str, page: int) -> dict[str, Any]:
    """One page of the organisation search. ``{}`` past the 1,000-result cap."""
    response = await client.get(
        SEARCH_URL,
        params={
            "q": f"type:org location:{location}",
            "sort": "followers",
            "order": "desc",
            "per_page": PER_PAGE,
            "page": page,
        },
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    _check_rate_limit(response)
    if response.status_code == 422:  # the page is beyond what search will serve
        return {}
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def fetch_org(client: httpx.AsyncClient, login: str) -> dict[str, Any] | None:
    """
    The organisation record for ``login``, or ``None`` when it is gone, is not
    a valid login or the body is not an object. Raises :class:`RateLimited`
    when GitHub is out of allowance, and ``httpx`` errors otherwise.
    """
    if not _LOGIN.match(login):
        return None
    response = await client.get(f"{GITHUB_API}/orgs/{login}", headers=_headers(), timeout=_TIMEOUT)
    _check_rate_limit(response)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def parse_search_page(payload: Any) -> tuple[list[str], int]:
    """``(logins, total_count)`` of a search page: organisations with a valid login only."""
    if not isinstance(payload, dict):
        return [], 0
    total = payload.get("total_count")
    items = payload.get("items")
    logins: list[str] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "Organization":
                continue
            login = item.get("login")
            if isinstance(login, str) and _LOGIN.match(login):
                logins.append(login)
    return logins, total if isinstance(total, int) and not isinstance(total, bool) else 0


def _cursor_at(query: int, offset: int) -> Cursor:
    """``(query, offset)``, or the start of a fresh cycle once the last query is done."""
    return Cursor(query, offset) if query < len(LOCATIONS) else Cursor()


async def fetch_candidates(client: httpx.AsyncClient, cursor: Cursor, limit: int) -> Walk:
    """
    The next ``limit`` organisations after ``cursor``.

    One search request serves a hundred of them, so a run of 60 usually costs
    one request and never more than a few. An organisation listed under both
    spellings is returned once. A rate-limited search is not an error: the walk
    returns what it has, flagged. Any other HTTP failure raises.
    """
    walk = Walk(cursor=cursor)
    seen: set[str] = set()
    query, offset = cursor.query, cursor.offset
    requests = 0

    while len(walk.candidates) < limit:
        if query >= len(LOCATIONS):
            walk.cursor, walk.wrapped = Cursor(), True
            break
        if offset >= RESULT_CAP:
            query, offset = query + 1, 0
            walk.cursor = _cursor_at(query, 0)
            continue
        if requests:
            await _sleep(_search_gap())
        requests += 1
        page = offset // PER_PAGE
        try:
            payload = await search_page(client, LOCATIONS[query], page + 1)
        except RateLimited:
            walk.rate_limited = True
            return walk
        logins, reported = parse_search_page(payload)
        total = min(reported, RESULT_CAP)
        rows = logins[offset - page * PER_PAGE :]
        if not rows or offset >= total:  # nothing left in this query
            query, offset = query + 1, 0
            walk.cursor = _cursor_at(query, 0)
            continue

        for index, login in enumerate(rows):
            position = offset + index + 1
            done = position >= total
            walk.cursor = _cursor_at(query + 1, 0) if done else Cursor(query, position)
            if login.lower() not in seen:
                seen.add(login.lower())
                walk.candidates.append(Candidate(login, walk.cursor))
            if done:
                query, offset = query + 1, 0
                break
            if len(walk.candidates) >= limit:
                return walk
        else:
            offset = (page + 1) * PER_PAGE  # the next page of the same query
    walk.wrapped = query >= len(LOCATIONS)
    return walk


# ---------------------------------------------------------------------------
# The filter
# ---------------------------------------------------------------------------


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    flat = " ".join(value.split())
    return flat[:limit] or None


def _own_site(blog: object) -> tuple[str, str] | None:
    """The organisation's website and domain, if the ``blog`` link is a company's own."""
    site = clean_website(blog, allow_hosted=False)
    if site is None:
        return None
    domain = site[1]
    if domain in _NOT_A_COMPANY_SITE or domain.endswith(_INSTITUTION_SUFFIXES):
        return None
    return site


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_org(org: Any) -> SourcedCompany | None:
    """
    The company a GitHub organisation record describes, or ``None`` if it is
    not one. See the module docstring for what makes it one.
    """
    if not isinstance(org, dict) or org.get("type") != "Organization":
        return None
    login = org.get("login")
    if not isinstance(login, str) or not _LOGIN.match(login):
        return None
    repos = _count(org.get("public_repos"))
    if repos is None or repos < 1:
        return None
    if not mentions_city(_text(org.get("location"), 200), "Bengaluru"):
        return None
    site = _own_site(org.get("blog"))
    if site is None:
        return None
    name = _text(org.get("name"), 256)
    description = _text(org.get("description"), _DESCRIPTION_MAX)
    if name is None and description is None:
        return None
    if _CLUB_LOGIN.match(login) or _CLUB_WORDS.search(f"{name or ''} {description or ''}"):
        return None
    website_url, domain = site
    followers = _count(org.get("followers"))
    # is_tech: True (source is tech by nature)
    return SourcedCompany(
        name=name or login,
        domain=normalize_domain(domain),
        website_url=website_url,
        github_org=login,
        description=description,
        sector=sector_from_text(description),
        source_metadata={
            "github_org_repos": repos,
            **({"github_org_followers": followers} if followers is not None else {}),
        },
    )
