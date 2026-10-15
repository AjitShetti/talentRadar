"""
tests/test_company_map_store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The store that merges what the importers find into ``companies``: identity,
source precedence, slugs, location precision and which rows each refresh
step picks up. Runs against Postgres (``integration``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from services.company_map.store import apply_location, due, find_existing, upsert_company
from services.company_map.types import GeoHit, ImportedCompany, PersonCandidate
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company, CompanyContact

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    """Tables created if missing, each test rolled back (see test_company_map_people)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
    await engine.dispose()


def _imported(**overrides: object) -> ImportedCompany:
    fields: dict[str, object] = {
        "name": "Razorpay",
        "domain": "www.razorpay.com",
        "website_url": "https://www.razorpay.com",
        "wikidata_id": "Q60747032",
    }
    fields.update(overrides)
    return ImportedCompany(**fields)  # type: ignore[arg-type]


async def _count(session, **where: object) -> int:
    query = select(func.count()).select_from(Company)
    for column, value in where.items():
        query = query.where(getattr(Company, column) == value)
    return int((await session.execute(query)).scalar_one())


async def test_upsert_matches_by_normalised_domain(db_session):
    db_session.add(Company(name="Razorpay Software Pvt Ltd", domain="razorpay.com"))
    await db_session.flush()

    company = await upsert_company(db_session, _imported(), "wikidata")

    assert company is not None
    assert company.domain == "razorpay.com"
    assert company.wikidata_id == "Q60747032"
    assert await _count(db_session, domain="razorpay.com") == 1


async def test_same_name_different_domain_never_merges(db_session):
    db_session.add(Company(name="Zeta", domain="zeta.tech"))
    await db_session.flush()

    other = await upsert_company(
        db_session,
        _imported(name="Zeta", domain="zeta.in", website_url="https://zeta.in", wikidata_id=None),
        "osm",
    )

    assert other is not None and other.domain == "zeta.in"
    rows = (await db_session.execute(select(Company).where(Company.name == "Zeta"))).scalars()
    assert {c.domain for c in rows} == {"zeta.tech", "zeta.in"}


async def test_name_match_attaches_to_a_row_without_a_domain(db_session):
    db_session.add(Company(name="Acme Technologies Private Limited"))
    await db_session.flush()

    found = await find_existing(
        db_session, domain="acme.example", wikidata_id=None, name="Acme Technologies"
    )

    assert found is not None and found.name == "Acme Technologies Private Limited"


async def test_curated_description_survives_a_wikidata_upsert(db_session):
    db_session.add(
        Company(
            name="Razorpay", domain="razorpay.com", description="Curated words.", tier="unicorn"
        )
    )
    await db_session.flush()

    company = await upsert_company(
        db_session,
        _imported(description="Indian fintech company", tier="startup", founded_year=2014),
        "wikidata",
    )

    assert company is not None
    assert company.description == "Curated words."
    assert company.tier == "unicorn"
    assert company.founded_year == 2014
    assert company.extra_metadata["provenance"]["founded_year"] == "wikidata"
    # The short Wikidata description feeds the summary step, never `description`.
    assert company.extra_metadata["wikidata_description"] == "Indian fintech company"


async def test_slug_collision_gets_a_suffix(db_session):
    db_session.add(Company(name="Nova", domain="nova.one", slug="nova"))
    await db_session.flush()

    company = await upsert_company(
        db_session,
        _imported(name="Nova", domain="nova.two", website_url=None, wikidata_id=None),
        "osm",
    )

    assert company is not None and company.slug == "nova-2"


async def test_create_false_never_creates(db_session):
    assert await upsert_company(db_session, _imported(), "news", create=False) is None
    assert await _count(db_session, domain="razorpay.com") == 0


async def test_bengaluru_import_adds_the_city_and_people(db_session):
    founder = PersonCandidate(
        name="Asha Rao",
        kind="founder",
        source_url="https://www.wikidata.org/wiki/Q60747032",
        source_kind="wikidata",
    )
    company = await upsert_company(
        db_session, _imported(lat=12.9352, lng=77.6245, people=(founder,)), "wikidata"
    )

    assert company is not None
    assert company.office_cities == ["Bengaluru"]
    assert company.geo_precision == "exact"
    assert company.area == "Koramangala"
    people = (
        await db_session.execute(
            select(CompanyContact).where(CompanyContact.company_id == company.id)
        )
    ).scalars()
    assert [p.name for p in people] == ["Asha Rao"]


async def test_a_point_outside_bengaluru_never_pins(db_session):
    company = await upsert_company(db_session, _imported(lat=19.07, lng=72.88), "wikidata")
    assert company is not None and company.lat is None


def test_location_keeps_the_more_precise_hit():
    company = Company(name="Acme")
    apply_location(
        company, GeoHit(lat=12.9, lng=77.6, precision="area", source="area", area="BTM Layout")
    )
    apply_location(
        company, GeoHit(lat=12.91, lng=77.61, precision="street", source="nominatim", area=None)
    )
    assert (company.geo_precision, company.lat) == ("street", 12.91)

    apply_location(
        company, GeoHit(lat=12.95, lng=77.65, precision="area", source="area", area="Domlur")
    )
    assert company.lat == 12.91
    assert company.area == "BTM Layout"


