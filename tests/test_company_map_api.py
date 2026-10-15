"""
tests/test_company_map_api.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The read side of the company map and the routes that serve it.

* The routes are tested with the read functions mocked, as
  ``tests/test_reverify_endpoint.py`` does.
* The read functions themselves run against an in-memory SQLite database
  (no Postgres, no network): the "listed" rule, the positional map rows, the
  news strip's listed-only rule, curated-only people and "hiring for you".
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.cache_backend import CacheBackend
from storage.database import Base
from storage.models import (
    Company,
    CompanyContact,
    CompanyNews,
    Job,
    JobStatus,
    Profile,
    User,
)

READ = "services.company_map.read"

CID = str(uuid.uuid4())
MAP_PAYLOAD: dict[str, Any] = {
    "generated_at": "2026-10-14T00:00:00+00:00",
    "city": "Bengaluru",
    "total": 2,
    "pinned": 1,
    "rows": [
        [
            CID,
            "Acme",
            12.93574,
            77.62408,
            "exact",
            "startup",
            "seed",
            "saas",
            "Koramangala",
            "1-50",
            3,
            True,
        ],
        [str(uuid.uuid4()), "Beta", None, None, "city", None, None, None, None, None, 0, False],
    ],
}
NEWS = [
    {
        "id": str(uuid.uuid4()),
        "url": "https://inc42.com/buzz/acme-raises/",
        "title": "Acme Raises $5 Mn",
        "category": "funding",
        "company_id": CID,
        "company_name": "Acme",
    }
]


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    """The in-process cache only: a developer's Redis is neither read nor written."""
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


class TestMapRoute:
    async def test_requires_auth(self, api_client):
        with patch(f"{READ}.map_rows", AsyncMock(return_value=MAP_PAYLOAD)) as run:
            response = await api_client.get("/api/v1/company-intel/map")
        assert response.status_code in (401, 403)
        assert run.await_count == 0

    async def test_returns_the_positional_rows(self, auth_client):
        with patch(f"{READ}.map_rows", AsyncMock(return_value=MAP_PAYLOAD)) as run:
            response = await auth_client.get("/api/v1/company-intel/map?city=Bengaluru")
        assert response.status_code == 200
        body = response.json()
        assert {"generated_at", "total", "pinned", "rows"} <= body.keys()
        assert all(len(row) == 12 for row in body["rows"])
        assert body["rows"][0][:2] == [CID, "Acme"]
        assert run.await_args.kwargs == {"city": "Bengaluru"}

    async def test_is_not_swallowed_by_the_company_id_route(self, auth_client):
        with (
            patch(f"{READ}.map_rows", AsyncMock(return_value=MAP_PAYLOAD)),
            patch("api.routers.company_intel.company_intel", AsyncMock(return_value=None)) as intel,
        ):
            response = await auth_client.get("/api/v1/company-intel/map")
        assert response.status_code == 200
        assert intel.await_count == 0

    async def test_carries_an_etag_and_honours_if_none_match(self, auth_client):
        with patch(f"{READ}.map_rows", AsyncMock(return_value=MAP_PAYLOAD)):
            first = await auth_client.get("/api/v1/company-intel/map")
            etag = first.headers["etag"]
            again = await auth_client.get(
                "/api/v1/company-intel/map", headers={"If-None-Match": etag}
            )
        assert again.status_code == 304
        assert again.content == b""

    async def test_a_city_sized_map_is_gzipped(self, auth_client):
        """Review #7: thousands of rows went over the wire uncompressed."""
        row = [
            CID,
            "Acme",
            12.97,
            77.59,
            "street",
            "startup",
            "seed",
            "fintech",
            "HSR",
            "s",
            0,
            False,
        ]
        payload = {**MAP_PAYLOAD, "rows": [row] * 3000}
        with patch(f"{READ}.map_rows", AsyncMock(return_value=payload)):
            response = await auth_client.get(
                "/api/v1/company-intel/map", headers={"Accept-Encoding": "gzip"}
            )
        assert response.status_code == 200
        assert response.headers.get("content-encoding") == "gzip"
        assert len(response.json()["rows"]) == 3000


