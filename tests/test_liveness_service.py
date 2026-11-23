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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from domain.job_boards import PACE_SECONDS, AtsName, BoardRef
from domain.liveness import LivenessState
from ingestion.boards import READERS
from ingestion.boards.base import BoardRead, build_job, failed
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


def replay(monkeypatch: pytest.MonkeyPatch, responses: dict[str, tuple[int, Any]]) -> list[str]:
    """
    Answer board reads from a url -> (status, JSON) map, through the real readers.

    The readers make their requests through an ``httpx.MockTransport``, so nothing
    leaves the process. Returns the urls asked for, query string removed.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url.copy_with(query=None))
        calls.append(key)
        status, payload = responses.get(key, (404, ""))
        return httpx.Response(status, json=payload)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ATSScraper, "recheck_client", staticmethod(client))
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


ASHBY_JOB = "https://jobs.ashbyhq.com/linear/{id}"


def ashby_board(*ids: str) -> dict[str, Any]:
    return {"jobs": [{"id": i, "title": "Engineer", "jobUrl": ASHBY_JOB.format(id=i)} for i in ids]}


async def test_ashby_posting_is_looked_up_on_its_board(monkeypatch):
    replay(monkeypatch, {ASHBY: (200, ashby_board("x1", "x2"))})
    listed = ASHBY_JOB.format(id="x2")
    assert await ATSScraper.check_posting("ashby:linear", "x2", source_url=listed) is PostingCheck.OPEN
    gone = ASHBY_JOB.format(id="gone")
    assert await ATSScraper.check_posting("ashby:linear", "gone", source_url=gone) is PostingCheck.CLOSED


async def test_ashby_board_that_fails_to_load_closes_nothing(monkeypatch):
    replay(monkeypatch, {ASHBY: (503, "")})
    listed = ASHBY_JOB.format(id="x2")
    assert await ATSScraper.check_posting("ashby:linear", "x2", source_url=listed) is PostingCheck.ERROR


# ── a board checked by membership ────────────────────────────────────────────
#
# Workable, Recruitee, Keka, Rippling, Personio, Breezy and Ashby have no per-posting
# endpoint. A posting is open when its URL is among the roles of its board, read once
# through READERS; the readers are replaced here, so each test says what the board lists.

WORKABLE_BOARD = BoardRef("workable", "acme")
POSTED = "https://apply.workable.com/acme/j/ABC123/"


def role(url: str) -> Any:
    """A role as a reader builds it. A membership check reads only its posting URL."""
    return build_job(
        WORKABLE_BOARD,
        external_id=None,
        title="Engineer",
        url=url,
        location="Bengaluru, Karnataka, India",
        description=None,
    )


def board_read(*roles: Any, complete: bool = True) -> BoardRead:
    return BoardRead(outcome="ok", roles=list(roles), total=len(roles), complete=complete)


def fake_reader(
    monkeypatch: pytest.MonkeyPatch, ats: AtsName, answer: Callable[[BoardRef], BoardRead]
) -> list[BoardRef]:
    """Replace ``ats``'s reader in ``READERS`` with ``answer``; return the boards it was asked for."""
    asked: list[BoardRef] = []

    async def reader(client: httpx.AsyncClient, board: BoardRef) -> BoardRead:
        asked.append(board)
        return answer(board)

    monkeypatch.setitem(READERS, ats, reader)
    return asked


async def test_check_posting_workable_open_by_membership(monkeypatch):
    listed = [role(POSTED), role("https://apply.workable.com/acme/j/XYZ789/")]
    asked = fake_reader(monkeypatch, "workable", lambda board: board_read(*listed))
    boards: dict[BoardRef, BoardRead] = {}

    check = ATSScraper.check_posting
    assert await check("workable:acme", "ABC123", source_url=POSTED, boards=boards) is PostingCheck.OPEN
    # The same posting written without the trailing slash is the same posting.
    bare = POSTED.rstrip("/")
    assert await check("workable:acme", "ABC123", source_url=bare, boards=boards) is PostingCheck.OPEN
    # A posting the board no longer lists is closed; all three checks read the board once.
    gone = "https://apply.workable.com/acme/j/QQQ111/"
    assert await check("workable:acme", "QQQ111", source_url=gone, boards=boards) is PostingCheck.CLOSED
    assert asked == [WORKABLE_BOARD]


