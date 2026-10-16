"""
tests/test_event_eventbrite.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Eventbrite, read only through its official API v3.

The two fixtures under ``tests/fixtures/events/`` are written by hand from the
documented v3 response shapes (an event with ``expand=venue,organizer,format``
and a paginated ``organizers/{id}/events`` list), because recording them needs
a personal token. Everything in them is fictional. The parser is also held to
being total: whatever a response holds, it returns facts or ``None``, never an
exception.
"""

from __future__ import annotations

import copy
import json
import logging
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from config.settings import Settings
from domain.event_facts import EventFacts
from services.events import eventbrite
from services.events.eventbrite import (
    API,
    EXPAND,
    get_event,
    organizer_events,
    parse_event,
    parse_event_list,
    venue_events,
)
from services.events.http import USER_AGENT

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
TOKEN = "TESTTOKEN0123456789ABCDEF"


def _load(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


EVENT = _load("eventbrite_event.json")
LIST = _load("eventbrite_organizer_events.json")


def _event(**changes: Any) -> dict[str, Any]:
    """The fixture event with top-level keys replaced; a value of ``...`` removes the key."""
    payload = copy.deepcopy(EVENT)
    for key, value in changes.items():
        if value is ...:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


def _facts(**changes: Any) -> EventFacts:
    facts = parse_event(_event(**changes))
    assert facts is not None
    return facts


# ── the parser on the fixtures ───────────────────────────────────────────────


def test_parse_event_fixture() -> None:
    facts = parse_event(EVENT)
    assert facts is not None
    assert facts.source == "eventbrite"
    assert facts.external_id.isdigit()
    host = urlsplit(facts.url).hostname or ""
    assert host.endswith("eventbrite.com") or host.endswith("eventbrite.co.in")
    assert facts.starts_at.tzinfo is not None
    assert facts.taxonomy["category"] == "102"


def test_parse_event_reads_every_field_the_brief_names() -> None:
    facts = parse_event(EVENT)
    assert facts == EventFacts(
        source="eventbrite",
        external_id="712345678901",
        url="https://www.eventbrite.com/e/async-rust-in-production-bengaluru-meetup-tickets-712345678901",
        title="Async Rust in Production: Bengaluru Meetup",
        starts_at=datetime(2026, 11, 21, 12, 30, tzinfo=UTC),
        ends_at=datetime(2026, 11, 21, 15, 0, tzinfo=UTC),
        snippet="Two talks on running async Rust services in production, then open questions and networking.",
        venue_name="Quill Commons Koramangala",
        address="14 Quill Lane, 5th Block, Koramangala, Bengaluru, KA 560095",
        locality="Bengaluru",
        lat=12.9352,
        lng=77.6245,
        is_online=False,
        is_free=True,
        organiser_name="Quill Tech Meetups",
        organiser_url="https://quilltechmeetups.example",
        timezone="Asia/Kolkata",
        taxonomy={"category": "102", "subcategory": "2004", "format": "2"},
    )
    assert facts is not None and isinstance(facts.lat, float) and isinstance(facts.lng, float)


def test_the_comment_key_is_ignored() -> None:
    assert "_comment" in EVENT and "_comment" in LIST
    assert parse_event(EVENT) == parse_event({k: v for k, v in EVENT.items() if k != "_comment"})
    assert parse_event_list(LIST) == parse_event_list(
        {k: v for k, v in LIST.items() if k != "_comment"}
    )


def test_parse_event_list_fixture() -> None:
    events, continuation = parse_event_list(LIST)
    assert continuation == "eyJwYWdlIjogMn0"
    assert [e.external_id for e in events] == ["712345678901", "712345678902", "712345678903"]
    assert events[0] == parse_event(EVENT)  # a list entry reads like the single event
    online, cancelled = events[1], events[2]
    assert online.is_online and online.is_free is False
    assert online.venue_name is None and online.lat is None and online.lng is None
    assert online.timezone == "Asia/Kolkata"
    assert cancelled.is_cancelled
    assert cancelled.taxonomy == {
        "category": "102",
        "format": "10",
    }  # the null subcategory is ignored
    assert not events[0].is_cancelled and not online.is_cancelled


def test_parsing_twice_gives_the_same_events() -> None:
    assert parse_event_list(LIST) == parse_event_list(LIST)


# ── cancelled, online, free ──────────────────────────────────────────────────


def test_cancelled() -> None:
    assert _facts(status="canceled").is_cancelled is True
    assert _facts(status="live").is_cancelled is False
    assert _facts(status=...).is_cancelled is False
    # only the API's own word counts: a draft, an ended event or free text is not a cancellation
    for status in (
        "draft",
        "ended",
        "completed",
        "started",
        "cancelled",
        "CANCELED",
        None,
        5,
        True,
    ):
        assert _facts(status=status).is_cancelled is False, status


def test_online_and_free_are_taken_only_from_a_true_boolean() -> None:
    assert _facts(online_event=True).is_online is True
    for odd in ("true", 1, None, "yes", [True]):
        assert _facts(online_event=odd).is_online is False, odd
    assert _facts(is_free=False).is_free is False
    for odd in ("true", 1, None, 0):
        assert _facts(is_free=odd).is_free is None, odd
    assert _facts(is_free=...).is_free is None


# ── times ────────────────────────────────────────────────────────────────────


def test_starts_at_is_the_utc_instant_not_the_local_wall_clock() -> None:
    facts = _facts(
        start={
            "timezone": "Asia/Kolkata",
            "local": "2026-11-21T18:00:00",
            "utc": "2026-11-21T12:30:00Z",
        }
    )
    assert facts.starts_at == datetime(2026, 11, 21, 12, 30, tzinfo=UTC)
    assert facts.starts_at.utcoffset() == datetime(2026, 1, 1, tzinfo=UTC).utcoffset()


def test_an_offset_utc_value_is_normalised_to_utc() -> None:
    facts = _facts(start={"timezone": "Asia/Kolkata", "utc": "2026-11-21T18:00:00+05:30"})
    assert facts.starts_at == datetime(2026, 11, 21, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "start",
    [
        None,
        {},
        "2026-11-21T12:30:00Z",  # a bare string, not the start object
        {"timezone": "Asia/Kolkata", "local": "2026-11-21T18:00:00"},  # no utc: never guess a zone
        {"utc": None},
        {"utc": 1763728200},
        {"utc": "2026-11-21T12:30:00"},  # naive
        {"utc": "2026-11-21"},  # a bare date is not midnight
        {"utc": "2026-11-21T12:30"},
        {"utc": "20261121T123000Z"},
        {"utc": "2026-13-21T12:30:00Z"},
        {"utc": "2026-11-31T12:30:00Z"},
        {"utc": "2026-11-21T25:30:00Z"},
        {"utc": "2026-11-21T12:30:60Z"},
        {"utc": "2026-11-21T12:30:00+99:00"},
        {"utc": "2026-11-21T12:30:00Z\n"},
        {"utc": "2026-11-21T12:30:00Z" + " " * 40},
        {"utc": "٢٠٢٦-١١-٢١T١٢:٣٠:٠٠Z"},  # Arabic-Indic digits
        {"utc": "1999-12-31T23:59:59Z"},
        {"utc": "2101-01-01T00:00:00Z"},
        {"utc": "0001-01-01T00:00:00Z"},
        {"utc": "9999-12-31T23:59:59Z"},
        {"utc": "9999-12-31T23:00:00-05:00"},  # would overflow once converted to UTC
        {"utc": "2100-12-31T23:00:00-05:00"},  # 2101 in UTC
        {"utc": "2000-01-01T00:00:00+05:30"},  # 1999 in UTC
    ],
)
def test_a_start_that_is_not_a_clean_aware_time_in_range_drops_the_event(start: Any) -> None:
    assert parse_event(_event(start=start)) is None


def test_the_edges_of_the_allowed_years_are_kept() -> None:
    low = _facts(start={"utc": "2000-01-01T00:00:00Z"})
    high = _facts(start={"utc": "2100-12-31T23:59:59Z"}, end=...)
    assert low.starts_at.year == 2000 and high.starts_at.year == 2100


def test_an_unusable_end_is_none_and_keeps_the_event() -> None:
    for end in (
        None,
        {},
        {"utc": "nonsense"},
        {"utc": "2026-11-21T15:00:00"},  # naive
        {"utc": "2026-11-21T12:30:00Z"},  # not after the start
        {"utc": "2026-11-21T10:00:00Z"},  # before the start
        {"utc": "2101-01-01T00:00:00Z"},
        "2026-11-21T15:00:00Z",
    ):
        facts = _facts(end=end)
        assert facts.ends_at is None, end
        assert facts.starts_at == datetime(2026, 11, 21, 12, 30, tzinfo=UTC)
    assert _facts(end=...).ends_at is None


def test_timezone_is_the_start_zone_name_or_none() -> None:
    assert _facts().timezone == "Asia/Kolkata"
    for odd in ("", "Asia/Kolkata\n", "../etc", "a b", 5, None, "x" * 80, "Asia/<b>"):
        start = {"timezone": odd, "utc": "2026-11-21T12:30:00Z"}
        assert _facts(start=start).timezone is None, odd


# ── identity and url ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ident", [None, "", "12a", " 123", "123 ", "12.5", "-5", "١٢٣", "1" * 33, [], {}]
)
def test_an_event_without_a_digit_only_id_is_dropped(ident: Any) -> None:
    assert parse_event(_event(id=ident)) is None
    assert parse_event(_event(id=...)) is None


