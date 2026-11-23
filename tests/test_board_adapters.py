"""
tests/test_board_adapters.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The board readers (``ingestion/boards``) for Greenhouse, Ashby, Lever, Workable,
Recruitee and Keka, replayed from recorded responses through ``httpx.MockTransport``.
Nothing here touches the network.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import pytest

from domain.entities import Job
from domain.job_boards import AtsName, BoardRef, is_india_role
from domain.liveness import parse_source_timestamp
from ingestion.boards import READERS
from ingestion.boards.base import (
    MAX_BODY_BYTES,
    OVER_CAP,
    USER_AGENT,
    BoardRead,
    clean_url,
    describe,
    get_json,
    html_to_text,
    looks_remote,
    one_line,
)
from ingestion.boards.greenhouse import read_board_name
from ingestion.boards.lever import MAX_PAGES, PAGE_SIZE
from ingestion.scrapers.ats_scraper import ATSScraper
from ingestion.validation import validate_job_url

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "boards"

#: The ATSes covered here and the board recorded for each.
RECORDED: list[tuple[AtsName, str]] = [
    ("greenhouse", "sigmoid"),
    ("ashby", "sarvam"),
    ("lever", "meesho"),
    ("workable", "apna"),
    ("recruitee", "lenskartcareers"),
    ("keka", "spyneai"),
]
ATSES: list[AtsName] = [ats for ats, _ in RECORDED]

#: The three ATSes live search also reads on demand (the rest are only swept).
LIVE_ATSES: list[AtsName] = ["greenhouse", "ashby", "lever"]

#: Requests one read of the recorded board makes: Keka asks for the company's name too.
REQUESTS_PER_READ: dict[str, int] = {"keka": 2}

Handler = Callable[[httpx.Request], httpx.Response]


def load_fixture(ats: str) -> dict[str, Any]:
    with gzip.open(FIXTURE_DIR / f"{ats}.json.gz", "rt", encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def lookup_key(url: str | httpx.URL) -> str:
    """
    A request's key with ``skip`` and ``limit`` taken out, and its parameters in order.

    The Lever recording predates paging and holds a whole board (under one page), which
    is what any first page answers. Stored keys and requests are both reduced this way,
    so a re-record, whose key carries ``skip=0&limit=50``, replays the same.
    """
    parts = urlsplit(str(url))
    pairs = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name not in ("skip", "limit")
    )
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def recording_handler(requests: dict[str, Any], seen: list[httpx.Request] | None = None) -> Handler:
    """Answer each request from recorded ``{url: {status, headers, body}}``; anything else is a 404."""
    recorded = {lookup_key(key): entry for key, entry in requests.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        entry = recorded.get(lookup_key(request.url))
        if entry is None:
            return httpx.Response(404, text="not recorded")
        return httpx.Response(
            int(entry["status"]), headers=entry["headers"], content=entry["body"].encode("utf-8")
        )

    return handler


def fixture_handler(ats: str, seen: list[httpx.Request] | None = None) -> Handler:
    """Answer each request from the committed recording of ``ats``."""
    return recording_handler(load_fixture(ats)["requests"], seen)


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
    assert len(seen) == REQUESTS_PER_READ.get(ats, 1), "never one request per posting"

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


@pytest.mark.parametrize("ats", ATSES)
def test_the_capture_script_records_what_the_committed_fixture_holds(ats: str) -> None:
    """A re-record keys the same request the same way, so the replay still finds it."""
    from scripts.capture_board_fixtures import CAPTURES, request_key

    capture = CAPTURES[ats]
    committed = {lookup_key(key) for key in load_fixture(ats)["requests"]}
    assert lookup_key(request_key(capture.url, capture.params)) in committed
    for url, _scrub in capture.also:
        assert lookup_key(request_key(url, None)) in committed


async def test_a_rerecorded_lever_board_replays_like_the_committed_one() -> None:
    from scripts.capture_board_fixtures import CAPTURES, request_key

    capture = CAPTURES["lever"]
    rerecorded_key = request_key(capture.url, capture.params)
    assert "skip=0" in rerecorded_key
    assert rerecorded_key not in load_fixture("lever")["requests"]
    (entry,) = load_fixture("lever")["requests"].values()

    again = await read("lever", recording_handler({rerecorded_key: entry}))
    committed = await read("lever", fixture_handler("lever"))

    assert again.outcome == "ok"
    assert [role.source_url for role in again.roles] == [
        role.source_url for role in committed.roles
    ]
    assert len(again.roles) == 47


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


# ── recordings of Workable, Recruitee and Keka name nobody ───────────────────


def test_board_scrubbers_keep_known_fields_only_and_hide_personal_addresses() -> None:
    """Default-deny: a field the scrubber does not know is dropped; a role inbox is public."""
    from scripts.fixture_privacy import (
        PLACEHOLDER_BOARD_EMAIL,
        scrub_keka_jobs,
        scrub_keka_portal,
        scrub_recruitee_board,
        scrub_workable_board,
    )

    workable = scrub_workable_board(
        {
            "name": "Acme",
            "description": "<p>Write to careers@acme.in or priya.s@acme.in</p>",
            "owner": {"name": "Priya S"},
            "jobs": [
                {
                    "title": "Engineer",
                    "shortcode": "ABC123DEF4",
                    "hiring_manager": "Priya S",
                    "recruiter": {"name": "Ravi K", "email": "ravi@acme.in"},
                    "locations": [{"city": "Pune", "country": "India", "contact": "Ravi K"}],
                },
                "not a posting",
                None,
            ],
        }
    )
    assert workable == {
        "name": "Acme",
        "description": f"<p>Write to careers@acme.in or {PLACEHOLDER_BOARD_EMAIL}</p>",
        "jobs": [
            {
                "title": "Engineer",
                "shortcode": "ABC123DEF4",
                "locations": [{"city": "Pune", "country": "India"}],
            }
        ],
    }

    recruitee = scrub_recruitee_board(
        {
            "offers": [
                {
                    "id": 1,
                    "slug": "engineer",
                    "title": "Engineer",
                    "description": "<p>Apply via jobs@acme.in</p>",
                    "mailbox_email": "job.abc12@acme.recruitee.com",
                    "recruiter_id": 9,
                    "open_questions": [{"body": "Why?"}],
                    "translations": {"en": {"description": "<p>copy</p>"}},
                    "locations": [
                        {"name": "Pune", "country": "India", "translations": {}, "note": "x"}
                    ],
                }
            ],
            "meta": {"owner": "Priya S"},
        }
    )
    assert recruitee == {
        "offers": [
            {
                "id": 1,
                "slug": "engineer",
                "title": "Engineer",
                "description": "<p>Apply via jobs@acme.in</p>",
                "locations": [{"name": "Pune", "country": "India"}],
            }
        ]
    }

    keka = scrub_keka_jobs(
        [
            {
                "id": 7,
                "title": "Engineer",
                "description": "<p>Mail Ravi at ravi.k@acme.in</p>",
                "hiringManager": {"name": "Ravi K"},
                "departmentIdentifier": "5cc6aa4e",
                "jobLocations": [{"name": "Pune", "countryName": "India", "managerId": 3}],
            },
            "junk",
        ]
    )
    assert keka == [
        {
            "id": 7,
            "title": "Engineer",
            "description": f"<p>Mail Ravi at {PLACEHOLDER_BOARD_EMAIL}</p>",
            "jobLocations": [{"name": "Pune", "countryName": "India"}],
        }
    ]
    portal = {"name": "ACME", "socialLinks": ["https://x.com/acme"], "careersPortalDomain": "a.b"}
    assert scrub_keka_portal(portal) == {"name": "ACME", "careersPortalDomain": "a.b"}

    for scrub, again in (
        (scrub_workable_board, workable),
        (scrub_recruitee_board, recruitee),
        (scrub_keka_jobs, keka),
    ):
        assert scrub(again) == again, "scrubbing a recording twice changes nothing"


def test_board_scrubbers_keep_nothing_of_a_shape_they_do_not_know() -> None:
    from scripts.fixture_privacy import (
        scrub_keka_jobs,
        scrub_keka_portal,
        scrub_recruitee_board,
        scrub_workable_board,
    )

    junk: Any
    for junk in (None, 5, "text", [], [1]):
        assert scrub_workable_board(junk) == {}
        assert scrub_recruitee_board(junk) == {}
        assert scrub_keka_portal(junk) == {}
    for junk in (None, 5, "text", {}, {"jobs": []}):
        assert scrub_keka_jobs(junk) == []


@pytest.mark.parametrize("ats", ["workable", "recruitee", "keka"])
def test_the_recorded_board_names_no_people(ats: str) -> None:
    """No address but a role inbox or the placeholder, and no field the scrubbers do not keep."""
    from scripts.fixture_privacy import PLACEHOLDER_BOARD_EMAIL, ROLE_INBOXES, emails_in

    def keys(node: object) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {key for child in node.values() for key in keys(child)}
        if isinstance(node, list):
            return {key for child in node for key in keys(child)}
        return set()

    for entry in load_fixture(ats)["requests"].values():
        body = json.loads(entry["body"])
        for address in emails_in(body):
            local = address.split("@", 1)[0].casefold()
            assert address == PLACEHOLDER_BOARD_EMAIL or local in ROLE_INBOXES, address
        assert not keys(body) & {"mailbox_email", "recruiter", "hiring_manager", "hiringManager"}


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
    assert data is OVER_CAP
    assert sent <= MAX_BODY_BYTES + 2 * len(chunk)


@pytest.mark.parametrize(
    "handler",
    [
        answering(500, text="boom"),
        answering(200, text=HTML_PAGE),
        answering(301, headers={"location": "https://example.test/x"}),
    ],
)
async def test_only_the_size_cap_is_marked_over_cap(handler: Handler) -> None:
    async with client_for(handler) as client:
        outcome, data = await get_json(client, "https://example.test/board")
    assert outcome == "error"
    assert data is None


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
    bodies: dict[AtsName, str] = {
        "greenhouse": '{"jobs": []}',
        "ashby": '{"jobs": []}',
        "lever": "[]",
        "workable": '{"name": "Acme", "description": "", "jobs": []}',
        "recruitee": '{"offers": []}',
        "keka": "[]",
    }
    for ats, body in bodies.items():
        result = await read(ats, answering(200, text=body))
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
        "workable": {"title": "Engineer", "shortcode": "ABC123DEF4", "city": "Bengaluru"},
        "recruitee": {"id": 7, "title": "Engineer", "slug": "engineer", "location": "Bengaluru"},
        "keka": {"id": 7, "title": "Engineer", "jobLocations": [{"name": "Bengaluru"}]},
    }[ats]
    bad: dict[str, Any] = {"id": "broken"}  # no title, no URL
    boards: dict[AtsName, Any] = {
        "greenhouse": {"jobs": [good, bad]},
        "ashby": {"jobs": [good, bad]},
        "lever": [good, bad],
        "workable": {"name": "Acme", "jobs": [good, bad]},
        "recruitee": {"offers": [good, bad]},
        "keka": [good, bad],
    }
    result = await read(ats, answering(200, json=boards[ats]))
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


@pytest.mark.parametrize(
    ("location", "remote"),
    [
        ("Remote", True),
        ("Remote - India", True),
        ("Bengaluru (Remote)", True),
        ("Remote-first, India", True),
        ("WFH", True),
        ("Work from home", True),
        ("Pune, work  from   home", True),
        ("Pune – Global Capability Centre", False),
        ("Global Business Services, Chennai", False),
        ("Anywhere Road, Pune", False),
        ("Worldwide Plaza, Mumbai", False),
        ("Remotely Bengaluru", False),
        ("Bengaluru", False),
        ("", False),
        (None, False),
    ],
)
def test_looks_remote_matches_whole_words_only(location: str | None, remote: bool) -> None:
    assert looks_remote(location) is remote


async def test_greenhouse_does_not_mark_a_global_capability_centre_remote() -> None:
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://job-boards.greenhouse.io/b/jobs/1",
                "location": {"name": "Pune – Global Capability Centre"},
            },
            {
                "id": 2,
                "title": "Engineer",
                "absolute_url": "https://job-boards.greenhouse.io/b/jobs/2",
                "location": {"name": "Remote - India"},
            },
        ]
    }
    result = await read("greenhouse", answering(200, json=payload), "b")
    centre, remote = result.roles
    assert centre.is_remote is False
    assert is_india_role(centre.location_raw)
    assert remote.is_remote is True


def test_clean_url_keeps_a_posting_url() -> None:
    assert clean_url(" https://jobs.lever.co/acme/1 ") == "https://jobs.lever.co/acme/1"


# ── big boards stay under the body cap ───────────────────────────────────────


def over_cap_body() -> bytes:
    return b"[" + b"1," * (MAX_BODY_BYTES // 2 + 1000) + b"1]"


GREENHOUSE_POSTING = {
    "id": 5,
    "title": "Engineer",
    "absolute_url": "https://job-boards.greenhouse.io/big/jobs/5",
    "location": {"name": "Bengaluru, India"},
    "first_published": "2026-03-01T00:00:00Z",
}


async def test_greenhouse_over_cap_falls_back_without_content() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("content") == "true":
            return httpx.Response(200, content=over_cap_body())
        return httpx.Response(200, json={"jobs": [GREENHOUSE_POSTING]})

    result = await read("greenhouse", handler, "big")

    assert result.outcome == "ok"
    assert result.complete is True
    assert result.total == len(result.roles) == 1
    assert [request.url.params.get("content") for request in seen] == ["true", None]
    assert seen[0].url.path == seen[1].url.path
    (role,) = result.roles
    assert role.description_clean is None
    assert role.title == "Engineer"
    assert role.posted_at == datetime(2026, 3, 1, tzinfo=UTC)


async def test_greenhouse_over_cap_without_content_is_still_error() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=over_cap_body())

    result = await read("greenhouse", handler, "big")

    assert result.outcome == "error"
    assert result.roles == []
    assert result.complete is False
    assert len(seen) == 2, "one retry without content, then it is reported"


@pytest.mark.parametrize(
    "handler",
    [
        answering(500, text="boom"),
        answering(429, text=HTML_PAGE),
        answering(404, json={"error": "no"}),
        answering(200, text=HTML_PAGE),
        answering(301, headers={"location": "https://example.test/x"}),
    ],
)
async def test_greenhouse_falls_back_only_for_the_size_cap(handler: Handler) -> None:
    seen: list[httpx.Request] = []

    def counting(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    result = await read("greenhouse", counting, "big")

    assert result.outcome in ("error", "rate_limited", "not_found")
    assert len(seen) == 1


def lever_posting(number: int) -> dict[str, Any]:
    return {
        "id": f"l{number}",
        "text": f"Role {number}",
        "hostedUrl": f"https://jobs.lever.co/big/l{number}",
        "categories": {"location": "Pune"},
    }


def lever_pages(total: int, seen: list[httpx.Request]) -> Handler:
    """A Lever board of ``total`` postings, answering ``skip`` and ``limit`` as Lever does."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        skip = int(request.url.params["skip"])
        limit = int(request.url.params["limit"])
        return httpx.Response(
            200, json=[lever_posting(n) for n in range(skip, min(skip + limit, total))]
        )

    return handler