@pytest.mark.parametrize("code", ["ABC123", "QQQ111"])  # one listed, one absent
async def test_check_posting_incomplete_board_is_error(monkeypatch, code):
    # A read with a posting it could not parse is not a whole board: it proves nothing.
    fake_reader(monkeypatch, "workable", lambda board: board_read(role(POSTED), complete=False))
    url = f"https://apply.workable.com/acme/j/{code}/"
    assert await ATSScraper.check_posting("workable:acme", code, source_url=url) is PostingCheck.ERROR


@pytest.mark.parametrize("outcome", ["rate_limited", "not_found", "error"])
async def test_check_posting_rate_limited_is_error_not_closed(monkeypatch, outcome):
    # A rate limit, an outage or a 404 for the whole board says nothing about one posting.
    fake_reader(monkeypatch, "workable", lambda board: failed(outcome))
    gone = "https://apply.workable.com/acme/j/QQQ111/"
    assert await ATSScraper.check_posting("workable:acme", "QQQ111", source_url=gone) is PostingCheck.ERROR


async def test_a_posting_whose_url_names_another_board_is_not_checked_against_this_one(monkeypatch):
    asked = fake_reader(monkeypatch, "workable", lambda board: board_read(role(POSTED)))
    other = "https://apply.workable.com/other/j/ABC123/"
    assert await ATSScraper.check_posting("workable:acme", "ABC123", source_url=other) is PostingCheck.ERROR
    assert asked == []


def test_a_personio_posting_is_checked_on_the_region_its_url_names():
    assert ATSScraper.board_for("personio:acme", "https://acme.jobs.personio.com/job/12") == BoardRef(
        "personio", "acme", "com"
    )
    assert ATSScraper.board_for("personio:acme", "https://acme.jobs.personio.de/job/12") == BoardRef(
        "personio", "acme", "de"
    )


SARVAM_UUID = "0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9"
SARVAM_OTHER = "0a0a0a0a-0000-4000-8000-000000000000"


async def test_membership_by_id_survives_url_drift(monkeypatch):
    # The board lists the posting under a URL with a query string and an upper-case id; the
    # stored URL is the plain one. The posting id is the same, so the posting is still open.
    listed = f"https://jobs.ashbyhq.com/Sarvam/{SARVAM_UUID.upper()}?utm_source=feed"
    fake_reader(monkeypatch, "ashby", lambda board: board_read(role(listed)))
    stored = f"https://jobs.ashbyhq.com/sarvam/{SARVAM_UUID}"
    check = ATSScraper.check_posting
    assert await check("ashby:sarvam", SARVAM_UUID, source_url=stored) is PostingCheck.OPEN
    # Another posting, not on the board, is still closed.
    other = f"https://jobs.ashbyhq.com/sarvam/{SARVAM_OTHER}"
    assert await check("ashby:sarvam", SARVAM_OTHER, source_url=other) is PostingCheck.CLOSED


async def test_unparsed_urls_are_matched_by_their_url(monkeypatch):
    # An Ashby id that is not a UUID does not parse, so only the posting's URL can say which
    # posting it is.
    listed = "https://jobs.ashbyhq.com/sarvam/x1"
    fake_reader(monkeypatch, "ashby", lambda board: board_read(role(listed)))
    check = ATSScraper.check_posting
    assert await check("ashby:sarvam", "x1", source_url=listed) is PostingCheck.OPEN
    absent = "https://jobs.ashbyhq.com/sarvam/x2"
    assert await check("ashby:sarvam", "x2", source_url=absent) is PostingCheck.CLOSED


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


