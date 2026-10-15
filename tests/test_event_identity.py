"""
tests/test_event_identity.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Event identity (is this the same event as that one?), the company names an
event mentions, and the strict patterns a pasted link must match. No database,
no network: every function under test is pure.
"""

from __future__ import annotations

import socket
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from domain.event_facts import EventFacts
from domain.event_identity import (
    CompanyCandidate,
    company_candidates,
    same_event,
    title_tokens,
)
from domain.event_urls import MAX_URL_LENGTH, PastedEvent, parse_event_url

NINE_THIRTY = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
HSR = (12.9163, 77.6463)
# 0.0027 degrees of latitude is about 300 m.
HSR_300M_NORTH = (12.9190, 77.6463)
WHITEFIELD = (12.9698, 77.7500)


@dataclass(frozen=True)
class Ev:
    """The least a row needs to be compared: a frozen stand-in, as EventFacts is."""

    title: str
    starts_at: datetime = NINE_THIRTY
    lat: float | None = None
    lng: float | None = None
    area: str | None = None
    is_online: bool = False


def ev(
    title: str,
    starts_at: datetime = NINE_THIRTY,
    *,
    point: tuple[float, float] | None = None,
    area: str | None = None,
    online: bool = False,
) -> Ev:
    lat, lng = point if point is not None else (None, None)
    return Ev(title, starts_at, lat, lng, area, online)


# --- title_tokens -----------------------------------------------------------


def test_title_tokens_ignore_case_spacing_symbols_and_stop_words() -> None:
    assert title_tokens("Swift Bengaluru × Okta") == title_tokens("swift  okta")
    assert title_tokens("swift  okta") == frozenset({"swift", "okta"})


def test_title_tokens_fold_accents_and_drop_punctuation() -> None:
    assert title_tokens("Café: the Rust-lang Meetup, 2026!") == frozenset({"cafe", "rust", "lang"})


def test_title_tokens_of_nothing_but_stop_words_is_empty() -> None:
    assert title_tokens("The Bangalore Meetup 2026 Edition") == frozenset()
    assert title_tokens("") == frozenset()


def test_title_tokens_keep_a_word_that_only_contains_a_stop_word() -> None:
    assert title_tokens("Xenon theatre") == frozenset({"xenon", "theatre"})


# --- same_event -------------------------------------------------------------


def test_same_meetup_on_two_sources_is_one_event() -> None:
    luma = ev("Swift Bengaluru × Okta", NINE_THIRTY, point=HSR)
    eventbrite = ev(
        "Swift Bengaluru Meetup at Okta",
        NINE_THIRTY + timedelta(minutes=15),
        point=HSR_300M_NORTH,
    )
    assert same_event(luma, eventbrite)
    assert same_event(eventbrite, luma)


def test_an_hour_and_a_half_later_is_another_event() -> None:
    luma = ev("Swift Bengaluru × Okta", NINE_THIRTY, point=HSR)
    later = ev(
        "Swift Bengaluru Meetup at Okta", datetime(2026, 10, 20, 11, 0, tzinfo=UTC), point=HSR
    )
    assert not same_event(luma, later)


def test_a_different_talk_at_the_same_time_and_place_is_another_event() -> None:
    luma = ev("Swift Bengaluru × Okta", NINE_THIRTY, point=HSR)
    other = ev("Embedded Swift: From Code to Hardware", NINE_THIRTY, point=HSR)
    assert not same_event(luma, other)


def test_both_online_at_the_same_time_with_the_same_title_is_one_event() -> None:
    one = ev("Intro to Rust", online=True)
    two = ev("Intro to Rust", online=True)
    assert same_event(one, two)


def test_the_45_minute_window_is_inclusive() -> None:
    a = ev("Rust async", NINE_THIRTY, online=True)
    assert same_event(a, ev("Rust async", NINE_THIRTY + timedelta(minutes=45), online=True))
    assert not same_event(a, ev("Rust async", NINE_THIRTY + timedelta(minutes=46), online=True))
    assert same_event(a, ev("Rust async", NINE_THIRTY - timedelta(minutes=45), online=True))