def test_lever_pages_are_small_enough_for_the_body_cap() -> None:
    """A page of 100 at Paytm was 1.5 MB (15.7 KB a role); 50 leaves the cap real headroom."""
    assert PAGE_SIZE == 50
    assert MAX_PAGES == 20
    assert PAGE_SIZE * 16 * 1024 < MAX_BODY_BYTES // 2


async def test_lever_pages_until_short_page() -> None:
    seen: list[httpx.Request] = []
    total = PAGE_SIZE * 4 + 30

    result = await read("lever", lever_pages(total, seen), "big")

    assert result.outcome == "ok"
    assert result.complete is True
    assert result.total == len(result.roles) == total
    assert [role.external_id for role in result.roles] == [f"l{n}" for n in range(total)]
    assert [request.url.params["skip"] for request in seen] == ["0", "50", "100", "150", "200"]
    assert {request.url.params["limit"] for request in seen} == {"50"}
    assert {request.url.params["mode"] for request in seen} == {"json"}


async def test_lever_board_of_exactly_one_page_asks_once_more_and_is_complete() -> None:
    seen: list[httpx.Request] = []

    result = await read("lever", lever_pages(PAGE_SIZE, seen), "big")

    assert result.outcome == "ok"
    assert result.complete is True
    assert result.total == len(result.roles) == PAGE_SIZE
    assert [request.url.params["skip"] for request in seen] == ["0", str(PAGE_SIZE)]