def candidate(source: str, external_id: str, source_url: str | None = None) -> liveness.Candidate:
    return liveness.Candidate(
        job_id=uuid.uuid4(), source=source, external_id=external_id, source_url=source_url
    )


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
    batch["candidates"] = [candidate("ashby:linear", f"x{i}", ASHBY_JOB.format(id=f"x{i}")) for i in range(6)]
    calls = replay(monkeypatch, {ASHBY: (200, ashby_board("x0", "x1"))})

    summary = await liveness.reverify_batch(limit=10)

    assert calls == [ASHBY]
    assert summary == {"checked": 6, "open": 2, "closed": 4, "errors": 0}


async def test_reverify_reads_each_board_once(monkeypatch, batch):
    # Two Workable boards share one pace; the Ashby board has its own. Each board is read
    # once for all of its postings, and the second Workable read waits its pace first.
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(liveness, "_sleep", record_sleep)
    workable = {
        "acme": [role("https://apply.workable.com/acme/j/ABC111/"), role("https://apply.workable.com/acme/j/ABC222/")],
        "beta": [],
    }
    ashby = {"linear": [role("https://jobs.ashbyhq.com/linear/x1")]}
    asked_workable = fake_reader(monkeypatch, "workable", lambda board: board_read(*workable[board.board]))
    asked_ashby = fake_reader(monkeypatch, "ashby", lambda board: board_read(*ashby[board.board]))
    batch["candidates"] = [
        candidate("workable:acme", "ABC111", "https://apply.workable.com/acme/j/ABC111/"),
        candidate("workable:acme", "ABC222", "https://apply.workable.com/acme/j/ABC222/"),
        candidate("workable:acme", "ABC333", "https://apply.workable.com/acme/j/ABC333/"),
        candidate("workable:beta", "BET001", "https://apply.workable.com/beta/j/BET001/"),
        candidate("ashby:linear", "x1", "https://jobs.ashbyhq.com/linear/x1"),
        candidate("ashby:linear", "x2", "https://jobs.ashbyhq.com/linear/x2"),
    ]

    summary = await liveness.reverify_batch(limit=10)

    assert asked_workable == [BoardRef("workable", "acme"), BoardRef("workable", "beta")]
    assert asked_ashby == [BoardRef("ashby", "linear")]
    assert sleeps == [PACE_SECONDS["workable"]]
    assert summary == {"checked": 6, "open": 3, "closed": 3, "errors": 0}


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


def test_retention_age_rule_binds_its_cutoff_as_a_value():
    """
    ``(:days || ' days')::interval`` with an integer bound through asyncpg
    raises "expected str, got int". The prune swallowed that and reported
    ``ran: False``, so retention never deleted anything on a real database.
    Rule 1 binds its cut-off as a timestamp computed in Python, so no interval
    is built in SQL and the string-concatenated form cannot come back.
    """
    import inspect

    from services import job_retention

    source = inspect.getsource(job_retention.prune_stale_jobs)
    assert "|| ' days'" not in source
    assert "raw_cutoff = datetime.now(UTC)" in source


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


# ── six ATSes are confirmed by the sweep, never by a request or the re-check batch ────────
#
# Workable, Recruitee, Keka, Rippling, Personio and Breezy have no per-posting endpoint: a posting
# is confirmed only by reading its whole board, 10 s apart for Workable and ten pages for Rippling.
# That read is the sweep's. A request (a dossier, a public page, the public check) never makes it,
# and the re-check batch never selects those rows: the sweep's complete reads confirm and close them.

RIPPLING_ID = "0f1e2d3c-4b5a-6978-8695-a4b3c2d1e0f9"
SWEEP_ONLY_ROWS = [
    ("workable:acme", "https://apply.workable.com/acme/j/ABC123/"),
    ("recruitee:acme", "https://acme.recruitee.com/o/backend-engineer"),
    ("keka:acme", "https://acme.keka.com/careers/jobdetails/12"),
    ("rippling:acme", f"https://ats.rippling.com/acme/jobs/{RIPPLING_ID}"),
    ("personio:acme", "https://acme.jobs.personio.de/job/12"),
    ("breezy:acme", "https://acme.breezy.hr/p/0123456789abcdef-backend"),
]


