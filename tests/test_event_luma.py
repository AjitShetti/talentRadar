"""
tests/test_event_luma.py
~~~~~~~~~~~~~~~~~~~~~~~
The Luma iCal reader: the feed parser on the recorded Bengaluru Discover feed
and on small inline feeds that pin one rule each, the feed URL builder, and the
bounded fetch every Events source goes through. No database, no network.

Every person in an inline feed is fictional; the recorded feeds under
``tests/fixtures/events/`` are pseudonymised by ``scripts/capture_fixtures.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from domain.event_facts import EventFacts
from domain.ical import IcalEvent, parse_ical
from services.events.http import MAX_BYTES, TIMEOUT_S, USER_AGENT, get_bounded
from services.events.luma import FEED_BASE, feed_url, fetch_feed, parse_feed

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
DISCOVER = FIXTURES / "luma_discover_bengaluru.ics"

DISCOVER_ID = "discplace-G0tGUVYwl7T17Sb"
HEADER = "Get up-to-date information at: https://luma.com/quill001"


def _vevent(
    *,
    uid: str = "evt-Quill0000001@events.lu.ma",
    summary: str | None = "Quill Rust Meetup",
    description: str | None = HEADER,
    location: str | None = None,
    geo: str | None = "12.9716;77.5946",
    status: str | None = "TENTATIVE",
    organizer: str | None = "Quill Collective",
    start: str = "20261101T123000Z",
    end: str | None = "20261101T153000Z",
) -> str:
    """One inline ``VEVENT``; ``description`` takes real newlines and is escaped here."""
    lines = ["BEGIN:VEVENT", f"DTSTART:{start}"]
    if end is not None:
        lines.append(f"DTEND:{end}")
    if organizer is not None:
        lines.append(f'ORGANIZER;CN="{organizer}":MAILTO:organiser@example.com')
    lines.append(f"UID:{uid}")
    if summary is not None:
        lines.append(f"SUMMARY:{summary}")
    if description is not None:
        lines.append("DESCRIPTION:" + description.replace("\n", "\\n"))
    if location is not None:
        lines.append(f"LOCATION:{location}")
    if geo is not None:
        lines.append(f"GEO:{geo}")
    if status is not None:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines) + "\r\n"


def _feed(*events: str) -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n").encode()


def _one(**kwargs: object) -> EventFacts:
    """The single event parsed out of an inline feed."""
    events = parse_feed(_feed(_vevent(**kwargs)))  # type: ignore[arg-type]
    assert len(events) == 1
    return events[0]


def _raw_by_id(path: Path) -> dict[str, IcalEvent]:
    """The recorded feed's VEVENTs as the iCal reader sees them, keyed like the parser keys them."""
    return {e.uid.removesuffix("@events.lu.ma"): e for e in parse_ical(path.read_bytes())}


def _described(body: str, *, tail: str | None = None, address: str | None = None) -> str:
    """A description laid out the way Luma writes it: header, address block, body, hosts."""
    parts = [HEADER]
    if address is not None:
        parts.append(f"Address:\n{address}")
    parts.append(body)
    if tail is not None:
        parts.append(tail)
    return "\n\n".join(parts)


# ── the recorded Discover feed ───────────────────────────────────────────────


def test_parse_discover_fixture() -> None:
    events = parse_feed(DISCOVER.read_bytes())
    assert len(events) >= 10
    for event in events:
        assert event.source == "luma"
        assert event.url.startswith("https://luma.com/")
        assert event.starts_at.tzinfo is not None
        assert event.starts_at.utcoffset() is not None
        assert event.external_id.startswith("evt-")
        assert "@" not in event.external_id
        assert event.title


def test_hosted_by_becomes_hosts() -> None:
    event = _one(
        description=_described(
            "A talk about ownership.", tail="Hosted by Ada Quill, Ben Quill & Cy Quill"
        )
    )
    assert event.hosts == ("Ada Quill", "Ben Quill", "Cy Quill")

    # On the recorded feed, every event whose description ends in "Hosted by" has hosts.
    parsed = {e.external_id: e for e in parse_feed(DISCOVER.read_bytes())}
    hosted = [
        uid
        for uid, e in _raw_by_id(DISCOVER).items()
        if e.description and e.description.rstrip().splitlines()[-1].startswith("Hosted by ")
    ]
    assert hosted, "the recorded feed has no Hosted by line to check"
    assert all(parsed[uid].hosts for uid in hosted if uid in parsed)


def test_hosts_leave_out_the_others_count() -> None:
    assert _one(
        description=_described("Body.", tail="Hosted by Quill Collective & 5 others")
    ).hosts == ("Quill Collective",)
    assert _one(description=_described("Body.", tail="Hosted by Quill Collective")).hosts == (
        "Quill Collective",
    )


