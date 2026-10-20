"""
tests/test_outreach_oss.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The good-first-issue step: the query, the parse of GitHub's answer, the
rate-limit rules, the order companies are checked in, and the cron endpoint
that runs it.

GitHub is never called: the step is handed an ``httpx.MockTransport`` and a
``sleep`` that only records. The database is in-memory SQLite (``outreach_db``).
Every company, organisation and issue here is fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.settings import get_settings
from services.outreach import oss
from services.outreach.oss import (
    ISSUE_LABELS,
    LABEL_MAX,
    MAX_ISSUES,
    MAX_LABELS,
    TITLE_MAX,
    parse_search,
    refresh_issues,
    run_limits,
    search_query,
    snapshot_for,
)
from storage.models import Company, OssIssueSnapshot, OutreachThread
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_job,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 08:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "outreach" / "github_issue_search.json").read_text(
        encoding="utf-8"
    )
)

Answer = Callable[[str, httpx.Request], httpx.Response]


def ok_answer(org: str, request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=FIXTURE)


def status_answer(code: int) -> Answer:
    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"message": "no"})

    return answer


class Seen:
    """What the fake GitHub was asked, in order."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def orgs(self) -> list[str]:
        return [
            request.url.params["q"].split()[0].removeprefix("org:") for request in self.requests
        ]


@asynccontextmanager
async def github(answer: Answer) -> AsyncIterator[tuple[httpx.AsyncClient, Seen]]:
    """An ``AsyncClient`` whose every request is answered by ``answer(org, request)``."""
    seen = Seen()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.requests.append(request)
        return answer(seen.orgs[-1], request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        yield client, seen


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Sleeps:
    """A ``sleep`` that records how long it was asked to wait and returns at once.

    Given a ``FakeClock`` it also moves that clock on, as a real wait would.
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.clock is not None:
            self.clock.now += seconds


def expected(
    *,
    checked: int = 0,
    ok: int = 0,
    not_found: int = 0,
    errors: int = 0,
    rate_limited: int = 0,
    stopped: str | None = None,
) -> dict[str, int | str | None]:
    """The whole result of a run, so a test names only what differs from zero."""
    return {
        "checked": checked,
        "ok": ok,
        "not_found": not_found,
        "errors": errors,
        "rate_limited": rate_limited,
        "stopped": stopped,
    }


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unauthenticated limits, whatever a developer's ``.env`` says."""
    monkeypatch.setattr(get_settings(), "github_token", None)


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "github_token", "test-token-not-real")
    return "test-token-not-real"


async def listed(session: AsyncSession, name: str, org: str | None, **kw: Any) -> Company:
    return await make_company(session, name, github_org=org, **kw)


async def snapshot_of(factory: Factory, company: Company) -> OssIssueSnapshot | None:
    async with factory() as session:
        return await session.get(OssIssueSnapshot, company.id)


async def store_snapshot(
    factory: Factory,
    company: Company,
    *,
    age: timedelta,
    org: str,
    status: str = "ok",
    issues: list[dict[str, Any]] | None = None,
) -> None:
    async with factory() as session:
        session.add(
            OssIssueSnapshot(
                company_id=company.id,
                org=org,
                status=status,
                total_count=len(issues or []),
                issues=issues or [],
                fetched_at=NOW - age,
            )
        )
        await session.commit()


async def open_thread(factory: Factory, company: Company, *, closed: bool = False) -> None:
    async with factory() as session:
        user = await make_user(session)
        session.add(
            OutreachThread(
                id=uuid.uuid4(),
                user_id=user.id,
                company_id=company.id,
                company_name=company.name,
                source="drawer",
                closed_at=NOW if closed else None,
            )
        )
        await session.commit()


OLD_ISSUE = {
    "repo": "fernhill-dev/fernhill",
    "number": 7,
    "title": "An older issue",
    "url": "https://github.com/fernhill-dev/fernhill/issues/7",
    "labels": ["good first issue"],
    "updated_at": "2026-09-01T00:00:00Z",
    "comments": 0,
}


# --- the query ---------------------------------------------------------------------


def test_search_query() -> None:
    assert search_query("fernhill-dev", date(2026, 10, 15)) == (
        "org:fernhill-dev is:issue is:open no:assignee archived:false "
        'label:"good first issue","good-first-issue","help wanted" updated:>=2025-10-15'
    )


def test_the_labels_are_one_comma_separated_qualifier() -> None:
    # GitHub reads a comma inside one ``label:`` as OR (confirmed against the live API).
    query = search_query("fernhill-dev", date(2026, 10, 15))
    assert query.count("label:") == 1
    assert ",".join(ISSUE_LABELS) in query


