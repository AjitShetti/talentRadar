"""
tests/test_source_health.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for the live source-health registry and its circuit breaker.

These run against the in-memory cache fallback, which is deliberate: the same
code path has to be correct when Redis is absent, because on the deployed free
tier it frequently is.
"""

from __future__ import annotations

import time

import pytest

from services.cache_backend import CacheBackend
from services.source_health import (
    COOLDOWN_SECONDS,
    FAILURE_THRESHOLD,
    ZERO_YIELD_THRESHOLD,
    SourceHealthService,
)


@pytest.fixture(autouse=True)
async def clean_cache():
    await CacheBackend.reset()
    # Force the memory fallback: these tests must not depend on a local Redis.
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


async def test_unknown_source_starts_unknown_and_available():
    health = await SourceHealthService.get("never_run")
    assert health.status == "unknown"
    assert health.is_circuit_open() is False
    assert await SourceHealthService.is_available("never_run") is True


async def test_successful_attempt_is_healthy():
    health = await SourceHealthService.record_attempt(
        "foundit", job_count=15, latency_ms=500.0, status="success"
    )
    assert health.status == "healthy"
    assert health.successes == 1
    assert health.total_jobs == 15
    assert health.last_success_at is not None


async def test_counters_accumulate_across_attempts():
    for _ in range(3):
        await SourceHealthService.record_attempt(
            "foundit", job_count=10, latency_ms=100.0, status="success"
        )
    health = await SourceHealthService.get("foundit")
    assert health.attempts == 3
    assert health.total_jobs == 30


async def test_repeated_zero_yield_becomes_degraded():
    """
    The silent failure: a 200 response that parses to nothing. It reports
    success every time, so only the repetition gives it away.
    """
    for _ in range(ZERO_YIELD_THRESHOLD - 1):
        health = await SourceHealthService.record_attempt(
            "foundit", job_count=0, latency_ms=300.0, status="success"
        )
        assert health.status == "healthy", "one empty result is ordinary"

    health = await SourceHealthService.record_attempt(
        "foundit", job_count=0, latency_ms=300.0, status="success"
    )
    assert health.status == "degraded"


async def test_a_single_result_clears_the_zero_yield_streak():
    for _ in range(ZERO_YIELD_THRESHOLD):
        await SourceHealthService.record_attempt("foundit", job_count=0, latency_ms=1.0, status="success")
    assert (await SourceHealthService.get("foundit")).status == "degraded"

    health = await SourceHealthService.record_attempt(
        "foundit", job_count=4, latency_ms=1.0, status="success"
    )
    assert health.consecutive_zero_yields == 0
    assert health.status == "healthy"


async def test_repeated_failures_open_the_circuit():
    for _ in range(FAILURE_THRESHOLD - 1):
        health = await SourceHealthService.record_attempt(
            "indeed_india", job_count=0, latency_ms=7000.0, status="timeout"
        )
        assert health.is_circuit_open() is False

    health = await SourceHealthService.record_attempt(
        "indeed_india", job_count=0, latency_ms=7000.0, status="timeout"
    )
    assert health.status == "failing"
    assert health.is_circuit_open() is True
    assert await SourceHealthService.is_available("indeed_india") is False


async def test_open_circuit_expires_after_cooldown():
    for _ in range(FAILURE_THRESHOLD):
        await SourceHealthService.record_attempt(
            "indeed_india", job_count=0, latency_ms=1.0, status="error: blocked"
        )
    health = await SourceHealthService.get("indeed_india")
    assert health.is_circuit_open(now=time.time()) is True
    assert health.is_circuit_open(now=time.time() + COOLDOWN_SECONDS + 1) is False


async def test_success_closes_the_circuit():
    for _ in range(FAILURE_THRESHOLD):
        await SourceHealthService.record_attempt("indeed_india", job_count=0, latency_ms=1.0, status="timeout")
    assert await SourceHealthService.is_available("indeed_india") is False

    await SourceHealthService.record_attempt("indeed_india", job_count=5, latency_ms=1.0, status="success")
    assert await SourceHealthService.is_available("indeed_india") is True


async def test_filter_available_drops_broken_sources():
    for _ in range(FAILURE_THRESHOLD):
        await SourceHealthService.record_attempt("naukri", job_count=0, latency_ms=1.0, status="timeout")
    await SourceHealthService.record_attempt("foundit", job_count=9, latency_ms=1.0, status="success")

    available = await SourceHealthService.filter_available(["foundit", "naukri", "linkedin"])
    assert "foundit" in available
    assert "linkedin" in available, "an untried source is available, not broken"
    assert "naukri" not in available


async def test_p50_latency_is_reported():
    for ms in (100.0, 200.0, 300.0):
        await SourceHealthService.record_attempt("foundit", job_count=1, latency_ms=ms, status="success")
    assert (await SourceHealthService.get("foundit")).p50_latency_ms == 200.0


async def test_latency_window_is_bounded():
    """Health is a signal, not a metrics store; the cache is 25 MB."""
    for i in range(60):
        await SourceHealthService.record_attempt("foundit", job_count=1, latency_ms=float(i), status="success")
    assert len((await SourceHealthService.get("foundit")).recent_latencies_ms) <= 20


async def test_reset_clears_a_broken_source():
    for _ in range(FAILURE_THRESHOLD):
        await SourceHealthService.record_attempt("naukri", job_count=0, latency_ms=1.0, status="timeout")
    assert await SourceHealthService.is_available("naukri") is False

    await SourceHealthService.reset("naukri")
    assert await SourceHealthService.is_available("naukri") is True
    assert (await SourceHealthService.get("naukri")).status == "unknown"


async def test_report_shape_is_serialisable():
    await SourceHealthService.record_attempt("foundit", job_count=3, latency_ms=42.0, status="success")
    report = await SourceHealthService.report(["foundit", "linkedin"])

    assert len(report) == 2
    entry = report[0]
    for key in ("name", "status", "attempts", "total_jobs", "p50_latency_ms", "circuit_open"):
        assert key in entry