def test_only_the_trailing_hosted_by_line_names_hosts() -> None:
    mid = "Hosted by Ada Quill\nDoors open at six."
    event = _one(description=_described(mid))
    assert event.hosts == ()
    assert event.snippet is not None and "Hosted by Ada Quill" in event.snippet


def test_boilerplate_removed() -> None:
    event = _one(
        description=_described(
            "Bring a laptop and a question.",
            address="Quill Hall\nBengaluru, India",
            tail="Hosted by Ada Quill & 2 others",
        )
    )
    assert event.snippet == "Bring a laptop and a question."

    raw = _raw_by_id(DISCOVER)
    for fixture_event in parse_feed(DISCOVER.read_bytes()):
        snippet = fixture_event.snippet or ""
        assert "Get up-to-date information" not in snippet
        assert "Address:" not in snippet
        trailer = (raw[fixture_event.external_id].description or "").rstrip().splitlines()[-1]
        if trailer.startswith("Hosted by "):
            assert trailer not in snippet


def test_snippet_is_flat_and_bounded() -> None:
    long_body = "Word " * 400 + "<b>tail</b>"
    event = _one(description=_described(long_body, tail="Hosted by Ada Quill"))
    assert event.snippet is not None
    assert len(event.snippet) <= 600
    assert event.snippet.endswith("…")
    assert "\n" not in event.snippet and "<" not in event.snippet

    plain = _one(description=_described("Line one.\nLine two.\n\nLine three."))
    assert plain.snippet == "Line one. Line two. Line three."


def test_event_with_only_boilerplate_has_no_snippet() -> None:
    event = _one(description=_described("", address="Check event page for more details."))
    assert event.snippet is None


def test_address_withheld_flag() -> None:
    withheld = _one(description=_described("Body.", address="Check event page for more details."))
    assert withheld.address_withheld is True
    assert withheld.venue_name is None

    shown = _one(description=_described("Body.", address="Quill Hall\nBengaluru, India"))
    assert shown.address_withheld is False
    assert shown.venue_name == "Quill Hall"

    parsed = {e.external_id: e for e in parse_feed(DISCOVER.read_bytes())}
    withheld_in_feed = [
        uid
        for uid, e in _raw_by_id(DISCOVER).items()
        if "Check event page for more details" in (e.description or "") and uid in parsed
    ]
    assert withheld_in_feed, "the recorded feed has no withheld address to check"
    assert all(parsed[uid].address_withheld for uid in withheld_in_feed)


def test_location_is_an_address_unless_it_is_a_luma_link() -> None:
    street = _one(
        location="Quill Hall, 1 Test Road, Indiranagar, Bengaluru, Karnataka 560038, India"
    )
    assert (
        street.address == "Quill Hall, 1 Test Road, Indiranagar, Bengaluru, Karnataka 560038, India"
    )
    assert _one(location="https://luma.com/event/evt-Quill0000001").address is None
    assert _one(location=None).address is None


def test_feed_url_rejects_bad_ids() -> None:
    with pytest.raises(ValueError):
        feed_url("calendar", "cal-x/../y")


@pytest.mark.parametrize(
    "ident",
    [
        "",
        "cal-",
        "cal-short",  # five characters after the prefix
        "cal-" + "a" * 41,
        "cal-abcdef/../x",
        "cal-abcdef&entity=discover",
        "cal-abcdef?x=1",
        "cal-abcdef\n",
        "cal-abcdef ",
        "CAL-abcdef1",
        "evt-abcdef123",
        "discplace_abcdef",
        "https://api.lu.ma/ics/get",
        "cal-abcdé123",
    ],
)
def test_feed_url_rejects_malformed_ids(ident: str) -> None:
    with pytest.raises(ValueError):
        feed_url("calendar", ident)


def test_feed_url_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        feed_url("event", "cal-abcdef123")  # type: ignore[arg-type]


def test_feed_url_builds_the_two_feed_addresses() -> None:
    assert FEED_BASE == "https://api.lu.ma/ics/get"
    assert feed_url("discover", DISCOVER_ID) == f"{FEED_BASE}?entity=discover&id={DISCOVER_ID}"
    assert (
        feed_url("calendar", "cal-lcQDNr9uH1UwkSa")
        == f"{FEED_BASE}?entity=calendar&id=cal-lcQDNr9uH1UwkSa"
    )
    assert feed_url("calendar", "cal-" + "a" * 6).endswith("id=cal-aaaaaa")
    assert feed_url("calendar", "cal-" + "a" * 40).endswith("id=cal-" + "a" * 40)


