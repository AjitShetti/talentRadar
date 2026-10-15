"""
services/company_map/store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Where importer output meets ``companies``.

* :func:`find_existing` decides identity: domain, then Wikidata id, then an
  exact normalised name, and the name only when neither side carries a
  conflicting domain. A name never merges two rows.
* Job ingestion keys an employer on ``<slug>.talentradar.internal``
  (:data:`PLACEHOLDER_SUFFIX`). That placeholder is not an identity: a real
  domain replaces it, and it never makes a name ambiguous.
* A record with no domain and no Wikidata id (a news headline) creates a row
  only when no row shares its name, so the same raise reported by four
  outlets cannot make four companies.
* :func:`upsert_company` writes what a source may write
  (``domain.company_identity.merge_fields``) and records provenance in
  ``metadata.provenance``.
* The tech verdict (``services/company_map/tech.py``) is recomputed on every
  upsert from everything stored, and only ever moves towards listed.
* :func:`apply_location` keeps the most precise position: exact > street >
  area, and a curated position is never replaced.
* :func:`due` picks the rows each refresh step should work on, companies
  with open roles first, and skips rows a step tried recently so a few
  unresolvable companies cannot eat every day's budget.

Callers own the transaction; nothing here commits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.areas import area_for_point, in_bengaluru
from domain.company_identity import merge_fields, normalize_domain, normalize_name, slugify
from services.company_map.people import upsert_people
from services.company_map.tech import apply_tech
from services.company_map.types import GeoHit, ImportedCompany
from storage.models import Company, Job, JobStatus

#: Importers whose every result is a Bengaluru company (Wikidata asks for
#: HQ = Bengaluru, Overpass for offices inside the Bengaluru bbox, YC for a
#: Bengaluru office, GitHub for an organisation whose profile says Bengaluru).
BENGALURU_SOURCES = frozenset({"wikidata", "osm", "yc", "github_org", "hiring_list"})

#: Fields an import may write through precedence. ``description`` is not one
#: of them: a source's short description is kept in metadata for the summary
#: step, so "Indian company" alone never makes a row listed.
_MERGED = (
    "website_url",
    "address",
    "founded_year",
    "employee_count",
    "x_url",
    "linkedin_url",
    "github_org",
    "logo_url",
    "sector",
    "tier",
    "stage",
)

_PRECISION_RANK = {"exact": 0, "street": 1, "area": 2, "city": 3}

#: How long a step leaves a row alone after trying it.
SITE_REFRESH = timedelta(days=30)
RETRY_AFTER = timedelta(days=30)
GITHUB_REFRESH = timedelta(days=7)

STEP_FIELDS = {"sites", "geocode", "summaries", "github"}


async def _one(session: AsyncSession, query: Select[tuple[Company]]) -> Company | None:
    return (await session.execute(query.limit(1))).scalars().first()


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: Job ingestion's placeholder domain (``ingestion/pipeline.py``,
#: ``services/job_persistence.py``).
PLACEHOLDER_SUFFIX = ".talentradar.internal"


def real_domain(domain: str | None) -> str | None:
    """``domain`` unless it is empty or job ingestion's placeholder."""
    return None if not domain or domain.endswith(PLACEHOLDER_SUFFIX) else domain


async def _same_name(session: AsyncSession, name: str) -> list[Company]:
    """Every row whose normalised name equals ``name``'s."""
    wanted = normalize_name(name)
    if not wanted:
        return []
    first_word = wanted.split()[0]
    candidates = (
        await session.execute(
            select(Company).where(
                func.lower(Company.name).like(f"{_escape_like(first_word)}%", escape="\\")
            )
        )
    ).scalars()
    return [c for c in candidates if normalize_name(c.name) == wanted]


