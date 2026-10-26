"""
services/company_map/people.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Named people, each from a public source that names them.

* **Engineers** are public members of the company's GitHub org. Repo
  contributors are not, because they may be outsiders. Fetched by the
  scheduled refresh, never by opening the drawer.
* **Founders on demand**: one Tavily search per company, ever. A person is
  accepted only from a result that is itself a LinkedIn profile whose title
  names a founder or CEO role at this company. The search engine's result is
  the source; LinkedIn itself is never requested.
* :func:`upsert_people` stores whatever the importers found, once per
  person, filling only links that are still empty and never marking anyone
  verified.

Nothing here guesses an email address or a name.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from domain.company_identity import normalize_name
from services.base import parse_uuid
from services.cache_backend import CacheBackend
from services.company_contacts import is_broker_source
from services.company_map.types import PersonCandidate
from services.github import _headers
from storage.database import AsyncSessionLocal
from storage.models import Company, CompanyContact

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
TAVILY_URL = "https://api.tavily.com/search"

#: Tavily's free tier is 1,000 searches a month; leave room for the rest of
#: the app (company contact lookups use it too).
MONTHLY_TAVILY_CAP = 800

_FOUNDERS_TTL_SECONDS = 365 * 24 * 3600  # "forever" for a cache entry
_BUDGET_TTL_SECONDS = 40 * 24 * 3600
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_ORG_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_LINKEDIN_PROFILE = re.compile(r"^https://(?:www|in)\.linkedin\.com/in/([A-Za-z0-9\-_%]+)/?$")
# LinkedIn titles separate with hyphens, en dashes and em dashes.
_SEP = r"\s[-–—]\s"
_PROFILE_TITLE = re.compile(
    rf"^(?P<name>[^|]+?){_SEP}"
    rf"(?P<title>[^|]*?\b(?:co-?founder|founder|ceo)\b[^|]*?){_SEP}"
    r"(?P<company>[^|]+?)\s*\|\s*LinkedIn\s*$",
    re.IGNORECASE,
)
_FOUNDER_WORD = re.compile(r"\bfounder\b|\bco-?founder\b", re.IGNORECASE)
_NAME_SHAPE = re.compile(r"^[^\d@/|<>]{2,80}$")

#: People kinds, strongest first. The same person seen as founder by one
#: source and CEO by another is one row, kept as the stronger kind.
_PEOPLE_KINDS = ("founder", "leader", "engineer")
_LINK_FIELDS = ("linkedin_url", "x_url", "github_url", "profile_url")

#: ``company_contacts`` column widths. A public profile can name anything (a
#: GitHub ``name`` runs to 255): text is cut to fit, a link that does not fit
#: is dropped, because a cut URL is a broken one.
_TEXT_MAX = 128
_URL_MAX = {"linkedin_url": 512, "x_url": 256, "github_url": 256, "profile_url": 512}
_SOURCE_URL_MAX = 512


def _bounded(person: PersonCandidate) -> PersonCandidate | None:
    """``person`` cut to fit its columns, or ``None`` if its source cannot be stored."""
    if len(person.source_url) > _SOURCE_URL_MAX:
        return None

    def link(field: str, value: str | None) -> str | None:
        return value if value and len(value) <= _URL_MAX[field] else None

    return replace(
        person,
        name=" ".join(person.name.split())[:_TEXT_MAX].strip(),
        title=person.title[:_TEXT_MAX] if person.title else None,
        linkedin_url=link("linkedin_url", person.linkedin_url),
        x_url=link("x_url", person.x_url),
        github_url=link("github_url", person.github_url),
        profile_url=link("profile_url", person.profile_url),
    )


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    parts = urlsplit(url)
    return url if parts.scheme in {"http", "https"} and parts.netloc else None


def parse_github_user(payload: dict[str, Any]) -> PersonCandidate:
    """A public GitHub user as an engineer, with only the links they published."""
    login = str(payload.get("login") or "").strip()
    html_url = str(payload.get("html_url") or f"https://github.com/{login}")
    name = str(payload.get("name") or "").strip() or login
    twitter = str(payload.get("twitter_username") or "").strip().lstrip("@")
    return PersonCandidate(
        name=name,
        kind="engineer",
        source_url=html_url,
        source_kind="github",
        github_url=html_url,
        x_url=f"https://x.com/{twitter}" if twitter else None,
        profile_url=_http_url(payload.get("blog")),
    )


async def _members(client: httpx.AsyncClient, org: str, limit: int) -> list[PersonCandidate]:
    resp = await client.get(
        f"{GITHUB_API}/orgs/{org}/public_members",
        params={"per_page": limit},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    found: list[PersonCandidate] = []
    for member in resp.json()[:limit]:
        login = str(member.get("login") or "")
        if not _ORG_SLUG.match(login) or member.get("type") != "User":
            continue
        user = await client.get(f"{GITHUB_API}/users/{login}", headers=_headers(), timeout=_TIMEOUT)
        if user.status_code != 200:
            continue
        found.append(parse_github_user(user.json()))
    return found


async def github_members(
    org: str, *, limit: int = 12, client: httpx.AsyncClient | None = None
) -> list[PersonCandidate]:
    """Public members of a GitHub org as engineers. Never raises."""
    slug = org.strip().strip("/")
    if not _ORG_SLUG.match(slug):
        return []
    try:
        if client is not None:
            return await _members(client, slug, limit)
        async with httpx.AsyncClient() as own:
            return await _members(own, slug, limit)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        logger.info("GitHub members for %s unavailable: %s", slug, exc)
        return []


# ---------------------------------------------------------------------------
# Tavily founders
# ---------------------------------------------------------------------------


def parse_tavily_founders(
    results: list[dict[str, Any]], company_name: str
) -> list[PersonCandidate]:
    """Founders and CEOs from search results that are LinkedIn profiles of this company."""
    wanted = normalize_name(company_name)
    found: dict[str, PersonCandidate] = {}
    for item in results:
        url = str(item.get("url") or "").strip()
        profile = _LINKEDIN_PROFILE.match(url)
        if not profile or is_broker_source(url):
            continue
        title = _PROFILE_TITLE.match(str(item.get("title") or "").strip())
        if not title or normalize_name(title["company"]) != wanted:
            continue
        name = " ".join(title["name"].split())
        if not _NAME_SHAPE.match(name):
            continue
        slug = profile.group(1)
        role = " ".join(title["title"].split())
        found.setdefault(
            slug.lower(),
            PersonCandidate(
                name=name,
                kind="founder" if _FOUNDER_WORD.search(role) else "leader",
                title=role,
                linkedin_url=f"https://www.linkedin.com/in/{slug}",
                source_url=url,
                source_kind="tavily",
            ),
        )
    return list(found.values())


def _api_key() -> str:
    return get_settings().tavily_api_key


async def _company_name(company_id: uuid.UUID) -> str | None:
    async with AsyncSessionLocal() as session:
        company = await session.get(Company, company_id)
        return company.name if company else None


async def _tavily_search(query: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        resp = await client.post(
            TAVILY_URL,
            json={
                "api_key": _api_key(),
                "query": query,
                "search_depth": "basic",
                "include_domains": ["linkedin.com"],
                "max_results": 10,
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results if isinstance(results, list) else []


async def _within_monthly_cap() -> bool:
    """Claim one search; increments first so two clicks cannot both take the last."""
    key = f"tr:budget:tavily:{datetime.now(UTC):%Y-%m}"
    try:
        used = await CacheBackend.incr(key, _BUDGET_TTL_SECONDS)
    except Exception as exc:
        logger.debug("Could not read the Tavily budget: %s", exc)
        return True
    return used <= MONTHLY_TAVILY_CAP


def _unavailable(message: str) -> dict[str, Any]:
    return {"available": False, "people": [], "message": message}


async def discover_founders(company_id: str) -> dict[str, Any]:
    """
    One founder search for a company, stored and cached for good.

    Returns ``{"available", "people", "message"}``. Never raises.
    """
    cid = parse_uuid(company_id)
    if cid is None:
        return _unavailable("Company not found.")
    name = await _company_name(cid)
    if not name:
        return _unavailable("Company not found.")

    cache_key = f"tr:founders:{cid}"
    cached = await CacheBackend.get_json(cache_key)
    if isinstance(cached, dict):
        return cached
    if not _api_key():
        return _unavailable("Founder search is not configured on this server.")
    if not await _within_monthly_cap():
        return _unavailable("Founder search has used this month's allowance. It resets on the 1st.")

    try:
        results = await _tavily_search(f'"{name}" founder OR co-founder')
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Founder search for %r failed: %s", name, exc)
        return _unavailable("Founder search failed. Try again later.")

    founders = parse_tavily_founders(results, name)
    if founders:
        async with AsyncSessionLocal() as session:
            await upsert_people(session, cid, founders)
            await session.commit()
    answer = {
        "available": True,
        "people": [asdict(p) for p in founders],
        "message": (
            f"Found {len(founders)} public founder profile{'' if len(founders) == 1 else 's'}."
            if founders
            else f"No public LinkedIn profile names a founder of {name}."
        ),
    }
    await CacheBackend.set_json(cache_key, answer, _FOUNDERS_TTL_SECONDS)
    return answer


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _stronger(kind: str, than: str) -> bool:
    rank = {k: i for i, k in enumerate(_PEOPLE_KINDS)}
    return rank.get(kind, len(rank)) < rank.get(than, len(rank))


async def upsert_people(
    session: AsyncSession, company_id: uuid.UUID, people: Iterable[PersonCandidate]
) -> int:
    """
    Store sourced people as curated contacts; returns how many were inserted.

    A person already stored under any people kind with the same name (case
    folded) is the same person: their empty links and title are filled, the
    stronger kind is kept, and nothing is ever overwritten or verified.
    """
    result = await session.execute(
        select(CompanyContact).where(
            CompanyContact.company_id == company_id,
            CompanyContact.user_id.is_(None),
            CompanyContact.kind.in_(_PEOPLE_KINDS),
            CompanyContact.name.is_not(None),
        )
    )
    existing = {(row.name or "").casefold(): row for row in result.scalars()}

    inserted = 0
    for candidate in people:
        person = _bounded(candidate)
        if person is None:
            continue
        name = person.name
        if not name or not person.source_url.strip() or person.kind not in _PEOPLE_KINDS:
            continue
        row = existing.get(name.casefold())
        if row is None:
            row = CompanyContact(
                company_id=company_id,
                user_id=None,
                kind=person.kind,
                name=name,
                title=person.title,
                linkedin_url=person.linkedin_url,
                x_url=person.x_url,
                github_url=person.github_url,
                profile_url=person.profile_url,
                source_url=person.source_url,
                source_kind=person.source_kind,
                verified=False,
            )
            session.add(row)
            existing[name.casefold()] = row
            inserted += 1
            continue
        for field in _LINK_FIELDS:
            if not getattr(row, field) and getattr(person, field):
                setattr(row, field, getattr(person, field))
        if not row.title and person.title:
            row.title = person.title
        if _stronger(person.kind, row.kind):
            row.kind = person.kind
    await session.flush()
    return inserted