def test_the_same_instant_in_another_offset_is_the_same_time() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    a = ev("Rust async", NINE_THIRTY, online=True)
    b = ev("Rust async", datetime(2026, 10, 20, 15, 0, tzinfo=ist), online=True)
    assert same_event(a, b)


def test_a_naive_start_is_read_as_utc() -> None:
    a = ev("Rust async", NINE_THIRTY, online=True)
    b = ev("Rust async", datetime(2026, 10, 20, 9, 30), online=True)
    assert same_event(a, b)


def test_title_overlap_must_reach_one_half() -> None:
    base = ev("Rust async", online=True)
    assert same_event(base, ev("Rust async tokio runtime", online=True))  # 2 of 4
    assert not same_event(base, ev("Rust async tokio runtime internals", online=True))  # 2 of 5


def test_titles_with_no_distinguishing_words_are_not_called_the_same() -> None:
    # "Meetup" and "Meetup Bangalore" say nothing about which event this is.
    a = ev("Meetup", online=True)
    b = ev("Meetup Bangalore", online=True)
    assert not same_event(a, b)
    assert not same_event(a, a)


def test_far_apart_in_different_areas_is_another_event() -> None:
    near = ev("Rust async", point=HSR, area="HSR Layout")
    far = ev("Rust async", point=WHITEFIELD, area="Whitefield")
    assert not same_event(near, far)


def test_the_same_area_is_enough_even_when_the_points_are_far_apart() -> None:
    near = ev("Rust async", point=HSR, area="HSR Layout")
    far = ev("Rust async", point=WHITEFIELD, area="hsr layout")
    assert same_event(near, far)


def test_areas_alone_decide_when_one_side_has_no_point() -> None:
    here = ev("Rust async", area="Indiranagar")
    assert same_event(here, ev("Rust async", area="Indiranagar"))
    assert not same_event(here, ev("Rust async", area="Whitefield"))


def test_an_unknown_location_on_either_side_does_not_block_a_match() -> None:
    placed = ev("Rust async", point=HSR, area="HSR Layout")
    unplaced = ev("Rust async")
    assert same_event(placed, unplaced)
    assert same_event(unplaced, placed)


def test_a_point_on_one_side_and_an_area_on_the_other_cannot_be_compared() -> None:
    assert same_event(ev("Rust async", point=HSR), ev("Rust async", area="Whitefield"))


def test_online_against_a_known_venue_is_compatible_when_the_venue_cannot_be_compared() -> None:
    assert same_event(ev("Rust async", online=True), ev("Rust async", point=HSR))


def test_same_event_accepts_a_parsed_event_fact() -> None:
    a = EventFacts(
        source="luma",
        external_id="1",
        url="https://luma.com/x",
        title="Swift Bengaluru × Okta",
        starts_at=NINE_THIRTY,
        lat=HSR[0],
        lng=HSR[1],
    )
    b = EventFacts(
        source="eventbrite",
        external_id="2",
        url="https://example.test/e/2",
        title="Swift Bengaluru Meetup at Okta",
        starts_at=NINE_THIRTY + timedelta(minutes=15),
        lat=HSR_300M_NORTH[0],
        lng=HSR_300M_NORTH[1],
    )
    assert same_event(a, b)


# --- company_candidates -----------------------------------------------------


def triples(found: list[CompanyCandidate]) -> set[tuple[str, str, str]]:
    return {(c.name, c.role, c.matched_on) for c in found}


def test_a_title_pair_names_both_sides() -> None:
    found = company_candidates(None, (), "BitGo x Giottus: Crypto Custody in Action")
    assert ("BitGo", "host", "title_pair") in triples(found)
    assert ("Giottus", "host", "title_pair") in triples(found)
    assert len(found) == 2