@pytest.mark.parametrize("total", [PAGE_SIZE * MAX_PAGES, PAGE_SIZE * MAX_PAGES * 5])
async def test_lever_page_cap_is_incomplete(total: int) -> None:
    seen: list[httpx.Request] = []

    result = await read("lever", lever_pages(total, seen), "big")

    assert result.outcome == "ok"
    assert result.complete is False, "page 20 was full: there may be more"
    assert result.total == len(result.roles) == PAGE_SIZE * MAX_PAGES
    assert len(seen) == MAX_PAGES
    assert seen[-1].url.params["skip"] == str(PAGE_SIZE * (MAX_PAGES - 1))


async def test_lever_a_page_that_fails_fails_the_whole_read() -> None:
    for status, outcome in ((429, "rate_limited"), (500, "error"), (404, "not_found")):
        seen: list[httpx.Request] = []
        pages = lever_pages(PAGE_SIZE * 6, seen)

        def handler(
            request: httpx.Request, status: int = status, pages: Handler = pages
        ) -> httpx.Response:
            if request.url.params["skip"] == str(PAGE_SIZE):
                return httpx.Response(status, text="no")
            return pages(request)

        result = await read("lever", handler, "big")
        assert result.outcome == outcome
        assert result.roles == []
        assert result.complete is False
        assert len(seen) == 1, "the page after the failure is never asked for"


