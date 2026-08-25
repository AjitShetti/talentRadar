"""
services/companies.py
~~~~~~~~~~~~~~~~~~~~~
Deterministic tools for Company Intelligence.

    list_companies()   — the browsable directory: filter by city/tier/industry,
                         with open-role counts folded in
    directory_facets() — the filter options that directory currently supports
    get_company()      — company record + open jobs + aggregate salary data
    company_intel()    — full intelligence report (what they do, tech stack,
                         open source, hiring trends, salaries) for one company

The directory filters on ``office_cities``, not ``hq_city``: Google is
headquartered in Mountain View but hires in Bengaluru, and the question the
page answers is "who is hiring here", not "who is registered here".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from storage.database import AsyncSessionLocal
from storage.models import Company, CompanyProfile, Job, JobStatus
from storage.repository import UnitOfWork
from services.base import parse_uuid

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Ordering for the directory: the tiers people scan for first come first.
TIER_ORDER = ["big_tech", "unicorn", "gcc", "scaleup", "startup", "services"]

# Industry facet tuning. The catalogue carries a descriptive industry string
# per company (good on a card, bad as a filter), so the dropdown only offers the
# ones that actually group companies.
_MIN_INDUSTRY_FACET_COUNT = 2
_MAX_INDUSTRY_FACETS = 40

TIER_LABELS = {
    "big_tech": "Big Tech",
    "gcc": "Global Capability Centre",
    "unicorn": "Unicorn",
    "scaleup": "Scale-up",
    "startup": "Startup",
    "services": "IT Services",
}


def _tier_rank(tier: str | None) -> int:
    """Sort key so Big Tech leads the directory and IT services trails it."""
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)


def _city_filter(city: str) -> ColumnElement[bool]:
    """
    Portable "has an office in <city>" predicate.

    ``office_cities`` is a Postgres text[] that degrades to JSON on SQLite in
    tests, so ``ARRAY.contains`` is not available on both. ``array_to_string``
    has a SQLite shim in storage/models.py and city names are distinctive
    enough that a substring match does not collide.
    """
    return func.array_to_string(Company.office_cities, " ").ilike(f"%{city}%")


async def _open_role_counts(
    session: AsyncSession, company_ids: list[Any]
) -> dict[Any, int]:
    """Active-job counts keyed by company id, in one grouped query."""
    if not company_ids:
        return {}
    stmt = (
        select(Job.company_id, func.count(Job.id))
        .where(Job.company_id.in_(company_ids), Job.status == JobStatus.ACTIVE)
        .group_by(Job.company_id)
    )
    return {cid: count for cid, count in (await session.execute(stmt)).all()}


def _card(company: Company, tech_stack: list[str], open_roles: int) -> dict[str, Any]:
    """The shape one company takes in the directory grid."""
    return {
        "id": str(company.id),
        "name": company.name,
        "domain": company.domain,
        "website_url": company.website_url,
        "logo_url": company.logo_url,
        "tier": company.tier,
        "tier_label": TIER_LABELS.get(company.tier or ""),
        "industry": company.industry,
        "description": company.description,
        "hq_city": company.hq_city,
        "hq_country": company.hq_country,
        "office_cities": list(company.office_cities or []),
        "employee_count_range": company.employee_count_range,
        "founded_year": company.founded_year,
        "github_org": company.github_org,
        "careers_url": company.careers_url,
        "tech_stack": tech_stack[:8],
        "open_roles": open_roles,
    }


async def list_companies(
    *,
    city: str | None = "Bengaluru",
    tier: str | None = None,
    industry: str | None = None,
    q: str | None = None,
    has_open_roles: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """
    The Company Intel directory.

    Only companies carrying a curated ``description`` are listed — the
    ``companies`` table also accumulates thin rows created as a side effect of
    job ingestion (a name and nothing else), and those would be dead cards.
    Seed the catalogue (``python scripts/seed_companies.py``) to populate it.
    """
    session = AsyncSessionLocal()
    try:
        stmt = select(Company).where(Company.description.is_not(None))
        if city:
            stmt = stmt.where(_city_filter(city))
        if tier:
            stmt = stmt.where(Company.tier == tier)
        if industry:
            # Substring, not equality: the catalogue has ~140 distinct industry
            # strings, so "Fintech" has to reach "Fintech & Payments" and
            # "Fintech & Lending" or the filter returns a single card.
            stmt = stmt.where(Company.industry.ilike(f"%{industry}%"))
        if q:
            pattern = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Company.name.ilike(pattern),
                    Company.industry.ilike(pattern),
                    Company.description.ilike(pattern),
                    cast(Company.domain, String).ilike(pattern),
                )
            )

        companies = list((await session.execute(stmt)).scalars().all())
        counts = await _open_role_counts(session, [c.id for c in companies])

        # Tech stack comes from the seeded profile; fall back to whatever the
        # live postings for that company advertise.
        profiles = {}
        if companies:
            rows = (
                await session.execute(
                    select(CompanyProfile).where(
                        CompanyProfile.company_id.in_([c.id for c in companies])
                    )
                )
            ).scalars().all()
            profiles = {p.company_id: list(p.tech_stack or []) for p in rows}
        await session.commit()

        cards = [
            _card(c, profiles.get(c.id, []), counts.get(c.id, 0))
            for c in companies
            if not has_open_roles or counts.get(c.id, 0) > 0
        ]
        # Big Tech first, then by open roles, then alphabetically — so an
        # unfiltered page opens on the names people are looking for.
        cards.sort(key=lambda c: (_tier_rank(c["tier"]), -c["open_roles"], c["name"].lower()))

        total = len(cards)
        return {
            "companies": cards[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "city": city,
        }
    finally:
        await session.close()


async def directory_facets(city: str | None = "Bengaluru") -> dict[str, Any]:
    """Filter options for the directory, counted against what is actually there."""
    session = AsyncSessionLocal()
    try:
        base = select(Company).where(Company.description.is_not(None))
        if city:
            base = base.where(_city_filter(city))
        companies = list((await session.execute(base)).scalars().all())

        cities = select(Company.office_cities).where(Company.description.is_not(None))
        all_cities: set[str] = set()
        for row in (await session.execute(cities)).scalars().all():
            all_cities.update(row or [])
        await session.commit()

        tiers: dict[str, int] = {}
        industries: dict[str, int] = {}
        for company in companies:
            if company.tier:
                tiers[company.tier] = tiers.get(company.tier, 0) + 1
            if company.industry:
                industries[company.industry] = industries.get(company.industry, 0) + 1

        return {
            "city": city,
            "cities": sorted(all_cities),
            "tiers": [
                {"value": t, "label": TIER_LABELS.get(t, t), "count": tiers[t]}
                for t in TIER_ORDER
                if t in tiers
            ],
            # Only industries shared by at least two companies are offered as
            # filter options; a one-company industry is noise in a dropdown, and
            # those companies remain reachable via search and the tier chips.
            "industries": [
                {"value": name, "count": count}
                for name, count in sorted(industries.items(), key=lambda kv: (-kv[1], kv[0]))
                if count >= _MIN_INDUSTRY_FACET_COUNT
            ][:_MAX_INDUSTRY_FACETS],
            "industries_total": len(industries),
            "total": len(companies),
        }
    finally:
        await session.close()



async def get_company(
    company_id: str | None = None,
    name: str | None = None,
    *,
    with_jobs: bool = True,
    job_limit: int = 20,
) -> dict[str, Any] | None:
    """
    Fetch a company by id or (fuzzy) name, optionally with its open jobs
    and a salary aggregate computed from those jobs.

    Returns ``None`` when not found.
    """
    session = AsyncSessionLocal()
    try:
        uow = UnitOfWork(session)
        company: Company | None = None
        if company_id and parse_uuid(company_id):
            company = await uow.companies.get(parse_uuid(company_id))
        elif name:
            company, _ = await uow.companies.search(name=name, limit=1)

        if company is None:
            return None

        jobs: list[Job] = []
        if with_jobs:
            jobs, _ = await uow.jobs.list_by_company(
                company.id, status=JobStatus.ACTIVE, limit=job_limit
            )

        # Salary aggregate from active jobs
        stmt = select(
            func.min(Job.salary_min),
            func.max(Job.salary_max),
            func.avg(Job.salary_min),
            func.count(Job.id),
        ).where(
            Job.company_id == company.id,
            Job.status == JobStatus.ACTIVE,
            Job.salary_min.is_not(None),
        )
        row = (await session.execute(stmt)).one()
        await session.commit()

        return {
            "id": str(company.id),
            "name": company.name,
            "domain": company.domain,
            "website_url": company.website_url,
            "industry": company.industry,
            "hq_country": company.hq_country,
            "hq_city": company.hq_city,
            "employee_count_range": company.employee_count_range,
            "founded_year": company.founded_year,
            "open_jobs_count": len(jobs),
            "salary_stats": {
                "min": row[0],
                "max": row[1],
                "avg": float(row[2]) if row[2] else None,
                "count_with_salary": row[3] or 0,
            },
            "jobs": [
                {
                    "id": str(j.id),
                    "title": j.title,
                    "location": j.location_raw,
                    "is_remote": j.is_remote,
                    "seniority": j.seniority.value if j.seniority else None,
                    "salary_raw": j.salary_raw,
                    "skills": list(j.skills or []),
                    "source_url": j.source_url,
                    "posted_at": j.posted_at.isoformat() if j.posted_at else None,
                }
                for j in jobs
            ],
        }
    finally:
        await session.close()


async def company_intel(
    company_id: str | None = None,
    name: str | None = None,
    *,
    with_github: bool = True,
    job_limit: int = 25,
) -> dict[str, Any] | None:
    """
    Everything the Company Intel side panel shows for one company:
    what they do, the technologies they work on, their public GitHub, and the
    roles TalentRadar currently has open for them.

    ``tech_stack`` merges the curated catalogue list with skills actually named
    in that company's live postings — the catalogue says what they build with,
    the postings say what they are hiring for right now, and both are useful.

    The GitHub call is best-effort: it is cached for a day and returns ``None``
    on a 404, a rate limit or a timeout, so this never fails because of it.
    Pass ``with_github=False`` to skip it entirely.
    """
    session = AsyncSessionLocal()
    try:
        uow = UnitOfWork(session)
        company: Company | None = None
        if company_id and parse_uuid(company_id):
            company = await uow.companies.get(parse_uuid(company_id))
        elif name:
            company, _ = await uow.companies.search(name=name, limit=1)
        if company is None:
            return None

        profile = await _get_profile(session, company.id)
        jobs, _ = await uow.jobs.list_by_company(
            company.id, status=JobStatus.ACTIVE, limit=job_limit
        )

        salary_stmt = select(
            func.min(Job.salary_min),
            func.max(Job.salary_max),
            func.avg(Job.salary_min),
            func.count(Job.id),
        ).where(
            Job.company_id == company.id,
            Job.status == JobStatus.ACTIVE,
            Job.salary_min.is_not(None),
        )
        salary_row = (await session.execute(salary_stmt)).one()
        await session.commit()

        # Curated stack first (it is ordered deliberately), then anything the
        # live postings add, de-duplicated case-insensitively.
        curated = list(profile.tech_stack or []) if profile else []
        posting_skills: dict[str, int] = {}
        for job in jobs:
            for skill in job.skills or []:
                posting_skills[skill] = posting_skills.get(skill, 0) + 1
        seen = {t.casefold() for t in curated}
        from_postings = [
            skill
            for skill, _ in sorted(posting_skills.items(), key=lambda kv: -kv[1])
            if skill.casefold() not in seen
        ][:10]

        github = None
        if with_github and company.github_org:
            from services.github import org_snapshot

            github = await org_snapshot(company.github_org)

        return {
            "company_id": str(company.id),
            "id": str(company.id),
            "name": company.name,
            "description": company.description,
            "domain": company.domain,
            "website_url": company.website_url,
            "careers_url": company.careers_url,
            "linkedin_url": company.linkedin_url,
            "logo_url": company.logo_url,
            "tier": company.tier,
            "tier_label": TIER_LABELS.get(company.tier or ""),
            "industry": company.industry,
            "hq_city": company.hq_city,
            "hq_country": company.hq_country,
            "office_cities": list(company.office_cities or []),
            "employee_count_range": company.employee_count_range,
            "founded_year": company.founded_year,
            "github_org": company.github_org,
            "github": github,
            "tech_stack": curated + from_postings,
            "tech_stack_curated": curated,
            "tech_stack_from_postings": from_postings,
            "salary_ranges": (profile.salary_ranges if profile else None) or {
                "min": salary_row[0],
                "max": salary_row[1],
                "avg": float(salary_row[2]) if salary_row[2] else None,
                "count_with_salary": salary_row[3] or 0,
            },
            "interview_patterns": (profile.interview_patterns if profile else None) or {},
            "hiring_trends": (profile.hiring_trends if profile else None) or {
                "open_jobs": len(jobs)
            },
            "culture_summary": profile.culture_summary if profile else None,
            "source": profile.source if profile else "computed",
            "has_profile": profile is not None,
            "open_roles": len(jobs),
            "jobs": [
                {
                    "id": str(j.id),
                    "title": j.title,
                    "location": j.location_raw,
                    "is_remote": j.is_remote,
                    "seniority": j.seniority.value if j.seniority else None,
                    "salary_raw": j.salary_raw,
                    "skills": list(j.skills or [])[:6],
                    "source_url": j.source_url,
                    "posted_at": j.posted_at.isoformat() if j.posted_at else None,
                }
                for j in jobs
            ],
        }
    finally:
        await session.close()


async def _get_profile(session, company_id: Any) -> CompanyProfile | None:
    stmt = (
        select(CompanyProfile)
        .where(CompanyProfile.company_id == company_id)
        .options(selectinload(CompanyProfile.company))
    )
    return (await session.execute(stmt)).scalar_one_or_none()
