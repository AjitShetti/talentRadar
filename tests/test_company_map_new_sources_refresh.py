"""
tests/test_company_map_new_sources_refresh.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The ``yc`` and ``github_orgs`` refresh steps, end to end against Postgres with
every outside call patched: what lands in ``companies``, that a re-run changes
nothing, that the GitHub walk resumes where it stopped and rests organisations
it checked recently, and that neither step ever raises.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from services import cache_backend
from services.cache_backend import CacheBackend
from services.company_map import refresh
from services.company_map.github_orgs import Candidate, Cursor, RateLimited, Walk
from services.company_map.refresh import run_step
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import Company

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "company_map"
LOGINS = ["SigNoz", "swiggy-private", "hotstar", "Auquan"]


@pytest_asyncio.fixture(autouse=True)
async def clean_state():
    """run_step commits: start every test from empty companies and an empty cache."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE companies, company_news CASCADE"))
    cache_backend._MEMORY.clear()
    yield
    cache_backend._MEMORY.clear()
    await engine.dispose()


def _yc() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / "yc.json").read_text(encoding="utf-8"))
    return data


def _orgs() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads(
        (FIXTURES / "github_orgs.json").read_text(encoding="utf-8")
    )
    return data


def _walk(logins: list[str], start: int = 0, **flags: Any) -> Walk:
    candidates = [Candidate(login, Cursor(0, start + i + 1)) for i, login in enumerate(logins)]
    return Walk(candidates=candidates, cursor=Cursor(0, start + len(logins)), **flags)


def _lookup(orgs: dict[str, dict[str, Any]] | None = None) -> AsyncMock:
    table = orgs if orgs is not None else _orgs()

    async def fetch_org(client: httpx.AsyncClient, login: str) -> dict[str, Any] | None:
        return table.get(login)

    return AsyncMock(side_effect=fetch_org)


async def _companies() -> list[Company]:
    async with AsyncSessionLocal() as session:
        return list((await session.execute(select(Company).order_by(Company.name))).scalars())


async def _company(domain: str) -> Company:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Company).where(Company.domain == domain))).scalar_one()
        return row


async def _add(**fields: Any) -> None:
    async with AsyncSessionLocal() as session:
        session.add(Company(**fields))
        await session.commit()


def test_the_new_steps_are_registered() -> None:
    assert {"yc", "github_orgs"} <= set(refresh.STEPS)
    assert refresh.MAX_LIMIT["yc"] >= 1000
    assert refresh.MAX_LIMIT["github_orgs"] <= 100  # one run must finish inside one request


# ---------------------------------------------------------------------------
# yc
# ---------------------------------------------------------------------------


async def test_yc_step_imports_the_active_bengaluru_companies() -> None:
    with patch.object(refresh.yc, "fetch", AsyncMock(return_value=_yc())):
        result = await run_step("yc", 5000)

    assert result == {"step": "yc", "seen": 8, "created": 8, "updated": 0, "skipped": 1}
    rows = {c.name: c for c in await _companies()}
    assert set(rows) == {
        "100x",
        "Dockup",
        "Meesho",
        "OneFin",
        "Razorpay",
        "Scribe",
        "Statiq.",
        "Supr Daily",
    }
    razorpay = rows["Razorpay"]
    assert razorpay.domain == "razorpay.com"
    assert razorpay.sector == "fintech"
    assert razorpay.employee_count == 2700
    assert razorpay.stage is None
    assert rows["OneFin"].stage == "seed"
    assert rows["Meesho"].stage == "public"
    assert "Bengaluru" in (razorpay.office_cities or [])


async def test_yc_text_goes_to_metadata_never_to_the_description_column() -> None:
    with patch.object(refresh.yc, "fetch", AsyncMock(return_value=_yc())):
        await run_step("yc", 5000)
    razorpay = await _company("razorpay.com")
    assert razorpay.description is None
    assert razorpay.summary is None
    meta = razorpay.extra_metadata or {}
    assert meta["yc_description"].startswith("India's only full-stack financial")
    assert meta["yc_batch"] == "Winter 2015"
    assert meta["provenance"]["sector"] == "yc"
    assert meta["provenance"]["employee_count"] == "yc"