async def test_lever_a_page_that_is_not_a_list_is_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["skip"] == str(PAGE_SIZE):
            return httpx.Response(200, json={"ok": False})
        return httpx.Response(200, json=[lever_posting(n) for n in range(PAGE_SIZE)])

    result = await read("lever", handler, "big")
    assert result.outcome == "error"
    assert result.roles == []


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


async def test_ashby_secondary_locations_follow_the_primary_in_location_raw() -> None:
    def posting(number: int, location: str, secondary: Any, **more: Any) -> dict[str, Any]:
        return {
            "id": f"a{number}",
            "title": f"Role {number}",
            "jobUrl": f"https://jobs.ashbyhq.com/b/a{number}",
            "location": location,
            "secondaryLocations": secondary,
            **more,
        }

    payload = {
        "jobs": [
            posting(
                1,
                "San Francisco",
                [
                    {
                        "location": "Bengaluru",
                        "address": {"postalAddress": {"addressCountry": "India"}},
                    },
                    {"location": "Mumbai"},
                    {"location": "bengaluru"},
                    {"location": " san francisco "},
                    {"location": ""},
                    {},
                    "junk",
                    None,
                ],
            ),
            posting(2, "San Francisco", []),
            posting(3, "", [{"location": "Pune"}], isRemote=True),
            posting(4, "", [{"location": "Chennai"}, {"location": "Hyderabad"}]),
            posting(5, "London", "not a list"),
            posting(6, "Berlin", [{"location": "Berlin"}]),
        ]
    }

    result = await read("ashby", answering(200, json=payload), "b")

    assert [role.location_raw for role in result.roles] == [
        "San Francisco; Bengaluru; Mumbai",
        "San Francisco",
        "Remote; Pune",
        "Chennai; Hyderabad",
        "London",
        "Berlin",
    ]
    assert [is_india_role(role.location_raw) for role in result.roles] == [
        True,
        False,
        True,
        True,
        False,
        False,
    ]


async def test_ashby_recorded_roles_open_in_several_places_name_them_all() -> None:
    result = await read("ashby", fixture_handler("ashby"))
    body = json.loads(next(iter(load_fixture("ashby")["requests"].values()))["body"])
    stated = {item["id"]: item for item in body["jobs"]}
    several = 0
    for role in result.roles:
        item = stated[role.external_id or ""]
        names = [item["location"]] + [entry["location"] for entry in item["secondaryLocations"]]
        assert role.location_raw == "; ".join(dict.fromkeys(names))
        several += len(names) > 1
    assert several, "the recording has roles with secondary locations"


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

    seen.clear()
    await read("workable", handler, "../admin")
    path = seen[0].url.raw_path.split(b"?")[0]
    assert path == b"/api/v1/widget/accounts/..%2Fadmin", "Workable's board stays one segment too"


# ── Workable ─────────────────────────────────────────────────────────────────

WORKABLE_LOCATION = {
    "country": "India",
    "countryCode": "IN",
    "city": "Pune",
    "region": "Maharashtra",
    "hidden": False,
}


def workable_board(*postings: dict[str, Any], name: str | None = "Acme") -> dict[str, Any]:
    board: dict[str, Any] = {"description": "<p>About us</p>", "jobs": list(postings)}
    if name is not None:
        board["name"] = name
    return board


def workable_posting(shortcode: str = "ABC123DEF4", **more: Any) -> dict[str, Any]:
    return {
        "title": "Engineer",
        "shortcode": shortcode,
        "url": f"https://apply.workable.com/j/{shortcode}",
        "telecommuting": False,
        "published_on": "2026-03-01",
        "locations": [WORKABLE_LOCATION],
        **more,
    }


async def test_workable_urls_are_built_from_the_board_and_the_shortcode() -> None:
    """The widget's own ``url`` is the short ``/j/<code>`` link, which names no board."""
    from domain.posting_urls import PostingRef, parse_posting_url

    result = await read("workable", fixture_handler("workable"))
    for role in result.roles:
        assert role.source_url == f"https://apply.workable.com/apna/j/{role.external_id}/"
        assert parse_posting_url(role.source_url) == PostingRef(
            "workable", "apna", role.external_id or ""
        )


async def test_workable_company_name_is_the_accounts_name() -> None:
    result = await read("workable", fixture_handler("workable"))
    assert result.company_name == "Apna"
    assert {(role.extra_metadata or {}).get("company_name") for role in result.roles} == {"Apna"}


async def test_workable_without_a_stated_name_has_no_company_name() -> None:
    for name in (None, "", 7):
        board = workable_board(workable_posting())
        board["name"] = name
        result = await read("workable", answering(200, json=board), "acme")
        assert result.company_name is None
        assert (result.roles[0].extra_metadata or {}).get("company_name") == "Acme"


async def test_workable_roles_carry_no_description_because_the_list_has_none() -> None:
    result = await read("workable", fixture_handler("workable"))
    assert all(role.description_clean is None for role in result.roles)