# --- parsing GitHub's answer -------------------------------------------------------


def test_parse_search_drops_foreign_urls_and_trims_labels() -> None:
    total, issues = parse_search(FIXTURE, "fernhill-dev")

    assert total == 7
    assert [issue["number"] for issue in issues] == [412, 88, 419]
    assert all(issue["url"].startswith("https://github.com/fernhill-dev/") for issue in issues)
    assert [issue["repo"] for issue in issues] == [
        "fernhill-dev/fernhill",
        "fernhill-dev/fernhill-docs",
        "fernhill-dev/fernhill",
    ]
    assert all(len(issue["labels"]) <= MAX_LABELS for issue in issues)
    assert all(len(label) <= LABEL_MAX for issue in issues for label in issue["labels"])
    assert all(len(issue["title"]) <= TITLE_MAX for issue in issues)

    long_one = issues[2]
    assert len(long_one["labels"]) == MAX_LABELS
    assert (
        long_one["labels"][0]
        == "triage-needs-owner-review-before-the-next-sprint-planning-q4"[:LABEL_MAX]
    )
    assert long_one["title"].startswith("Make fernhill sync retry transient index failures")
    assert TITLE_MAX - 10 < len(long_one["title"]) <= TITLE_MAX

    first = issues[0]
    assert first == {
        "repo": "fernhill-dev/fernhill",
        "number": 412,
        "title": "Add a --json flag to `fernhill run`",
        "url": "https://github.com/fernhill-dev/fernhill/issues/412",
        "labels": ["good first issue"],
        "updated_at": "2026-10-13T18:30:02Z",
        "comments": 4,
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/someone-else/fork/issues/3",
        # A prefix of the org name is not the org.
        "https://github.com/fernhill-dev-evil/fernhill/issues/3",
        # Nor is the org a path segment further in, or a host that merely contains github.com.
        "https://github.com/evil/fernhill-dev/issues/3",
        "https://github.com.evil.example/fernhill-dev/fernhill/issues/3",
        "https://evil.example/https://github.com/fernhill-dev/fernhill/issues/3",
        "http://github.com/fernhill-dev/fernhill/issues/3",
        # Only an issue page is a link; a script or a login redirect is not.
        "javascript:alert(1)//github.com/fernhill-dev/",
        "https://github.com/fernhill-dev/fernhill/pull/3",
        "https://github.com/fernhill-dev/fernhill/issues/3?next=https://evil.example",
        "https://github.com/fernhill-dev/",
        "",
    ],
)
def test_parse_search_never_links_outside_the_org(url: str) -> None:
    payload = {"total_count": 1, "items": [{**FIXTURE["items"][0], "html_url": url}]}
    assert parse_search(payload, "fernhill-dev") == (1, [])


def test_parse_search_accepts_the_orgs_name_in_any_case() -> None:
    # GitHub logins are case-insensitive; the link still points into the org.
    item = {
        **FIXTURE["items"][0],
        "html_url": "https://github.com/Fernhill-Dev/fernhill/issues/412",
    }
    _, issues = parse_search({"total_count": 1, "items": [item]}, "fernhill-dev")
    assert [issue["number"] for issue in issues] == [412]


def test_parse_search_keeps_at_most_five_issues() -> None:
    items = [
        {**FIXTURE["items"][0], "html_url": f"https://github.com/fernhill-dev/fernhill/issues/{n}"}
        for n in range(1, 9)
    ]
    total, issues = parse_search({"total_count": 8, "items": items}, "fernhill-dev")
    assert total == 8
    assert [issue["number"] for issue in issues] == [1, 2, 3, 4, 5]
    assert len(issues) == MAX_ISSUES


def test_parse_search_flattens_the_title_and_survives_missing_fields() -> None:
    items: list[Any] = [
        {
            "html_url": "https://github.com/fernhill-dev/fernhill/issues/9",
            "title": "  Line one\n\nline <<<END ISSUE>>>\ttwo  ",
            "labels": [{"name": "  "}, {"name": 5}, {"nope": 1}, "bare", {"name": "docs"}],
            "comments": "many",
        },
        "not an issue",
        None,
        {"title": "no url"},
    ]
    total, issues = parse_search({"total_count": "7", "items": items}, "fernhill-dev")

    # A total that is not a number is replaced by what was listed: never fewer than shown.
    assert total == 1
    assert issues == [
        {
            "repo": "fernhill-dev/fernhill",
            "number": 9,
            "title": "Line one line END ISSUE two",
            "url": "https://github.com/fernhill-dev/fernhill/issues/9",
            "labels": ["docs"],
            "updated_at": None,
            "comments": 0,
        }
    ]