async def test_yc_step_twice_changes_nothing() -> None:
    with patch.object(refresh.yc, "fetch", AsyncMock(return_value=_yc())):
        first = await run_step("yc", 5000)
        slugs = {c.slug for c in await _companies()}
        second = await run_step("yc", 5000)

    assert first["created"] == 8
    assert second["created"] == 0
    assert second["updated"] == second["seen"] == 8
    assert {c.slug for c in await _companies()} == slugs
    async with AsyncSessionLocal() as session:
        count = (await session.execute(select(func.count()).select_from(Company))).scalar_one()
    assert count == 8


async def test_yc_does_not_overwrite_wikidata_but_does_overwrite_osm() -> None:
    await _add(
        name="Razorpay",
        slug="razorpay",
        domain="razorpay.com",
        sector="saas",
        extra_metadata={"provenance": {"sector": "wikidata"}},
    )
    await _add(
        name="OneFin",
        slug="onefin",
        domain="onefin.in",
        sector="consumer",
        extra_metadata={"provenance": {"sector": "osm"}},
    )
    with patch.object(refresh.yc, "fetch", AsyncMock(return_value=_yc())):
        result = await run_step("yc", 5000)

    assert result["created"] == 6 and result["updated"] == 2
    razorpay, onefin = await _company("razorpay.com"), await _company("onefin.in")
    assert razorpay.sector == "saas"
    assert razorpay.extra_metadata["provenance"]["sector"] == "wikidata"
    assert onefin.sector == "fintech"
    assert onefin.extra_metadata["provenance"]["sector"] == "yc"


async def test_yc_joins_a_company_the_jobs_pipeline_already_created_by_name() -> None:
    await _add(name="Razorpay", slug="razorpay", domain="razorpay.talentradar.internal")
    with patch.object(refresh.yc, "fetch", AsyncMock(return_value=_yc())):
        await run_step("yc", 5000)
    rows = [c for c in await _companies() if c.name == "Razorpay"]
    assert len(rows) == 1
    assert rows[0].domain == "razorpay.com"


async def test_yc_limit_bounds_the_import() -> None:
    with patch.object(refresh.yc, "fetch", AsyncMock(return_value=_yc())):
        result = await run_step("yc", 3)
    assert result["seen"] == 3 and result["created"] == 3
    assert len(await _companies()) == 3


async def test_a_failing_yc_fetch_returns_an_error() -> None:
    with patch.object(refresh.yc, "fetch", AsyncMock(side_effect=httpx.ConnectError("down"))):
        result = await run_step("yc", 10)
    assert result["step"] == "yc"
    assert str(result["error"]).startswith("ConnectError")
    assert await _companies() == []


async def test_a_yc_body_that_is_not_a_list_returns_an_error() -> None:
    with patch.object(refresh.yc, "fetch", AsyncMock(side_effect=ValueError("not a list"))):
        result = await run_step("yc", 10)
    assert "error" in result


# ---------------------------------------------------------------------------
# github_orgs
# ---------------------------------------------------------------------------


async def test_github_orgs_step_keeps_only_real_companies() -> None:
    walk = _walk(LOGINS)
    with (
        patch.object(refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=walk)),
        patch.object(refresh.github_orgs, "fetch_org", _lookup()),
    ):
        result = await run_step("github_orgs", 60)

    assert result == {
        "step": "github_orgs",
        "seen": 4,
        "lookups": 4,
        "created": 3,
        "updated": 0,
        "skipped": 1,
    }
    rows = {c.github_org: c for c in await _companies()}
    assert set(rows) == {"SigNoz", "hotstar", "Auquan"}
    signoz = rows["SigNoz"]
    assert signoz.domain == "signoz.io"
    assert signoz.name == "SigNoz"
    assert signoz.sector == "devtools"
    assert signoz.description is None
    assert "Bengaluru" in (signoz.office_cities or [])
    meta = signoz.extra_metadata or {}
    assert meta["github_org_description"] == "Open source Observability Platform"
    assert meta["github_org_followers"] == 719
    assert meta["github_org_repos"] == 158
    assert meta["provenance"]["github_org"] == "github_org"
    stamp = datetime.fromisoformat(meta["github_org_checked_at"])
    assert abs(datetime.now(UTC) - stamp) < timedelta(minutes=5)