async def find_existing(
    session: AsyncSession, *, domain: str | None, wikidata_id: str | None, name: str
) -> Company | None:
    """The row this record describes, or ``None``. See the module docstring."""
    if domain:
        found = await _one(session, select(Company).where(Company.domain == domain))
        if found:
            return found
    if wikidata_id:
        found = await _one(session, select(Company).where(Company.wikidata_id == wikidata_id))
        if found:
            return found

    matches = [
        c
        for c in await _same_name(session, name)
        if (not domain or not real_domain(c.domain) or c.domain == domain)
        and (not wikidata_id or not c.wikidata_id or c.wikidata_id == wikidata_id)
    ]
    if len(matches) == 1:
        return matches[0]
    # A jobs placeholder beside the employer's real row is one employer seen
    # twice, not two employers: the row with the real domain is the one.
    with_domain = [c for c in matches if real_domain(c.domain)]
    if len(with_domain) == 1:
        return with_domain[0]
    # Two real rows with the same name: attaching to either would be a guess.
    return None


async def company_for_posting(session: AsyncSession, name: str) -> Company | None:
    """
    The row a scraped posting's employer name belongs to, before job ingestion
    falls back to creating its placeholder row.

    Once an importer has given a jobs row its real domain, the placeholder no
    longer exists; without this the next posting would create it again.
    """
    return await find_existing(session, domain=None, wikidata_id=None, name=name)


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)
    taken = set(
        (
            await session.execute(
                select(Company.slug).where(
                    or_(
                        Company.slug == base,
                        Company.slug.like(f"{_escape_like(base)}-%", escape="\\"),
                    )
                )
            )
        ).scalars()
    )
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}"[:96] in taken:
        n += 1
    suffix = f"-{n}"
    return f"{base[: 96 - len(suffix)]}{suffix}"


async def _free(session: AsyncSession, column: Any, value: str, company: Company) -> bool:
    """True when no other row already holds ``value`` in a unique column."""
    holder = await _one(session, select(Company).where(column == value))
    return holder is None or holder is company


def apply_location(company: Company, hit: GeoHit) -> None:
    """Take ``hit`` when it is more precise than what the row has; never over curated."""
    if not in_bengaluru(hit.lat, hit.lng) or company.geo_source == "curated":
        return
    current = _PRECISION_RANK.get(company.geo_precision or "", len(_PRECISION_RANK))
    new = _PRECISION_RANK.get(hit.precision, len(_PRECISION_RANK))
    same_source_refresh = company.geo_source == hit.source and new == current
    if company.lat is not None and new >= current and not same_source_refresh:
        return
    company.lat, company.lng = hit.lat, hit.lng
    company.geo_precision = hit.precision
    company.geo_source = hit.source
    if not company.area:
        company.area = hit.area or area_for_point(hit.lat, hit.lng)


def _metadata(company: Company) -> dict[str, Any]:
    # A new dict each time: JSONB columns only notice reassignment.
    return dict(company.extra_metadata or {})


def _current(company: Company) -> dict[str, Any]:
    fields: dict[str, Any] = {field: getattr(company, field) for field in _MERGED}
    fields["last_funding_at"] = company.last_funding_at
    return fields


