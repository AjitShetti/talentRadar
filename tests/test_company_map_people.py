"""
tests/test_company_map_people.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Sourced people: GitHub org members as engineers, Tavily founder lookups, and
the upsert that stores them without duplicates.

The fixtures keep each API's real shape but name fictitious people, so no
real individual's data lives in the repository.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from services.company_map import people
from services.company_map.people import (
    github_members,
    parse_github_user,
    parse_tavily_founders,
    upsert_people,
)
from services.company_map.types import PersonCandidate
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company, CompanyContact

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "company_map"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestGithub:
    def test_user_becomes_an_engineer_with_public_links(self):
        person = parse_github_user(_load("github_user.json"))
        assert person == PersonCandidate(
            name="Asha Rao",
            kind="engineer",
            source_url="https://github.com/asharao-dev",
            source_kind="github",
            github_url="https://github.com/asharao-dev",
            x_url="https://x.com/asharao",
            profile_url="https://asharao.dev",
        )

    def test_name_falls_back_to_login(self):
        payload = {**_load("github_user.json"), "name": None}
        assert parse_github_user(payload).name == "asharao-dev"

    @pytest.mark.parametrize("blog", ["javascript:alert(1)", "asharao.dev", "", None])
    def test_only_http_blogs_become_profile_links(self, blog):
        payload = {**_load("github_user.json"), "blog": blog}
        assert parse_github_user(payload).profile_url is None

    async def test_members_are_fetched_then_each_user(self):
        users = {"asharao-dev": _load("github_user.json")}
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/orgs/acmepay/public_members":
                return httpx.Response(200, json=_load("github_members.json"))
            login = request.url.path.rsplit("/", 1)[-1]
            if login in users:
                return httpx.Response(200, json=users[login])
            return httpx.Response(404, json={"message": "Not Found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            found = await github_members("acmepay", limit=5, client=client)

        assert [p.name for p in found] == ["Asha Rao"]
        assert seen[0] == "/orgs/acmepay/public_members"

    async def test_a_github_failure_is_an_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "rate limited"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await github_members("acmepay", client=client) == []

    async def test_an_unsafe_org_slug_makes_no_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await github_members("../users/x", client=client) == []


class TestTavily:
    def _founders(self) -> list[PersonCandidate]:
        return parse_tavily_founders(_load("tavily_founders.json")["results"], "Acme Pay Pvt Ltd")

    def test_linkedin_founder_results_are_accepted(self):
        by_name = {p.name: p for p in self._founders()}
        assert set(by_name) == {"Asha Rao", "Vikram Iyer"}
        asha = by_name["Asha Rao"]
        assert asha.kind == "founder"
        assert asha.title == "Co-Founder & CEO"
        assert asha.linkedin_url == "https://www.linkedin.com/in/asha-rao-123"
        assert asha.source_url == "https://in.linkedin.com/in/asha-rao-123"
        assert asha.source_kind == "tavily"

    def test_non_founder_title_wrong_company_and_non_profiles_are_rejected(self):
        names = {p.name for p in self._founders()}
        assert "Neha K" not in names  # an engineer, not a founder
        assert "Ravi S" not in names  # founder of a different company

    def test_broker_and_non_linkedin_hosts_are_rejected(self):
        results = [
            r
            for r in _load("tavily_founders.json")["results"]
            if "rocketreach" in r["url"] or "crunchbase" in r["url"]
        ]
        assert parse_tavily_founders(results, "Acme Pay") == []

    def test_a_ceo_without_founder_is_a_leader(self):
        results = [
            {
                "url": "https://www.linkedin.com/in/meera-n",
                "title": "Meera N - CEO - Acme Pay | LinkedIn",
            }
        ]
        [person] = parse_tavily_founders(results, "Acme Pay")
        assert person.kind == "leader"


class TestDiscover:
    async def test_unknown_company(self):
        with patch.object(people, "_company_name", AsyncMock(return_value=None)):
            result = await people.discover_founders(str(uuid.uuid4()))
        assert result["available"] is False

    async def test_monthly_cap_stops_the_search(self):
        search = AsyncMock()
        with (
            patch.object(people, "_company_name", AsyncMock(return_value="Acme Pay")),
            patch.object(people.CacheBackend, "get_json", AsyncMock(return_value=None)),
            patch.object(people.CacheBackend, "incr", AsyncMock(return_value=801)),
            patch.object(people, "_tavily_search", search),
            patch.object(people, "_api_key", return_value="key"),
        ):
            result = await people.discover_founders(str(uuid.uuid4()))
        assert result["available"] is False
        assert "month" in result["message"]
        search.assert_not_awaited()

    async def test_a_cached_answer_is_returned_without_searching(self):
        cached = {"available": True, "people": [], "message": "cached"}
        search = AsyncMock()
        with (
            patch.object(people, "_company_name", AsyncMock(return_value="Acme Pay")),
            patch.object(people.CacheBackend, "get_json", AsyncMock(return_value=cached)),
            patch.object(people, "_tavily_search", search),
        ):
            assert await people.discover_founders(str(uuid.uuid4())) == cached
        search.assert_not_awaited()


_ASHA = PersonCandidate(
    name="Asha Rao",
    kind="founder",
    source_url="https://www.wikidata.org/wiki/Q1",
    source_kind="wikidata",
)


@pytest_asyncio.fixture
async def db_session():
    """
    Tables created if missing, each test rolled back.

    Not conftest's fixture: its ``drop_all`` cannot sort the unnamed
    profiles <-> resumes foreign-key cycle once the tables exist, so a
    second test using it errors.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
    # Each test has its own event loop; pooled connections must not outlive it.
    await engine.dispose()