async def test_workable_locations_are_city_region_country_and_hidden_ones_are_left_out() -> None:
    def place(city: str, region: str | None, country: str, **more: Any) -> dict[str, Any]:
        return {"city": city, "region": region, "country": country, "hidden": False, **more}

    postings = [
        workable_posting("AAAAAAAAAA", locations=[place("Bengaluru", "Karnataka", "India")]),
        workable_posting(
            "BBBBBBBBBB",
            locations=[
                place("London", None, "United Kingdom"),
                place("Pune", "Maharashtra", "India"),
            ],
        ),
        workable_posting("CCCCCCCCCC", locations=[place("Delhi", "Delhi", "India")]),
        workable_posting("DDDDDDDDDD", locations=[place("", None, "India")]),
        workable_posting(
            "EEEEEEEEEE",
            locations=[place("Bengaluru", "Karnataka", "India", hidden=True)],
            city="Bengaluru",
            country="India",
        ),
        workable_posting(
            "FFFFFFFFFF", locations=[], city="Chennai", state="Tamil Nadu", country="India"
        ),
        workable_posting("GGGGGGGGGG", locations=None, city="Noida", country="India"),
        workable_posting(
            "HHHHHHHHHH", locations=["junk", None, {"city": "Mumbai", "country": "India"}]
        ),
    ]
    result = await read("workable", answering(200, json=workable_board(*postings)), "acme")
    assert [role.location_raw for role in result.roles] == [
        "Bengaluru, Karnataka, India",
        "London, United Kingdom; Pune, Maharashtra, India",
        "Delhi, India",
        "India",
        None,
        "Chennai, Tamil Nadu, India",
        "Noida, India",
        "Mumbai, India",
    ]
    assert [is_india_role(role.location_raw) for role in result.roles] == [
        True, True, True, True, False, True, True, True,
    ]  # fmt: skip


async def test_workable_telecommuting_is_the_remote_flag_and_stands_in_for_a_hidden_place() -> None:
    hidden = {**WORKABLE_LOCATION, "hidden": True}
    postings = [
        workable_posting("AAAAAAAAAA", telecommuting=True),
        workable_posting("BBBBBBBBBB", telecommuting=False),
        workable_posting("CCCCCCCCCC", telecommuting="yes"),
        workable_posting("DDDDDDDDDD", telecommuting=True, locations=[hidden]),
    ]
    result = await read("workable", answering(200, json=workable_board(*postings)), "acme")
    assert [role.is_remote for role in result.roles] == [True, False, False, True]
    assert result.roles[3].location_raw == "Remote"


async def test_workable_published_on_is_the_stated_day_and_nothing_else_is_a_date() -> None:
    postings = [
        workable_posting("AAAAAAAAAA", published_on="2026-03-01", created_at="2020-01-01"),
        workable_posting("BBBBBBBBBB", published_on=None, created_at="2026-03-02"),
        workable_posting("CCCCCCCCCC", published_on="not a date"),
    ]
    result = await read("workable", answering(200, json=workable_board(*postings)), "acme")
    first, second, third = result.roles
    assert first.posted_at == datetime(2026, 3, 1, tzinfo=UTC)
    assert first.source_posted_at == first.posted_at
    assert second.posted_at is None, "created_at is when it was drafted, not when it went up"
    assert third.posted_at is None
    assert all(role.source_updated_at is None for role in result.roles)


@pytest.mark.parametrize("shortcode", ["", "  ", "../x", "AB CD", None, 7, "a/b", "A" * 40])
async def test_workable_a_shortcode_that_is_not_one_makes_the_posting_unreadable(
    shortcode: Any,
) -> None:
    postings = [
        workable_posting("AAAAAAAAAA"),
        {**workable_posting("BBBBBBBBBB"), "shortcode": shortcode},
    ]
    result = await read("workable", answering(200, json=workable_board(*postings)), "acme")
    assert result.outcome == "ok"
    assert [role.external_id for role in result.roles] == ["AAAAAAAAAA"]
    assert result.total == 2
    assert result.complete is False


# ── Recruitee ────────────────────────────────────────────────────────────────


def recruitee_offer(number: int = 1, **more: Any) -> dict[str, Any]:
    return {
        "id": number,
        "slug": f"role-{number}",
        "title": f"Role {number}",
        "description": "<p>Build things.</p>",
        "locations": [{"name": "Pune", "city": "Pune", "state": "Maharashtra", "country": "India"}],
        "published_at": "2026-11-12 06:42:00 UTC",
        "updated_at": "2026-11-13 07:00:00 UTC",
        "remote": False,
        **more,
    }


async def test_recruitee_date_parses() -> None:
    result = await read("recruitee", fixture_handler("recruitee"))
    body = json.loads(next(iter(load_fixture("recruitee")["requests"].values()))["body"])
    stated = {str(offer["id"]): offer for offer in body["offers"]}
    for role in result.roles:
        assert role.posted_at is not None
        assert role.posted_at.tzinfo is not None
        assert role.posted_at.utcoffset() == timedelta(0)
        offer = stated[role.external_id or ""]
        assert offer["published_at"].endswith(" UTC")
        assert role.posted_at == datetime.strptime(
            offer["published_at"], "%Y-%m-%d %H:%M:%S UTC"
        ).replace(tzinfo=UTC)
        assert role.source_updated_at is not None
        assert role.source_updated_at >= role.posted_at


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        ("2026-11-12 06:42:00 UTC", datetime(2026, 11, 12, 6, 42, tzinfo=UTC)),
        ("2026-11-12 23:59:59 UTC", datetime(2026, 11, 12, 23, 59, 59, tzinfo=UTC)),
        ("2026-11-12T06:42:00Z", datetime(2026, 11, 12, 6, 42, tzinfo=UTC)),
        ("2026-11-12", datetime(2026, 11, 12, tzinfo=UTC)),
        ("yesterday", None),
        ("2026-13-45 06:42:00 UTC", None),
        ("", None),
        (None, None),
        (20261112, None),
    ],
)
async def test_recruitee_dates_are_read_as_stated_or_not_at_all(
    stated: Any, expected: datetime | None
) -> None:
    offer = recruitee_offer(published_at=stated, updated_at=stated)
    result = await read("recruitee", answering(200, json={"offers": [offer]}), "acme")
    (role,) = result.roles
    assert role.posted_at == expected
    assert role.source_posted_at == expected
    assert role.source_updated_at == expected


