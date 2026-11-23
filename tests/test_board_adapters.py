"""
tests/test_board_adapters.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The board readers (``ingestion/boards``) for Greenhouse, Ashby and Lever, replayed
from recorded responses through ``httpx.MockTransport``. Nothing here touches the
network.

What each test pins is something the sweep depends on:

* a board is read whole, and every role carries what persistence needs
* a 200 that is not the ATS's JSON (a captive portal, an error page) is an
  ``error`` read, never "this board has no India roles", which would demote a live
  board
* a 3xx, a body over the cap, a 429 and a 404 each map to their own outcome
* the live-search methods on ``ATSScraper`` keep filtering as they did
"""

from __future__ import annotations

import gzip
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from domain.entities import Job
from domain.job_boards import AtsName, BoardRef, is_india_role
from domain.liveness import parse_source_timestamp
from ingestion.boards import READERS
from ingestion.boards.base import (
    MAX_BODY_BYTES,
    USER_AGENT,
    BoardRead,
    clean_url,
    describe,
    get_json,
    html_to_text,
    one_line,
)
from ingestion.boards.greenhouse import read_board_name
from ingestion.scrapers.ats_scraper import ATSScraper
from ingestion.validation import validate_job_url

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "boards"

#: The ATSes this task covers and the board recorded for each.
RECORDED: list[tuple[AtsName, str]] = [
    ("greenhouse", "sigmoid"),
    ("ashby", "sarvam"),
    ("lever", "meesho"),
]
ATSES: list[AtsName] = [ats for ats, _ in RECORDED]

Handler = Callable[[httpx.Request], httpx.Response]