async def test_github_orgs_yields_to_better_sources() -> None:
    await _add(
        name="SigNoz",
        slug="signoz",
        domain="signoz.io",
        sector="saas",
        github_org="signoz-io",
        extra_metadata={"provenance": {"sector": "osm", "github_org": "curated"}},
    )
    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(["SigNoz"]))
        ),
        patch.object(refresh.github_orgs, "fetch_org", _lookup()),
    ):
        result = await run_step("github_orgs", 60)

    assert result["created"] == 0 and result["updated"] == 1
    row = await _company("signoz.io")
    assert row.sector == "saas"  # OSM outranks a GitHub profile
    assert row.github_org == "signoz-io"  # a curated slug is never replaced
    assert row.extra_metadata["provenance"]["github_org"] == "curated"


async def test_the_cursor_is_remembered_and_the_next_run_resumes_there() -> None:
    fetch_candidates = AsyncMock(side_effect=[_walk(LOGINS[:2]), _walk(LOGINS[2:], start=2)])
    with (
        patch.object(refresh.github_orgs, "fetch_candidates", fetch_candidates),
        patch.object(refresh.github_orgs, "fetch_org", _lookup()),
    ):
        await run_step("github_orgs", 2)
        assert Cursor.decode(await CacheBackend.get_json(refresh.GITHUB_ORGS_CURSOR)) == Cursor(
            0, 2
        )
        await run_step("github_orgs", 2)

    first, second = fetch_candidates.await_args_list
    assert first.args[1] == Cursor(0, 0)
    assert second.args[1] == Cursor(0, 2)
    assert first.args[2] == second.args[2] == 2
    assert Cursor.decode(await CacheBackend.get_json(refresh.GITHUB_ORGS_CURSOR)) == Cursor(0, 4)


async def test_an_organisation_checked_recently_is_not_looked_up_again() -> None:
    lookup = _lookup()
    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(LOGINS))
        ),
        patch.object(refresh.github_orgs, "fetch_org", lookup),
    ):
        await run_step("github_orgs", 60)
        assert lookup.await_count == 4
        second = await run_step("github_orgs", 60)

    # The three that became companies are rested; the rejected one is the only
    # lookup left (nothing was stored for it).
    assert lookup.await_count == 5
    assert [call.args[1] for call in lookup.await_args_list][4:] == ["swiggy-private"]
    assert second["seen"] == 4 and second["lookups"] == 1
    assert second["created"] == 0 and second["skipped"] == 4


async def test_the_login_is_matched_without_regard_to_case() -> None:
    await _add(
        name="SigNoz",
        slug="signoz",
        domain="signoz.io",
        github_org="signoz",
        extra_metadata={"github_org_checked_at": datetime.now(UTC).isoformat()},
    )
    lookup = _lookup()
    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(["SigNoz"]))
        ),
        patch.object(refresh.github_orgs, "fetch_org", lookup),
    ):
        result = await run_step("github_orgs", 60)
    assert lookup.await_count == 0
    assert result["skipped"] == 1 and result["lookups"] == 0


async def test_an_organisation_checked_over_thirty_days_ago_is_looked_up_again() -> None:
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    await _add(
        name="SigNoz",
        slug="signoz",
        domain="signoz.io",
        github_org="SigNoz",
        extra_metadata={"github_org_checked_at": old},
    )
    lookup = _lookup()
    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(["SigNoz"]))
        ),
        patch.object(refresh.github_orgs, "fetch_org", lookup),
    ):
        result = await run_step("github_orgs", 60)
    assert lookup.await_count == 1
    assert result["updated"] == 1
    assert (await _company("signoz.io")).extra_metadata["github_org_checked_at"] > old


async def test_a_curated_row_with_this_org_but_no_stamp_is_looked_up_once() -> None:
    await _add(name="SigNoz", slug="signoz", domain="signoz.io", github_org="SigNoz")
    lookup = _lookup()
    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(["SigNoz"]))
        ),
        patch.object(refresh.github_orgs, "fetch_org", lookup),
    ):
        await run_step("github_orgs", 60)
        await run_step("github_orgs", 60)
    assert lookup.await_count == 1


