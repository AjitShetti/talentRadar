"""
tests/test_company_map_news_ingest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
News ingest against Postgres with the feed fetch patched to a recorded
fixture: stored once by URL, funding linked to companies, thin rows for
unknown funded companies, and never a fabricated date.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from domain.news import classify, extract_funding
from services.company_map import news_ingest
from services.company_map.news_feeds import parse_feed
from services.company_map.types import FeedItem
from services.source_health import SourceHealthService
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company, CompanyNews

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "company_map" / "rss_inc42.xml"


@pytest_asyncio.fixture(autouse=True)
async def clean():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE companies, company_news CASCADE"))
    await SourceHealthService.reset("news:inc42")
    yield
    await engine.dispose()


def _patched():
    return (
        patch.dict(news_ingest.FEEDS, {"inc42": "https://inc42.com/feed/"}, clear=True),
        patch.object(news_ingest, "fetch_feed", AsyncMock(return_value=FIXTURE.read_bytes())),
        patch.object(news_ingest, "_llm_subject", AsyncMock(return_value=None)),
    )


def _first_funded() -> tuple[str, object]:
    for item in parse_feed(FIXTURE.read_bytes(), "inc42"):
        if classify(item.title) == "funding":
            subject = extract_funding(item.title, item.snippet).subject
            if subject and item.published_at:
                return subject, item
    raise AssertionError("fixture has no dated funding item with a subject")


def _stats() -> dict[str, int]:
    return dict.fromkeys(("fetched", "new", "funding", "linked", "created", "people", "llm"), 0)


async def _count(model) -> int:
    async with AsyncSessionLocal() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_ingest_twice_stores_each_article_once():
    feeds, fetch, llm = _patched()
    with feeds, fetch, llm:
        first = await news_ingest.ingest_news()
        second = await news_ingest.ingest_news()

    assert first["new"] == first["fetched"] > 0
    assert second["new"] == 0
    assert second["created"] == 0
    assert await _count(CompanyNews) == first["new"]


async def test_funding_moves_a_known_company():
    subject, item = _first_funded()
    async with AsyncSessionLocal() as session:
        session.add(Company(name=subject, domain="known.example", stage="seed"))
        await session.commit()

    feeds, fetch, llm = _patched()
    with feeds, fetch, llm:
        await news_ingest.ingest_news()

    async with AsyncSessionLocal() as session:
        company = (
            await session.execute(select(Company).where(Company.domain == "known.example"))
        ).scalar_one()
        linked = (
            await session.execute(select(CompanyNews).where(CompanyNews.url == item.url))
        ).scalar_one()
    assert linked.company_id == company.id
    assert linked.category == "funding"
    assert company.last_funding_at == item.published_at
    assert company.extra_metadata["provenance"]["last_funding_at"] == "news"


async def test_an_unknown_funded_company_becomes_a_thin_row():
    feeds, fetch, llm = _patched()
    with feeds, fetch, llm:
        result = await news_ingest.ingest_news()

    assert result["created"] > 0
    async with AsyncSessionLocal() as session:
        thin = (
            (await session.execute(select(Company).where(Company.last_funding_at.is_not(None))))
            .scalars()
            .first()
        )
    assert thin is not None
    assert thin.slug
    # News is India-wide; a thin row is not a Bengaluru company until a source says so.
    assert not thin.office_cities


async def test_a_dead_feed_is_reported_not_raised():
    feeds, _fetch, llm = _patched()
    import httpx

    dead = patch.object(
        news_ingest, "fetch_feed", AsyncMock(side_effect=httpx.ConnectError("down"))
    )
    with feeds, dead, llm:
        result = await news_ingest.ingest_news()
    assert "inc42" in str(result["errors"])
    assert result["new"] == 0


async def test_the_same_raise_from_four_outlets_never_duplicates_the_company():
    # Review #1: a jobs row plus a listed row for one employer, then the same
    # raise reported four times. Funding lands on the listed row; no new rows.
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                Company(name="Ledgerline", domain="ledgerline.talentradar.internal"),
                Company(name="Ledgerline", domain="ledgerline.in", slug="ledgerline"),
            ]
        )
        await session.commit()
    items = [
        FeedItem(
            url=f"https://{outlet}.example/ledgerline",
            source=outlet,
            title="Ledgerline raises $18 Mn in Series B",
            snippet="",
            published_at=datetime(2026, 10, 12, tzinfo=UTC),
        )
        for outlet in ("inc42", "yourstory", "ettech", "mint")
    ]
    async with AsyncSessionLocal() as session:
        for item in items:
            await news_ingest._store_item(session, item, _stats())
        await session.commit()

    async with AsyncSessionLocal() as session:
        rows = list(
            (await session.execute(select(Company).where(Company.name == "Ledgerline"))).scalars()
        )
    assert len(rows) == 2
    listed = next(c for c in rows if c.domain == "ledgerline.in")
    assert listed.stage == "series_b"
    assert listed.last_funding_at is not None