def load_fixture(ats: str) -> dict[str, Any]:
    with gzip.open(FIXTURE_DIR / f"{ats}.json.gz", "rt", encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def fixture_handler(ats: str, seen: list[httpx.Request] | None = None) -> Handler:
    """Answer each request from the recording, by full URL; anything unrecorded is a 404."""
    requests: dict[str, Any] = load_fixture(ats)["requests"]

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        entry = requests.get(str(request.url))
        if entry is None:
            return httpx.Response(404, text="not recorded")
        return httpx.Response(
            int(entry["status"]), headers=entry["headers"], content=entry["body"].encode("utf-8")
        )

    return handler


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def answering(status: int, **kwargs: Any) -> Handler:
    return lambda _request: httpx.Response(status, **kwargs)


def ref_for(ats: AtsName, board: str | None = None) -> BoardRef:
    return BoardRef(ats, board or dict(RECORDED)[ats])


async def read(ats: AtsName, handler: Handler, board: str | None = None) -> BoardRead:
    async with client_for(handler) as client:
        return await READERS[ats](client, ref_for(ats, board))


# ── the recordings ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("ats", "board"), RECORDED)
async def test_recorded_board_reads_whole(ats: AtsName, board: str) -> None:
    seen: list[httpx.Request] = []
    result = await read(ats, fixture_handler(ats, seen), board)

    assert result.outcome == "ok"
    assert result.complete is True
    assert result.total == len(result.roles) > 0
    assert len(seen) == 1, "a board is one request, never one per posting"

    for role in result.roles:
        assert isinstance(role, Job)
        assert role.title
        assert role.source == f"{ats}:{board}"
        assert role.external_id
        assert role.source_url
        valid, reason = validate_job_url(role.source_url)
        assert valid, f"{role.source_url}: {reason}"
        assert (role.extra_metadata or {}).get("company_name")

    assert any(is_india_role(role.location_raw) for role in result.roles)
    assert (
        any(not is_india_role(role.location_raw) for role in result.roles) or ats != "greenhouse"
    ), "the reader returns every role; India filtering is the caller's"


@pytest.mark.parametrize(("ats", "board"), RECORDED)
async def test_every_request_is_identified_and_not_redirected(ats: AtsName, board: str) -> None:
    seen: list[httpx.Request] = []
    await read(ats, fixture_handler(ats, seen), board)
    assert seen
    for request in seen:
        assert request.headers["user-agent"] == USER_AGENT
        assert request.method == "GET"


@pytest.mark.parametrize(("ats", "board"), RECORDED)
async def test_roles_carry_the_employers_dates_and_none_of_ours(ats: AtsName, board: str) -> None:
    result = await read(ats, fixture_handler(ats), board)
    now = datetime.now(UTC)
    for role in result.roles:
        assert role.posted_at is not None
        assert role.posted_at == role.source_posted_at
        assert role.posted_at.tzinfo is not None
        assert (now - role.posted_at).total_seconds() > 60


async def test_greenhouse_dates_come_from_first_published_and_updated_at() -> None:
    result = await read("greenhouse", fixture_handler("greenhouse"))
    stated = {
        str(item["id"]): item
        for item in json.loads(next(iter(load_fixture("greenhouse")["requests"].values()))["body"])[
            "jobs"
        ]
    }
    for role in result.roles:
        item = stated[role.external_id or ""]
        assert role.posted_at == parse_source_timestamp(item["first_published"])
        assert role.source_updated_at == parse_source_timestamp(item["updated_at"])
        assert role.source_updated_at >= role.source_posted_at  # type: ignore[operator]


async def test_greenhouse_content_is_unescaped() -> None:
    result = await read("greenhouse", fixture_handler("greenhouse"))
    described = [role.description_clean for role in result.roles if role.description_clean]
    assert described, "content=true carries a description for every role"
    for text in described:
        assert "&lt;" not in text
        assert "&gt;" not in text
        assert "&amp;" not in text
        assert "<p>" not in text
        assert "</div>" not in text
        assert "<h2>" not in text
    assert any("\n" in text for text in described), "paragraph breaks survive the clean-up"


async def test_descriptions_are_bounded() -> None:
    from services.job_persistence import MAX_DESCRIPTION_CHARS

    for ats in ATSES:
        result = await read(ats, fixture_handler(ats))
        assert all(
            len(role.description_clean or "") <= MAX_DESCRIPTION_CHARS for role in result.roles
        )


async def test_ashby_and_lever_descriptions_are_plain_text() -> None:
    for ats in ("ashby", "lever"):
        result = await read(ats, fixture_handler(ats))
        described = [role.description_clean for role in result.roles if role.description_clean]
        assert described
        for text in described:
            assert "<p" not in text
            assert "<li" not in text
            assert "&nbsp;" not in text


async def test_lever_description_includes_the_requirement_lists() -> None:
    result = await read("lever", fixture_handler("lever"))
    first = result.roles[0]
    assert "What you will need" in (first.description_clean or "")


async def test_the_recorded_greenhouse_board_names_no_people() -> None:
    """A board's custom fields can hold employees' names; the committed recording keeps none."""
    body = json.loads(next(iter(load_fixture("greenhouse")["requests"].values()))["body"])
    for job in body["jobs"]:
        for entry in job["metadata"]:
            if entry["name"] in {"Hiring Manager", "Business Head"}:
                assert entry["value"] in (None, "[redacted]")


# ── what a board can answer instead of its JSON ──────────────────────────────

HTML_PAGE = "<!DOCTYPE html><html><body><h1>Too many requests</h1></body></html>"


@pytest.mark.parametrize("ats", ATSES)
async def test_html_200_is_error(ats: AtsName) -> None:
    result = await read(ats, answering(200, text=HTML_PAGE, headers={"content-type": "text/html"}))
    assert result.outcome == "error"
    assert result.roles == []
    assert result.complete is False


@pytest.mark.parametrize("ats", ATSES)
async def test_429_is_rate_limited(ats: AtsName) -> None:
    result = await read(ats, answering(429, text=HTML_PAGE))
    assert result.outcome == "rate_limited"
    assert result.roles == []
    assert result.complete is False


@pytest.mark.parametrize("ats", ATSES)
@pytest.mark.parametrize("status", [404, 410])
async def test_404_is_not_found(ats: AtsName, status: int) -> None:
    result = await read(ats, answering(status, json={"error": "not found"}))
    assert result.outcome == "not_found"
    assert result.roles == []
    assert result.complete is False


@pytest.mark.parametrize("ats", ATSES)
@pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
async def test_other_statuses_are_error(ats: AtsName, status: int) -> None:
    result = await read(ats, answering(status, text="nope"))
    assert result.outcome == "error"
    assert result.roles == []


@pytest.mark.parametrize("ats", ATSES)
async def test_redirect_is_error(ats: AtsName) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(301, headers={"location": "https://example.invalid/elsewhere"})

    result = await read(ats, handler)
    assert result.outcome == "error"
    assert result.roles == []
    assert len(seen) == 1, "a redirect is reported, not followed"


@pytest.mark.parametrize("ats", ATSES)
async def test_body_over_cap_is_error(ats: AtsName) -> None:
    filler = "x" * 1024
    body = json.dumps([{"filler": filler} for _ in range(MAX_BODY_BYTES // 1024 + 64)])
    assert len(body) > MAX_BODY_BYTES
    result = await read(ats, answering(200, content=body.encode("utf-8")))
    assert result.outcome == "error"
    assert result.roles == []
    assert result.complete is False


async def test_body_is_not_read_past_the_cap() -> None:
    """The cap stops the download; it does not wait for a whole body and then refuse it."""
    chunk = b"[" + b"1," * 32_000
    sent = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal sent
        while True:
            sent += len(chunk)
            yield chunk

    async with client_for(answering(200, content=endless())) as client:
        outcome, data = await get_json(client, "https://example.test/board")

    assert outcome == "error"
    assert data is None
    assert sent <= MAX_BODY_BYTES + 2 * len(chunk)


async def test_a_declared_length_over_the_cap_is_refused_without_reading() -> None:
    headers = {"content-length": str(MAX_BODY_BYTES + 1)}

    async def never() -> AsyncIterator[bytes]:
        raise AssertionError("the body must not be read")
        yield b""  # pragma: no cover

    async with client_for(answering(200, headers=headers, content=never())) as client:
        outcome, _ = await get_json(client, "https://example.test/board")
    assert outcome == "error"


@pytest.mark.parametrize("ats", ATSES)
async def test_timeout_and_connection_errors_are_error(ats: AtsName) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, refused):
        result = await read(ats, handler)
        assert result.outcome == "error"
        assert result.roles == []
        assert result.complete is False


@pytest.mark.parametrize("ats", ATSES)
@pytest.mark.parametrize(
    "body",
    [
        '{"error": "something"}',
        "null",
        "42",
        '"a string"',
        '{"jobs": "none"}',
        "not json at all",
        "",
    ],
)
async def test_json_that_is_not_a_board_is_error(ats: AtsName, body: str) -> None:
    result = await read(ats, answering(200, text=body))
    assert result.outcome == "error"
    assert result.roles == []
    assert result.complete is False


async def test_an_empty_board_is_ok_not_error() -> None:
    for ats, body in (("greenhouse", '{"jobs": []}'), ("ashby", '{"jobs": []}'), ("lever", "[]")):
        result = await read(ats, answering(200, text=body))  # type: ignore[arg-type]
        assert result.outcome == "ok"
        assert result.roles == []
        assert result.total == 0
        assert result.complete is True


@pytest.mark.parametrize("ats", ATSES)
async def test_a_posting_that_cannot_be_read_makes_the_read_incomplete(ats: AtsName) -> None:
    """Absent from ``roles`` must not look like closed: a skipped posting is not a complete read."""
    good = {
        "greenhouse": {
            "id": 1,
            "title": "Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/b/jobs/1",
            "location": {"name": "Bengaluru, India"},
        },
        "ashby": {
            "id": "a1",
            "title": "Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/b/a1",
            "location": "Bengaluru",
        },
        "lever": {
            "id": "l1",
            "text": "Engineer",
            "hostedUrl": "https://jobs.lever.co/b/l1",
            "categories": {"location": "Bengaluru"},
        },
    }[ats]
    bad: dict[str, Any] = {"id": "broken"}  # no title, no URL
    items: Any = [good, bad] if ats == "lever" else {"jobs": [good, bad]}
    result = await read(ats, answering(200, json=items))
    assert result.outcome == "ok"
    assert result.total == 2
    assert len(result.roles) == 1
    assert result.complete is False


# ── get_json ─────────────────────────────────────────────────────────────────


async def test_get_json_sends_params_and_the_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async with client_for(handler) as client:
        outcome, data = await get_json(client, "https://example.test/board", params={"a": "1"})

    assert (outcome, data) == ("ok", {"ok": True})
    assert str(seen[0].url) == "https://example.test/board?a=1"
    assert seen[0].headers["user-agent"] == USER_AGENT


async def test_get_json_can_post_a_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[1, 2])

    async with client_for(handler) as client:
        outcome, data = await get_json(
            client, "https://example.test/search", method="POST", json_body={"q": "x"}
        )

    assert (outcome, data) == ("ok", [1, 2])
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"q": "x"}


async def test_get_json_does_not_follow_redirects_even_when_the_client_would() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://example.test/next"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        outcome, _ = await get_json(client, "https://example.test/board")

    assert outcome == "error"
    assert len(seen) == 1


# ── what is made of a posting's text ─────────────────────────────────────────


def test_html_to_text_keeps_blocks_apart_and_drops_scripts() -> None:
    html = (
        "<div><h2>Role</h2><p>Build <b>things</b> well.</p><ul><li>Python</li><li>SQL</li></ul>"
        "<script>alert(1)</script><style>p{}</style>Line one<br>Line two&nbsp;end</div>"
    )
    assert html_to_text(html) == "Role\nBuild things well.\nPython\nSQL\nLine one\nLine two end"


def test_html_to_text_of_nothing_is_nothing() -> None:
    assert html_to_text("") == ""
    assert html_to_text("   \n ") == ""


def test_describe_cuts_to_the_persistence_limit_and_is_none_when_empty() -> None:
    from services.job_persistence import MAX_DESCRIPTION_CHARS

    assert describe("", "") is None
    long = describe("a" * (MAX_DESCRIPTION_CHARS + 500))
    assert long is not None
    assert len(long) == MAX_DESCRIPTION_CHARS


def test_one_line_drops_control_characters_and_collapses_whitespace() -> None:
    assert one_line("  Senior\x00 \n Engineer\t ") == "Senior Engineer"
    assert one_line(None) == ""
    assert one_line(7) == ""


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "//evil.example/x",
        "ftp://example.test/x",
        "/relative",
        "",
        None,
        5,
        "https://example.test/a b",
        "https://example.test/\x00",
    ],
)
def test_clean_url_refuses_anything_but_an_absolute_http_url(value: object) -> None:
    assert clean_url(value) is None