def test_a_numeric_id_is_stringified_but_a_boolean_is_not_an_id() -> None:
    assert _facts(id=712345678901).external_id == "712345678901"
    assert parse_event(_event(id=True)) is None


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://www.eventbrite.com/e/x-1",  # not https
        "https://eventbrite.com.evil.io/e/x-1",
        "https://www.eventbrite.com.evil.io/e/x-1",
        "https://evil-eventbrite.com/e/x-1",
        "https://notwww.eventbrite.com/e/x-1",
        "https://evil.io/www.eventbrite.com/e/x-1",
        "https://www.eventbrite.com@evil.io/e/x-1",
        "https://user:pw@www.eventbrite.com/e/x-1",
        "https://www.eventbrite.com:8443/e/x-1",
        "https://www.eventbrite.com./e/x-1",
        "https://ｗｗｗ.eventbrite.com/e/x-1",  # full-width look-alike letters
        "https://www.eventbrite.com%2eevil.io/e/x-1",
        "https://www.еventbrite.com/e/x-1",  # Cyrillic e
        "https://www.eventbrite.org/e/x-1",
        "https://www.eventbrite.co.uk/e/x-1",
        "https://www.eventbriteapi.com/v3/events/1/",
        "//www.eventbrite.com/e/x-1",
        "www.eventbrite.com/e/x-1",
        "javascript:alert(1)",
        "https://www.eventbrite.com/e/x 1",
        "https://www.eventbrite.com/e/x\n1",
        "https://www.eventbrite.com\\@evil.io/e/x-1",
        "https://[::1/e/x-1",
        "https://www.eventbrite.com/e/" + "x" * 3000,
        12345,
        ["https://www.eventbrite.com/e/x-1"],
    ],
)
def test_an_event_url_that_is_not_https_on_eventbrite_drops_the_event(url: Any) -> None:
    assert parse_event(_event(url=url)) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.eventbrite.com/e/x-tickets-1",
        "https://www.eventbrite.co.in/e/x-tickets-1",
        "https://eventbrite.com/e/x-tickets-1",
        "https://WWW.EventBrite.COM/e/x-tickets-1",
        "https://www.eventbrite.com:443/e/x-tickets-1?aff=oddtdtcreator",
    ],
)
def test_https_urls_on_the_eventbrite_hosts_are_kept(url: str) -> None:
    assert _facts(url=url).url == url