def _no_board_read(monkeypatch: pytest.MonkeyPatch) -> tuple[list[BoardRef], list[str]]:
    """Every reader and every HTTP call answers by recording: returns (boards read, urls asked)."""
    asked: list[BoardRef] = []

    async def reader(client: httpx.AsyncClient, board: BoardRef) -> BoardRead:
        asked.append(board)
        return board_read(complete=True)

    for ats in READERS:
        monkeypatch.setitem(READERS, ats, reader)
    calls = replay(monkeypatch, {})
    return asked, calls


@pytest.mark.parametrize(("source", "url"), SWEEP_ONLY_ROWS)
async def test_an_on_demand_recheck_never_reads_a_sweep_only_board(monkeypatch, source, url):
    asked, calls = _no_board_read(monkeypatch)
    job = SimpleNamespace(
        source=source, external_id=HASHED, closed_at=None, last_verified_at=None, source_url=url
    )

    assert await liveness.reverify_job(job) is PostingCheck.ERROR

    assert asked == [] and calls == []
    assert job.closed_at is None and job.last_verified_at is None


@pytest.mark.parametrize(("source", "url"), SWEEP_ONLY_ROWS)
async def test_a_sweep_only_row_is_never_due_for_an_on_demand_recheck(monkeypatch, source, url):
    asked, calls = _no_board_read(monkeypatch)
    session, job = _Session(), _stored(source=source, source_url=url)

    assert liveness.recheck_is_due(job, datetime.now(UTC)) is False
    await liveness.refresh_if_due(session, job)

    assert asked == [] and calls == []
    assert session.commits == 0 and job.last_verified_at is None


async def test_an_ashby_row_is_still_due_for_an_on_demand_recheck():
    job = _stored(source="ashby:linear", source_url=ASHBY_JOB.format(id="x1"))
    assert liveness.recheck_is_due(job, datetime.now(UTC)) is True


@pytest.fixture
async def memory_db(monkeypatch: pytest.MonkeyPatch):
    """An in-memory database from the real models, behind ``AsyncSessionLocal``."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import storage.database as database

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    sessions = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
    yield sessions
    await engine.dispose()


async def test_the_batch_never_selects_a_sweep_only_row(memory_db):
    from storage.models import Company, Job

    company = Company(name="Acme", domain="acme.example")
    sources = [
        "greenhouse:acme",
        "lever:acme",
        "ashby:acme",
        "workable:acme",
        "recruitee:acme",
        "keka:acme",
        "rippling:acme",
        "personio:acme",
        "breezy:acme",
        "linkedin",
    ]
    async with memory_db() as session:
        session.add(company)
        await session.flush()
        for number, source in enumerate(sources):
            session.add(
                Job(
                    company_id=company.id,
                    source=source,
                    external_id=f"id-{number}",
                    source_url=f"https://example.com/{number}",
                    title=f"Engineer {number}",
                )
            )
        await session.commit()

    chosen = await liveness._load_candidates(50)

    assert sorted(c.source for c in chosen) == ["ashby:acme", "greenhouse:acme", "lever:acme"]


async def test_the_batch_reads_no_sweep_only_board_even_when_they_are_all_due(monkeypatch, memory_db):
    from storage.models import Company, Job

    asked, calls = _no_board_read(monkeypatch)
    async with memory_db() as session:
        company = Company(name="Acme", domain="acme.example")
        session.add(company)
        await session.flush()
        for number, (source, url) in enumerate(SWEEP_ONLY_ROWS):
            session.add(
                Job(
                    company_id=company.id,
                    source=source,
                    external_id=HASHED[:-1] + str(number),
                    source_url=url,
                    title=f"Engineer {number}",
                )
            )
        await session.commit()

    summary = await liveness.reverify_batch(limit=50)

    assert summary == {"checked": 0, "open": 0, "closed": 0, "errors": 0}
    assert asked == [] and calls == []