def test_clean_url_keeps_a_posting_url() -> None:
    assert clean_url(" https://jobs.lever.co/acme/1 ") == "https://jobs.lever.co/acme/1"


# ── Greenhouse's board name ──────────────────────────────────────────────────


async def test_read_board_name_reads_the_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "Sigmoid", "content": "<p>About</p>"})

    async with client_for(handler) as client:
        name = await read_board_name(client, BoardRef("greenhouse", "sigmoid"))

    assert name == "Sigmoid"
    assert str(seen[0].url) == "https://boards-api.greenhouse.io/v1/boards/sigmoid"


@pytest.mark.parametrize(
    "handler",
    [
        answering(404, json={"error": "no"}),
        answering(429, text="slow down"),
        answering(200, text=HTML_PAGE),
        answering(200, json={"content": "no name here"}),
        answering(200, json={"name": ""}),
        answering(200, json={"name": 7}),
        answering(200, json=["not", "an", "object"]),
    ],
)
async def test_read_board_name_is_none_unless_the_board_states_one(handler: Handler) -> None:
    async with client_for(handler) as client:
        assert await read_board_name(client, BoardRef("greenhouse", "x")) is None


# ── how each ATS's fields land on a Job ──────────────────────────────────────


async def test_ashby_location_falls_back_to_remote_and_carries_the_remote_flag() -> None:
    payload = {
        "jobs": [
            {
                "id": "a1",
                "title": "Remote role",
                "jobUrl": "https://jobs.ashbyhq.com/b/a1",
                "location": "",
                "isRemote": True,
                "publishedAt": "2026-05-01T00:00:00.000+00:00",
            },
            {
                "id": "a2",
                "title": "Office role",
                "jobUrl": "https://jobs.ashbyhq.com/b/a2",
                "location": "Bengaluru",
                "isRemote": False,
            },
        ]
    }
    result = await read("ashby", answering(200, json=payload), "b")
    remote, office = result.roles
    assert remote.location_raw == "Remote"
    assert remote.is_remote is True
    assert office.location_raw == "Bengaluru"
    assert office.is_remote is False
    assert office.posted_at is None, "a posting with no stated date stays dateless"
    assert office.source_posted_at is None