async def upsert_company_ex(
    session: AsyncSession, imported: ImportedCompany, source: str, *, create: bool = True
) -> tuple[Company | None, bool]:
    """:func:`upsert_company`, also saying whether the row was created."""
    domain = normalize_domain(imported.domain) or normalize_domain(imported.website_url)
    company = await find_existing(
        session, domain=domain, wikidata_id=imported.wikidata_id, name=imported.name
    )
    created = company is None
    if company is None:
        if not create:
            return None, False
        if not domain and not imported.wikidata_id and await _same_name(session, imported.name):
            # The name is taken and this record cannot say which row it means.
            return None, False
        company = Company(
            name=imported.name.strip()[:256], slug=await _unique_slug(session, imported.name)
        )
        session.add(company)

    if (
        domain
        and not real_domain(company.domain)
        and await _free(session, Company.domain, domain, company)
    ):
        company.domain = domain
    if (
        imported.wikidata_id
        and not company.wikidata_id
        and await _free(session, Company.wikidata_id, imported.wikidata_id, company)
    ):
        company.wikidata_id = imported.wikidata_id
    if not company.slug:
        company.slug = await _unique_slug(session, company.name)

    metadata = _metadata(company)
    incoming = {field: getattr(imported, field) for field in _MERGED}
    updates, provenance = merge_fields(
        _current(company), dict(metadata.get("provenance") or {}), incoming, source
    )
    for field, value in updates.items():
        setattr(company, field, value)
    metadata["provenance"] = provenance
    if imported.description:
        metadata[f"{source}_description"] = imported.description.strip()[:500]
    if imported.industry_labels:
        metadata[f"{source}_industries"] = list(imported.industry_labels)[:20]
    company.extra_metadata = metadata
    apply_tech(company)

    if source in BENGALURU_SOURCES:
        cities = list(company.office_cities or [])
        if not any(c.lower() in {"bengaluru", "bangalore"} for c in cities):
            company.office_cities = [*cities, "Bengaluru"]
        if source == "wikidata" and not company.hq_city:
            company.hq_city, company.hq_country = "Bengaluru", company.hq_country or "India"

    if imported.lat is not None and imported.lng is not None:
        apply_location(
            company,
            GeoHit(lat=imported.lat, lng=imported.lng, precision="exact", source=source),
        )

    await session.flush()
    if imported.people:
        await upsert_people(session, company.id, imported.people)
    return company, created


async def upsert_company(
    session: AsyncSession, imported: ImportedCompany, source: str, *, create: bool = True
) -> Company | None:
    """Merge one imported record into ``companies``; ``None`` when ``create`` is off and it is new."""
    company, _created = await upsert_company_ex(session, imported, source, create=create)
    return company


def _not_tried_since(key: str, cutoff: datetime) -> Any:
    stamp = Company.extra_metadata[key].astext
    return or_(Company.extra_metadata.is_(None), stamp.is_(None), stamp < cutoff.isoformat())


def _in_bengaluru() -> Any:
    cities = func.lower(func.array_to_string(Company.office_cities, ","))
    return or_(
        cities.like("%bengaluru%"),
        cities.like("%bangalore%"),
        Company.area.is_not(None),
        func.lower(Company.hq_city).in_(["bengaluru", "bangalore"]),
    )


async def due(session: AsyncSession, step: str, limit: int) -> list[Company]:
    """Rows the row-based refresh ``step`` should work on now, open roles first."""
    if step not in STEP_FIELDS:
        raise ValueError(f"step {step!r} does not work on rows")
    now = datetime.now(UTC)
    if step == "sites":
        where = [
            Company.website_url.is_not(None),
            or_(Company.enriched_at.is_(None), Company.enriched_at < now - SITE_REFRESH),
        ]
    elif step == "geocode":
        where = [
            Company.lat.is_(None),
            or_(Company.address.is_not(None), Company.pincode.is_not(None), _in_bengaluru()),
            _not_tried_since("geocode_at", now - RETRY_AFTER),
        ]
    elif step == "summaries":
        where = [
            Company.summary.is_(None),
            Company.enriched_at.is_not(None),
            _not_tried_since("summary_at", now - RETRY_AFTER),
        ]
    else:
        where = [
            Company.github_org.is_not(None),
            _not_tried_since("github_people_at", now - GITHUB_REFRESH),
        ]

    open_roles = (
        select(Job.company_id, func.count().label("n"))
        .where(Job.status == JobStatus.ACTIVE)
        .group_by(Job.company_id)
        .subquery()
    )
    query = (
        select(Company)
        .outerjoin(open_roles, open_roles.c.company_id == Company.id)
        .where(*where)
        .order_by(func.coalesce(open_roles.c.n, 0).desc(), Company.name)
        .limit(limit)
    )
    return list((await session.execute(query)).scalars())


def mark_tried(company: Company, key: str) -> None:
    """Record that a step looked at this row, so :func:`due` rests it."""
    metadata = _metadata(company)
    metadata[key] = datetime.now(UTC).isoformat()
    company.extra_metadata = metadata
