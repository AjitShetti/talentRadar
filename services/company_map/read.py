"""
services/company_map/read.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The read side of the company map: what the map, the news strip and the
drawer show. Reads stored rows only.

* :func:`map_rows` — every listed company as one positional row, cached for an
  hour per city so the map's first paint never waits on a full table scan.
* :func:`hiring_for_you` — companies with an open Bengaluru role matching the
  user's target role. Separate from the map so the map stays cacheable
  across users.
* :func:`people_for` — founders, leaders and engineers, curated rows only.
* :func:`news_for` / :func:`latest_news` — stored news items.

No LLM call and no outside request happens here. LinkedIn links are built,
never fetched (``domain/linkedin_links.py``).

"Listed" (:func:`listed_clause`) is the one rule for which companies the map,
the list and the facets show: a company with a summary or a description that
is in the city — an office there, a canonical area, or its headquarters.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select

from domain.areas import in_bengaluru
from domain.company_taxonomy import size_bucket
from domain.geo import city_search_terms, resolve_city
from domain.linkedin_links import person_search_link
from services.base import parse_uuid
from services.cache_backend import CacheBackend
from services.company_contacts import _serialise
from storage.database import AsyncSessionLocal
from storage.models import Company, CompanyContact, CompanyNews, Job, JobStatus, Profile
from storage.repository import tokenize_and_expand_query

if TYPE_CHECKING:
    import uuid

    from sqlalchemy import ColumnElement

logger = logging.getLogger(__name__)

#: The city the area taxonomy (``domain/areas.py``) covers.
AREA_CITY = "Bengaluru"

MAP_TTL_SECONDS = 3600
RAISED_RECENTLY_DAYS = 90

#: Positional row layout of :func:`map_rows`, kept here for readers.
MAP_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "lat",
    "lng",
    "precision",
    "tier",
    "stage",
    "sector",
    "area",
    "size",
    "open_roles",
    "raised_90d",
)

NEWS_CATEGORIES: tuple[str, ...] = ("funding", "acquisition", "launch", "other")

#: contact kind -> key in :func:`people_for`'s answer, in drawer order.
PEOPLE_GROUPS: dict[str, str] = {
    "founder": "founders",
    "leader": "leaders",
    "engineer": "engineers",
}

_COORD_DECIMALS = 5  # ~1 m; more is payload, not precision
_MAX_PEOPLE = 60
_MAX_NEWS = 50


# ---------------------------------------------------------------------------
# The "listed" rule
# ---------------------------------------------------------------------------


def canonical_city(city: str | None) -> str | None:
    """'Bangalore' -> 'Bengaluru'; ``None`` for anything that is not a known city."""
    return resolve_city(city) if city else None


def _city_terms(city: str) -> tuple[str, ...]:
    return city_search_terms(city) or (city,)


def has_text_clause() -> ColumnElement[bool]:
    """A company says what it does: a one-line summary or a curated description."""
    return or_(Company.summary.is_not(None), Company.description.is_not(None))


def in_city_clause(city: str) -> ColumnElement[bool]:
    """
    The company is in ``city``: an office there, its headquarters there, or
    (for Bengaluru, the only city with an area taxonomy) a canonical area.

    ``office_cities`` is a Postgres text[] that degrades to JSON on SQLite, so
    it is matched through ``array_to_string`` (shimmed for SQLite in
    storage/models.py), as ``services/companies.py`` always has.
    """
    terms = _city_terms(city)
    clauses: list[ColumnElement[bool]] = [
        func.array_to_string(Company.office_cities, " ").ilike(f"%{term}%") for term in terms
    ]
    clauses.extend(Company.hq_city.ilike(f"%{term}%") for term in terms)
    if canonical_city(city) == AREA_CITY:
        clauses.append(Company.area.is_not(None))
    return or_(*clauses)


def listed_clause(city: str | None) -> ColumnElement[bool]:
    """Summary or description, and (when a city is given) in that city."""
    if not city:
        return has_text_clause()
    return and_(has_text_clause(), in_city_clause(city))


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; stored values are UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def raised_within(last_funding_at: datetime | None, days: int, now: datetime) -> bool:
    funded = _aware(last_funding_at)
    return funded is not None and funded >= now - timedelta(days=days)


async def _open_role_counts(session: Any, company_ids: Sequence[uuid.UUID]) -> dict[Any, int]:
    if not company_ids:
        return {}
    stmt = (
        select(Job.company_id, func.count(Job.id))
        .where(Job.company_id.in_(list(company_ids)), Job.status == JobStatus.ACTIVE)
        .group_by(Job.company_id)
    )
    return {cid: int(count) for cid, count in (await session.execute(stmt)).all()}


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), _COORD_DECIMALS)


def _position(
    city: str, lat: float | None, lng: float | None, precision: str | None
) -> tuple[float | None, float | None, str]:
    """
    Where to draw a company, never more precisely than we know.

    No coordinate, or a Bengaluru company whose stored point is outside the
    Bengaluru box, is ``city`` precision: List only.
    """
    if lat is None or lng is None:
        return None, None, "city"
    if city == AREA_CITY and not in_bengaluru(lat, lng):
        return None, None, "city"
    return _round(lat), _round(lng), precision or "exact"


async def _build_map(city: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(
                Company.id,
                Company.name,
                Company.lat,
                Company.lng,
                Company.geo_precision,
                Company.tier,
                Company.stage,
                Company.sector,
                Company.area,
                Company.employee_count,
                Company.employee_count_range,
                Company.last_funding_at,
            )
            .where(listed_clause(city))
            .order_by(Company.name)
        )
        companies = (await session.execute(stmt)).all()
        counts = await _open_role_counts(session, [c.id for c in companies])

    rows: list[list[Any]] = []
    for c in companies:
        lat, lng, precision = _position(city, c.lat, c.lng, c.geo_precision)
        rows.append(
            [
                str(c.id),
                c.name,
                lat,
                lng,
                precision,
                c.tier,
                c.stage,
                c.sector,
                c.area,
                size_bucket(c.employee_count, c.employee_count_range),
                counts.get(c.id, 0),
                raised_within(c.last_funding_at, RAISED_RECENTLY_DAYS, now),
            ]
        )
    return {
        "generated_at": now.isoformat(),
        "city": city,
        "total": len(rows),
        "pinned": sum(1 for row in rows if row[2] is not None),
        "rows": rows,
    }


async def map_rows(city: str = "Bengaluru") -> dict[str, Any]:
    """
    ``{generated_at, city, total, pinned, rows}`` for every listed company.

    Each row is positional (:data:`MAP_COLUMNS`) to keep the payload small;
    ``lat``/``lng`` are null at ``city`` precision. Cached for an hour under
    ``tr:map:<city>``, keyed by the canonical city name so "Bangalore" and
    "Bengaluru" share one entry. An unknown city is an empty map, not cached.
    """
    canonical = canonical_city(city)
    if canonical is None:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "city": city,
            "total": 0,
            "pinned": 0,
            "rows": [],
        }
    key = f"tr:map:{canonical}"
    cached = await CacheBackend.get_json(key)
    if isinstance(cached, dict):
        return cached
    payload = await _build_map(canonical)
    await CacheBackend.set_json(key, payload, MAP_TTL_SECONDS)
    return payload


# ---------------------------------------------------------------------------
# Hiring for you
# ---------------------------------------------------------------------------


def _title_matches(role: str) -> ColumnElement[bool] | None:
    """
    Every keyword of a target role, each with its search synonyms, in the title.

    The tokens and synonyms are search's own (``tokenize_and_expand_query``),
    so "Backend Engineer" here matches what it matches in search.
    """
    groups = tokenize_and_expand_query(role)
    if not groups:
        return None
    return and_(*(or_(*(Job.title.ilike(f"%{term}%") for term in group)) for group in groups))


async def target_roles(session: Any, user_id: uuid.UUID) -> list[str]:
    roles = (
        await session.execute(select(Profile.target_roles).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    return [r for r in (roles or []) if isinstance(r, str) and r.strip()]


async def hiring_for_you(user_id: str, *, city: str = AREA_CITY) -> list[str]:
    """
    Ids of listed companies with an open role in ``city`` whose title matches
    one of the user's target roles. Empty without a profile or a target role.
    """
    uid = parse_uuid(user_id)
    canonical = canonical_city(city)
    if uid is None or canonical is None:
        return []
    async with AsyncSessionLocal() as session:
        matches = [
            m
            for m in (_title_matches(r) for r in await target_roles(session, uid))
            if m is not None
        ]
        if not matches:
            return []
        located = or_(
            *(
                clause
                for term in _city_terms(canonical)
                for clause in (Job.city.ilike(f"%{term}%"), Job.location_raw.ilike(f"%{term}%"))
            )
        )
        stmt = (
            select(Job.company_id)
            .join(Company, Company.id == Job.company_id)
            .where(
                Job.status == JobStatus.ACTIVE,
                or_(*matches),
                located,
                listed_clause(canonical),
            )
            .distinct()
        )
        ids = (await session.execute(stmt)).scalars().all()
    return sorted(str(cid) for cid in ids)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def _empty_people() -> dict[str, list[dict[str, Any]]]:
    return {group: [] for group in PEOPLE_GROUPS.values()}


async def people_for(
    company_id: str | uuid.UUID, *, company_name: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """
    ``{founders, leaders, engineers}``: curated people only (``user_id IS NULL``),
    each serialised as a contact. A person with no LinkedIn profile gets a
    people-search deep link in ``linkedin_search_url``, built here and never
    stored.
    """
    cid = parse_uuid(str(company_id))
    people = _empty_people()
    if cid is None:
        return people
    async with AsyncSessionLocal() as session:
        if company_name is None:
            company_name = (
                await session.execute(select(Company.name).where(Company.id == cid))
            ).scalar_one_or_none()
        stmt = (
            select(CompanyContact)
            .where(
                CompanyContact.company_id == cid,
                CompanyContact.user_id.is_(None),
                CompanyContact.kind.in_(list(PEOPLE_GROUPS)),
                CompanyContact.name.is_not(None),
            )
            .order_by(CompanyContact.created_at, CompanyContact.name)
            .limit(_MAX_PEOPLE)
        )
        contacts = (await session.execute(stmt)).scalars().all()

    for contact in contacts:
        row = _serialise(contact)
        row["linkedin_search_url"] = (
            person_search_link(contact.name or "", company_name or "")
            if not contact.linkedin_url and contact.name
            else None
        )
        people[PEOPLE_GROUPS[contact.kind]].append(row)
    return people


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def _news_item(news: CompanyNews, company_name: str | None = None) -> dict[str, Any]:
    published = _aware(news.published_at)
    return {
        "id": str(news.id),
        "url": news.url,
        "source": news.source,
        "title": news.title,
        "snippet": news.snippet,
        "published_at": published.isoformat() if published else None,
        "category": news.category,
        "round": news.round,
        "amount_text": news.amount_text,
        "amount_usd": news.amount_usd,
        "investors": list(news.investors or []),
        "company_id": str(news.company_id) if news.company_id else None,
        "company_name": company_name,
    }


def _newest_first() -> tuple[Any, ...]:
    return (
        CompanyNews.published_at.is_(None),
        CompanyNews.published_at.desc(),
        CompanyNews.created_at.desc(),
    )


async def news_for(company_id: str | uuid.UUID, limit: int = 5) -> list[dict[str, Any]]:
    """A company's stored news, newest first."""
    cid = parse_uuid(str(company_id))
    if cid is None:
        return []
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CompanyNews)
            .where(CompanyNews.company_id == cid)
            .order_by(*_newest_first())
            .limit(max(1, min(limit, _MAX_NEWS)))
        )
        items = (await session.execute(stmt)).scalars().all()
    return [_news_item(n) for n in items]


async def latest_news(
    limit: int = 10, category: str | None = None, *, city: str = AREA_CITY
) -> list[dict[str, Any]]:
    """
    The map's news strip: newest items linked to a listed company.

    Everything else (unmatched items, other cities) stays stored for phase 2
    but is not shown here.
    """
    canonical = canonical_city(city)
    if canonical is None:
        return []
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CompanyNews, Company.name)
            .join(Company, Company.id == CompanyNews.company_id)
            .where(listed_clause(canonical))
        )
        if category:
            stmt = stmt.where(CompanyNews.category == category)
        stmt = stmt.order_by(*_newest_first()).limit(max(1, min(limit, _MAX_NEWS)))
        rows = (await session.execute(stmt)).all()
    return [_news_item(news, name) for news, name in rows]