# ── text ─────────────────────────────────────────────────────────────────────


def test_title_is_required_and_flattened() -> None:
    assert parse_event(_event(name=None)) is None
    assert parse_event(_event(name={"text": None})) is None
    assert parse_event(_event(name={"text": "   "})) is None
    assert parse_event(_event(name="A plain string")) is None
    assert (
        _facts(name={"text": "  Rust\n  <b>night</b> &amp; talks  "}).title == "Rust night & talks"
    )
    assert len(_facts(name={"text": "w " * 400}).title) <= eventbrite.TITLE_MAX


def test_snippet_is_the_summary_flattened_and_bounded() -> None:
    assert _facts(summary="<p>One</p><p>Two</p>\n\n three").snippet == "One Two three"
    long = _facts(summary="word " * 400).snippet
    assert long is not None and len(long) <= eventbrite.SNIPPET_MAX and long.endswith("…")
    for odd in (None, "", "   ", 5, {"text": "x"}, ...):
        assert _facts(summary=odd).snippet is None, odd


def test_a_missing_summary_is_not_filled_from_the_description() -> None:
    facts = _facts(summary=...)
    assert facts.snippet is None  # the snippet is the summary field only


# ── venue ────────────────────────────────────────────────────────────────────


def test_venue_fields_and_coordinates() -> None:
    facts = _facts()
    assert (facts.venue_name, facts.locality) == ("Quill Commons Koramangala", "Bengaluru")
    assert facts.address == "14 Quill Lane, 5th Block, Koramangala, Bengaluru, KA 560095"
    assert (facts.lat, facts.lng) == (12.9352, 77.6245)
    assert facts.address_withheld is False and facts.area is None