def test_the_organiser_comes_first_and_is_not_repeated_by_the_title() -> None:
    found = company_candidates("Swift Bengaluru", (), "Swift Bengaluru × Okta")
    assert triples(found) == {
        ("Swift Bengaluru", "organiser", "organiser_name"),
        ("Okta", "host", "title_pair"),
    }
    assert found[0].role == "organiser"


def test_hosts_and_a_trailing_at_name_are_candidates() -> None:
    found = company_candidates(None, ("Team Lossfunk", "Ila Quill"), "Talk @ Razorpay")
    assert triples(found) == {
        ("Team Lossfunk", "host", "host_name"),
        ("Ila Quill", "host", "host_name"),
        ("Razorpay", "host", "title_at"),
    }


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Swift Bengaluru Meetup at Okta", "Okta"),
        ("Talk@Razorpay", "Razorpay"),
        ("Looking at logs at Okta: Q&A", "Okta"),
        ("Platform night @ Razorpay | Bengaluru", "Razorpay"),
        ("Platform night @ Razorpay, Koramangala", "Razorpay"),
        ("Build day AT Giottus (in person)", "Giottus"),
    ],
)
def test_the_last_at_marker_names_a_company(title: str, expected: str) -> None:
    assert triples(company_candidates(None, (), title)) == {(expected, "host", "title_at")}


@pytest.mark.parametrize(
    "title",
    [
        "Chat Bots Meetup",
        "Coffee, chat and a hat",
        "At Scale: systems design",
        "Mixer at 6 PM",
        "Hands-on at-scale ML",
        "Talks at",
        "Plain title with nothing to link",
        "C++ Bengaluru",
        "",
    ],
)
def test_a_title_with_no_company_in_it_yields_nothing(title: str) -> None:
    assert company_candidates(None, (), title) == []


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Swift + Okta", {"Swift", "Okta"}),
        ("Swift X Okta", {"Swift", "Okta"}),
        ("Swift×Okta", {"Swift", "Okta"}),
        ("Workshop: BitGo x Giottus", {"BitGo", "Giottus"}),
        ("Intro | BitGo × Giottus x Okta - fireside", {"BitGo", "Giottus", "Okta"}),
    ],
)
def test_title_pair_forms(title: str, expected: set[str]) -> None:
    found = company_candidates(None, (), title)
    assert {c.name for c in found} == expected
    assert {c.matched_on for c in found} == {"title_pair"}


def test_candidates_are_deduped_by_normalised_name_and_the_first_wins() -> None:
    found = company_candidates("Okta Inc.", ("OKTA", "Okta Pvt Ltd"), "Okta x Okta")
    assert found == [CompanyCandidate("Okta Inc.", "organiser", "organiser_name")]


def test_blank_and_oversized_names_are_ignored() -> None:
    found = company_candidates("  ", ("", "   ", "x" * 500, "Okta"), "")
    assert triples(found) == {("Okta", "host", "host_name")}


def test_whitespace_in_a_name_is_collapsed() -> None:
    found = company_candidates("Swift   Bengaluru\n", (), "")
    assert [c.name for c in found] == ["Swift Bengaluru"]


def test_a_candidate_is_immutable() -> None:
    candidate = CompanyCandidate("Okta", "host", "title_at")
    with pytest.raises(FrozenInstanceError):
        candidate.name = "x"  # type: ignore[misc]


