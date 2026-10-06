"""
tests/test_liveness_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Re-checking postings against the employer's ATS, and what gets recorded.

The database is stubbed at the two functions that touch it; what is pinned
here is the decision each response leads to, and that nothing in this path
can raise into the scheduler that calls it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from domain.liveness import LivenessState
from ingestion.scrapers.ats_scraper import ATSScraper, PostingCheck
from ingestion.scrapling_manager import ScraplingManager
from services import liveness

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def serve(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]) -> list[str]:
    """Answer ``fetch_html_or_json`` from a url -> (status, payload) | Exception map."""
    calls: list[str] = []

    async def fake_fetch(url: str, **kwargs: Any) -> tuple[int, Any]:
        calls.append(url)
        result = responses.get(url, (404, ""))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ScraplingManager, "fetch_html_or_json", fake_fetch)
    return calls


# ── one posting ──────────────────────────────────────────────────────────────

GH = "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/101"
LEVER = "https://api.lever.co/v0/postings/spotify/abc"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/linear"


async def test_greenhouse_posting_still_served_is_open(monkeypatch):
    serve(monkeypatch, {GH: (200, {"id": 101, "title": "Engineer"})})
    assert await ATSScraper.check_posting("greenhouse:stripe", "101") is PostingCheck.OPEN


async def test_greenhouse_404_is_closed(monkeypatch):
    serve(monkeypatch, {GH: (404, "")})
    assert await ATSScraper.check_posting("greenhouse:stripe", "101") is PostingCheck.CLOSED


async def test_lever_posting_is_checked_by_id(monkeypatch):
    serve(monkeypatch, {LEVER: (200, {"id": "abc"})})
    assert await ATSScraper.check_posting("lever:spotify", "abc") is PostingCheck.OPEN


async def test_ashby_posting_is_looked_up_on_its_board(monkeypatch):
    serve(monkeypatch, {ASHBY: (200, {"jobs": [{"id": "x1"}, {"id": "x2"}]})})
    assert await ATSScraper.check_posting("ashby:linear", "x2") is PostingCheck.OPEN
    assert await ATSScraper.check_posting("ashby:linear", "gone") is PostingCheck.CLOSED


async def test_ashby_board_that_fails_to_load_closes_nothing(monkeypatch):
    serve(monkeypatch, {ASHBY: (503, "")})
    assert await ATSScraper.check_posting("ashby:linear", "x2") is PostingCheck.ERROR


@pytest.mark.parametrize("status", [0, 403, 429, 500, 503])
async def test_anything_but_200_or_gone_is_an_error_not_a_closure(monkeypatch, status):
    # A rate limit or an outage must never be recorded as "the role closed".
    serve(monkeypatch, {GH: (status, "")})
    assert await ATSScraper.check_posting("greenhouse:stripe", "101") is PostingCheck.ERROR


async def test_a_200_with_a_body_that_is_not_a_posting_is_an_error(monkeypatch):
    serve(monkeypatch, {GH: (200, "<html>challenge</html>")})
    assert await ATSScraper.check_posting("greenhouse:stripe", "101") is PostingCheck.ERROR


async def test_a_raising_fetch_is_an_error(monkeypatch):
    serve(monkeypatch, {GH: TimeoutError("slow")})
    assert await ATSScraper.check_posting("greenhouse:stripe", "101") is PostingCheck.ERROR


@pytest.mark.parametrize(
    ("source", "external_id"),
    [("linkedin", "1"), ("greenhouse", "101"), ("greenhouse:stripe", ""), ("", "1")],
)
async def test_unverifiable_inputs_are_errors_without_a_request(monkeypatch, source, external_id):
    calls = serve(monkeypatch, {})
    assert await ATSScraper.check_posting(source, external_id) is PostingCheck.ERROR
    assert calls == []


async def test_a_timeout_is_retried_once(monkeypatch):
    attempts = 0

    async def flaky(url: str, **kwargs: Any) -> tuple[int, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("slow")
        return 200, {"id": 101}

    monkeypatch.setattr(ScraplingManager, "fetch_html_or_json", flaky)
    assert await ATSScraper.check_posting("greenhouse:stripe", "101") is PostingCheck.OPEN
    assert attempts == 2


# ── the batch ────────────────────────────────────────────────────────────────


def candidate(source: str, external_id: str) -> liveness.Candidate:
    return liveness.Candidate(job_id=uuid.uuid4(), source=source, external_id=external_id)


@pytest.fixture
def batch(monkeypatch: pytest.MonkeyPatch):
    """Stub the two database touchpoints; return what was written."""
    written: dict[str, list[uuid.UUID]] = {"open": [], "closed": []}
    state: dict[str, Any] = {"candidates": [], "written": written, "limit": None}

    async def fake_load(limit: int) -> list[liveness.Candidate]:
        state["limit"] = limit
        return state["candidates"][:limit]

    async def fake_apply(
        open_ids: list[uuid.UUID], closed_ids: list[uuid.UUID], *, now: datetime
    ) -> None:
        written["open"].extend(open_ids)
        written["closed"].extend(closed_ids)

    monkeypatch.setattr(liveness, "_load_candidates", fake_load)
    monkeypatch.setattr(liveness, "_apply_results", fake_apply)
    return state


async def test_batch_records_open_and_closed_and_leaves_errors_alone(monkeypatch, batch):
    still_open = candidate("greenhouse:stripe", "101")
    gone = candidate("greenhouse:stripe", "102")
    flaky = candidate("greenhouse:stripe", "103")
    batch["candidates"] = [still_open, gone, flaky]
    base = "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/"
    serve(monkeypatch, {base + "101": (200, {"id": 101}), base + "102": (404, ""), base + "103": (500, "")})

    summary = await liveness.reverify_batch(limit=10)

    assert summary == {"checked": 3, "open": 1, "closed": 1, "errors": 1}
    assert batch["written"]["open"] == [still_open.job_id]
    assert batch["written"]["closed"] == [gone.job_id]


async def test_batch_respects_its_limit(monkeypatch, batch):
    batch["candidates"] = [candidate("greenhouse:stripe", str(i)) for i in range(20)]
    serve(monkeypatch, {})
    summary = await liveness.reverify_batch(limit=5)
    assert batch["limit"] == 5
    assert summary["checked"] == 5


async def test_batch_fetches_an_ashby_board_once_for_all_its_postings(monkeypatch, batch):
    batch["candidates"] = [candidate("ashby:linear", f"x{i}") for i in range(6)]
    calls = serve(monkeypatch, {ASHBY: (200, {"jobs": [{"id": "x0"}, {"id": "x1"}]})})

    summary = await liveness.reverify_batch(limit=10)

    assert calls == [ASHBY]
    assert summary == {"checked": 6, "open": 2, "closed": 4, "errors": 0}


async def test_batch_never_raises_when_the_database_is_down(monkeypatch):
    async def broken(limit: int) -> list[liveness.Candidate]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(liveness, "_load_candidates", broken)
    summary = await liveness.reverify_batch()
    assert summary == {"checked": 0, "open": 0, "closed": 0, "errors": 0}


async def test_batch_never_raises_when_the_write_fails(monkeypatch, batch):
    async def broken_apply(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("deadlock")

    monkeypatch.setattr(liveness, "_apply_results", broken_apply)
    batch["candidates"] = [candidate("greenhouse:stripe", "101")]
    serve(monkeypatch, {GH: (200, {"id": 101})})
    summary = await liveness.reverify_batch()
    assert summary["checked"] == 1


async def test_empty_batch_makes_no_requests(monkeypatch, batch):
    calls = serve(monkeypatch, {})
    assert (await liveness.reverify_batch())["checked"] == 0
    assert calls == []


# ── verdict from stored rows ─────────────────────────────────────────────────


def job_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "source": "greenhouse:stripe",
        "source_posted_at": NOW - timedelta(days=10),
        "first_seen_at": NOW - timedelta(days=9),
        "last_seen_at": NOW - timedelta(hours=1),
        "last_verified_at": NOW - timedelta(hours=1),
        "closed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_verdict_for_reads_the_row_and_its_sighting():
    verdict = liveness.verdict_for(job_row(), None, now=NOW)
    assert verdict.state is LivenessState.VERIFIED_OPEN


def test_verdict_for_takes_reposts_from_the_sighting():
    sighting = SimpleNamespace(times_reposted=3)
    verdict = liveness.verdict_for(job_row(), sighting, now=NOW)
    assert verdict.state is LivenessState.REPOSTED


def test_verdict_for_marks_board_rows_unverifiable():
    verdict = liveness.verdict_for(
        job_row(source="linkedin", last_verified_at=None), None, now=NOW
    )
    assert verdict.state is LivenessState.OPEN_UNVERIFIED


def test_verdict_to_dict_is_json_ready():
    payload = liveness.verdict_to_dict(liveness.verdict_for(job_row(), None, now=NOW))
    assert payload == {
        "state": "verified_open",
        "headline": "Confirmed open 1 hour ago",
        "evidence": list(liveness.verdict_for(job_row(), None, now=NOW).evidence),
        "open_days": 10,
    }


# ── retention leaves the repost history alone ────────────────────────────────


async def test_pruning_jobs_never_touches_role_sightings(monkeypatch):
    """
    ``role_sightings`` exists precisely because job rows are pruned. If a
    prune ever deleted from it, "re-listed 3 times" would quietly reset to
    zero on a schedule.
    """
    import storage.database as database
    from services import job_retention

    statements: list[str] = []

    class FakeResult:
        rowcount = 0

        def scalar(self) -> int:
            return 0

        def scalar_one(self) -> int:
            return 0

    class FakeSession:
        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def execute(self, statement: Any, params: Any = None) -> FakeResult:
            statements.append(str(statement))
            return FakeResult()

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(database, "AsyncSessionLocal", FakeSession)
    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert any("DELETE FROM jobs" in s for s in statements)
    assert not any("role_sightings" in s for s in statements)


def test_retention_age_rule_binds_days_as_a_number():
    """
    ``(:days || ' days')::interval`` with an integer bound through asyncpg
    raises "expected str, got int". The prune swallowed that and reported
    ``ran: False``, so retention never deleted anything on a real database.
    """
    import inspect

    from services import job_retention

    source = inspect.getsource(job_retention.prune_stale_jobs)
    assert "|| ' days'" not in source
    assert "make_interval(days => :days)" in source


# ── which posting to ask about ───────────────────────────────────────────────
#
# The live-scrape path does not keep the ATS's own posting id: it stores an MD5
# of the posting URL as ``external_id``. Asking Greenhouse for a posting by
# that hash is a 404, which reads as "closed" - so a re-check marked every
# such role closed while the employer was still serving it. The URL carries
# the real id, so that is what a re-check must use.

HASHED = "5f4dcc3b5aa765d61d8327deb882cf99"


@pytest.mark.parametrize(
    ("source", "url", "external_id", "expected"),
    [
        ("greenhouse:inmobi", "https://job-boards.greenhouse.io/inmobi/jobs/7393433", HASHED,
         ("greenhouse:inmobi", "7393433")),
        ("greenhouse:inmobi", "https://www.inmobi.com/careers?gh_jid=7393433&x=1", HASHED,
         ("greenhouse:inmobi", "7393433")),
        ("lever:spotify", "https://jobs.lever.co/spotify/0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9", HASHED,
         ("lever:spotify", "0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9")),
        ("ashby:linear", "https://jobs.ashbyhq.com/linear/0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9", HASHED,
         ("ashby:linear", "0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9")),
        # A row that does hold the real id is still usable without a readable URL.
        ("greenhouse:stripe", None, "101", ("greenhouse:stripe", "101")),
    ],
)
def test_the_posting_to_ask_about_comes_from_the_url(source, url, external_id, expected):
    from domain.posting_urls import posting_ref

    ref = posting_ref(source, url, external_id)
    assert ref is not None
    assert (ref.source, ref.external_id) == expected


@pytest.mark.parametrize(
    ("source", "url", "external_id"),
    [
        ("greenhouse:inmobi", "https://www.inmobi.com/careers/devops", HASHED),
        ("greenhouse:inmobi", None, HASHED),
        ("greenhouse:inmobi", None, None),
        ("linkedin", "https://www.linkedin.com/jobs/view/1", "1"),
        # A URL for a different ATS than the row claims proves nothing.
        ("lever:spotify", "https://job-boards.greenhouse.io/spotify/jobs/1", HASHED),
    ],
)
def test_a_row_with_no_real_posting_id_cannot_be_asked_about(source, url, external_id):
    from domain.posting_urls import posting_ref

    assert posting_ref(source, url, external_id) is None


async def test_batch_asks_about_the_id_in_the_url_not_the_stored_hash(monkeypatch, batch):
    row = liveness.Candidate(
        job_id=uuid.uuid4(), source="greenhouse:inmobi", external_id=HASHED,
        source_url="https://job-boards.greenhouse.io/inmobi/jobs/7393433",
    )
    batch["candidates"] = [row]
    real = "https://boards-api.greenhouse.io/v1/boards/inmobi/jobs/7393433"
    calls = serve(monkeypatch, {real: (200, {"id": 7393433})})

    summary = await liveness.reverify_batch(limit=10)

    assert calls == [real]
    assert summary == {"checked": 1, "open": 1, "closed": 0, "errors": 0}
    assert batch["written"]["closed"] == []


async def test_batch_never_closes_a_row_it_cannot_identify(monkeypatch, batch):
    row = liveness.Candidate(
        job_id=uuid.uuid4(), source="greenhouse:inmobi", external_id=HASHED,
        source_url="https://www.inmobi.com/careers/devops",
    )
    batch["candidates"] = [row]
    calls = serve(monkeypatch, {})

    summary = await liveness.reverify_batch(limit=10)

    assert calls == []
    assert summary == {"checked": 1, "open": 0, "closed": 0, "errors": 1}
    assert batch["written"] == {"open": [], "closed": []}


async def test_on_demand_recheck_uses_the_url_and_does_not_close_an_open_role(monkeypatch):
    from types import SimpleNamespace

    job = SimpleNamespace(
        source="greenhouse:inmobi", external_id=HASHED, closed_at=None, last_verified_at=None,
        source_url="https://job-boards.greenhouse.io/inmobi/jobs/7393433",
    )
    real = "https://boards-api.greenhouse.io/v1/boards/inmobi/jobs/7393433"
    serve(monkeypatch, {real: (200, {"id": 7393433})})

    assert await liveness.reverify_job(job) is PostingCheck.OPEN
    assert job.closed_at is None and job.last_verified_at is not None


async def test_on_demand_recheck_leaves_an_unidentifiable_row_alone(monkeypatch):
    from types import SimpleNamespace

    job = SimpleNamespace(
        source="greenhouse:inmobi", external_id=HASHED, closed_at=None, last_verified_at=None,
        source_url=None,
    )
    calls = serve(monkeypatch, {})

    assert await liveness.reverify_job(job) is PostingCheck.ERROR
    assert calls == [] and job.closed_at is None


# ── refreshing a verdict when someone is about to read it ────────────────────


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _stored(**fields: Any) -> Any:
    from types import SimpleNamespace

    base = {
        "source": "greenhouse:inmobi", "external_id": HASHED, "closed_at": None,
        "last_verified_at": None,
        "source_url": "https://job-boards.greenhouse.io/inmobi/jobs/7393433",
    }
    base.update(fields)
    return SimpleNamespace(**base)


async def test_a_never_verified_role_is_checked_before_it_is_shown(monkeypatch):
    real = "https://boards-api.greenhouse.io/v1/boards/inmobi/jobs/7393433"
    serve(monkeypatch, {real: (200, {"id": 7393433})})
    session, job = _Session(), _stored()

    await liveness.refresh_if_due(session, job)

    assert job.last_verified_at is not None
    assert session.commits == 1


async def test_a_recently_verified_role_is_not_checked_again(monkeypatch):
    calls = serve(monkeypatch, {})
    session, job = _Session(), _stored(last_verified_at=datetime.now(UTC))

    await liveness.refresh_if_due(session, job)

    assert calls == [] and session.commits == 0


async def test_a_failed_refresh_never_breaks_the_page(monkeypatch):
    async def boom(job: Any) -> PostingCheck:
        raise RuntimeError("network down")

    monkeypatch.setattr(liveness, "reverify_job", boom)
    session, job = _Session(), _stored()

    await liveness.refresh_if_due(session, job)

    assert session.rollbacks == 1 and job.closed_at is None


def test_a_stored_role_is_matched_to_a_pasted_url_by_the_id_in_its_own_url():
    from domain.posting_urls import parse_posting_url
    from services.public_roles import _is_the_posting

    ref = parse_posting_url("https://boards.greenhouse.io/inmobi/jobs/7393433")
    assert ref is not None
    assert _is_the_posting(_stored(), ref)
    assert not _is_the_posting(
        _stored(source_url="https://job-boards.greenhouse.io/inmobi/jobs/73934339"), ref
    )