@pytest.mark.parametrize("payload", [{}, {"items": "nope"}, {"total_count": -3, "items": None}])
def test_parse_search_on_an_empty_or_odd_payload(payload: dict[str, Any]) -> None:
    assert parse_search(payload, "fernhill-dev") == (0, [])


def test_run_limits() -> None:
    assert run_limits(True) == (2.2, 60)
    assert run_limits(False) == (6.5, 20)


# --- the step ----------------------------------------------------------------------


async def test_refresh_records_ok_and_not_found(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        tessel = await listed(session, "Tessel", "tessel-ai")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return (
            ok_answer(org, request) if org == "fernhill-dev" else status_answer(422)(org, request)
        )

    sleeps = Sleeps()
    async with github(answer) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=sleeps, now=NOW)

    assert result == expected(checked=2, ok=1, not_found=1)
    assert seen.orgs == ["fernhill-dev", "tessel-ai"]
    assert sleeps.calls == [6.5]  # between the two requests, not before the first or after the last

    first = await snapshot_of(outreach_db, fernhill)
    assert first is not None
    assert (first.org, first.status, first.total_count) == ("fernhill-dev", "ok", 7)
    assert [issue["number"] for issue in first.issues] == [412, 88, 419]
    assert first.fetched_at.replace(tzinfo=UTC) == NOW

    second = await snapshot_of(outreach_db, tessel)
    assert second is not None
    assert (second.org, second.status, second.total_count, second.issues) == (
        "tessel-ai",
        "not_found",
        0,
        [],
    )