class TestForMeRoute:
    async def test_returns_the_company_ids_for_the_caller(self, auth_client):
        with patch(f"{READ}.hiring_for_you", AsyncMock(return_value=[CID])) as run:
            response = await auth_client.get("/api/v1/company-intel/map/for-me")
        assert response.status_code == 200
        assert response.json() == {"hiring_for_you": [CID]}
        (user_id,) = run.await_args.args
        assert uuid.UUID(user_id)

    async def test_requires_auth(self, api_client):
        response = await api_client.get("/api/v1/company-intel/map/for-me")
        assert response.status_code in (401, 403)


class TestNewsRoute:
    async def test_returns_a_list(self, auth_client):
        with patch(f"{READ}.latest_news", AsyncMock(return_value=NEWS)) as run:
            response = await auth_client.get("/api/v1/company-intel/news?limit=5&category=funding")
        assert response.status_code == 200
        assert response.json()[0]["title"] == "Acme Raises $5 Mn"
        assert run.await_args.kwargs == {"limit": 5, "category": "funding"}

    async def test_unknown_category_is_refused(self, auth_client):
        with patch(f"{READ}.latest_news", AsyncMock(return_value=NEWS)) as run:
            response = await auth_client.get("/api/v1/company-intel/news?category=gossip")
        assert response.status_code == 422
        assert run.await_count == 0


class TestDiscoverPeopleRoute:
    async def test_passes_the_service_result_through(self, auth_client):
        result = {
            "available": False,
            "people": [],
            "message": "Founder search has used this month's allowance. It resets on the 1st.",
        }
        with patch(
            "services.company_map.people.discover_founders", AsyncMock(return_value=result)
        ) as run:
            response = await auth_client.post(f"/api/v1/company-intel/{CID}/people/discover")
        assert response.status_code == 200
        assert response.json() == result
        assert run.await_args.args == (CID,)

    async def test_requires_auth(self, api_client):
        response = await api_client.post(f"/api/v1/company-intel/{CID}/people/discover")
        assert response.status_code in (401, 403)


class TestDirectoryFilters:
    async def test_new_filters_reach_the_service(self, auth_client):
        page = {"companies": [], "total": 0, "offset": 0, "limit": 100, "city": "Bengaluru"}
        with patch("api.routers.company_intel.list_companies", AsyncMock(return_value=page)) as run:
            response = await auth_client.get(
                "/api/v1/company-intel/?stage=seed&sector=fintech&area=HSR%20Layout"
                "&size=51-200&raised_within_days=90"
            )
        assert response.status_code == 200
        kwargs = run.await_args.kwargs
        assert kwargs["stage"] == "seed"
        assert kwargs["sector"] == "fintech"
        assert kwargs["area"] == "HSR Layout"
        assert kwargs["size"] == "51-200"
        assert kwargs["raised_within_days"] == 90

    @pytest.mark.parametrize(
        "query", ["stage=unicorn", "sector=crypto", "size=huge", "area=Atlantis"]
    )
    async def test_unknown_vocabulary_is_refused(self, auth_client, query):
        with patch("api.routers.company_intel.list_companies", AsyncMock()) as run:
            response = await auth_client.get(f"/api/v1/company-intel/?{query}")
        assert response.status_code == 422
        assert run.await_count == 0

    async def test_facets_take_the_same_filters(self, auth_client):
        with patch("api.routers.company_intel.directory_facets", AsyncMock(return_value={})) as run:
            response = await auth_client.get("/api/v1/company-intel/facets?stage=seed")
        assert response.status_code == 200
        assert run.await_args.kwargs["stage"] == "seed"

    async def test_vc_is_a_type(self, auth_client):
        page = {"companies": [], "total": 0, "offset": 0, "limit": 100, "city": "Bengaluru"}
        with patch("api.routers.company_intel.list_companies", AsyncMock(return_value=page)):
            response = await auth_client.get("/api/v1/company-intel/?tier=vc")
        assert response.status_code == 200