@pytest.mark.integration
class TestUpsert:
    async def _company(self, db_session) -> uuid.UUID:
        company = Company(name="Acme Pay", domain="acmepay.example")
        db_session.add(company)
        await db_session.flush()
        return company.id

    async def _rows(self, db_session, company_id: uuid.UUID) -> list[CompanyContact]:
        result = await db_session.execute(
            select(CompanyContact).where(CompanyContact.company_id == company_id)
        )
        return list(result.scalars())

    async def test_people_upsert_is_idempotent(self, db_session):
        company_id = await self._company(db_session)
        assert await upsert_people(db_session, company_id, [_ASHA]) == 1
        assert await upsert_people(db_session, company_id, [_ASHA]) == 0
        [row] = await self._rows(db_session, company_id)
        assert row.user_id is None
        assert row.verified is False
        assert row.source_url == "https://www.wikidata.org/wiki/Q1"

    async def test_a_later_source_fills_only_empty_links(self, db_session):
        company_id = await self._company(db_session)
        await upsert_people(
            db_session,
            company_id,
            [
                PersonCandidate(
                    name="Asha Rao",
                    kind="founder",
                    source_url="https://acmepay.example",
                    source_kind="site",
                    x_url="https://x.com/asha_site",
                )
            ],
        )
        await upsert_people(
            db_session,
            company_id,
            [
                PersonCandidate(
                    name="asha rao",
                    kind="leader",
                    title="CEO",
                    source_url="https://www.wikidata.org/wiki/Q1",
                    source_kind="wikidata",
                    x_url="https://x.com/asha_wikidata",
                    linkedin_url="https://www.linkedin.com/in/asha-rao-123",
                )
            ],
        )
        [row] = await self._rows(db_session, company_id)
        assert row.kind == "founder"  # the same person, not a second row
        assert row.x_url == "https://x.com/asha_site"
        assert row.linkedin_url == "https://www.linkedin.com/in/asha-rao-123"
        assert row.title == "CEO"

    async def test_oversized_fields_are_bounded_not_raised(self, db_session):
        """Review #8: a 200-char GitHub name or a 600-char blog stalled the step."""
        company_id = await self._company(db_session)
        long_blog = "https://example.com/" + "a" * 600
        person = PersonCandidate(
            name="N" * 200,
            kind="engineer",
            title="T" * 200,
            source_url="https://github.com/nnn",
            source_kind="github",
            github_url="https://github.com/nnn",
            x_url="https://x.com/" + "x" * 300,
            profile_url=long_blog,
        )
        assert await upsert_people(db_session, company_id, [person]) == 1
        await db_session.flush()
        [row] = await self._rows(db_session, company_id)
        assert row.name == "N" * 128
        assert row.title == "T" * 128
        # A cut URL is a broken link: it is dropped, never truncated.
        assert row.profile_url is None
        assert row.x_url is None
        assert row.github_url == "https://github.com/nnn"

    async def test_a_source_url_too_long_to_store_skips_the_person(self, db_session):
        company_id = await self._company(db_session)
        person = PersonCandidate(
            name="Asha Rao",
            kind="founder",
            source_url="https://example.com/" + "a" * 600,
            source_kind="site",
        )
        assert await upsert_people(db_session, company_id, [person]) == 0
        await db_session.flush()
        assert await self._rows(db_session, company_id) == []

    async def test_a_person_without_a_source_is_never_stored(self, db_session):
        company_id = await self._company(db_session)
        nameless = PersonCandidate(name=" ", kind="founder", source_url="x", source_kind="site")
        sourceless = PersonCandidate(
            name="Asha Rao", kind="founder", source_url="", source_kind="site"
        )
        assert await upsert_people(db_session, company_id, [nameless, sourceless]) == 0