# --- parse_event_url --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://eventbrite.com.evil.io/e/x-123",
        "https://user@gdg.community.dev/events/details/x/",
        "https://gdg.community.dev/api/event/1",
        "http://lu.ma/abc",
        "javascript:alert(1)",
        "https://www.eventbrite.com:8443/e/x-1",
        "https://lu.ma/" + "a" * 5000,
    ],
)
def test_hostile_links_are_not_matched(url: str) -> None:
    assert parse_event_url(url) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.eventbrite.co.in/e/startup-mixer-tickets-123456789012",
            PastedEvent("eventbrite", "123456789012"),
        ),
        (
            "https://gdg.community.dev/events/details/google-gdg-bangalore-presents-build-with-gemini-bangalore/",
            PastedEvent("gdg", "google-gdg-bangalore-presents-build-with-gemini-bangalore"),
        ),
        ("https://luma.com/rohanquill", PastedEvent("luma", "rohanquill")),
        (
            "https://luma.com/event/evt-xshFGBCaWiF6N6F",
            PastedEvent("luma", "evt-xshFGBCaWiF6N6F"),
        ),
        ("https://lu.ma/rohanquill", PastedEvent("luma", "rohanquill")),
        ("https://lu.ma/event/evt-xshFGBCaWiF6N6F", PastedEvent("luma", "evt-xshFGBCaWiF6N6F")),
    ],
)
def test_valid_links_parse_to_their_key(url: str, expected: PastedEvent) -> None:
    assert parse_event_url(url) == expected


@pytest.mark.parametrize("tld", ["com", "co.in", "co.uk", "com.au", "ca", "ie"])
def test_every_listed_eventbrite_site_is_matched(tld: str) -> None:
    parsed = parse_event_url(f"https://www.eventbrite.{tld}/e/rust-night-tickets-99887766554")
    assert parsed == PastedEvent("eventbrite", "99887766554")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.eventbrite.de/e/rust-night-tickets-99887766554",
        "https://eventbrite.com/e/rust-night-tickets-99887766554",
        "https://www.eventbrite.com.evil.io/e/rust-night-tickets-99887766554",
        "https://evilwww.eventbrite.com/e/rust-night-tickets-99887766554",
        "https://www.eventbrite.com./e/rust-night-tickets-99887766554",
        "https://www.eventbrite.com/d/india--bengaluru/events/",
        "https://www.eventbrite.com/o/some-organiser-12345",
        "https://www.eventbrite.com/e/rust-night-tickets-",
        "https://www.eventbrite.com/e/rust-night-tickets-12ab",
        "https://www.eventbrite.com/e/rust-night-tickets-123/extra",
        "https://www.eventbrite.com/e/" + "a" * 201 + "-123",
        "https://www.eventbrite.com/e/rust-night-tickets-" + "1" * 21,
        "https://www.eventbriteapi.com/v3/events/123/",
    ],
)
def test_eventbrite_links_that_are_not_an_event_page_are_not_matched(url: str) -> None:
    assert parse_event_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://gdg.community.dev/events/details/",
        "https://gdg.community.dev/events/details//",
        "https://gdg.community.dev/events/details/Upper-Case/",
        "https://gdg.community.dev/events/details/under_score/",
        "https://gdg.community.dev/events/details/" + "a" * 161 + "/",
        "https://gdg.community.dev/events/details/a/b/",
        "https://gdg.community.dev/gdg-bangalore/",
        "https://gdg.community.dev/accounts/login/",
        "https://gdg.community.dev/newsletter/",
        "https://www.gdg.community.dev/events/details/x/",
        "https://gdg.community.dev.evil.io/events/details/x/",
        "https://evil.io/gdg.community.dev/events/details/x/",
    ],
)
def test_gdg_links_that_are_not_an_event_page_are_not_matched(url: str) -> None:
    assert parse_event_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://lu.ma/",
        "https://lu.ma/ab",
        "https://lu.ma/a_b_c",
        "https://lu.ma/" + "a" * 65,
        "https://lu.ma/abc/def",
        "https://lu.ma/event",
        "https://lu.ma/event/",
        "https://lu.ma/event/not-an-evt-id",
        "https://lu.ma/event/evt-",
        "https://lu.ma/event/evt-abc/extra",
        "https://api.lu.ma/ics/get?entity=discover&id=discplace-abc123",
        "https://www.lu.ma/abc",
        "https://lu.ma.evil.io/abc",
        "https://luma.com.evil.io/abc",
        "https://evil.io/lu.ma/abc",
    ],
)
def test_luma_links_that_are_not_an_event_or_calendar_are_not_matched(url: str) -> None:
    assert parse_event_url(url) is None