def test_coordinates_fall_back_to_the_address_block() -> None:
    venue = copy.deepcopy(EVENT["venue"])
    del venue["latitude"], venue["longitude"]
    facts = _facts(venue=venue)
    assert (facts.lat, facts.lng) == (12.9352, 77.6245)


@pytest.mark.parametrize(
    "lat,lng",
    [
        ("abc", "77.6"),
        ("12.9", "east"),
        ("", ""),
        (None, None),
        ("12.9", None),
        (None, "77.6"),
        ("nan", "77.6"),
        ("NaN", "NaN"),
        ("inf", "77.6"),
        ("-Infinity", "77.6"),
        ("1e999", "77.6"),
        ("1e1", "77.6"),
        ("12,9", "77,6"),
        ("12.9 N", "77.6 E"),
        ("91.0", "77.6"),  # latitude out of range
        ("12.9", "181.0"),  # longitude out of range
        ("-90.5", "77.6"),
        ("+12.9", "77.6"),
        ("١٢.٩", "٧٧.٦"),  # Arabic-Indic digits
        (True, False),
        ([12.9], [77.6]),
        ({"v": 1}, {"v": 2}),
        (10**400, 10**400),
        (float("nan"), float("nan")),
        (float("inf"), 1.0),
        ("1" * 400, "1" * 400),
    ],
)
def test_a_coordinate_that_is_not_a_finite_number_in_range_is_none(lat: Any, lng: Any) -> None:
    venue = copy.deepcopy(EVENT["venue"])
    venue["latitude"], venue["longitude"] = lat, lng
    venue["address"]["latitude"], venue["address"]["longitude"] = lat, lng
    facts = _facts(venue=venue)
    assert facts.lat is None and facts.lng is None
    assert facts.venue_name == "Quill Commons Koramangala"  # the rest of the event survives


def test_coordinates_come_as_a_pair_or_not_at_all() -> None:
    venue = copy.deepcopy(EVENT["venue"])
    venue["longitude"] = "not-a-number"
    venue["address"].pop("latitude")
    venue["address"].pop("longitude")
    facts = _facts(venue=venue)
    assert facts.lat is None and facts.lng is None


def test_numeric_coordinates_are_accepted_and_negative_ones_too() -> None:
    venue = copy.deepcopy(EVENT["venue"])
    venue["latitude"], venue["longitude"] = 12.5, -77
    facts = _facts(venue=venue)
    assert (facts.lat, facts.lng) == (12.5, -77.0)
    assert isinstance(facts.lng, float)


@pytest.mark.parametrize(
    "venue", [None, {}, [], "Quill Hall", 5, {"address": None}, {"address": "x"}]
)
def test_a_missing_or_odd_venue_leaves_the_place_empty(venue: Any) -> None:
    facts = _facts(venue=venue)
    assert facts.venue_name is None and facts.address is None and facts.locality is None
    assert facts.lat is None and facts.lng is None


def test_venue_text_is_flattened_and_bounded() -> None:
    venue = copy.deepcopy(EVENT["venue"])
    venue["name"] = "<i>Quill</i>   Hall\n" + "x" * 500
    venue["address"]["localized_address_display"] = "y " * 400
    venue["address"]["city"] = "z" * 300
    facts = _facts(venue=venue)
    assert facts.venue_name is not None and len(facts.venue_name) <= eventbrite.VENUE_MAX
    assert facts.venue_name.startswith("Quill Hall")
    assert facts.address is not None and len(facts.address) <= eventbrite.ADDRESS_MAX
    assert facts.locality is not None and len(facts.locality) <= eventbrite.LOCALITY_MAX


