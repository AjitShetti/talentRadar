"""
tests/test_llm_budget.py
~~~~~~~~~~~~~~~~~~~~~~~~
The shared daily draft budget.

A budget here is a cost guard on a free Groq tier, not a ledger: it counts
with ``CacheBackend.incr``, claims before it compares, and fails open. Every
test runs on the in-process cache so a developer's Redis is neither read nor
written.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from services import llm_budget
from services.cache_backend import CacheBackend


@pytest.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    """The in-process cache only: a developer's Redis is neither read nor written."""
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


async def test_claim_allows_up_to_the_limit() -> None:
    results = [await llm_budget.claim("tr:budget:test:a", limit=2) for _ in range(3)]
    assert results == [True, True, False]


async def test_claim_counts_each_key_on_its_own() -> None:
    assert await llm_budget.claim("tr:budget:test:one", limit=1) is True
    assert await llm_budget.claim("tr:budget:test:one", limit=1) is False
    assert await llm_budget.claim("tr:budget:test:two", limit=1) is True


async def test_claim_fails_open() -> None:
    with patch.object(CacheBackend, "incr", AsyncMock(side_effect=RuntimeError("redis down"))):
        assert await llm_budget.claim("tr:budget:test:down", limit=1) is True


async def test_claim_sets_the_forty_eight_hour_ttl_by_default() -> None:
    with patch.object(CacheBackend, "incr", AsyncMock(return_value=1)) as incr:
        await llm_budget.claim("tr:budget:test:ttl", limit=5)
    incr.assert_awaited_once_with("tr:budget:test:ttl", 48 * 3600)
    assert llm_budget.BUDGET_TTL_SECONDS == 48 * 3600


async def test_used_counts_claims_and_is_zero_for_an_unseen_key() -> None:
    assert await llm_budget.used("tr:budget:test:fresh") == 0
    await llm_budget.claim("tr:budget:test:fresh", limit=10)
    await llm_budget.claim("tr:budget:test:fresh", limit=10)
    assert await llm_budget.used("tr:budget:test:fresh") == 2


async def test_used_is_zero_when_the_cache_cannot_be_read() -> None:
    with patch.object(CacheBackend, "get", AsyncMock(side_effect=RuntimeError("redis down"))):
        assert await llm_budget.used("tr:budget:test:down") == 0


async def test_used_ignores_a_value_that_is_not_a_number() -> None:
    await CacheBackend.set("tr:budget:test:junk", "not-a-number", 60)
    assert await llm_budget.used("tr:budget:test:junk") == 0


def test_user_key_uses_the_ist_date() -> None:
    # 19:00 UTC on the 14th is 00:30 IST on the 15th.
    now = datetime(2026, 10, 14, 19, 0, tzinfo=UTC)
    assert llm_budget.user_draft_key("u1", now) == "tr:budget:drafts:user:u1:2026-10-15"


def test_user_key_just_before_the_ist_midnight_is_still_that_day() -> None:
    now = datetime(2026, 10, 14, 18, 29, tzinfo=UTC)  # 23:59 IST on the 14th
    assert llm_budget.user_draft_key("u1", now) == "tr:budget:drafts:user:u1:2026-10-14"


def test_user_key_defaults_to_now() -> None:
    key = llm_budget.user_draft_key("u1")
    assert key.startswith("tr:budget:drafts:user:u1:20")


async def test_user_drafts_left() -> None:
    assert llm_budget.PER_USER_DAILY_DRAFTS == 20
    for _ in range(3):
        assert await llm_budget.claim_user_draft("u1") is True
    assert await llm_budget.user_drafts_left("u1") == 17


async def test_user_cap_is_per_user() -> None:
    for _ in range(llm_budget.PER_USER_DAILY_DRAFTS):
        assert await llm_budget.claim_user_draft("u1") is True
    assert await llm_budget.claim_user_draft("u1") is False
    assert await llm_budget.user_drafts_left("u1") == 0
    assert await llm_budget.claim_user_draft("u2") is True
    assert await llm_budget.user_drafts_left("u2") == llm_budget.PER_USER_DAILY_DRAFTS - 1


async def test_user_drafts_left_never_goes_negative() -> None:
    # Claims past the cap still increment the counter; the display must not go below zero.
    for _ in range(llm_budget.PER_USER_DAILY_DRAFTS + 3):
        await llm_budget.claim_user_draft("u1")
    assert await llm_budget.user_drafts_left("u1") == 0