async def test_lever_location_falls_back_to_the_first_of_all_locations() -> None:
    payload = [
        {
            "id": "1",
            "text": "A",
            "hostedUrl": "https://jobs.lever.co/b/1",
            "categories": {"allLocations": ["Pune, Maharashtra", "Mumbai"]},
            "workplaceType": "remote",
        },
        {
            "id": "2",
            "text": "B",
            "hostedUrl": "https://jobs.lever.co/b/2",
            "categories": {"allLocations": []},
        },
        {"id": "3", "text": "C", "hostedUrl": "https://jobs.lever.co/b/3", "categories": None},
    ]
    result = await read("lever", answering(200, json=payload), "b")
    first, second, third = result.roles
    assert first.location_raw == "Pune, Maharashtra"
    assert first.is_remote is True
    assert second.location_raw is None
    assert third.location_raw is None
    assert first.posted_at is None


async def test_greenhouse_falls_back_to_a_board_url_when_the_posting_states_none() -> None:
    payload = {"jobs": [{"id": 77, "title": "Engineer", "location": {"name": "Pune, India"}}]}
    result = await read("greenhouse", answering(200, json=payload), "acme")
    (role,) = result.roles
    assert role.source_url == "https://boards.greenhouse.io/acme/jobs/77"
    assert role.external_id == "77"
    assert (role.extra_metadata or {}).get("company_name") == "Acme"


