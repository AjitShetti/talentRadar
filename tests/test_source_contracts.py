"""
tests/test_source_contracts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Replay recorded source responses and assert each parser still works.

This is the regression half of the verification system. It never touches the
network: every source is run against a committed capture of what that site
really returned (see ``scripts/capture_fixtures.py``), so a change to a
parser, to the shared location helpers, or to URL validation shows up here
immediately and deterministically.

What it cannot catch, by construction, is a site changing under us - the
fixture keeps passing. That is what ``make verify-sources`` and the live
source-health registry are for.

The properties asserted per source are the ones the rest of the pipeline
depends on:

* jobs are produced at all (the silent failure mode: a 200 that parses to [])
* title, company and URL are populated (a job without them is unusable)
* the URL survives ingestion/validation.py (or persistence drops the row)
* the dedup hash is stable (or the same posting re-inserts forever)
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from ingestion.engine import compute_job_dedup_hash
from ingestion.scrapling_manager import ScraplingManager
from ingestion.sources.registry import LiveSource, default_live_sources
from ingestion.validation import validate_job_url

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sources"

# Only sources that can run without a browser are covered. A browser-only
# source cannot be captured on this machine or in CI, and asserting against a
# fixture nobody can regenerate is worse than not asserting.
REPLAYABLE_SOURCES = default_live_sources(enable_stealth=False)


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json.gz"


def load_fixture(name: str) -> dict[str, Any]:
    with gzip.open(fixture_path(name), "rt", encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def _request_key(url: str, params: dict[str, Any] | None) -> str:
    """Must match scripts/capture_fixtures.py, or replay cannot find a response."""
    if not params:
        return url
    encoded = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{url}?{encoded}"


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch):
    """Serve ``ScraplingManager.fetch_html_or_json`` from a recorded fixture."""

    def _install(source_name: str) -> dict[str, Any]:
        data = load_fixture(source_name)
        requests: dict[str, Any] = data["requests"]

        async def fake_fetch(
            url: str,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            timeout: float = 6.0,
            impersonate: str | None = None,
            **kwargs: Any,
        ) -> tuple[int, str | dict[str, Any]]:
            entry = requests.get(_request_key(url, params))
            if entry is None:
                # An unrecorded URL means the scraper now asks for something
                # the capture predates. Returning 404 keeps the scrape
                # honest: the source yields fewer jobs and the assertion
                # below reports it, rather than the test erroring obscurely.
                return 404, ""
            return int(entry["status"]), entry["payload"]

        monkeypatch.setattr(ScraplingManager, "fetch_html_or_json", fake_fetch)
        return data

    return _install


def _source_ids(source: LiveSource) -> str:
    return source.name


@pytest.mark.parametrize("source", REPLAYABLE_SOURCES, ids=_source_ids)
def test_every_replayable_source_has_a_fixture(source: LiveSource):
    """
    A source cannot join the roster without a recorded response.

    Without this, adding a scraper and forgetting its fixture would leave it
    permanently untested while every other test still passed.
    """
    assert fixture_path(source.name).exists(), (
        f"{source.name} has no fixture. Run: "
        f"python scripts/capture_fixtures.py --source {source.name}"
    )


@pytest.mark.parametrize("source", REPLAYABLE_SOURCES, ids=_source_ids)
async def test_source_parses_jobs_from_recorded_response(source: LiveSource, replay):
    data = replay(source.name)
    jobs = await source.fetch(data["query"], data["location"], None)

    assert jobs, (
        f"{source.name} parsed 0 jobs from a response that yielded "
        f"{data['captured_job_count']} at capture time - the parser broke."
    )
    assert len(jobs) >= data["expected_min_jobs"], (
        f"{source.name} yield dropped to {len(jobs)}, below the "
        f"{data['expected_min_jobs']} floor for this fixture."
    )


@pytest.mark.parametrize("source", REPLAYABLE_SOURCES, ids=_source_ids)
async def test_parsed_jobs_carry_the_required_fields(source: LiveSource, replay):
    data = replay(source.name)
    jobs = await source.fetch(data["query"], data["location"], None)

    for job in jobs:
        assert job.title and job.title.strip(), f"{source.name}: job with empty title"
        assert job.source == source.name or job.source, f"{source.name}: job with no source"
        assert job.source_url, f"{source.name}: job {job.title!r} has no URL"
        company = (job.extra_metadata or {}).get("company_name")
        assert company and company.strip(), f"{source.name}: job {job.title!r} has no company"


@pytest.mark.parametrize("source", REPLAYABLE_SOURCES, ids=_source_ids)
async def test_parsed_job_urls_survive_validation(source: LiveSource, replay):
    """
    Every URL must pass ingestion/validation.py.

    This is not academic: foundit.in, instahyre.com and freshersworld.com were
    all absent from the allowlist, which would have silently dropped every row
    those sources produced at persistence time.
    """
    data = replay(source.name)
    jobs = await source.fetch(data["query"], data["location"], None)

    rejected = []
    for job in jobs:
        valid, reason = validate_job_url(job.source_url or "")
        if not valid:
            rejected.append(f"{job.source_url} ({reason})")

    assert not rejected, f"{source.name} produced URLs validation rejects:\n  " + "\n  ".join(rejected[:5])


@pytest.mark.parametrize("source", REPLAYABLE_SOURCES, ids=_source_ids)
async def test_dedup_hash_is_stable_across_parses(source: LiveSource, replay):
    """
    The same posting must hash identically on every scrape.

    An unstable hash - one seeded with a fresh uuid4, say - re-inserts the
    same job forever, which on a 0.5 GB database is a storage leak rather
    than a cosmetic bug.
    """
    data = replay(source.name)
    first = await source.fetch(data["query"], data["location"], None)
    second = await source.fetch(data["query"], data["location"], None)

    assert [compute_job_dedup_hash(j) for j in first] == [compute_job_dedup_hash(j) for j in second]


@pytest.mark.parametrize("source", REPLAYABLE_SOURCES, ids=_source_ids)
async def test_source_returns_empty_list_rather_than_raising(source: LiveSource, monkeypatch):
    """
    A dead upstream degrades the fan-out; it never breaks it.

    Every scraper is called concurrently behind one user request, so an
    exception escaping here would take out a whole search.
    """

    async def dead_fetch(*args: Any, **kwargs: Any) -> tuple[int, str]:
        return 503, ""

    monkeypatch.setattr(ScraplingManager, "fetch_html_or_json", dead_fetch)
    assert await source.fetch("Python Developer", "Bengaluru", None) == []