def test_curated_location_is_never_replaced():
    company = Company(name="Acme", lat=12.9, lng=77.6, geo_precision="area", geo_source="curated")
    apply_location(company, GeoHit(lat=12.91, lng=77.61, precision="exact", source="osm"))
    assert company.lat == 12.9


async def test_due_sites_skips_recently_read_sites(db_session):
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Company(
                name="Fresh",
                domain="fresh.example",
                website_url="https://fresh.example",
                enriched_at=now - timedelta(days=2),
            ),
            Company(
                name="Stale",
                domain="stale.example",
                website_url="https://stale.example",
                enriched_at=now - timedelta(days=45),
            ),
            Company(name="Never", domain="never.example", website_url="https://never.example"),
        ]
    )
    await db_session.flush()

    names = {c.name for c in await due(db_session, "sites", 10)}

    assert {"Stale", "Never"} <= names
    assert "Fresh" not in names


async def test_due_geocode_skips_a_recent_attempt(db_session):
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    db_session.add_all(
        [
            Company(name="Addressed", domain="a.example", address="HSR Layout, Bengaluru 560102"),
            Company(
                name="Tried",
                domain="t.example",
                address="Somewhere 560001",
                extra_metadata={"geocode_at": recent},
            ),
        ]
    )
    await db_session.flush()

    names = {c.name for c in await due(db_session, "geocode", 10)}

    assert "Addressed" in names
    assert "Tried" not in names


async def test_unknown_step_has_no_rows(db_session):
    with pytest.raises(ValueError):
        await due(db_session, "nonsense", 10)


# ── Rows made by job ingestion (review #1, #2) ───────────────────────────────
# Job ingestion keys an employer on ``<slug>.talentradar.internal``. That is a
# placeholder, not an identity: it must never stop a real record attaching,
# and it must never make a name look ambiguous enough to create a duplicate.


async def test_import_attaches_to_a_jobs_row_and_gives_it_the_real_domain(db_session):
    jobs_row = Company(name="Razorpay", domain="razorpay.talentradar.internal")
    db_session.add(jobs_row)
    await db_session.flush()

    company = await upsert_company(db_session, _imported(), "wikidata")

    assert company is jobs_row
    assert company.domain == "razorpay.com"
    assert await _count(db_session, name="Razorpay") == 1


async def test_ambiguous_name_prefers_the_one_row_with_a_real_domain(db_session):
    jobs_row = Company(name="Acme", domain="acme.talentradar.internal")
    real_row = Company(name="Acme Pvt Ltd", domain="acme.com")
    db_session.add_all([jobs_row, real_row])
    await db_session.flush()

    found = await find_existing(db_session, domain=None, wikidata_id=None, name="Acme")

    assert found is real_row


async def test_a_record_with_no_identity_never_duplicates_a_taken_name(db_session):
    # Two employers called Acme with different real domains: a news item
    # naming "Acme" cannot say which one, so it creates nothing.
    db_session.add_all(
        [Company(name="Acme", domain="acme.com"), Company(name="Acme", domain="acme.in")]
    )
    await db_session.flush()

    company = await upsert_company(
        db_session, ImportedCompany(name="Acme", domain=None, website_url=None), "news"
    )

    assert company is None
    assert await _count(db_session, name="Acme") == 2


async def test_posting_company_resolves_to_the_listed_row_by_name(db_session):
    from services.company_map.store import company_for_posting

    listed = Company(name="Razorpay", domain="razorpay.com")
    db_session.add(listed)
    await db_session.flush()

    assert await company_for_posting(db_session, "Razorpay Pvt. Ltd.") is listed
    assert await company_for_posting(db_session, "Someone Else") is None


# ─────────────────────────────────────────────────────────────────────────────
# The tech verdict (decision 2026-10-15: the map lists tech employers only)
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_import_stores_the_tech_verdict(db_session):
    tech = await upsert_company(
        db_session, _imported(description="Indian enterprise software company"), "wikidata"
    )
    school = await upsert_company(
        db_session,
        _imported(
            name="Christ University",
            domain="christuniversity.in",
            website_url="https://christuniversity.in",
            wikidata_id="Q1",
            description="Private autonomous university in Bangalore",
        ),
        "wikidata",
    )
    assert tech is not None and tech.is_tech is True
    assert school is not None and school.is_tech is False


async def test_industry_labels_count_and_are_kept_for_the_reclassifier(db_session):
    company = await upsert_company(
        db_session,
        _imported(description="company", industry_labels=("software industry",)),
        "wikidata",
    )
    assert company is not None
    assert company.is_tech is True
    assert company.extra_metadata["wikidata_industries"] == ["software industry"]


async def test_a_later_non_tech_import_never_unlists_a_tech_row(db_session):
    db_session.add(Company(name="Razorpay", domain="razorpay.com", tier="unicorn", is_tech=True))
    await db_session.flush()

    company = await upsert_company(db_session, _imported(description="company"), "osm")

    assert company is not None
    assert company.is_tech is True


async def test_more_evidence_can_turn_a_no_into_a_yes(db_session):
    first = await upsert_company(db_session, _imported(description="company"), "wikidata")
    assert first is not None and first.is_tech is False

    again = await upsert_company(
        db_session, _imported(description="Indian payments platform"), "osm"
    )
    assert again is first
    assert again.is_tech is True