async def test_a_board_name_with_a_slash_is_not_a_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    await read("greenhouse", handler, "../admin")
    path = seen[0].url.raw_path.split(b"?")[0]
    assert path == b"/v1/boards/..%2Fadmin/jobs", "the board stays one path segment"


# ── live search still filters as it did ──────────────────────────────────────


def _board_payload(ats: AtsName) -> Any:
    if ats == "greenhouse":
        return {
            "jobs": [
                {
                    "id": 1,
                    "title": "Senior Python Developer",
                    "location": {"name": "Bengaluru, India"},
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/1",
                    "first_published": "2026-01-02T03:04:05Z",
                    "updated_at": "2026-02-03T04:05:06Z",
                },
                {
                    "id": 2,
                    "title": "Marketing Manager",
                    "location": {"name": "New York, USA"},
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/2",
                },
                {
                    "id": 3,
                    "title": "Python Remote Engineer",
                    "location": {"name": "Remote - Worldwide"},
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/3",
                },
            ]
        }
    if ats == "ashby":
        return {
            "jobs": [
                {
                    "id": "a1",
                    "title": "Python Developer",
                    "location": "Bengaluru",
                    "jobUrl": "https://jobs.ashbyhq.com/test/a1",
                    "isRemote": False,
                },
                {
                    "id": "a2",
                    "title": "Python Developer",
                    "location": "Berlin",
                    "jobUrl": "https://jobs.ashbyhq.com/test/a2",
                    "isRemote": False,
                },
                {
                    "id": "a3",
                    "title": "Python Developer",
                    "location": "Berlin",
                    "jobUrl": "https://jobs.ashbyhq.com/test/a3",
                    "isRemote": True,
                },
            ]
        }
    return [
        {
            "id": "l1",
            "text": "Python Developer",
            "hostedUrl": "https://jobs.lever.co/test/l1",
            "categories": {"location": "Pune"},
            "workplaceType": "onsite",
            "descriptionPlain": "Write Python.",
        },
        {
            "id": "l2",
            "text": "Python Developer",
            "hostedUrl": "https://jobs.lever.co/test/l2",
            "categories": {"location": "Berlin"},
            "workplaceType": "onsite",
        },
        {
            "id": "l3",
            "text": "Python Developer",
            "hostedUrl": "https://jobs.lever.co/test/l3",
            "categories": {"location": "Berlin"},
            "workplaceType": "remote",
        },
    ]