class _CapturingSession:
    """Stands in for AsyncSessionLocal() and records every statement."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(statement)

        class _Result:
            def scalars(self) -> Any:
                return self

            def all(self) -> list[Any]:
                return []

        return _Result()

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        return None


async def test_list_companies_passes_the_stage_filter_through(monkeypatch):
    from services import companies

    session = _CapturingSession()
    monkeypatch.setattr(companies, "AsyncSessionLocal", lambda: session)
    result = await companies.list_companies(stage="seed", raised_within_days=90)

    assert result["total"] == 0
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "companies.stage = " in sql
    assert "companies.last_funding_at >= " in sql
    assert "seed" in compiled.params.values()


# ─────────────────────────────────────────────────────────────────────────────
# Read functions, against SQLite
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from services.company_map import read

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(read, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


def _company(name: str, **fields: Any) -> Company:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "domain": f"{name.lower()}.example",
        "is_tech": True,  # the map lists tech employers only; tests opt out explicitly
    }
    return Company(name=name, **{**defaults, **fields})


@pytest_asyncio.fixture
async def seeded(db: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    now = datetime.now(UTC)
    pinned = _company(
        "Acme",
        summary="Payroll software for Indian SMBs.",
        office_cities=["Bengaluru"],
        lat=12.935741234,
        lng=77.624081234,
        geo_precision="exact",
        tier="startup",
        stage="seed",
        sector="saas",
        area="Koramangala",
        employee_count=40,
        last_funding_at=now - timedelta(days=10),
    )
    by_area_only = _company(
        "Beta", description="Logistics for D2C brands.", area="HSR Layout", tier="scaleup"
    )
    by_hq_only = _company("Gamma", summary="Card issuing APIs.", hq_city="Bangalore")
    no_text = _company("Thin", office_cities=["Bengaluru"], lat=12.9, lng=77.6)
    elsewhere = _company("Delta", summary="Mumbai brokerage.", office_cities=["Mumbai"])
    outside_bbox = _company(
        "Epsilon",
        summary="Has a bad pin.",
        office_cities=["Bengaluru"],
        lat=19.07,
        lng=72.87,
        geo_precision="exact",
    )
    # Says what it does and is in Bengaluru, but is not a tech employer: one
    # row flagged False, one never classified (NULL). Neither may be listed.
    brewery = _company(
        "Brewco",
        summary="Craft beer brewery in Bengaluru.",
        office_cities=["Bengaluru"],
        lat=12.97,
        lng=77.59,
        geo_precision="exact",
        is_tech=False,
    )
    unclassified = _company(
        "Legacy", summary="Has a summary, never classified.", office_cities=["Bengaluru"]
    )
    unclassified.is_tech = None
    companies = [
        pinned,
        by_area_only,
        by_hq_only,
        no_text,
        elsewhere,
        outside_bbox,
        brewery,
        unclassified,
    ]

    user = User(id=uuid.uuid4(), email="u@example.com", hashed_password="x")
    async with db() as session:
        session.add_all(companies)
        session.add(user)
        await session.flush()
        session.add(Profile(user_id=user.id, target_roles=["Backend Engineer"]))
        session.add_all(
            [
                Job(
                    external_id=f"j{i}",
                    source="greenhouse",
                    source_url=f"https://boards.greenhouse.io/x/jobs/{i}",
                    title=title,
                    company_id=company.id,
                    location_raw=location,
                    status=JobStatus.ACTIVE,
                )
                for i, (company, title, location) in enumerate(
                    [
                        (pinned, "Senior Backend Engineer", "Bangalore, India"),
                        (pinned, "Product Designer", "Bengaluru"),
                        (by_area_only, "Backend Engineer", "Mumbai"),
                        (by_hq_only, "Data Analyst", "Bengaluru"),
                        (elsewhere, "Backend Engineer", "Bengaluru"),
                        (brewery, "Backend Engineer", "Bengaluru"),
                        (unclassified, "Backend Engineer", "Bengaluru"),
                    ]
                )
            ]
        )
        session.add_all(
            [
                CompanyContact(
                    company_id=pinned.id,
                    kind="founder",
                    name="Asha Rao",
                    source_url="https://www.wikidata.org/wiki/Q1",
                    source_kind="wikidata",
                ),
                CompanyContact(
                    company_id=pinned.id,
                    kind="engineer",
                    name="Neha K",
                    github_url="https://github.com/nehak",
                    linkedin_url="https://www.linkedin.com/in/nehak",
                    source_url="https://github.com/orgs/acme/people",
                    source_kind="github",
                ),
                CompanyContact(
                    company_id=pinned.id,
                    kind="recruiter",
                    name="Not A Person Kind",
                    source_url="https://acme.example/careers",
                ),
                CompanyContact(
                    company_id=pinned.id,
                    user_id=user.id,
                    kind="founder",
                    name="Private Note",
                    source_kind="user",
                ),
            ]
        )
        session.add_all(
            [
                CompanyNews(
                    url="https://inc42.com/a",
                    source="inc42",
                    title="Acme Raises $5 Mn In Seed Round",
                    category="funding",
                    company_id=pinned.id,
                    published_at=now - timedelta(days=10),
                ),
                CompanyNews(
                    url="https://inc42.com/b",
                    source="inc42",
                    title="Acme launches payroll app",
                    category="launch",
                    company_id=pinned.id,
                    published_at=now - timedelta(days=2),
                ),
                CompanyNews(
                    url="https://inc42.com/c",
                    source="inc42",
                    title="Delta Bags $20 Mn",
                    category="funding",
                    company_id=elsewhere.id,
                    published_at=now - timedelta(days=1),
                ),
                CompanyNews(
                    url="https://inc42.com/e",
                    source="inc42",
                    title="Brewco Bags $3 Mn",
                    category="funding",
                    company_id=brewery.id,
                    published_at=now - timedelta(hours=1),
                ),
                CompanyNews(
                    url="https://inc42.com/d",
                    source="inc42",
                    title="Unknown Startup Bags $1 Mn",
                    category="funding",
                    published_at=now,
                ),
            ]
        )
        await session.commit()
    return {
        "acme": str(pinned.id),
        "beta": str(by_area_only.id),
        "gamma": str(by_hq_only.id),
        "thin": str(no_text.id),
        "delta": str(elsewhere.id),
        "epsilon": str(outside_bbox.id),
        "brewco": str(brewery.id),
        "legacy": str(unclassified.id),
        "user": str(user.id),
    }


class TestMapRows:
    async def test_lists_only_bengaluru_companies_with_text(self, seeded):
        from services.company_map.read import map_rows

        payload = await map_rows("Bengaluru")
        ids = {row[0] for row in payload["rows"]}
        assert ids == {seeded["acme"], seeded["beta"], seeded["gamma"], seeded["epsilon"]}
        assert payload["total"] == 4
        assert payload["pinned"] == 1

    async def test_a_non_tech_row_with_a_summary_is_not_on_the_map(self, seeded):
        from services.company_map.read import map_rows

        ids = {row[0] for row in (await map_rows())["rows"]}
        assert seeded["brewco"] not in ids  # is_tech = False
        assert seeded["legacy"] not in ids  # is_tech = NULL: not yet classified

    async def test_row_shape_and_rounding(self, seeded):
        from services.company_map.read import map_rows

        rows = {row[0]: row for row in (await map_rows())["rows"]}
        assert rows[seeded["acme"]] == [
            seeded["acme"],
            "Acme",
            12.93574,
            77.62408,
            "exact",
            "startup",
            "seed",
            "saas",
            "Koramangala",
            "1-50",
            2,
            True,
        ]
        assert rows[seeded["beta"]][2:5] == [None, None, "city"]
        assert rows[seeded["beta"]][10:] == [1, False]

    async def test_a_pin_outside_bengaluru_is_not_drawn(self, seeded):
        from services.company_map.read import map_rows

        rows = {row[0]: row for row in (await map_rows())["rows"]}
        assert rows[seeded["epsilon"]][2:5] == [None, None, "city"]

    async def test_is_cached_per_city(self, seeded, db):
        from services.company_map.read import map_rows

        first = await map_rows("Bangalore")
        async with db() as session:
            session.add(_company("Late", summary="Arrived later.", office_cities=["Bengaluru"]))
            await session.commit()
        assert await map_rows("Bengaluru") == first
        assert await CacheBackend.get_json("tr:map:Bengaluru") == first

    async def test_unknown_city_is_empty(self, seeded):
        from services.company_map.read import map_rows

        payload = await map_rows("Atlantis")
        assert payload["rows"] == []
        assert payload["total"] == 0


class TestHiringForYou:
    async def test_matches_target_role_in_bengaluru_at_listed_companies(self, seeded):
        from services.company_map.read import hiring_for_you

        # Beta's backend role is in Mumbai; Delta is not a Bengaluru company.
        assert await hiring_for_you(seeded["user"]) == [seeded["acme"]]

    async def test_no_profile_means_nothing(self, seeded):
        from services.company_map.read import hiring_for_you

        assert await hiring_for_you(str(uuid.uuid4())) == []
        assert await hiring_for_you("not-a-uuid") == []


class TestPeopleFor:
    async def test_groups_curated_people_only(self, seeded):
        from services.company_map.read import people_for

        people = await people_for(seeded["acme"])
        assert [p["name"] for p in people["founders"]] == ["Asha Rao"]
        assert [p["name"] for p in people["engineers"]] == ["Neha K"]
        assert people["leaders"] == []
        assert all(p["is_curated"] for group in people.values() for p in group)

    async def test_person_without_a_profile_gets_a_search_link(self, seeded):
        from services.company_map.read import people_for

        people = await people_for(seeded["acme"])
        assert people["founders"][0]["linkedin_search_url"] == (
            "https://www.linkedin.com/search/results/people/?keywords=%22Asha%20Rao%22%20Acme"
        )
        assert people["engineers"][0]["linkedin_search_url"] is None

    async def test_bad_id_is_empty(self, seeded):
        from services.company_map.read import people_for

        assert await people_for("nope") == {"founders": [], "leaders": [], "engineers": []}


class TestNews:
    async def test_news_for_is_newest_first(self, seeded):
        from services.company_map.read import news_for

        items = await news_for(seeded["acme"])
        assert [i["title"] for i in items] == [
            "Acme launches payroll app",
            "Acme Raises $5 Mn In Seed Round",
        ]
        assert await news_for(seeded["acme"], limit=1) == items[:1]

    async def test_latest_news_only_links_listed_companies(self, seeded):
        from services.company_map.read import latest_news

        items = await latest_news()
        assert [i["company_name"] for i in items] == ["Acme", "Acme"]

    async def test_latest_news_leaves_out_a_non_tech_company(self, seeded):
        from services.company_map.read import latest_news

        names = {i["company_name"] for i in await latest_news(limit=50)}
        assert "Brewco" not in names

    async def test_latest_news_by_category(self, seeded):
        from services.company_map.read import latest_news

        items = await latest_news(category="funding")
        assert [i["title"] for i in items] == ["Acme Raises $5 Mn In Seed Round"]
        assert items[0]["company_id"] == seeded["acme"]


# ─────────────────────────────────────────────────────────────────────────────
# Retention
# ─────────────────────────────────────────────────────────────────────────────


class _RetentionSession:
    """A session that records statements; ``fail_on`` makes one of them raise."""

    def __init__(self, fail_on: str | None = None, rowcount: int = 0) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.fail_on = fail_on
        self.rowcount = rowcount
        self.rollbacks = 0

    async def __aenter__(self) -> _RetentionSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("relation does not exist")
        rowcount = self.rowcount

        class _Result:
            def __init__(self) -> None:
                self.rowcount = rowcount

            def scalar(self) -> int:
                return 0

        return _Result()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_retention_prunes_news_on_two_clocks(monkeypatch):
    import storage.database as database
    from services import job_retention

    session = _RetentionSession(rowcount=3)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)
    summary = await job_retention.prune_stale_jobs()

    news = [(sql, params) for sql, params in session.statements if "company_news" in sql]
    assert len(news) == 1
    sql, params = news[0]
    assert "DELETE FROM company_news" in sql
    assert "category = 'funding'" in sql
    assert params["news_days"] == 180
    assert params["funding_days"] == 730
    assert summary["deleted_news"] == 3
    assert summary["ran"] is True


async def test_a_failed_news_prune_does_not_stop_the_job_prune(monkeypatch):
    import storage.database as database
    from services import job_retention

    session = _RetentionSession(fail_on="company_news")
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)
    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_news"] == 0
    assert session.rollbacks == 1


# ─────────────────────────────────────────────────────────────────────────────
# The directory's server-side filters, against SQLite
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def directory(
    db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Any]:
    """Seeds listed Bengaluru companies and returns ``list_companies`` bound to them."""
    from services import companies

    monkeypatch.setattr(companies, "AsyncSessionLocal", db)

    async def seed(*rows: Company) -> None:
        async with db() as session:
            session.add_all(rows)
            await session.commit()

    async def names(**filters: Any) -> tuple[set[str], int]:
        page = await companies.list_companies(**filters)
        return {card["name"] for card in page["companies"]}, page["total"]

    yield type("Directory", (), {"seed": staticmethod(seed), "names": staticmethod(names)})


def _listed(name: str, **fields: Any) -> Company:
    """A company the directory will show: it says what it does and is in Bengaluru."""
    defaults: dict[str, Any] = {
        "summary": f"{name} does things.",
        "office_cities": ["Bengaluru"],
    }
    return _company(name, **{**defaults, **fields})


class TestDirectoryPinnedFilter:
    async def test_unpinned_returns_only_companies_without_a_position(self, directory):
        await directory.seed(
            _listed("Pinned", lat=12.93, lng=77.62, geo_precision="exact"),
            _listed("Loose"),
        )
        names, total = await directory.names(pinned=False)
        assert names == {"Loose"}
        assert total == 1

    async def test_pinned_returns_only_companies_with_a_position(self, directory):
        await directory.seed(
            _listed("Pinned", lat=12.93, lng=77.62, geo_precision="exact"),
            _listed("Loose"),
        )
        names, total = await directory.names(pinned=True)
        assert names == {"Pinned"}
        assert total == 1

    async def test_no_preference_returns_both(self, directory):
        await directory.seed(_listed("Pinned", lat=12.93, lng=77.62), _listed("Loose"))
        names, total = await directory.names()
        assert names == {"Pinned", "Loose"}
        assert total == 2

    async def test_a_pin_outside_bengaluru_counts_as_unpinned(self, directory):
        # The map refuses to draw it (read._position), so "not pinned" must agree.
        await directory.seed(
            _listed("Pinned", lat=12.93, lng=77.62),
            _listed("FarAway", lat=19.07, lng=72.87),
        )
        assert await directory.names(pinned=False) == ({"FarAway"}, 1)
        assert await directory.names(pinned=True) == ({"Pinned"}, 1)

    async def test_the_filter_reaches_the_service_from_the_route(self, auth_client):
        page = {"companies": [], "total": 0, "offset": 0, "limit": 100, "city": "Bengaluru"}
        with patch("api.routers.company_intel.list_companies", AsyncMock(return_value=page)) as run:
            response = await auth_client.get("/api/v1/company-intel/?pinned=false")
        assert response.status_code == 200
        assert run.await_args.kwargs["pinned"] is False


class TestDirectoryIsTechOnly:
    async def test_a_non_tech_row_with_a_summary_is_not_on_the_list(self, directory):
        await directory.seed(
            _listed("Software", tier="startup", sector="saas"),
            _listed("Brewery", summary="Craft beer.", is_tech=False, tier="startup"),
            _listed("Unsorted", summary="Never classified.", is_tech=None),
        )
        assert await directory.names() == ({"Software"}, 1)
        assert await directory.names(q="Craft") == (set(), 0)

    async def test_facets_do_not_count_a_non_tech_row(self, directory):
        from services import companies

        await directory.seed(
            _listed("Software", tier="startup", sector="saas", stage="seed", area="HSR Layout"),
            _listed(
                "Brewery",
                is_tech=False,
                tier="startup",
                sector="consumer",
                stage="seed",
                area="HSR Layout",
            ),
        )
        facets = await companies.directory_facets()
        assert facets["total"] == 1
        assert [s["value"] for s in facets["sectors"]] == ["saas"]
        assert facets["stages"] == [{"value": "seed", "label": "Seed", "count": 1}]
        assert facets["areas"] == [{"value": "HSR Layout", "label": "HSR Layout", "count": 1}]
        assert [t["value"] for t in facets["tiers"]] == ["startup"]
        assert facets["tiers"][0]["count"] == 1


class TestDirectorySearch:
    @pytest_asyncio.fixture(autouse=True)
    async def _rows(self, directory):
        await directory.seed(
            _listed(
                "Zeta",
                summary="Ledger tooling.",
                stage="series_a",
                sector="fintech",
                area="Koramangala",
                tier="startup",
            ),
            _listed(
                "Omega",
                summary="Build pipelines.",
                stage="seed",
                sector="devtools",
                area="HSR Layout",
                tier="scaleup",
            ),
        )

    async def test_a_stage_label_finds_the_company(self, directory):
        assert await directory.names(q="Series A") == ({"Zeta"}, 1)

    async def test_an_area_word_finds_the_company(self, directory):
        assert await directory.names(q="Koramangala") == ({"Zeta"}, 1)

    async def test_a_sector_label_finds_the_company(self, directory):
        assert await directory.names(q="Developer tools") == ({"Omega"}, 1)

    async def test_a_tier_label_finds_the_company(self, directory):
        assert await directory.names(q="Scale-up") == ({"Omega"}, 1)

    async def test_the_stored_value_finds_the_company_too(self, directory):
        assert await directory.names(q="series_a") == ({"Zeta"}, 1)

    async def test_every_word_must_match(self, directory):
        assert await directory.names(q="Koramangala Series A") == ({"Zeta"}, 1)
        assert await directory.names(q="Koramangala seed") == (set(), 0)
        assert await directory.names(q="HSR scale-up") == ({"Omega"}, 1)

    async def test_words_may_match_different_fields(self, directory):
        # name + area + stage label, none of them in the same column
        assert await directory.names(q="zeta koramangala series") == ({"Zeta"}, 1)

    async def test_a_single_substring_still_matches_name_and_summary(self, directory):
        assert await directory.names(q="zet") == ({"Zeta"}, 1)
        assert await directory.names(q="pipelines") == ({"Omega"}, 1)

    async def test_percent_and_underscore_are_literal(self, directory):
        assert await directory.names(q="%") == (set(), 0)
        # an unescaped "_" would match any character and so every company
        assert await directory.names(q="ser_es") == (set(), 0)
        # only Zeta has a literal underscore (its stored stage, series_a)
        assert await directory.names(q="_") == ({"Zeta"}, 1)

    async def test_whitespace_only_is_no_search(self, directory):
        assert (await directory.names(q="   "))[1] == 2
