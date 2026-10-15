"""
tests/test_company_map_refresh.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled refresh steps, end to end against Postgres with every outside
call patched: idempotent imports, the site and summary steps, and a step
that fails without raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from services.company_map import refresh
from services.company_map.refresh import STEPS, run_step
from services.company_map.types import SiteFacts
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "company_map"


@pytest_asyncio.fixture(autouse=True)
async def clean_companies():
    """run_step commits, so each test starts from an empty companies table."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE companies, company_news CASCADE"))
    yield
    await engine.dispose()


async def _companies() -> list[Company]:
    async with AsyncSessionLocal() as session:
        return list((await session.execute(select(Company).order_by(Company.name))).scalars())


async def _add(**fields: Any) -> None:
    async with AsyncSessionLocal() as session:
        session.add(Company(**fields))
        await session.commit()


def _overpass() -> dict[str, Any]:
    return json.loads((FIXTURES / "overpass.json").read_text(encoding="utf-8"))


async def test_unknown_step_is_an_error_not_an_exception():
    result = await run_step("nonsense", 10)
    assert "error" in result
    assert set(STEPS) == {
        "wikidata",
        "osm",
        "yc",
        "github_orgs",
        "sites",
        "geocode",
        "summaries",
        "github",
    }


async def test_refresh_step_twice_changes_nothing():
    with patch.object(refresh.osm, "fetch", AsyncMock(return_value=_overpass())):
        first = await run_step("osm", 5000)
        slugs = {c.slug for c in await _companies()}
        second = await run_step("osm", 5000)

    assert first["created"] > 0
    assert second["created"] == 0
    # OSM lists one company's several offices, so even the first run merges
    # some; the second merges every record into the rows the first made.
    assert second["updated"] == second["seen"] == first["seen"]
    assert {c.slug for c in await _companies()} == slugs
    async with AsyncSessionLocal() as session:
        count = (await session.execute(select(func.count()).select_from(Company))).scalar_one()
    assert count == first["created"]


async def test_a_failing_fetch_returns_an_error():
    with patch.object(refresh.osm, "fetch", AsyncMock(side_effect=ValueError("HTML, not JSON"))):
        result = await run_step("osm", 10)
    assert result["error"].startswith("ValueError")


async def test_sites_step_takes_address_people_and_a_site_summary():
    await _add(name="Acme Pay", domain="acmepay.example", website_url="https://acmepay.example")
    facts = SiteFacts(
        title="Acme Pay",
        description="Acme Pay runs payment rails for small Indian merchants.",
        text="Acme Pay runs payment rails.",
        address="27th Main, HSR Layout, Bengaluru 560102",
        pincode="560102",
    )
    with patch.object(refresh, "read_site", AsyncMock(return_value=facts)):
        result = await run_step("sites", 10)

    assert result["read"] == 1
    [company] = await _companies()
    assert company.pincode == "560102"
    assert company.area == "HSR Layout"
    assert company.summary == "Acme Pay runs payment rails for small Indian merchants."
    assert company.summary_source == "site"
    assert company.enriched_at is not None
    assert company.extra_metadata["provenance"]["address"] == "site"


async def test_summary_step_skips_company_with_no_source_text():
    # robots.txt said no: the site step recorded nothing but the attempt.
    await _add(name="Quiet Co", domain="quiet.example", website_url="https://quiet.example")
    with patch.object(refresh, "read_site", AsyncMock(return_value=None)):
        await run_step("sites", 10)
    llm = AsyncMock(return_value="Should never be asked.")
    with patch.object(refresh, "llm_summary", llm):
        result = await run_step("summaries", 10)

    assert result["due"] == 1
    llm.assert_not_awaited()
    [company] = await _companies()
    assert company.summary is None
    # Rested, so tomorrow's batch is not the same unanswerable rows.
    assert await run_step("summaries", 10) == {
        "step": "summaries",
        "due": 0,
        "from_sources": 0,
        "llm": 0,
    }


async def test_summary_step_uses_the_llm_only_with_source_text():
    await _add(
        name="Thin Co",
        domain="thin.example",
        website_url="https://thin.example",
        extra_metadata={
            "site": {"title": "Thin", "description": None, "text": "We build ledgers " * 10}
        },
    )
    async with AsyncSessionLocal() as session:
        company = (await session.execute(select(Company))).scalar_one()
        from datetime import UTC, datetime

        company.enriched_at = datetime.now(UTC)
        await session.commit()
    with patch.object(refresh, "llm_summary", AsyncMock(return_value="Ledger software for banks.")):
        result = await run_step("summaries", 10)

    assert result["llm"] == 1
    [company] = await _companies()
    assert (company.summary, company.summary_source) == ("Ledger software for banks.", "llm")


async def test_geocode_falls_back_to_the_area_centroid():
    await _add(
        name="Area Co", domain="area.example", area="Indiranagar", office_cities=["Bengaluru"]
    )
    with patch.object(refresh, "geocode", AsyncMock(return_value=None)):
        result = await run_step("geocode", 10)

    assert result["placed"] == 1
    [company] = await _companies()
    assert company.geo_precision == "area"
    assert company.lat is not None


async def test_an_area_level_address_hit_is_nudged_off_the_centroid():
    """Review #6: a suburb-level address hit is the area's centroid, like a PIN code's."""
    from domain.areas import centroid, jitter
    from services.company_map.types import GeoHit

    await _add(name="Suburb Co", domain="suburb.example", address="Koramangala, Bengaluru")
    middle = centroid("Koramangala")
    assert middle is not None
    lat, lng = middle
    hit = GeoHit(lat=lat, lng=lng, precision="area", source="nominatim", area="Koramangala")
    with patch.object(refresh, "geocode", AsyncMock(return_value=hit)):
        await run_step("geocode", 10)

    [company] = await _companies()
    assert company.geo_precision == "area"
    assert (company.lat, company.lng) == pytest.approx(jitter(str(company.id), lat, lng))
    assert (company.lat, company.lng) != pytest.approx((lat, lng))