async def _fetch(ats: AtsName, query: str, location: str | None, remote: bool | None) -> list[Job]:
    fetch = {
        "greenhouse": ATSScraper.fetch_greenhouse_company,
        "ashby": ATSScraper.fetch_ashby_company,
        "lever": ATSScraper.fetch_lever_company,
    }[ats]
    async with client_for(answering(200, json=_board_payload(ats))) as client:
        return await fetch("test", query, location, remote, client=client)


async def test_live_greenhouse_keeps_matching_location_and_query() -> None:
    jobs = await _fetch("greenhouse", "python", "Bengaluru", None)
    assert [job.external_id for job in jobs] == ["1"]
    (job,) = jobs
    assert job.source == "greenhouse:test"
    assert job.city == "Bengaluru"
    assert job.country == "India"
    assert job.tags == ["ats", "greenhouse", "test"]
    assert job.posted_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert job.extra_metadata == {"company_name": "Test", "ats": "greenhouse"}


async def test_live_greenhouse_without_a_location_keeps_india_and_worldwide_remote() -> None:
    jobs = await _fetch("greenhouse", "python", None, None)
    assert [job.external_id for job in jobs] == ["1", "3"]
    assert [job.is_remote for job in jobs] == [False, True]


async def test_live_ashby_keeps_a_flagged_remote_role_whatever_its_location() -> None:
    jobs = await _fetch("ashby", "python", "Bengaluru", None)
    assert [job.external_id for job in jobs] == ["a1", "a3"]
    assert jobs[1].is_remote is True
    assert jobs[0].is_remote is False


async def test_live_lever_keeps_a_remote_workplace_whatever_its_location() -> None:
    jobs = await _fetch("lever", "python", "Pune", None)
    assert [job.external_id for job in jobs] == ["l1", "l3"]
    assert jobs[0].description_clean == "Write Python."
    assert jobs[1].is_remote is True


@pytest.mark.parametrize("ats", ATSES)
async def test_live_search_returns_nothing_when_the_board_cannot_be_read(ats: AtsName) -> None:
    fetch = {
        "greenhouse": ATSScraper.fetch_greenhouse_company,
        "ashby": ATSScraper.fetch_ashby_company,
        "lever": ATSScraper.fetch_lever_company,
    }[ats]
    for handler in (answering(404), answering(429), answering(200, text=HTML_PAGE)):
        async with client_for(handler) as client:
            assert await fetch("test", "python", None, None, client=client) == []