async def test_recruitee_urls_are_built_from_the_board_and_the_slug() -> None:
    from domain.posting_urls import PostingRef, parse_posting_url

    result = await read("recruitee", fixture_handler("recruitee"))
    for role in result.roles:
        url = role.source_url or ""
        assert url.startswith("https://lenskartcareers.recruitee.com/o/")
        assert parse_posting_url(url) == PostingRef(
            "recruitee", "lenskartcareers", url.rsplit("/", 1)[1]
        )


async def test_recruitee_url_falls_back_to_the_offers_own_link_when_it_has_no_slug() -> None:
    offers = [
        recruitee_offer(1, slug=None, careers_url="https://acme.recruitee.com/o/odd-one"),
        recruitee_offer(2, slug="", careers_url=None),
        recruitee_offer(3, slug="../x", careers_url="javascript:alert(1)"),
    ]
    result = await read("recruitee", answering(200, json={"offers": offers}), "acme")
    assert [role.source_url for role in result.roles] == ["https://acme.recruitee.com/o/odd-one"]
    assert result.total == 3
    assert result.complete is False


async def test_recruitee_roles_in_several_places_name_each_of_them() -> None:
    offers = [
        recruitee_offer(
            1,
            locations=[
                {"name": "Bengaluru", "city": "Gurugram", "state": "Haryāna", "country": "India"},
                {"name": "Bangalore/ Gurgaon", "city": "Bangalore", "state": "Karnātaka"},
            ],
        ),
        recruitee_offer(2, locations=[], location="Mumbai, Maharashtra, India"),
        recruitee_offer(3, locations=None, location="", city="Chennai", country="India"),
        recruitee_offer(4, locations=[{"city": "Delhi", "name": "Delhi", "country": "India"}] * 2),
        recruitee_offer(5, locations=[], location=""),
        recruitee_offer(
            6, locations=[{"name": "Singapore", "city": "Singapore ", "country": "Singapore"}]
        ),
    ]
    result = await read("recruitee", answering(200, json={"offers": offers}), "acme")
    assert [role.location_raw for role in result.roles] == [
        "Bengaluru, Gurugram, Haryāna, India; Bangalore/ Gurgaon, Karnātaka",
        "Mumbai, Maharashtra, India",
        "Chennai, India",
        "Delhi, India",
        None,
        "Singapore",
    ]
    assert [is_india_role(role.location_raw) for role in result.roles] == [
        True, True, True, True, False, False,
    ]  # fmt: skip


async def test_recruitee_recorded_roles_open_in_several_places_name_all_of_them() -> None:
    result = await read("recruitee", fixture_handler("recruitee"))
    several = [role for role in result.roles if ";" in (role.location_raw or "")]
    assert several, "the recording has offers with two locations"
    assert all(is_india_role(role.location_raw) for role in several)


async def test_recruitee_description_is_the_text_of_the_description_and_requirements() -> None:
    offers = [
        recruitee_offer(
            1,
            description="<p>Build <b>things</b>.</p><ul><li>Python</li></ul>",
            requirements="<p>5 years &amp; a degree</p>",
        ),
        recruitee_offer(2, description=None, requirements="<p>Only this</p>"),
        recruitee_offer(3, description="", requirements=None),
    ]
    result = await read("recruitee", answering(200, json={"offers": offers}), "acme")
    first, second, third = result.roles
    assert first.description_clean == "Build things.\nPython\n\n5 years & a degree"
    assert second.description_clean == "Only this"
    assert third.description_clean is None


async def test_recruitee_recorded_descriptions_are_plain_text() -> None:
    result = await read("recruitee", fixture_handler("recruitee"))
    described = [role.description_clean for role in result.roles if role.description_clean]
    assert len(described) == len(result.roles)
    for text in described:
        assert "<p" not in text
        assert "<li" not in text
        assert "&nbsp;" not in text
        assert "&amp;" not in text


async def test_recruitee_remote_is_the_offers_own_flag() -> None:
    offers = [
        recruitee_offer(1, remote=True),
        recruitee_offer(2, remote=False),
        recruitee_offer(3, remote="true", locations=[{"name": "Remote", "country": "India"}]),
    ]
    result = await read("recruitee", answering(200, json={"offers": offers}), "acme")
    assert [role.is_remote for role in result.roles] == [True, False, False]


async def test_recruitee_company_name_is_the_offers_own() -> None:
    result = await read("recruitee", fixture_handler("recruitee"))
    assert {(role.extra_metadata or {}).get("company_name") for role in result.roles} == {
        "Lenskart"
    }


# ── Keka ─────────────────────────────────────────────────────────────────────

KEKA_API = "https://spyneai.keka.com/careers/api/jobs/default/active"
KEKA_PORTAL = "https://spyneai.keka.com/careers/api/organization/default/careerportalinfo"


def keka_job(number: int = 1, **more: Any) -> dict[str, Any]:
    return {
        "id": number,
        "title": f"Role {number}",
        "description": "<div>Build things.</div>",
        "jobLocations": [{"name": "Pune", "city": "Pune", "countryName": "India"}],
        "publishedOn": "2026-10-30T08:17:09.217Z",
        **more,
    }


