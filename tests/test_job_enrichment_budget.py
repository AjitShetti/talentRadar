"""
tests/test_job_enrichment_budget.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for the daily LLM enrichment budget.

The budget is the mechanism that keeps live sourcing affordable: postings are
stored with no model call and parsed later, only as far as the day's quota
allows. If it leaks, a free Groq tier is exhausted at an unpredictable hour and
the features that genuinely need a model - intent classification, the copilot -
start failing.
"""

from __future__ import annotations

from datetime import date

import pytest

from services.cache_backend import CacheBackend
from services.job_enrichment import (
    DAILY_ENRICHMENT_BUDGET,
    _budget_key,
    _consume_budget,
    remaining_budget,
)


@pytest.fixture(autouse=True)
async def memory_cache():
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


async def test_full_budget_available_at_the_start_of_a_day():
    assert await remaining_budget() == DAILY_ENRICHMENT_BUDGET


async def test_consuming_reduces_the_remaining_budget():
    assert await _consume_budget() is True
    assert await remaining_budget() == DAILY_ENRICHMENT_BUDGET - 1


async def test_budget_is_exhausted_after_the_daily_limit():
    for _ in range(DAILY_ENRICHMENT_BUDGET):
        assert await _consume_budget() is True

    assert await remaining_budget() == 0
    assert await _consume_budget() is False


async def test_budget_key_is_per_day():
    """A new day restores the quota without anything having to reset it."""
    assert _budget_key(date(2026, 9, 11)) != _budget_key(date(2026, 9, 12))
    assert _budget_key(date(2026, 9, 11)).startswith("tr:budget:llm:")


async def test_a_cache_failure_does_not_disable_enrichment(monkeypatch):
    """
    The budget is a cost guard. Losing the cache should not turn it into a
    kill switch for the feature it is guarding.
    """

    async def broken_incr(*args, **kwargs):
        raise RuntimeError("cache down")

    monkeypatch.setattr(CacheBackend, "incr", broken_incr)
    assert await _consume_budget() is True


async def test_scheduled_pass_stops_when_the_budget_is_gone():
    from services.job_enrichment import enrich_pending

    for _ in range(DAILY_ENRICHMENT_BUDGET):
        await _consume_budget()

    summary = await enrich_pending()
    assert summary["attempted"] == 0
    assert summary["enriched"] == 0