def test_cancelled_status() -> None:
    assert _one(status="CANCELLED").is_cancelled is True
    assert _one(status="TENTATIVE").is_cancelled is False
    assert _one(status="CONFIRMED").is_cancelled is False
    assert _one(status=None).is_cancelled is False


# ── the other fields ─────────────────────────────────────────────────────────


def test_identity_and_times() -> None:
    event = _one()
    assert event.source == "luma"
    assert event.external_id == "evt-Quill0000001"
    assert event.title == "Quill Rust Meetup"
    assert event.starts_at.isoformat() == "2026-11-01T12:30:00+00:00"
    assert event.ends_at is not None and event.ends_at.isoformat() == "2026-11-01T15:30:00+00:00"
    assert event.organiser_name == "Quill Collective"
    assert event.is_free is None and event.timezone is None


def test_a_uid_from_elsewhere_is_kept_whole() -> None:
    assert _one(uid="plain-id-1@example.com").external_id == "plain-id-1@example.com"


def test_geo_becomes_lat_and_lng() -> None:
    event = _one(geo="12.9208058;77.68740129999999")
    assert (event.lat, event.lng) == (12.9208058, 77.68740129999999)
    nowhere = _one(geo=None)
    assert nowhere.lat is None and nowhere.lng is None


def test_url_is_the_first_luma_link_in_the_description() -> None:
    body = "Slides at https://luma.com/second after the talk."
    event = _one(description=_described(body), location="https://luma.com/event/evt-Quill0000001")
    assert event.url == "https://luma.com/quill001"
    assert (
        _one(description=f"Intro https://luma.com/abc123xy.\n\n{body}").url
        == "https://luma.com/abc123xy"
    )


def test_url_falls_back_to_a_luma_location() -> None:
    event = _one(description="No link here.", location="https://luma.com/event/evt-Quill0000001")
    assert event.url == "https://luma.com/event/evt-Quill0000001"


@pytest.mark.parametrize(
    "link",
    [
        "https://luma.com.evil.example/x",
        "http://luma.com/x",
        "https://lu.ma/x",
        "https://example.com/?next=https://luma.com/x",
        "https://notluma.com/x",
        "javascript:alert(1)",
    ],
)
def test_a_link_that_is_not_luma_is_not_an_event_url(link: str) -> None:
    assert parse_feed(_feed(_vevent(description=f"See {link} now", location=link))) == []


def test_an_event_without_a_url_is_dropped() -> None:
    assert (
        parse_feed(_feed(_vevent(description="No link.", location="Quill Hall, Bengaluru"))) == []
    )


def test_an_event_without_a_title_is_dropped() -> None:
    assert parse_feed(_feed(_vevent(summary=None))) == []
    assert parse_feed(_feed(_vevent(summary="   "))) == []
    kept = parse_feed(_feed(_vevent(summary=None), _vevent(uid="evt-Quill0000002@events.lu.ma")))
    assert [e.external_id for e in kept] == ["evt-Quill0000002"]


def test_title_is_flattened() -> None:
    assert _one(summary="Quill <b>Rust</b> &amp; Friends").title == "Quill Rust & Friends"
    assert len(_one(summary="x" * 500).title) <= 300


def test_online_needs_no_geo_and_a_meeting_link() -> None:
    zoom = "Join: https://us02web.zoom.us/j/123456789"
    assert _one(geo=None, description=_described(zoom)).is_online is True
    assert _one(geo=None, location="https://meet.google.com/abc-defg-hij").is_online is True
    assert _one(geo=None, description=_described("Join https://teams.microsoft.com/l/x")).is_online
    # A pin means a place, whatever the description links to.
    assert _one(geo="12.9716;77.5946", description=_described(zoom)).is_online is False
    # No GEO and no meeting link is not online: the feed does not say.
    assert _one(geo=None).is_online is False
    assert (
        _one(geo=None, description=_described("See notzoom.us/x and zoom.us.example")).is_online
        is False
    )


def test_a_feed_over_the_cap_reads_as_empty() -> None:
    filler = "X-FILLER:" + "a" * 1000 + "\r\n"
    data = _feed(_vevent()) + (filler * 1100).encode()
    assert len(data) > MAX_BYTES
    assert parse_feed(data) == []


def test_garbage_is_an_empty_list() -> None:
    assert parse_feed(b"") == []
    assert parse_feed(b"<html>not a calendar</html>") == []


def test_parsing_the_fixture_twice_gives_the_same_events() -> None:
    data = DISCOVER.read_bytes()
    assert parse_feed(data) == parse_feed(data)