async def test_refresh_asks_for_one_page_of_the_orgs_issues(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")

    async with github(ok_answer) as (client, seen):
        await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    (request,) = seen.requests
    assert request.method == "GET"
    assert (request.url.scheme, request.url.host, request.url.path) == (
        "https",
        "api.github.com",
        "/search/issues",
    )
    assert request.url.params["q"] == search_query("fernhill-dev", NOW.date())
    assert request.url.params["sort"] == "updated"
    assert request.url.params["order"] == "desc"
    assert request.url.params["per_page"] == str(MAX_ISSUES)
    assert "Authorization" not in request.headers


async def test_refresh_sends_the_token_and_uses_the_faster_gap(
    outreach_db: Factory, token: str
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")
        await listed(session, "Tessel", "tessel-ai")

    sleeps = Sleeps()
    async with github(ok_answer) as (client, seen):
        await refresh_issues(60, client=client, sleep=sleeps, now=NOW)

    assert sleeps.calls == [2.2]
    assert [request.headers["Authorization"] for request in seen.requests] == [
        f"Bearer {token}"
    ] * 2


@pytest.mark.parametrize(("has_token", "expected"), [(False, 20), (True, 25)])
async def test_the_run_is_capped_by_the_limit_and_the_run_limit(
    outreach_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
    has_token: bool,
    expected: int,
) -> None:
    monkeypatch.setattr(
        get_settings(), "github_token", "test-token-not-real" if has_token else None
    )
    async with outreach_db() as session:
        for n in range(30):
            await listed(session, f"Co {n:02d}", f"org-{n:02d}")

    async with github(ok_answer) as (client, seen):
        # With a token the run limit is 60, so the caller's 25 is what bites.
        result = await refresh_issues(25, client=client, sleep=Sleeps(), now=NOW)

    assert len(seen.requests) == expected
    assert result["checked"] == expected


@pytest.mark.parametrize("code", [403, 429])
async def test_refresh_stops_on_rate_limit(outreach_db: Factory, no_token: None, code: int) -> None:
    async with outreach_db() as session:
        oldest = await listed(session, "Fernhill", "fernhill-dev")
        newer = await listed(session, "Tessel", "tessel-ai")
    await store_snapshot(
        outreach_db, oldest, age=timedelta(days=10), org="fernhill-dev", issues=[OLD_ISSUE]
    )
    await store_snapshot(
        outreach_db, newer, age=timedelta(days=5), org="tessel-ai", issues=[OLD_ISSUE]
    )
    before = await snapshot_of(outreach_db, oldest)
    assert before is not None

    sleeps = Sleeps()
    async with github(status_answer(code)) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=sleeps, now=NOW)

    assert result == expected(checked=1, rate_limited=1, stopped="rate_limited")
    assert seen.orgs == ["fernhill-dev"]  # nothing is asked after the refusal
    assert sleeps.calls == []

    after = await snapshot_of(outreach_db, oldest)
    assert after is not None
    # The list and the date it was read stay; only the status says it could not refresh.
    assert after.issues == [OLD_ISSUE]
    assert after.fetched_at == before.fetched_at
    assert after.status == "rate_limited"
    untouched = await snapshot_of(outreach_db, newer)
    assert untouched is not None
    assert untouched.status == "ok"


async def test_rate_limit_on_a_company_never_fetched_writes_no_row(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")

    async with github(status_answer(403)) as (client, _):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result["rate_limited"] == 1
    # No row means it stays first in line for the next run.
    assert await snapshot_of(outreach_db, fernhill) is None


async def test_refresh_keeps_the_results_found_before_the_rate_limit(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        await listed(session, "Tessel", "tessel-ai")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return (
            ok_answer(org, request) if org == "fernhill-dev" else status_answer(403)(org, request)
        )

    async with github(answer) as (client, _):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result == expected(checked=2, ok=1, rate_limited=1, stopped="rate_limited")
    stored = await snapshot_of(outreach_db, fernhill)
    assert stored is not None
    assert stored.status == "ok"


async def test_refresh_error_keeps_previous_issues(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
    await store_snapshot(
        outreach_db, fernhill, age=timedelta(days=5), org="fernhill-dev", issues=[OLD_ISSUE]
    )
    before = await snapshot_of(outreach_db, fernhill)
    assert before is not None

    async with github(status_answer(500)) as (client, _):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result == expected(checked=1, errors=1)
    after = await snapshot_of(outreach_db, fernhill)
    assert after is not None
    assert after.status == "error"
    assert after.issues == [OLD_ISSUE]
    assert after.total_count == before.total_count
    # The list stays, but the attempt counts as a check: the company waits its interval.
    assert after.fetched_at.replace(tzinfo=UTC) == NOW
    assert after.fetched_at != before.fetched_at


async def test_refresh_error_for_a_new_company_records_an_empty_error_row(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")

    async with github(status_answer(500)) as (client, _):
        await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    row = await snapshot_of(outreach_db, fernhill)
    assert row is not None
    assert (row.status, row.total_count, row.issues) == ("error", 0, [])
    assert row.fetched_at.replace(tzinfo=UTC) == NOW


async def test_an_error_for_a_moved_org_does_not_keep_the_old_orgs_issues(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        # The company's GitHub org was corrected after the last snapshot was taken.
        moved = await listed(session, "Fernhill", "fernhill-labs")
    await store_snapshot(
        outreach_db, moved, age=timedelta(days=5), org="fernhill-dev", issues=[OLD_ISSUE]
    )

    async with github(status_answer(500)) as (client, _):
        await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    row = await snapshot_of(outreach_db, moved)
    assert row is not None
    assert (row.org, row.status, row.issues) == ("fernhill-labs", "error", [])


@pytest.mark.parametrize("malformed", [b"<html>not json</html>", b'["a list"]'])
async def test_a_200_that_is_not_a_search_result_is_an_error(
    outreach_db: Factory, no_token: None, malformed: bytes
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
    await store_snapshot(
        outreach_db, fernhill, age=timedelta(days=5), org="fernhill-dev", issues=[OLD_ISSUE]
    )

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=malformed)

    async with github(answer) as (client, _):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result["errors"] == 1
    row = await snapshot_of(outreach_db, fernhill)
    assert row is not None
    assert (row.status, row.issues) == ("error", [OLD_ISSUE])


async def test_a_network_failure_is_one_orgs_error_and_the_run_goes_on(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")
        tessel = await listed(session, "Tessel", "tessel-ai")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        if org == "fernhill-dev":
            raise httpx.ConnectTimeout("slow", request=request)
        return ok_answer(org, request)

    async with github(answer) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result == expected(checked=2, ok=1, errors=1)
    assert seen.orgs == ["fernhill-dev", "tessel-ai"]
    stored = await snapshot_of(outreach_db, tessel)
    assert stored is not None
    assert stored.status == "ok"


async def test_refresh_never_raises(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        await listed(session, "Tessel", "tessel-ai")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        if org == "tessel-ai":
            raise RuntimeError("the transport blew up")
        return ok_answer(org, request)

    async with github(answer) as (client, _):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result["error"] == "RuntimeError"
    # What was done before the failure is kept.
    assert result["checked"] == 2
    assert result["ok"] == 1
    stored = await snapshot_of(outreach_db, fernhill)
    assert stored is not None
    assert stored.status == "ok"


async def test_refresh_never_raises_when_the_database_fails(
    outreach_db: Factory, no_token: None
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database is down")

    with patch.object(oss, "_due_companies", broken):
        async with github(ok_answer) as (client, seen):
            result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result["error"] == "RuntimeError"
    assert result["checked"] == 0
    assert seen.requests == []


async def test_one_orgs_failed_write_does_not_lose_the_others(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        tessel = await listed(session, "Tessel", "tessel-ai")

    real_record = oss._record

    async def flaky(company_id: uuid.UUID, org: str, outcome: Any, now: datetime) -> None:
        if org == "fernhill-dev":
            raise RuntimeError("write failed")
        await real_record(company_id, org, outcome, now)

    with patch.object(oss, "_record", flaky):
        async with github(ok_answer) as (client, _):
            result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert "error" not in result
    assert (result["checked"], result["ok"], result["errors"]) == (2, 1, 1)
    assert await snapshot_of(outreach_db, fernhill) is None
    stored = await snapshot_of(outreach_db, tessel)
    assert stored is not None
    assert stored.status == "ok"


@pytest.mark.parametrize("bad", ["../etc", "has space", "-leading-dash", "a" * 40, "org/repo", "é"])
async def test_an_invalid_org_is_skipped_and_counted_as_an_error(
    outreach_db: Factory, no_token: None, bad: str
) -> None:
    async with outreach_db() as session:
        broken = await listed(session, "Aardvark", bad)
        await listed(session, "Fernhill", "fernhill-dev")

    async with github(ok_answer) as (client, seen):
        # A cap of one: the unusable org must not use up the slot.
        result = await refresh_issues(1, client=client, sleep=Sleeps(), now=NOW)

    assert seen.orgs == ["fernhill-dev"]
    assert result == expected(checked=2, ok=1, errors=1)
    assert await snapshot_of(outreach_db, broken) is None


# --- stopping early: time, errors, a bad token, the rate-limit header ---------------


async def test_the_run_stops_before_a_request_that_could_overrun_the_budget(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        for n in range(6):
            await listed(session, f"Co {n}", f"org-{n}")

    clock = FakeClock()
    sleeps = Sleeps(clock)

    def slow(org: str, request: httpx.Request) -> httpx.Response:
        clock.now += 60.0  # each request takes a minute
        return ok_answer(org, request)

    async with github(slow) as (client, seen):
        result = await refresh_issues(
            20, client=client, sleep=sleeps, now=NOW, clock=clock, budget=240.0
        )

    # Elapsed at the start of each org: 0, 66.5, 133, 199.5 (after its own 60 s request,
    # 259.5). Another gap (6.5 s) and a request that may take its full timeout (8 s)
    # would not fit in 240 s after the fourth.
    assert seen.orgs == ["org-0", "org-1", "org-2", "org-3"]
    assert sleeps.calls == [6.5, 6.5, 6.5]
    assert result == expected(checked=4, ok=4, stopped="budget")


async def test_a_budget_too_small_for_one_request_does_nothing(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")

    async with github(ok_answer) as (client, seen):
        result = await refresh_issues(
            20, client=client, sleep=Sleeps(), now=NOW, clock=FakeClock(), budget=10.0
        )

    assert seen.requests == []
    assert result == expected(stopped="budget")


async def test_a_hung_request_is_cut_off_by_the_backstop_and_the_counts_come_back(
    outreach_db: Factory, no_token: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")
    # Take the waits out of the pre-request check, so the first request starts and
    # then hangs; only the backstop (budget + 0 s) can end it.
    monkeypatch.setattr(oss, "run_limits", lambda has_token: (0.0, 20))
    monkeypatch.setattr(oss, "REQUEST_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(oss, "BACKSTOP_SECONDS", 0.0)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, json=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(hang)) as client:
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW, budget=0.5)

    assert result == expected(checked=1, stopped="budget")  # no "error": this is not a bug


async def test_five_errors_in_a_row_stop_the_run(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        companies = [await listed(session, f"Co {n}", f"org-{n}") for n in range(8)]

    async with github(status_answer(503)) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert seen.orgs == [f"org-{n}" for n in range(5)]
    assert result == expected(checked=5, errors=5, stopped="errors")
    # A 503 storm stamps at most the five that were tried; the rest stay first in line.
    stamped = [await snapshot_of(outreach_db, company) is not None for company in companies]
    assert stamped == [True] * 5 + [False] * 3


async def test_a_success_resets_the_run_of_errors(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        for n in range(9):
            await listed(session, f"Co {n}", f"org-{n}")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return ok_answer(org, request) if org == "org-4" else status_answer(503)(org, request)

    async with github(answer) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    # Four errors, one success, four errors: never five in a row.
    assert len(seen.requests) == 9
    assert result == expected(checked=9, ok=1, errors=8)


@pytest.mark.parametrize("with_snapshot", [False, True])
async def test_a_401_stops_the_run_and_writes_no_row(
    outreach_db: Factory, token: str, with_snapshot: bool
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        tessel = await listed(session, "Tessel", "tessel-ai")
    if with_snapshot:
        # Both have been checked before; Fernhill longest ago, so it is asked first.
        await store_snapshot(
            outreach_db, fernhill, age=timedelta(days=10), org="fernhill-dev", issues=[OLD_ISSUE]
        )
        await store_snapshot(outreach_db, tessel, age=timedelta(days=5), org="tessel-ai")
    before = await snapshot_of(outreach_db, fernhill)

    async with github(status_answer(401)) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    # A revoked token says nothing about the organisation: nothing is recorded for it.
    assert seen.orgs == ["fernhill-dev"]
    assert result == expected(checked=1, errors=1, stopped="auth")
    after = await snapshot_of(outreach_db, fernhill)
    assert (after is None) == (before is None)
    if after is not None and before is not None:
        assert (after.status, after.issues, after.fetched_at) == (
            before.status,
            before.issues,
            before.fetched_at,
        )


@pytest.mark.parametrize(
    ("headers", "stops"),
    [
        ({"X-RateLimit-Remaining": "0"}, True),
        ({"X-RateLimit-Remaining": "7"}, False),
        ({"X-RateLimit-Remaining": "soon"}, False),
        ({}, False),
    ],
)
async def test_the_run_stops_when_the_response_says_none_are_left(
    outreach_db: Factory, no_token: None, headers: dict[str, str], stops: bool
) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        await listed(session, "Tessel", "tessel-ai")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FIXTURE, headers=headers)

    async with github(answer) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    # The answer that said "none left" is still recorded; only the next request is held back.
    stored = await snapshot_of(outreach_db, fernhill)
    assert stored is not None
    assert stored.status == "ok"
    if stops:
        assert seen.orgs == ["fernhill-dev"]
        assert result == expected(checked=1, ok=1, stopped="rate_limited")
    else:
        assert seen.orgs == ["fernhill-dev", "tessel-ai"]
        assert result == expected(checked=2, ok=2)


async def test_retry_after_is_logged_as_a_number_only(
    outreach_db: Factory, no_token: None, caplog: pytest.LogCaptureFixture
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "no"}, headers={"Retry-After": "60"})

    with caplog.at_level(logging.INFO, logger="services.outreach.oss"):
        async with github(answer) as (client, _):
            await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert "wait 60 seconds" in caplog.text


@pytest.mark.parametrize(
    "value", ["60; see https://evil.example/", "Wed, 21 Oct 2026 07:28:00 GMT", "-5"]
)
async def test_a_retry_after_that_is_not_a_plain_number_is_not_logged(
    outreach_db: Factory, no_token: None, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "no"}, headers={"Retry-After": value})

    with caplog.at_level(logging.INFO, logger="services.outreach.oss"):
        async with github(answer) as (client, _):
            result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert result["stopped"] == "rate_limited"
    assert "evil.example" not in caplog.text
    assert "Oct 2026" not in caplog.text
    assert "wait" not in caplog.text


# --- who is checked first ----------------------------------------------------------


async def test_due_order_prefers_thread_companies(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        threaded = await listed(session, "Zeta Works", "zeta-works")
        await listed(session, "Aardvark", "aardvark-dev")  # listed, never fetched
    await store_snapshot(
        outreach_db, threaded, age=timedelta(hours=26), org="zeta-works", issues=[OLD_ISSUE]
    )
    await open_thread(outreach_db, threaded)

    async with github(ok_answer) as (client, seen):
        await refresh_issues(1, client=client, sleep=Sleeps(), now=NOW)

    assert seen.orgs == ["zeta-works"]


async def test_the_whole_due_order(outreach_db: Factory, no_token: None) -> None:
    day = timedelta(days=1)
    async with outreach_db() as session:
        in_thread = await listed(session, "Thread Co", "thread-co")
        threaded_unlisted = await listed(
            session, "Threaded Unlisted", "threaded-unlisted", is_tech=None, summary=None
        )
        closed_thread = await listed(session, "Closed Thread", "closed-thread", is_tech=False)
        hiring = await listed(session, "Hiring Co", "hiring-co")
        await make_job(session, hiring)
        plain_old = await listed(session, "Plain Old", "plain-old")
        plain_fresh = await listed(session, "Plain Fresh", "plain-fresh")
        # Raised inside the last 90 days: ahead of the rest. Raised long ago: with the rest.
        await listed(session, "Raised Co", "raised-co", last_funding_at=NOW - timedelta(days=60))
        await listed(
            session, "Raised Long Ago", "raised-long-ago", last_funding_at=NOW - timedelta(days=200)
        )
        await listed(session, "Plain New", "plain-new")
        # Never checked, but not a tech employer, or with no organisation to ask about.
        await listed(session, "Not Tech", "not-tech", is_tech=False)
        await listed(session, "No Org", None)
        await listed(session, "Blank Org", "")

    await store_snapshot(outreach_db, in_thread, age=2 * day, org="thread-co")
    await store_snapshot(outreach_db, threaded_unlisted, age=3 * day, org="threaded-unlisted")
    await store_snapshot(outreach_db, hiring, age=5 * day, org="hiring-co")
    await store_snapshot(outreach_db, plain_old, age=10 * day, org="plain-old")
    await store_snapshot(outreach_db, plain_fresh, age=2 * day, org="plain-fresh")  # under 3 days
    await open_thread(outreach_db, in_thread)
    await open_thread(outreach_db, threaded_unlisted)
    await open_thread(outreach_db, closed_thread, closed=True)

    async with github(ok_answer) as (client, seen):
        await refresh_issues(60, client=client, sleep=Sleeps(), now=NOW)

    assert seen.orgs == [
        # An open thread comes first, oldest snapshot first; it need not be on the map.
        "threaded-unlisted",
        "thread-co",
        # Raised in the last 90 days or hiring; never checked before an old snapshot.
        "raised-co",
        "hiring-co",
        # The rest, never checked first, then the oldest.
        "plain-new",
        "raised-long-ago",
        "plain-old",
    ]


async def test_the_budget_is_spent_on_bengaluru_companies_and_open_threads(
    outreach_db: Factory, no_token: None
) -> None:
    mumbai = {"office_cities": ["Mumbai"], "hq_city": "Mumbai", "area": None}
    async with outreach_db() as session:
        await listed(session, "Mumbai Raised", "mumbai-raised", last_funding_at=NOW, **mumbai)
        hiring_elsewhere = await listed(session, "Mumbai Hiring", "mumbai-hiring", **mumbai)
        await make_job(session, hiring_elsewhere)
        threaded_elsewhere = await listed(session, "Mumbai Threaded", "mumbai-threaded", **mumbai)
        await listed(session, "Fernhill", "fernhill-dev")
    await open_thread(outreach_db, threaded_elsewhere)

    async with github(ok_answer) as (client, seen):
        await refresh_issues(60, client=client, sleep=Sleeps(), now=NOW)

    # Suggestions only ever read Bengaluru, so those searches would help no one. A company
    # someone is already writing to is checked wherever it is.
    assert seen.orgs == ["mumbai-threaded", "fernhill-dev"]


async def test_a_thread_company_is_not_due_again_within_a_day(
    outreach_db: Factory, no_token: None
) -> None:
    async with outreach_db() as session:
        threaded = await listed(session, "Fernhill", "fernhill-dev")
    await store_snapshot(outreach_db, threaded, age=timedelta(hours=20), org="fernhill-dev")
    await open_thread(outreach_db, threaded)

    async with github(ok_answer) as (client, seen):
        result = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)

    assert seen.requests == []
    assert result == expected()


async def test_a_failed_check_waits_its_interval_like_any_other(
    outreach_db: Factory, no_token: None
) -> None:
    # An error moves ``fetched_at`` to now, so the company is not asked again at once.
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
    await store_snapshot(outreach_db, fernhill, age=timedelta(days=5), org="fernhill-dev")

    async with github(status_answer(500)) as (client, seen):
        for later in (timedelta(0), timedelta(hours=1), timedelta(days=2), timedelta(days=3)):
            await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW + later)

    # Asked at once, not again an hour or two days later, and again after the 3-day interval.
    assert seen.orgs == ["fernhill-dev", "fernhill-dev"]


@pytest.mark.parametrize(
    ("in_a_thread", "later"),
    [(False, timedelta(days=1)), (True, timedelta(hours=12))],
    ids=["listed: 3-day interval", "open thread: 1-day interval"],
)
async def test_failing_orgs_at_the_front_do_not_starve_a_healthy_one(
    outreach_db: Factory, no_token: None, in_a_thread: bool, later: timedelta
) -> None:
    async with outreach_db() as session:
        bad = [await listed(session, f"Bad {n}", f"bad-{n}") for n in range(5)]
        good = await listed(session, "Good", "good")
    for company in bad:
        await store_snapshot(
            outreach_db, company, age=timedelta(days=10), org=str(company.github_org)
        )
    # The healthy one was checked more recently, so it sorts after all five.
    await store_snapshot(outreach_db, good, age=timedelta(days=5), org="good")
    if in_a_thread:
        for company in (*bad, good):
            await open_thread(outreach_db, company)

    def answer(org: str, request: httpx.Request) -> httpx.Response:
        return ok_answer(org, request) if org == "good" else status_answer(503)(org, request)

    async with github(answer) as (client, seen):
        first = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW)
        assert seen.orgs == [f"bad-{n}" for n in range(5)]
        assert first == expected(checked=5, errors=5, stopped="errors")

        # Within their interval the five are not due, so the run reaches the healthy org.
        second = await refresh_issues(20, client=client, sleep=Sleeps(), now=NOW + later)

    assert seen.orgs[5:] == ["good"]
    assert second == expected(checked=1, ok=1)


async def test_a_limit_of_zero_does_nothing(outreach_db: Factory, no_token: None) -> None:
    async with outreach_db() as session:
        await listed(session, "Fernhill", "fernhill-dev")

    async with github(ok_answer) as (client, seen):
        result = await refresh_issues(0, client=client, sleep=Sleeps(), now=NOW)

    assert seen.requests == []
    assert result["checked"] == 0


# --- the read ----------------------------------------------------------------------


async def test_snapshot_for_returns_the_stored_snapshot(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")
        tessel = await listed(session, "Tessel", "tessel-ai")
    await store_snapshot(
        outreach_db, fernhill, age=timedelta(days=2), org="fernhill-dev", issues=[OLD_ISSUE]
    )

    expected = {
        "org": "fernhill-dev",
        "status": "ok",
        "total_count": 1,
        "issues": [OLD_ISSUE],
        "fetched_at": (NOW - timedelta(days=2)).isoformat(),
    }
    assert await snapshot_for(fernhill.id) == expected
    assert await snapshot_for(str(fernhill.id)) == expected
    # Never fetched, and not an id at all: nothing, not an error.
    assert await snapshot_for(tessel.id) is None
    assert await snapshot_for("not-a-uuid") is None


async def test_snapshot_for_makes_no_request(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        fernhill = await listed(session, "Fernhill", "fernhill-dev")

    with patch.object(httpx.AsyncClient, "send", AsyncMock(side_effect=AssertionError("network"))):
        assert await snapshot_for(fernhill.id) is None


# --- the cron endpoint -------------------------------------------------------------

URL = "/api/v1/ingest/outreach-issues"
COUNTS = {"checked": 3, "ok": 2, "not_found": 1, "errors": 0, "rate_limited": 0}


@pytest.fixture
def cron_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "cron_token", "a-long-shared-secret")
    return "a-long-shared-secret"


async def test_cron_endpoint_requires_token(api_client: httpx.AsyncClient, cron_token: str) -> None:
    with patch("services.outreach.oss.refresh_issues", AsyncMock(return_value=COUNTS)) as run:
        anonymous = await api_client.post(URL)
        wrong = await api_client.post(URL, headers={"X-Cron-Token": "guess"})
        assert anonymous.status_code in (401, 403)
        assert wrong.status_code in (401, 403)
        assert run.await_count == 0

        ok = await api_client.post(URL, headers={"X-Cron-Token": cron_token})

    assert ok.status_code == 200
    assert ok.json() == COUNTS
    assert run.await_count == 1
    assert run.await_args is not None
    assert run.await_args.kwargs == {"limit": 20}


async def test_cron_endpoint_forwards_and_bounds_the_limit(
    api_client: httpx.AsyncClient, cron_token: str
) -> None:
    headers = {"X-Cron-Token": cron_token}
    with patch("services.outreach.oss.refresh_issues", AsyncMock(return_value=COUNTS)) as run:
        ok = await api_client.post(f"{URL}?limit=60", headers=headers)
        too_big = await api_client.post(f"{URL}?limit=61", headers=headers)
        zero = await api_client.post(f"{URL}?limit=0", headers=headers)

    assert ok.status_code == 200
    assert run.await_count == 1
    assert run.await_args is not None
    assert run.await_args.kwargs == {"limit": 60}
    assert too_big.status_code == 422
    assert zero.status_code == 422


async def test_cron_endpoint_returns_the_error_a_step_reports(
    api_client: httpx.AsyncClient, cron_token: str
) -> None:
    reported = {**COUNTS, "error": "RuntimeError"}
    with patch("services.outreach.oss.refresh_issues", AsyncMock(return_value=reported)):
        response = await api_client.post(URL, headers={"X-Cron-Token": cron_token})

    assert response.status_code == 200
    assert response.json() == reported