# ── organiser ────────────────────────────────────────────────────────────────


def test_organiser_url_prefers_the_website_then_the_eventbrite_page() -> None:
    organizer = copy.deepcopy(EVENT["organizer"])
    assert _facts(organizer=organizer).organiser_url == "https://quilltechmeetups.example"
    organizer["website"] = None
    assert (
        _facts(organizer=organizer).organiser_url
        == "https://www.eventbrite.com/o/quill-tech-meetups-98765432101"
    )
    del organizer["website"]
    assert _facts(organizer=organizer).organiser_url == organizer["url"]


@pytest.mark.parametrize(
    "website",
    [
        "javascript:alert(1)",
        "ftp://quilltechmeetups.example",
        "quilltechmeetups.example",
        "https://",
        "https://localhost/",
        "https://user:pw@quilltechmeetups.example/",
        "https://quilltech meetups.example/",
        "https://quilltechmeetups.example/" + "p" * 3000,
        "",
        5,
        ["https://quilltechmeetups.example"],
    ],
)
def test_an_unusable_organiser_website_falls_back_to_the_organiser_page(website: Any) -> None:
    organizer = copy.deepcopy(EVENT["organizer"])
    organizer["website"] = website
    facts = _facts(organizer=organizer)
    assert facts.organiser_url == organizer["url"]
    assert facts.organiser_name == "Quill Tech Meetups"


def test_a_missing_organiser_leaves_name_and_url_empty() -> None:
    odd_organizers: list[Any] = [None, {}, [], "Quill", 5]
    for organizer in odd_organizers:
        facts = _facts(organizer=organizer)
        assert facts.organiser_name is None and facts.organiser_url is None


def test_hosts_are_never_invented() -> None:
    assert _facts().hosts == ()


# ── taxonomy ─────────────────────────────────────────────────────────────────


def test_taxonomy_holds_only_the_ids_the_payload_gave() -> None:
    assert _facts().taxonomy == {"category": "102", "subcategory": "2004", "format": "2"}
    only_category = _facts(subcategory_id=None, format_id=..., format=...)
    assert only_category.taxonomy == {"category": "102"}
    assert _facts(category_id=..., subcategory_id=..., format_id=..., format=...).taxonomy == {}


def test_taxonomy_ids_are_digit_strings() -> None:
    assert _facts(category_id=102).taxonomy["category"] == "102"
    odd_ids: list[Any] = ["10a", "", " 102", "١٠٢", True, -1, 1.5, [], {"id": "102"}, "1" * 20]
    for odd in odd_ids:
        assert "category" not in _facts(category_id=odd).taxonomy, odd


def test_the_format_falls_back_to_the_expanded_format_object() -> None:
    facts = _facts(format_id=..., format={"id": "9", "name": "Class, Training, or Workshop"})
    assert facts.taxonomy["format"] == "9"
    assert _facts(format_id="2", format={"id": "9"}).taxonomy["format"] == "2"


# ── totality: nothing the API sends can raise ────────────────────────────────

_HOSTILE = [
    None,
    0,
    "",
    "text",
    b"bytes",
    [],
    [1, 2],
    {},
    {"id": "1"},
    {"id": {"nested": [1, 2, 3]}, "name": [], "start": "x", "end": 5, "url": {}, "venue": []},
    {"id": "1", "name": {"text": {"x": 1}}, "start": {"utc": {"y": 1}}, "url": ["a"]},
    {
        "id": "1",
        "url": "https://www.eventbrite.com/e/x-1",
        "name": {"text": "x"},
        "start": {"utc": [1]},
    },
]


@pytest.mark.parametrize("payload", _HOSTILE, ids=lambda p: repr(p)[:40])
def test_parse_event_is_total_on_hostile_input(payload: Any) -> None:
    assert parse_event(payload) is None


