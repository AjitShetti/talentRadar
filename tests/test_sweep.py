"""
tests/test_sweep.py
~~~~~~~~~~~~~~~~~~~
The overnight sweep (services/sweep.py).

The behaviour worth pinning is the ordering and the failure containment: the
sweep must ingest *before* it ranks (ranking fresh rows is the whole point), and
neither half may take the process down, because it runs unattended in-process.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import sweep as sweep_module


@pytest.fixture
def _no_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty() -> list[str]:
        return []

    monkeypatch.setattr(sweep_module, "collect_target_roles", _empty)


@pytest.mark.asyncio
async def test_sweep_ingests_before_it_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranking a stale table is the bug this module exists to fix."""
    calls: list[str] = []

    async def _roles() -> list[str]:
        return ["Backend Engineer"]

    async def _dispatch(**kwargs: Any) -> dict[str, Any]:
        calls.append("ingest")
        return {"sources": ["greenhouse", "lever"], "total_fetched": 61, "inserted": 4}

    async def _match() -> None:
        calls.append("match")

    monkeypatch.setattr(sweep_module, "collect_target_roles", _roles)
    monkeypatch.setattr(sweep_module, "run_daily_matching_for_all_users", _match)
    monkeypatch.setattr("ingestion.dispatcher.dispatch_ingestion", _dispatch)

    summary = await sweep_module.run_overnight_sweep()

    assert calls == ["ingest", "match"]
    assert summary["ingested"]["fetched"] == 61
    assert summary["matched"] is True


@pytest.mark.asyncio
async def test_sweep_still_ranks_when_ingestion_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scraper outage degrades the sweep, it does not skip the day."""
    matched: list[bool] = []

    async def _roles() -> list[str]:
        return ["Backend Engineer"]

    async def _boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("every source is down")

    async def _match() -> None:
        matched.append(True)

    monkeypatch.setattr(sweep_module, "collect_target_roles", _roles)
    monkeypatch.setattr(sweep_module, "run_daily_matching_for_all_users", _match)
    monkeypatch.setattr("ingestion.dispatcher.dispatch_ingestion", _boom)

    summary = await sweep_module.run_overnight_sweep()

    assert matched == [True]
    assert summary["ingested"] is None
    assert summary["matched"] is True


@pytest.mark.asyncio
async def test_sweep_skips_ingestion_with_no_target_roles(
    monkeypatch: pytest.MonkeyPatch, _no_roles: None
) -> None:
    """No profiles, no scraping — an empty deployment must not scrape blindly."""
    called: list[str] = []

    async def _dispatch(**kwargs: Any) -> dict[str, Any]:
        called.append("ingest")
        return {}

    async def _match() -> None:
        return None

    monkeypatch.setattr(sweep_module, "run_daily_matching_for_all_users", _match)
    monkeypatch.setattr("ingestion.dispatcher.dispatch_ingestion", _dispatch)

    summary = await sweep_module.run_overnight_sweep()

    assert called == []
    assert summary["ingested"] is None


@pytest.mark.asyncio
async def test_sweep_survives_a_matching_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scheduler must never see an exception escape."""

    async def _roles() -> list[str]:
        return []

    async def _boom() -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(sweep_module, "collect_target_roles", _roles)
    monkeypatch.setattr(sweep_module, "run_daily_matching_for_all_users", _boom)

    summary = await sweep_module.run_overnight_sweep()

    assert summary["matched"] is False