def keka_answers(jobs: Any, portal: Any = None, seen: list[httpx.Request] | None = None) -> Handler:
    """Keka's two answers: the jobs list, and the careers portal's description of the company."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if str(request.url) == KEKA_API:
            return httpx.Response(200, json=jobs)
        if str(request.url) == KEKA_PORTAL and portal is not None:
            return httpx.Response(200, json=portal)
        return httpx.Response(404)

    return handler


async def test_keka_requests_only_careers_paths() -> None:
    seen: list[httpx.Request] = []
    result = await read("keka", fixture_handler("keka", seen))

    assert result.outcome == "ok"
    assert [str(request.url) for request in seen] == [KEKA_API, KEKA_PORTAL]
    assert all(str(request.url).startswith("https://spyneai.keka.com/careers/") for request in seen)


async def test_keka_company_name_comes_from_the_careers_portal() -> None:
    result = await read("keka", fixture_handler("keka"))
    assert result.company_name == "SPYNE"
    assert {(role.extra_metadata or {}).get("company_name") for role in result.roles} == {"SPYNE"}


async def test_keka_urls_are_built_from_the_board_and_the_job_id() -> None:
    from domain.posting_urls import PostingRef, parse_posting_url

    result = await read("keka", fixture_handler("keka"))
    for role in result.roles:
        assert role.source_url == f"https://spyneai.keka.com/careers/jobdetails/{role.external_id}"
        assert parse_posting_url(role.source_url) == PostingRef(
            "keka", "spyneai", role.external_id or ""
        )


async def test_keka_dates_are_publishedon() -> None:
    result = await read("keka", fixture_handler("keka"))
    body = json.loads(next(iter(load_fixture("keka")["requests"].values()))["body"])
    stated = {str(job["id"]): job for job in body}
    for role in result.roles:
        assert role.posted_at == parse_source_timestamp(
            stated[role.external_id or ""]["publishedOn"]
        )
        assert role.source_updated_at is None


async def test_keka_locations_are_the_name_and_country_and_several_are_joined() -> None:
    def place(**fields: Any) -> list[Any]:
        return [fields]

    jobs = [
        keka_job(
            1, jobLocations=place(name="Gurgaon, Haryana", city="Gurgaon", countryName="India")
        ),
        keka_job(2, jobLocations=place(name="Gurugram", city="Gurugram", countryName="India")),
        keka_job(3, jobLocations=place(name="Mumbai, India", countryName="India")),
        keka_job(
            4,
            jobLocations=[
                {"name": "London", "countryName": "United Kingdom"},
                {"name": "Pune", "countryName": "India"},
                {"name": "pune", "countryName": "India"},
            ],
        ),
        keka_job(5, jobLocations=place(city="Noida", countryName="India")),
        keka_job(6, jobLocations=[{"countryName": "India"}, "junk", None, {}]),
        keka_job(7, jobLocations=[]),
        keka_job(8, jobLocations=None),
        keka_job(9, jobLocations=place(name="Remote", countryName="India")),
    ]
    result = await read("keka", keka_answers(jobs), "spyneai")
    assert [role.location_raw for role in result.roles] == [
        "Gurgaon, Haryana, India",
        "Gurugram, India",
        "Mumbai, India",
        "London, United Kingdom; Pune, India",
        "Noida, India",
        "India",
        None,
        None,
        "Remote, India",
    ]
    assert [is_india_role(role.location_raw) for role in result.roles] == [
        True, True, True, True, True, True, False, False, True,
    ]  # fmt: skip
    assert [role.is_remote for role in result.roles] == [False] * 8 + [True]


async def test_keka_description_is_the_text_of_the_description_or_the_excerpt() -> None:
    jobs = [
        keka_job(1, description="<div>Build <b>things</b>.</div><ul><li>Python</li></ul>"),
        keka_job(2, description="", excerpt="Only the excerpt"),
        keka_job(3, description=None, excerpt=None),
    ]
    result = await read("keka", keka_answers(jobs), "spyneai")
    first, second, third = result.roles
    assert first.description_clean == "Build things.\nPython"
    assert second.description_clean == "Only the excerpt"
    assert third.description_clean is None


async def test_keka_recorded_descriptions_are_plain_text() -> None:
    result = await read("keka", fixture_handler("keka"))
    for role in result.roles:
        assert role.description_clean
        assert "<div" not in role.description_clean
        assert "<li" not in role.description_clean


@pytest.mark.parametrize("job_id", [None, "", "abc", "12/34", -5, 1.5, True, "1" * 13])
async def test_keka_an_id_that_is_not_a_job_number_makes_the_job_unreadable(job_id: Any) -> None:
    jobs = [keka_job(1), keka_job(2, id=job_id)]
    result = await read("keka", keka_answers(jobs, {"name": "Acme"}), "spyneai")
    assert [role.external_id for role in result.roles] == ["1"]
    assert result.total == 2
    assert result.complete is False


async def test_keka_a_name_that_cannot_be_read_leaves_the_read_ok() -> None:
    portal: Any
    for portal in (None, [], "text", {"name": ""}, {"name": 7}, {}):
        seen: list[httpx.Request] = []
        result = await read("keka", keka_answers([keka_job()], portal, seen), "spyneai")
        assert result.outcome == "ok"
        assert result.complete is True
        assert result.company_name is None
        assert len(result.roles) == 1
        assert (result.roles[0].extra_metadata or {}).get("company_name") == "Spyneai"
        assert len(seen) == 2


@pytest.mark.parametrize("status", [429, 500, 200])
async def test_keka_a_failing_name_request_does_not_fail_the_read(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == KEKA_API:
            return httpx.Response(200, json=[keka_job()])
        return httpx.Response(status, text=HTML_PAGE)

    result = await read("keka", handler, "spyneai")
    assert result.outcome == "ok"
    assert len(result.roles) == 1
    assert result.company_name is None


async def test_keka_asks_for_the_name_only_when_there_are_jobs_to_name() -> None:
    for status, body in ((429, "slow"), (404, "no"), (500, "boom"), (200, HTML_PAGE), (200, "[]")):
        seen: list[httpx.Request] = []

        def handler(
            request: httpx.Request,
            status: int = status,
            body: str = body,
            seen: list[httpx.Request] = seen,
        ) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, text=body)

        await read("keka", handler, "spyneai")
        assert [str(request.url) for request in seen] == [KEKA_API]


# ── a board id becomes part of a host ────────────────────────────────────────


@pytest.mark.parametrize("ats", ["recruitee", "keka"])
@pytest.mark.parametrize(
    "board",
    [
        "evil.example/x",
        "evil.example",
        "a.b",
        "-lead",
        "trail-",
        "has space",
        "../admin",
        "a@b",
        "a:80",
        "a_b",
        "x" * 64,
        "ünï",
        "a/b",
        "a#b",
        "a?b",
    ],
)
async def test_a_board_that_cannot_be_a_host_label_is_never_requested(
    ats: AtsName, board: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    result = await read(ats, handler, board)

    assert seen == [], "a board id must not be able to choose the host that is asked"
    assert result.outcome == "not_found"
    assert result.roles == []
    assert result.complete is False


async def test_an_empty_board_id_is_never_requested() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    for ats in ("recruitee", "keka"):
        async with client_for(handler) as client:
            result = await READERS[ats](client, BoardRef(ats, ""))
        assert result.outcome == "not_found"
    assert seen == []


@pytest.mark.parametrize(
    ("ats", "board", "host"),
    [
        ("recruitee", "LensKartCareers", "lenskartcareers.recruitee.com"),
        ("recruitee", "a", "a.recruitee.com"),
        ("recruitee", "my-company-2", "my-company-2.recruitee.com"),
        ("recruitee", "x" * 63, f"{'x' * 63}.recruitee.com"),
        ("keka", "SpyneAI", "spyneai.keka.com"),
        ("keka", "9lives", "9lives.keka.com"),
    ],
)
async def test_a_board_that_is_a_host_label_is_asked_for_on_its_own_host(
    ats: AtsName, board: str, host: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    await read(ats, handler, board)
    assert [request.url.host for request in seen] == [host]
    assert seen[0].url.scheme == "https"


def test_place_name_and_join_locations_drop_what_is_already_said() -> None:
    from ingestion.boards.base import host_label, join_locations, place_name

    assert place_name("Delhi", "Delhi", None, "India") == "Delhi, India"
    assert place_name("Gurgaon, Haryana", "Gurgaon", "India") == "Gurgaon, Haryana, India"
    assert place_name("Mumbai, India", "India") == "Mumbai, India"
    assert place_name("  Pune \n", 7, None, "") == "Pune"
    assert place_name() == ""
    assert join_locations(["Pune", "", "pune", "Mumbai"]) == "Pune; Mumbai"
    assert join_locations([]) == ""
    assert host_label("LensKart") == "lenskart"
    assert host_label("a.b") is None
    assert host_label("") is None


# ── the capture script records what a reader asks for ────────────────────────


async def test_the_capture_script_records_kekas_second_request_scrubbed_and_paced() -> None:
    from scripts.capture_board_fixtures import CAPTURES, record

    jobs = [{**keka_job(1), "hiringManager": {"name": "Ravi K"}, "departmentIdentifier": "x"}]
    portal = {"name": "SPYNE", "socialLinks": ["https://x.com/spyne"], "shortName": "SPYNE"}
    pauses: list[float] = []

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    async with client_for(keka_answers(jobs, portal)) as client:
        fixture = await record("keka", CAPTURES["keka"], client, pause=pause)

    assert fixture is not None
    assert pauses == [2.0], "a second request to Keka waits Keka's pace"
    assert list(fixture["requests"]) == [KEKA_API, KEKA_PORTAL]
    assert "hiringManager" not in fixture["requests"][KEKA_API]["body"]
    assert json.loads(fixture["requests"][KEKA_PORTAL]["body"]) == {
        "name": "SPYNE",
        "shortName": "SPYNE",
    }


async def test_the_capture_script_records_nothing_when_the_second_request_fails() -> None:
    from scripts.capture_board_fixtures import CAPTURES, record

    async def pause(_seconds: float) -> None:
        return None

    portal: Any
    for portal in (None, {}, {"socialLinks": []}):
        async with client_for(keka_answers([keka_job()], portal)) as client:
            assert await record("keka", CAPTURES["keka"], client, pause=pause) is None


@pytest.mark.parametrize(
    ("ats", "payload"),
    [
        ("workable", {"name": "Acme", "jobs": []}),
        ("recruitee", {"offers": []}),
        ("recruitee", {"jobs": [{"title": "wrong key"}]}),
        ("keka", []),
        ("keka", {"jobs": [keka_job()]}),
    ],
)
async def test_the_capture_script_records_nothing_that_proves_nothing(
    ats: str, payload: Any
) -> None:
    from scripts.capture_board_fixtures import CAPTURES, record

    async def pause(_seconds: float) -> None:
        return None

    async with client_for(answering(200, json=payload)) as client:
        assert await record(ats, CAPTURES[ats], client, pause=pause) is None


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


@pytest.mark.parametrize("ats", LIVE_ATSES)
async def test_live_search_returns_nothing_when_the_board_cannot_be_read(ats: AtsName) -> None:
    fetch = {
        "greenhouse": ATSScraper.fetch_greenhouse_company,
        "ashby": ATSScraper.fetch_ashby_company,
        "lever": ATSScraper.fetch_lever_company,
    }[ats]
    for handler in (answering(404), answering(429), answering(200, text=HTML_PAGE)):
        async with client_for(handler) as client:
            assert await fetch("test", "python", None, None, client=client) == []