def test_parse_event_survives_every_field_being_the_wrong_type() -> None:
    wrong_values: list[Any] = [None, True, 0, 1.5, "", "x", [], {}, [[]], {"a": {"b": []}}]
    for key in EVENT:
        for value in wrong_values:
            parse_event({**EVENT, key: value})  # must not raise
    for key in EVENT["venue"]:
        for value in wrong_values:
            parse_event({**EVENT, "venue": {**EVENT["venue"], key: value}})
    for key in EVENT["venue"]["address"]:
        for value in wrong_values:
            address = {**EVENT["venue"]["address"], key: value}
            parse_event({**EVENT, "venue": {**EVENT["venue"], "address": address}})
    for key in EVENT["organizer"]:
        for value in wrong_values:
            parse_event({**EVENT, "organizer": {**EVENT["organizer"], key: value}})


def test_parse_event_survives_a_very_deep_payload() -> None:
    deep: Any = "leaf"
    for _ in range(5000):
        deep = {"x": deep}
    assert parse_event({**EVENT, "name": deep, "venue": deep, "organizer": deep}) is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        5,
        "x",
        [],
        [{"id": "1"}],
        {},
        {"events": None},
        {"events": "x"},
        {"events": {}},
        {"pagination": 5},
    ],
    ids=repr,
)
def test_parse_event_list_is_total_on_hostile_input(payload: Any) -> None:
    assert parse_event_list(payload) == ([], None)


def test_parse_event_list_skips_events_it_cannot_read() -> None:
    payload: dict[str, Any] = {
        "events": [None, 5, "x", [], {"id": "x"}, copy.deepcopy(EVENT), {"id": "9", "url": 5}],
        "pagination": {},
    }
    events, continuation = parse_event_list(payload)
    assert [e.external_id for e in events] == ["712345678901"]
    assert continuation is None


def test_parse_event_list_keeps_an_id_once() -> None:
    payload = {"events": [copy.deepcopy(EVENT), copy.deepcopy(EVENT)]}
    assert len(parse_event_list(payload)[0]) == 1


# ── pagination ───────────────────────────────────────────────────────────────


def test_the_continuation_is_returned_only_while_there_are_more_items() -> None:
    page = copy.deepcopy(LIST)
    assert parse_event_list(page)[1] == "eyJwYWdlIjogMn0"
    page["pagination"]["has_more_items"] = False
    assert parse_event_list(page)[1] is None
    page["pagination"]["has_more_items"] = "true"
    assert parse_event_list(page)[1] is None
    del page["pagination"]["has_more_items"]
    assert parse_event_list(page)[1] is None
    del page["pagination"]
    assert parse_event_list(page)[1] is None


@pytest.mark.parametrize(
    "token",
    ["", "a b", "a&b=c", "a#b", "a?b", "../x", "a%20b", "a\nb", "x" * 600, 5, ["a"], None, "é"],
)
def test_a_continuation_that_is_not_a_plain_token_is_not_followed(token: Any) -> None:
    page = copy.deepcopy(LIST)
    page["pagination"]["continuation"] = token
    events, continuation = parse_event_list(page)
    assert continuation is None
    assert len(events) == 3  # the page itself is still read


# ── the requests ─────────────────────────────────────────────────────────────