def test_the_limits_of_a_slug_are_inclusive() -> None:
    assert parse_event_url("https://lu.ma/abc") == PastedEvent("luma", "abc")
    assert parse_event_url("https://lu.ma/eventful") == PastedEvent("luma", "eventful")
    assert parse_event_url("https://lu.ma/" + "a" * 64) == PastedEvent("luma", "a" * 64)
    gdg = parse_event_url("https://gdg.community.dev/events/details/" + "a" * 160 + "/")
    assert gdg == PastedEvent("gdg", "a" * 160)


def test_a_trailing_slash_is_optional() -> None:
    assert parse_event_url("https://lu.ma/rohanquill/") == PastedEvent("luma", "rohanquill")
    assert parse_event_url("https://gdg.community.dev/events/details/x") == PastedEvent("gdg", "x")
    assert parse_event_url("https://www.eventbrite.com/e/x-123/") == PastedEvent(
        "eventbrite", "123"
    )


def test_a_query_or_fragment_is_ignored_and_never_reaches_the_key() -> None:
    assert parse_event_url("https://lu.ma/rohanquill?tk=abc#top") == PastedEvent(
        "luma", "rohanquill"
    )
    assert parse_event_url(
        "https://www.eventbrite.co.in/e/startup-mixer-tickets-123456789012?aff=ebdssbdestsearch"
    ) == PastedEvent("eventbrite", "123456789012")
    assert parse_event_url("https://gdg.community.dev/events/details/x/?foo=bar#y") == PastedEvent(
        "gdg", "x"
    )


def test_scheme_and_host_case_do_not_matter_but_the_key_keeps_its_case() -> None:
    assert parse_event_url("HTTPS://LU.MA/Rohan-Quill") == PastedEvent("luma", "Rohan-Quill")


def test_surrounding_whitespace_is_trimmed() -> None:
    assert parse_event_url("  https://lu.ma/rohanquill \n") == PastedEvent("luma", "rohanquill")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "lu.ma/rohanquill",
        "//lu.ma/rohanquill",
        "ftp://lu.ma/rohanquill",
        "https://",
        "https://@lu.ma/rohanquill",
        "https://:@lu.ma/rohanquill",
        "https://user:pass@lu.ma/rohanquill",
        "https://lu.ma:/rohanquill",
        "https://lu.ma:443/rohanquill",
        "https://lu.ma:abc/rohanquill",
        "https://[::1]/rohanquill",
        "https://lu.ma/roh anquill",
        "https://lu.ma/roh\tanquill",
        "https://lu.ma/roh\nanquill",
        "https://lu.ma/roh\x00anquill",
        "https://lu.ma/rohanquill‮",
        "https://lu․ma/rohanquill",
        "https://lу.ma/rohanquill",
    ],
)
def test_malformed_and_unusual_urls_are_not_matched(url: str) -> None:
    assert parse_event_url(url) is None


def test_the_length_limit_is_512_characters() -> None:
    # A tracking query can be long; the limit is on the whole pasted string.
    prefix = "https://lu.ma/rohanquill?utm="
    ok = prefix + "a" * (MAX_URL_LENGTH - len(prefix))
    assert len(ok) == MAX_URL_LENGTH
    assert parse_event_url(ok) == PastedEvent("luma", "rohanquill")
    assert parse_event_url(ok + "a") is None


def test_a_valid_link_is_matched_without_any_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("parse_event_url touched the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert parse_event_url("https://lu.ma/rohanquill") == PastedEvent("luma", "rohanquill")
    assert parse_event_url("https://eventbrite.com.evil.io/e/x-123") is None


def test_a_pasted_event_is_immutable() -> None:
    event = PastedEvent("luma", "rohanquill")
    with pytest.raises(FrozenInstanceError):
        event.key = "x"  # type: ignore[misc]