# ── the bounded fetch ────────────────────────────────────────────────────────


def _client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport)


def test_http_limits_are_the_documented_ones() -> None:
    assert MAX_BYTES == 1_048_576
    assert TIMEOUT_S == 15.0
    assert USER_AGENT == "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"


async def test_get_bounded_returns_the_body_and_identifies_itself() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"BEGIN:VCALENDAR")

    async with _client(httpx.MockTransport(handler)) as client:
        body = await get_bounded(client, "https://api.lu.ma/ics/get?entity=discover&id=x")
    assert body == b"BEGIN:VCALENDAR"
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].extensions["timeout"]["read"] == TIMEOUT_S


async def test_get_bounded_keeps_caller_headers_but_never_replaces_the_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    async with _client(httpx.MockTransport(handler)) as client:
        await get_bounded(
            client,
            "https://example.test/x",
            headers={"Authorization": "Bearer test-token", "user-agent": "other/1.0"},
        )
    assert seen[0].headers["Authorization"] == "Bearer test-token"
    assert seen[0].headers.get_list("User-Agent") == [USER_AGENT]


@pytest.mark.parametrize("status", [301, 302, 404, 429, 500, 503])
async def test_get_bounded_raises_on_a_non_2xx(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Location": "https://example.test/elsewhere"})

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPError):
            await get_bounded(client, "https://example.test/x")


async def test_get_bounded_does_not_follow_redirects_unless_asked() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://example.test/end"})
        return httpx.Response(200, content=b"landed")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_bounded(client, "https://example.test/start")
        assert seen == ["https://example.test/start"]
        followed = await get_bounded(client, "https://example.test/start", follow_redirects=True)
    assert followed == b"landed"


class _Chunks(httpx.AsyncByteStream):
    """A body of ``count`` chunks that records how many were pulled."""

    def __init__(self, count: int, size: int) -> None:
        self.count, self.size, self.pulled = count, size, 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.pulled += 1
            yield b"x" * self.size


async def test_get_bounded_stops_reading_past_the_cap() -> None:
    stream = _Chunks(count=100, size=64 * 1024)  # 6.25 MB offered

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="larger than"):
            await get_bounded(client, "https://example.test/big")
    assert stream.pulled <= MAX_BYTES // (64 * 1024) + 1


async def test_get_bounded_accepts_a_body_of_exactly_the_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * MAX_BYTES)

    async with _client(httpx.MockTransport(handler)) as client:
        assert len(await get_bounded(client, "https://example.test/edge")) == MAX_BYTES


async def test_get_bounded_refuses_one_byte_past_the_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_BYTES + 1))

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await get_bounded(client, "https://example.test/edge")


async def test_fetch_feed_requests_exactly_the_feed_and_follows_nothing() -> None:
    seen: list[httpx.Request] = []
    url = feed_url("discover", DISCOVER_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, content=b"BEGIN:VCALENDAR\r\n")
        return httpx.Response(302, headers={"Location": "https://luma.com/bengaluru"})

    async with _client(httpx.MockTransport(handler)) as client:
        assert await fetch_feed(client, url) == b"BEGIN:VCALENDAR\r\n"
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_feed(client, url)
    assert [str(r.url) for r in seen] == [url, url]
    assert all(r.headers["User-Agent"] == USER_AGENT for r in seen)
    assert all(r.url.host == "api.lu.ma" and r.url.path == "/ics/get" for r in seen)


@pytest.mark.parametrize(
    "url",
    [
        "https://luma.com/bengaluru",
        "https://lu.ma/bengaluru",
        "https://api.lu.ma/ics/get",
        "https://api.lu.ma/ics/get?entity=discover",
        f"https://api.lu.ma/ics/get?entity=discover&id={DISCOVER_ID}&extra=1",
        f"https://api.lu.ma/ics/get?id={DISCOVER_ID}&entity=discover",
        f"http://api.lu.ma/ics/get?entity=discover&id={DISCOVER_ID}",
        f"https://api.lu.ma.evil.example/ics/get?entity=discover&id={DISCOVER_ID}",
        f"https://user@api.lu.ma/ics/get?entity=discover&id={DISCOVER_ID}",
        "https://api.lu.ma/ics/get?entity=calendar&id=cal-x/../y",
        "https://api.lu.ma/ics/get?entity=event&id=cal-abcdef123",
        f"https://api.lu.ma/ics/get?entity=discover&id={DISCOVER_ID}#frag",
        "https://api.lu.ma/api/event/get?api_id=evt-abc",
    ],
)
async def test_fetch_feed_refuses_every_other_address(url: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await fetch_feed(client, url)
    assert calls == []