class _Recorder:
    """A MockTransport that records every request and answers from ``respond``."""

    def __init__(self, body: Any = None, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._body = {"id": "1"} if body is None else body
        self._status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def test_the_module_constants_are_the_documented_ones() -> None:
    assert API == "https://www.eventbriteapi.com/v3"
    assert EXPAND == "venue,organizer,format"


async def test_get_event_requests_the_event_with_the_expansions() -> None:
    recorder = _Recorder(EVENT)
    async with recorder.client() as client:
        payload = await get_event(client, TOKEN, "712345678901")
    assert payload == EVENT
    (request,) = recorder.requests
    assert request.method == "GET"
    assert str(request.url) == f"{API}/events/712345678901/?expand={EXPAND}"


async def test_organizer_events_requests_live_events_in_start_order() -> None:
    recorder = _Recorder(LIST)
    async with recorder.client() as client:
        payload = await organizer_events(client, TOKEN, "98765432101")
    assert payload == LIST
    (request,) = recorder.requests
    parts = urlsplit(str(request.url))
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{API}/organizers/98765432101/events/"
    assert parse_qs(parts.query) == {
        "status": ["live"],
        "order_by": ["start_asc"],
        "expand": [EXPAND],
    }


async def test_venue_events_requests_the_venue_endpoint() -> None:
    recorder = _Recorder(LIST)
    async with recorder.client() as client:
        await venue_events(client, TOKEN, "45678901234")
    (request,) = recorder.requests
    assert request.url.path == "/v3/venues/45678901234/events/"
    assert parse_qs(request.url.query.decode())["expand"] == [EXPAND]


async def test_a_continuation_is_url_encoded_and_sent_as_one_parameter() -> None:
    recorder = _Recorder(LIST)
    async with recorder.client() as client:
        await organizer_events(client, TOKEN, "98765432101", "ab+cd/ef==")
        await venue_events(client, TOKEN, "45678901234", "eyJwYWdlIjogMn0")
    first, second = recorder.requests
    assert parse_qs(first.url.query.decode())["continuation"] == ["ab+cd/ef=="]
    assert "continuation=ab%2Bcd%2Fef%3D%3D" in str(first.url)
    assert parse_qs(second.url.query.decode())["continuation"] == ["eyJwYWdlIjogMn0"]


@pytest.mark.parametrize(
    "ident",
    [
        "12a",
        "",
        " 1",
        "1 ",
        "12\n",
        "1/2",
        "../1",
        "1?x=y",
        "1#",
        "١٢٣",
        "²",
        "1" * 33,
        "-1",
        "1.0",
    ],
)
async def test_get_event_rejects_non_digit_id(ident: str) -> None:
    recorder = _Recorder()
    async with recorder.client() as client:
        with pytest.raises(ValueError):
            await get_event(client, TOKEN, ident)
    assert recorder.requests == []


async def test_get_event_rejects_an_id_that_is_not_a_string() -> None:
    recorder = _Recorder()
    async with recorder.client() as client:
        for odd in (123, None, b"123", ["1"]):
            with pytest.raises(ValueError):
                await get_event(client, TOKEN, odd)  # type: ignore[arg-type]
    assert recorder.requests == []


@pytest.mark.parametrize("fetch", [organizer_events, venue_events], ids=["organizer", "venue"])
@pytest.mark.parametrize("ident", ["12a", "", "1/2", "../1", "1 2", "١٢٣", "1" * 33])
async def test_list_endpoints_reject_a_non_digit_id(fetch: Any, ident: str) -> None:
    recorder = _Recorder()
    async with recorder.client() as client:
        with pytest.raises(ValueError):
            await fetch(client, TOKEN, ident)
    assert recorder.requests == []


@pytest.mark.parametrize("fetch", [organizer_events, venue_events], ids=["organizer", "venue"])
@pytest.mark.parametrize(
    "token",
    ["", "a b", "a&b=c", "a#b", "a?b", "../x", "a%20b", "a\nb", "x" * 600, "é", "a=b&expand=x"],
)
async def test_list_endpoints_reject_an_unsafe_continuation(fetch: Any, token: str) -> None:
    recorder = _Recorder()
    async with recorder.client() as client:
        with pytest.raises(ValueError):
            await fetch(client, TOKEN, "98765432101", token)
    assert recorder.requests == []


async def test_a_token_that_is_not_a_plain_token_is_rejected_before_any_request() -> None:
    recorder = _Recorder()
    bad_tokens: list[Any] = [
        "",
        "has space",
        "line\nbreak",
        "crlf\r\nX-Evil: 1",
        "é",
        "t" * 600,
        None,
        5,
    ]
    async with recorder.client() as client:
        for bad in bad_tokens:
            with pytest.raises(ValueError) as caught:
                await get_event(client, bad, "712345678901")
            if isinstance(bad, str) and bad:
                assert bad not in str(caught.value)
    assert recorder.requests == []


async def test_no_page_request() -> None:
    """Every request goes to the API host with a bearer token, and nothing else is read."""
    recorder = _Recorder(LIST)
    async with recorder.client() as client:
        await get_event(client, TOKEN, "712345678901")
        await organizer_events(client, TOKEN, "98765432101")
        await organizer_events(client, TOKEN, "98765432101", "eyJwYWdlIjogMn0")
        await venue_events(client, TOKEN, "45678901234")
        await venue_events(client, TOKEN, "45678901234", "eyJwYWdlIjogMn0")
    assert len(recorder.requests) == 5
    for request in recorder.requests:
        assert request.url.scheme == "https"
        assert request.url.host == "www.eventbriteapi.com"
        assert request.url.port is None
        assert request.url.path.startswith("/v3/")
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.headers["Accept"] == "application/json"


async def test_the_token_is_only_ever_in_the_authorization_header() -> None:
    recorder = _Recorder(LIST)
    async with recorder.client() as client:
        await get_event(client, TOKEN, "712345678901")
        await organizer_events(client, TOKEN, "98765432101", "eyJwYWdlIjogMn0")
    for request in recorder.requests:
        assert TOKEN not in str(request.url)
        assert TOKEN not in request.content.decode()
        assert [name for name, value in request.headers.items() if TOKEN in value] == [
            "authorization"
        ]


@pytest.mark.parametrize(
    "client_follows", [False, True], ids=["default-client", "following-client"]
)
async def test_a_redirect_is_not_followed(client_follows: bool) -> None:
    """Not even when the caller's client follows redirects by default."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "www.eventbriteapi.com":
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
        return httpx.Response(200, json={"id": "1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=client_follows) as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await get_event(client, TOKEN, "712345678901")
    assert [r.url.host for r in seen] == ["www.eventbriteapi.com"]  # the token went nowhere else
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_a_failed_response_raises_without_the_token_in_the_message(status: int) -> None:
    recorder = _Recorder({"error": "x"}, status=status)
    async with recorder.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await get_event(client, TOKEN, "712345678901")
    text = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in str(caught.value) and TOKEN not in repr(caught.value) and TOKEN not in text


async def test_the_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    recorder = _Recorder(LIST)
    with caplog.at_level(logging.DEBUG):
        async with recorder.client() as client:
            await get_event(client, TOKEN, "712345678901")
            await organizer_events(client, TOKEN, "98765432101", "eyJwYWdlIjogMn0")
            with pytest.raises(ValueError):
                await get_event(client, TOKEN, "12a")
    assert TOKEN not in caplog.text


async def test_a_response_that_is_not_a_json_object_is_a_value_error() -> None:
    bodies = [b"", b"<html>nope</html>", b"[1, 2]", b"null", b'"text"', b"123", b"\xff\xfe\x00bad"]
    for body in bodies:

        def handler(request: httpx.Request, body: bytes = body) -> httpx.Response:
            return httpx.Response(200, content=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError) as caught:
                await get_event(client, TOKEN, "712345678901")
        assert TOKEN not in str(caught.value)
        assert "nope" not in str(caught.value)  # the body is never echoed


async def test_a_deeply_nested_response_is_a_value_error_not_a_recursion_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await get_event(client, TOKEN, "712345678901")


async def test_a_response_over_the_size_cap_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"x": "' + b"a" * 1_100_000 + b'"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError) as caught:
            await organizer_events(client, TOKEN, "98765432101")
    assert TOKEN not in str(caught.value)


async def test_the_fixtures_round_trip_through_the_fetchers() -> None:
    """What the client returns is what the parser reads: the two halves agree."""
    recorder = _Recorder(LIST)
    async with recorder.client() as client:
        events, continuation = parse_event_list(
            await organizer_events(client, TOKEN, "98765432101")
        )
        assert continuation is not None
        assert len(events) == 3


# ── settings ─────────────────────────────────────────────────────────────────

_SETTINGS_ENV = {
    "GROQ_API_KEY": "test_key",
    "JWT_SECRET_KEY": "ci_secret_key_at_least_32_characters_long",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.delenv("EVENTBRITE_TOKEN", raising=False)
    for name, value in {**_SETTINGS_ENV, **env}.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_the_token_setting_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch).eventbrite_token is None


def test_the_token_setting_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, EVENTBRITE_TOKEN=TOKEN).eventbrite_token == TOKEN


def test_a_blank_token_means_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``EVENTBRITE_TOKEN=`` in .env.example must read as unset, not as an empty token."""
    assert _settings(monkeypatch, EVENTBRITE_TOKEN="").eventbrite_token is None
    assert _settings(monkeypatch, EVENTBRITE_TOKEN="   ").eventbrite_token is None


def test_the_token_does_not_appear_in_the_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, EVENTBRITE_TOKEN=TOKEN)
    assert TOKEN not in repr(settings) and TOKEN not in str(settings)