async def test_a_rate_limited_lookup_stops_the_run_and_keeps_its_place() -> None:
    orgs = _lookup()

    async def fetch_org(client: httpx.AsyncClient, login: str) -> dict[str, Any] | None:
        if login == "hotstar":
            raise RateLimited(reset_at=None)
        result: dict[str, Any] | None = await orgs(client, login)
        return result

    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(LOGINS))
        ),
        patch.object(refresh.github_orgs, "fetch_org", AsyncMock(side_effect=fetch_org)),
    ):
        result = await run_step("github_orgs", 60)

    assert result["stopped"] == "rate_limited"
    assert result["created"] == 1 and result["seen"] == 2
    # The two orgs before the limit are done; the limited one is retried next run.
    assert Cursor.decode(await CacheBackend.get_json(refresh.GITHUB_ORGS_CURSOR)) == Cursor(0, 2)


async def test_a_rate_limited_search_still_works_through_what_it_found() -> None:
    walk = _walk(LOGINS[:2], rate_limited=True)
    with (
        patch.object(refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=walk)),
        patch.object(refresh.github_orgs, "fetch_org", _lookup()),
    ):
        result = await run_step("github_orgs", 60)
    assert result["stopped"] == "rate_limited"
    assert result["created"] == 1 and result["skipped"] == 1


async def test_one_failed_lookup_is_skipped_and_five_in_a_row_stop_the_run() -> None:
    async def flaky(client: httpx.AsyncClient, login: str) -> dict[str, Any] | None:
        if login == "swiggy-private":
            raise httpx.ReadTimeout("slow")
        return _orgs().get(login)

    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(LOGINS))
        ),
        patch.object(refresh.github_orgs, "fetch_org", AsyncMock(side_effect=flaky)),
    ):
        result = await run_step("github_orgs", 60)
    assert "stopped" not in result
    assert result["created"] == 3 and result["skipped"] == 1

    async def down(client: httpx.AsyncClient, login: str) -> dict[str, Any] | None:
        raise httpx.ConnectError("down")

    many = [f"org-{n}" for n in range(9)]
    with (
        patch.object(refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(many))),
        patch.object(refresh.github_orgs, "fetch_org", AsyncMock(side_effect=down)) as lookup,
    ):
        stopped = await run_step("github_orgs", 60)
    assert stopped["stopped"] == "errors"
    assert lookup.await_count == 5


async def test_a_finished_cycle_starts_over() -> None:
    walk = _walk(LOGINS[:1], wrapped=True)
    walk.cursor = Cursor(0, 0)
    with (
        patch.object(refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=walk)),
        patch.object(refresh.github_orgs, "fetch_org", _lookup()),
    ):
        await run_step("github_orgs", 60)
    assert Cursor.decode(await CacheBackend.get_json(refresh.GITHUB_ORGS_CURSOR)) == Cursor(0, 0)


async def test_a_failing_search_returns_an_error_and_keeps_the_cursor() -> None:
    await CacheBackend.set_json(refresh.GITHUB_ORGS_CURSOR, Cursor(1, 40).encode(), 3600)
    boom = AsyncMock(side_effect=httpx.ConnectError("down"))
    with patch.object(refresh.github_orgs, "fetch_candidates", boom):
        result = await run_step("github_orgs", 60)
    assert result["step"] == "github_orgs"
    assert "error" in result
    assert Cursor.decode(await CacheBackend.get_json(refresh.GITHUB_ORGS_CURSOR)) == Cursor(1, 40)


async def test_the_limit_is_capped_before_it_reaches_the_walk() -> None:
    fetch_candidates = AsyncMock(return_value=_walk([]))
    with patch.object(refresh.github_orgs, "fetch_candidates", fetch_candidates):
        await run_step("github_orgs", 5000)
    assert fetch_candidates.await_args.args[2] == refresh.MAX_LIMIT["github_orgs"]


async def test_github_orgs_step_twice_changes_nothing() -> None:
    with (
        patch.object(
            refresh.github_orgs, "fetch_candidates", AsyncMock(return_value=_walk(LOGINS))
        ),
        patch.object(refresh.github_orgs, "fetch_org", _lookup()),
    ):
        await run_step("github_orgs", 60)
        before = {c.slug: (c.domain, c.sector, c.github_org) for c in await _companies()}
        await run_step("github_orgs", 60)
    after = {c.slug: (c.domain, c.sector, c.github_org) for c in await _companies()}
    assert before == after and len(after) == 3
