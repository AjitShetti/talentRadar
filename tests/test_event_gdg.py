"""
tests/test_event_gdg.py
~~~~~~~~~~~~~~~~~~~~~~~
The GDG reader: the robots.txt guard, the two page parsers on the recorded pages
and on small inline pages that pin one rule each, and the page fetch. No
database, no network.

Every person in an inline page is fictional; the recorded pages under
``tests/fixtures/events/`` are reduced and pseudonymised by
``scripts/capture_fixtures.py``.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from domain.event_facts import EventFacts, SourceScope
from domain.event_relevance import assess
from services.events import gdg
from services.events.gdg import (
    BASE,
    CRAWL_DELAY_S,
    DISALLOWED,
    GdgListing,
    assert_allowed,
    chapter_url,
    event_page_url,
    fetch_page,
    parse_chapter,
    parse_event_page,
)
from services.events.http import MAX_BYTES, USER_AGENT

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
BANGALORE = FIXTURES / "gdg_chapter_gdg-bangalore.html.gz"
CLOUD = FIXTURES / "gdg_chapter_gdg-cloud-bengaluru.html.gz"
DEVFEST_SLUG = "google-gdg-cloud-bengaluru-presents-road-to-devfest-opensource-edition"
GEMINI_SLUG = "google-gdg-bangalore-presents-build-with-gemini-bangalore"
DEVFEST = FIXTURES / f"gdg_event_{DEVFEST_SLUG}.html.gz"
GEMINI = FIXTURES / f"gdg_event_{GEMINI_SLUG}.html.gz"

IST = timezone(timedelta(hours=5, minutes=30))
GDG_SCOPE = SourceScope(key="GDG Quill", india=False, trusted_tech=False)


def _read(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes())


# ── inline pages ─────────────────────────────────────────────────────────────


def _ld(**overrides: Any) -> dict[str, Any]:
    """A GDG-shaped Event JSON-LD block with a fictional venue; ``None`` drops a key."""
    block: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": "Quill Cloud Study Jam",
        "startDate": "2026-11-07T10:00:00+05:30",
        "endDate": "2026-11-07T13:00:00+05:30",
        "description": "A hands-on study jam on cloud basics.",
        "organizer": {
            "@type": "Organization",
            "name": "Google Developer Groups",
            "url": "https://gdg.community.dev",
        },
        "location": {
            "@type": "Place",
            "name": "Quill Hall",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "12 Quill Road, Indiranagar",
                "addressLocality": "Bengaluru",
                "postalCode": "560038",
                "addressRegion": "Karnataka",
                "addressCountry": "US",
            },
        },
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    }
    block.update(overrides)
    return {key: value for key, value in block.items() if value is not None}


def _event_page(*blocks: object, extra_head: str = "") -> bytes:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in blocks
    )
    return f"<html><head>{extra_head}</head><body>{scripts}</body></html>".encode()


def _facts(**overrides: Any) -> EventFacts:
    facts = parse_event_page(
        _event_page(_ld(**overrides)), slug="quill-study-jam", chapter="GDG Quill"
    )
    assert facts is not None
    return facts


def _chapter_page(results: Any, *, wrapper: str = "props") -> bytes:
    data: dict[str, Any] = {
        wrapper: {"pageProps": {"prerenderData": {"upcomingEvents": {"results": results}}}}
    }
    return (
        '<html><body><script type="application/json" id="__NEXT_DATA__">'
        f"{json.dumps(data)}</script></body></html>"
    ).encode()


def _listing(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "title": "Quill Android Night",
        "start_date": "2026-11-14T12:30:00Z",
        "url": f"{BASE}/events/details/google-gdg-quill-presents-android-night/",
        "event_type_title": "In-person Event",
        "description_short": "An evening of <b>Android</b> talks.",
    }
    entry.update(overrides)
    return entry


# ── the robots.txt guard ─────────────────────────────────────────────────────


def test_the_documented_limits() -> None:
    assert BASE == "https://gdg.community.dev"
    assert CRAWL_DELAY_S == 2.0
    assert DISALLOWED == (
        "/health/",
        "/accounts/",
        "/api/",
        "/newsletter/",
        "/gql/",
        "/triggers/",
        "/u/",
        "/viewserver-health/",
    )


def test_assert_allowed_blocks_api() -> None:
    with pytest.raises(ValueError):
        assert_allowed("https://gdg.community.dev/api/event_slim/for_chapter/793/")
    with pytest.raises(ValueError):
        assert_allowed("https://gdg.community.dev/gql/")
    with pytest.raises(ValueError):
        assert_allowed("https://evil.dev/gdg-bangalore/")
    assert_allowed("https://gdg.community.dev/gdg-bangalore/")


@pytest.mark.parametrize("prefix", DISALLOWED)
def test_assert_allowed_blocks_every_disallowed_prefix(prefix: str) -> None:
    with pytest.raises(ValueError):
        assert_allowed(f"{BASE}{prefix}anything")
    with pytest.raises(ValueError):
        assert_allowed(f"{BASE}{prefix}")


@pytest.mark.parametrize(
    "url",
    [
        "https://gdg.community.dev/gdg-bangalore/",
        "https://gdg.community.dev/gdg-bangalore/?utm_source=x",
        "https://gdg.community.dev/events/details/google-gdg-quill-presents-x/",
        "https://GDG.Community.Dev/gdg-bangalore/",
        "https://gdg.community.dev:443/gdg-bangalore/",
        # not a disallowed prefix: "/api" alone is not "/api/", "/users/" is not "/u/"
        "https://gdg.community.dev/users/",
    ],
)
def test_assert_allowed_passes_the_public_pages(url: str) -> None:
    assert_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://gdg.community.dev/gdg-bangalore/",
        "https://gdg.community.dev.evil.dev/gdg-bangalore/",
        "https://evilgdg.community.dev/gdg-bangalore/",
        "https://gdg.community.dev./gdg-bangalore/",
        "https://user@gdg.community.dev/gdg-bangalore/",
        "https://user:pw@gdg.community.dev/gdg-bangalore/",
        "https://gdg.community.dev:8443/gdg-bangalore/",
        "https://gdg.community.dev\\@evil.dev/gdg-bangalore/",
        "//gdg.community.dev/gdg-bangalore/",
        "/gdg-bangalore/",
        "gdg.community.dev/gdg-bangalore/",
        "ftp://gdg.community.dev/gdg-bangalore/",
        "javascript:alert(1)",
        "",
        # a path that would have to be normalised to be understood is refused, not normalised
        "https://gdg.community.dev/gdg-bangalore/../api/x",
        "https://gdg.community.dev/gdg-bangalore/./../gql/",
        "https://gdg.community.dev//api/x",
        "https://gdg.community.dev/%61pi/x",
        "https://gdg.community.dev/gdg%2fapi/",
        "https://gdg.community.dev/%2e%2e/api/",
        "https://gdg.community.dev/API/x",
        "https://gdg.community.dev/Api/event/",
        "https://gdg.community.dev/gdg-bangalore\\..\\api\\x",
        "https://gdg.community.dev/ap\ti/x",
        "https://gdg.community.dev/api/x\n",
        "https://gdg.community.dev/gdg bangalore/",
        "https://gdg.community.dev/gdg-bangalore/;/api/",
        "https://gdg.community.dev/" + "a" * 3000,
    ],
)
def test_assert_allowed_refuses_what_it_cannot_vouch_for(url: str) -> None:
    with pytest.raises(ValueError):
        assert_allowed(url)


# ── building a page address ──────────────────────────────────────────────────


def test_chapter_and_event_urls() -> None:
    assert chapter_url("gdg-bangalore") == "https://gdg.community.dev/gdg-bangalore/"
    assert (
        event_page_url("google-gdg-quill-presents-x")
        == "https://gdg.community.dev/events/details/google-gdg-quill-presents-x/"
    )
    assert chapter_url("a" * 160).endswith("/")


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "GDG-Bangalore",
        "gdg_bangalore",
        "gdg bangalore",
        "gdg.bangalore",
        "gdg/bangalore",
        "../api",
        "a/../b",
        "gdg-bangalore/",
        "gdg-bangalore?x=1",
        "gdg-bangalore#x",
        "%61pi",
        "gdg-bangalore\n",
        "٣",
        "a" * 161,
    ],
)
def test_the_url_builders_refuse_a_malformed_slug(slug: str) -> None:
    with pytest.raises(ValueError):
        chapter_url(slug)
    with pytest.raises(ValueError):
        event_page_url(slug)


@pytest.mark.parametrize(
    "slug", ["api", "gql", "u", "health", "accounts", "newsletter", "triggers", "events"]
)
def test_a_chapter_slug_cannot_name_a_path_that_is_not_a_chapter(slug: str) -> None:
    """Seven are robots.txt's; "events" is the site's own listing, where event pages live below."""
    with pytest.raises(ValueError):
        chapter_url(slug)


# ── the chapter page ─────────────────────────────────────────────────────────


def test_parse_chapter_missing_key_is_empty() -> None:
    assert parse_chapter(b"<html></html>") == []


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not html at all",
        b"<html><body><script type='application/json'>{</script></body></html>",
        b"<html><body><script type='application/json'>[1, 2]</script></body></html>",
        b"<html><body><script type='application/json'>null</script></body></html>",
        b"<html><body><script type='application/json'>{\"props\": 3}</script></body></html>",
        b'<html><body><script type=\'application/json\'>{"props": {"pageProps": []}}</script>',
        b'\xff\xfe<html>\x00<script type="application/json">{}</script>',
    ],
)
def test_parse_chapter_is_total_on_bad_input(data: bytes) -> None:
    assert parse_chapter(data) == []


@pytest.mark.parametrize(
    "results",
    [None, {}, "x", 7, [], [None, 3, "x", []]],
)
def test_parse_chapter_ignores_a_results_value_it_cannot_read(results: Any) -> None:
    assert parse_chapter(_chapter_page(results)) == []


def test_parse_chapter_reads_the_upcoming_events() -> None:
    listings = parse_chapter(_chapter_page([_listing()]))
    assert listings == [
        GdgListing(
            slug="google-gdg-quill-presents-android-night",
            title="Quill Android Night",
            starts_at=datetime(2026, 11, 14, 12, 30, tzinfo=UTC),
            url=f"{BASE}/events/details/google-gdg-quill-presents-android-night/",
            event_type="In-person Event",
            description="An evening of Android talks.",
        )
    ]
    assert listings[0].starts_at.utcoffset() == timedelta(0)


def test_parse_chapter_keeps_the_page_order_and_the_first_of_a_repeated_slug() -> None:
    url_a = f"{BASE}/events/details/a-night/"
    url_b = f"{BASE}/events/details/b-night/"
    listings = parse_chapter(
        _chapter_page(
            [
                _listing(title="B", url=url_b),
                _listing(title="A", url=url_a),
                _listing(title="B again", url=url_b),
            ]
        )
    )
    assert [(item.slug, item.title) for item in listings] == [("b-night", "B"), ("a-night", "A")]


def test_parse_chapter_optional_fields_may_be_missing() -> None:
    listing = parse_chapter(
        _chapter_page([_listing(event_type_title=None, description_short=None)])
    )[0]
    assert listing.event_type is None
    assert listing.description is None


def test_parse_chapter_reads_an_offset_and_treats_a_bare_time_as_utc() -> None:
    first, second = parse_chapter(
        _chapter_page(
            [
                _listing(
                    start_date="2026-11-14T18:00:00+05:30",
                    url=f"{BASE}/events/details/a-night/",
                ),
                _listing(
                    start_date="2026-11-14T12:30:00",
                    url=f"{BASE}/events/details/b-night/",
                ),
            ]
        )
    )
    assert first.starts_at == datetime(2026, 11, 14, 12, 30, tzinfo=UTC)
    assert second.starts_at == datetime(2026, 11, 14, 12, 30, tzinfo=UTC)
    assert first.starts_at.tzinfo is not None and second.starts_at.tzinfo is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"title": None},
        {"title": "   "},
        {"title": 7},
        {"start_date": None},
        {"start_date": "soon"},
        {"start_date": "2026-11-14"},
        {"start_date": 1760000000},
        {"url": None},
        {"url": f"{BASE}/api/event_slim/for_chapter/793/"},
        {"url": f"{BASE}/gql/"},
        {"url": f"{BASE}/events/details/"},
        {"url": f"{BASE}/events/details/UPPER/"},
        {"url": f"{BASE}/events/details/a/../../api/x/"},
        {"url": "https://evil.dev/events/details/a-night/"},
        {"url": "http://gdg.community.dev/events/details/a-night/"},
        {"url": "https://user@gdg.community.dev/events/details/a-night/"},
        {"url": "javascript:alert(1)"},
        {"url": "/events/details/a-night/"},
    ],
)
def test_parse_chapter_drops_a_listing_it_cannot_trust(bad: dict[str, Any]) -> None:
    good = _listing(url=f"{BASE}/events/details/good-night/", title="Good")
    listings = parse_chapter(_chapter_page([_listing(**bad), good]))
    assert [item.slug for item in listings] == ["good-night"]


def test_parse_chapter_uses_the_page_data_even_when_another_json_script_comes_first() -> None:
    """GDG pages carry other JSON scripts (a bare list of tags); the Next.js data is the one with props."""
    page = (
        b'<html><body><script type="application/json" id="event_tags">["a", "b"]</script>'
        b'<script type="application/json">{"other": true}</script>'
        + _chapter_page([_listing()]).split(b"<body>")[1]
    )
    assert [item.title for item in parse_chapter(page)] == ["Quill Android Night"]


def test_parse_chapter_rewrites_a_listing_url_to_the_canonical_event_address() -> None:
    listing = parse_chapter(
        _chapter_page([_listing(url=f"{BASE}/events/details/a-night")])  # no trailing slash
    )[0]
    assert listing.url == event_page_url("a-night")
    assert listing.slug == "a-night"


def test_parse_chapter_flattens_and_bounds_free_text() -> None:
    listing = parse_chapter(
        _chapter_page(
            [
                _listing(
                    title="<b>Quill</b>   Night\u0000\n&amp; Friends " + "x" * 400,
                    description_short="<p>One</p><p>Two</p> " + "y" * 900,
                )
            ]
        )
    )[0]
    assert listing.title.startswith("Quill Night & Friends")
    assert "<" not in listing.title and "\u0000" not in listing.title
    assert len(listing.title) <= gdg.TITLE_MAX
    assert listing.description is not None
    assert listing.description.startswith("One Two")
    assert len(listing.description) <= gdg.SNIPPET_MAX


def test_parse_chapter_survives_a_json_bomb() -> None:
    deep = "[" * 200_000 + "]" * 200_000
    page = f'<html><body><script type="application/json">{deep}</script></body></html>'.encode()
    assert parse_chapter(page) == []


def test_past_event_slugs_reads_the_earlier_events_in_page_order() -> None:
    blob = gdg.chapter_blob(_read(BANGALORE))
    assert blob is not None
    slugs = gdg.past_event_slugs(blob)
    assert slugs[0] == GEMINI_SLUG
    assert len(slugs) == len(set(slugs)) >= 2
    assert gdg.past_event_slugs({}) == []
    assert (
        gdg.past_event_slugs({"props": {"pageProps": {"prerenderData": {"pastEvents": 3}}}}) == []
    )


def test_past_event_slugs_skips_what_is_not_an_event_address() -> None:
    results = [
        None,
        {"url": f"{BASE}/api/x/"},
        {"url": f"{BASE}/events/details/old-night/"},
        {"url": f"{BASE}/events/details/old-night/"},
        {"url": 3},
    ]
    blob = {"props": {"pageProps": {"prerenderData": {"pastEvents": {"results": results}}}}}
    assert gdg.past_event_slugs(blob) == ["old-night"]


def test_parse_chapter_fixture_with_upcoming_events() -> None:
    listings = parse_chapter(_read(CLOUD))
    assert [item.slug for item in listings] == [DEVFEST_SLUG]
    only = listings[0]
    assert only.title == "Road to Devfest: Opensource Edition"
    assert only.starts_at == datetime(2026, 10, 24, 4, 0, tzinfo=UTC)
    assert only.url == event_page_url(DEVFEST_SLUG)
    assert only.event_type == "External registration"
    assert only.description and "Open Source Build Session" in only.description


def test_parse_chapter_fixture_without_upcoming_events() -> None:
    """GDG Bangalore had no upcoming event on the day it was recorded."""
    assert parse_chapter(_read(BANGALORE)) == []


# ── the event page ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "slug", "chapter", "venue", "pin", "starts"),
    [
        (
            DEVFEST,
            DEVFEST_SLUG,
            "GDG Cloud Bengaluru",
            "Google Ananta",
            "560037",
            datetime(2026, 10, 24, 9, 30, tzinfo=IST),
        ),
        (
            GEMINI,
            GEMINI_SLUG,
            "GDG Bangalore",
            "The LaLiT Ashok Bengaluru",
            "560001",
            datetime(2026, 9, 29, 9, 0, tzinfo=IST),
        ),
    ],
    ids=["upcoming", "past"],
)
def test_parse_event_page_fixture(
    path: Path, slug: str, chapter: str, venue: str, pin: str, starts: datetime
) -> None:
    facts = parse_event_page(_read(path), slug=slug, chapter=chapter)
    assert facts is not None
    assert facts.starts_at.tzinfo is not None
    assert facts.starts_at == starts
    assert facts.title
    assert facts.locality in ("Bengaluru", "Bangalore")
    assert facts.address is not None and pin in facts.address
    assert facts.url == event_page_url(slug)
    assert facts.source == "gdg"
    assert facts.external_id == slug
    assert facts.venue_name == venue
    assert facts.organiser_name == chapter
    assert facts.is_online is False
    assert facts.is_cancelled is False
    assert facts.ends_at is not None and facts.ends_at > facts.starts_at
    assert facts.lat is None and facts.lng is None
    assert facts.snippet


def test_the_recorded_listing_and_page_agree_on_when_it_starts() -> None:
    listing = parse_chapter(_read(CLOUD))[0]
    facts = parse_event_page(_read(DEVFEST), slug=listing.slug, chapter="GDG Cloud Bengaluru")
    assert facts is not None
    assert facts.starts_at == listing.starts_at
    assert facts.title == listing.title


def test_the_fixture_event_is_stored_and_listed() -> None:
    facts = parse_event_page(_read(DEVFEST), slug=DEVFEST_SLUG, chapter="GDG Cloud Bengaluru")
    assert facts is not None
    verdict = assess(facts, GDG_SCOPE)
    assert verdict.store and verdict.listed
    assert "GDG chapter" in verdict.reasons


def test_address_country_ignored() -> None:
    """GDG prints "US" as the country of a Bengaluru venue; only the locality counts."""
    facts = _facts()
    assert facts.locality == "Bengaluru"
    assert facts.address is not None and "US" not in facts.address
    verdict = assess(facts, GDG_SCOPE)
    assert verdict.store is True
    assert verdict.listed is True
    assert any("Bengaluru" in reason or "Indiranagar" in reason for reason in verdict.reasons)


def test_a_country_is_never_a_locality() -> None:
    location = {
        "@type": "Place",
        "name": "Quill Hall",
        "address": {"@type": "PostalAddress", "addressCountry": "IN"},
    }
    facts = _facts(location=location)
    assert facts.locality is None
    assert facts.address is None


def test_address_is_street_locality_and_pin_without_repeating_the_city() -> None:
    assert _facts().address == "12 Quill Road, Indiranagar, Bengaluru, 560038"
    location = {
        "@type": "Place",
        "name": "Quill Hall",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Mahadevpura, Bengaluru",
            "addressLocality": "Bengaluru",
            "postalCode": "560037",
        },
    }
    assert _facts(location=location).address == "Mahadevpura, Bengaluru, 560037"


def test_address_parts_may_be_missing() -> None:
    def address(**parts: str) -> str | None:
        location = {"@type": "Place", "name": "Quill Hall", "address": parts}
        return _facts(location=location).address

    assert address(postalCode="560001") == "560001"
    assert address(addressLocality="Bengaluru", postalCode="560001") == "Bengaluru, 560001"
    assert address(streetAddress="  ", addressLocality="") is None


def test_the_event_has_the_values_the_page_states() -> None:
    facts = _facts()
    assert facts.source == "gdg"
    assert facts.external_id == "quill-study-jam"
    assert facts.url == "https://gdg.community.dev/events/details/quill-study-jam/"
    assert facts.title == "Quill Cloud Study Jam"
    assert facts.starts_at == datetime(2026, 11, 7, 10, 0, tzinfo=IST)
    assert facts.ends_at == datetime(2026, 11, 7, 13, 0, tzinfo=IST)
    assert facts.venue_name == "Quill Hall"
    assert facts.organiser_name == "GDG Quill"
    assert facts.snippet == "A hands-on study jam on cloud basics."
    assert facts.is_online is False
    assert facts.is_cancelled is False
    assert facts.hosts == ()
    assert facts.is_free is None


def test_the_url_comes_from_the_slug_never_from_the_page() -> None:
    facts = _facts(url="https://evil.dev/phish", **{"@id": "https://evil.dev/x"})
    assert facts.url == event_page_url("quill-study-jam")


@pytest.mark.parametrize(
    ("mode", "online"),
    [
        ("https://schema.org/OnlineEventAttendanceMode", True),
        ("http://schema.org/OnlineEventAttendanceMode", True),
        ("OnlineEventAttendanceMode", True),
        ("https://schema.org/OfflineEventAttendanceMode", False),
        ("https://schema.org/MixedEventAttendanceMode", False),
        (None, False),
        ("", False),
    ],
)
def test_online_comes_from_the_attendance_mode(mode: str | None, online: bool) -> None:
    assert _facts(eventAttendanceMode=mode).is_online is online


@pytest.mark.parametrize(
    ("status", "cancelled"),
    [
        ("https://schema.org/EventCancelled", True),
        ("http://schema.org/EventCancelled", True),
        ("EventCancelled", True),
        ("https://schema.org/EventScheduled", False),
        ("https://schema.org/EventPostponed", False),
        ("https://schema.org/EventRescheduled", False),
        (None, False),
    ],
)
def test_cancelled_comes_only_from_an_explicit_status(status: str | None, cancelled: bool) -> None:
    assert _facts(eventStatus=status).is_cancelled is cancelled


def test_an_online_event_has_no_street_address() -> None:
    online = _facts(
        eventAttendanceMode="https://schema.org/OnlineEventAttendanceMode",
        location={"@type": "VirtualLocation", "url": "https://meet.example.com/quill"},
    )
    assert online.is_online is True
    assert online.venue_name is None
    assert online.address is None
    assert online.locality is None


def test_a_place_among_several_locations_is_the_venue() -> None:
    mixed = _facts(
        eventAttendanceMode="https://schema.org/MixedEventAttendanceMode",
        location=[
            {"@type": "VirtualLocation", "url": "https://meet.example.com/quill"},
            _ld()["location"],
        ],
    )
    assert mixed.venue_name == "Quill Hall"
    assert mixed.locality == "Bengaluru"
    assert mixed.is_online is False


def test_the_event_block_is_found_among_other_ld_json() -> None:
    page = _event_page(
        {"@context": "https://schema.org", "@type": "Organization", "name": "Quill"},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": []},
        _ld(),
    )
    facts = parse_event_page(page, slug="quill-study-jam", chapter="GDG Quill")
    assert facts is not None and facts.title == "Quill Cloud Study Jam"


@pytest.mark.parametrize(
    "wrap",
    [
        lambda block: [block],
        lambda block: {"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, block]},
    ],
    ids=["list", "graph"],
)
def test_the_event_block_may_sit_in_a_list_or_a_graph(wrap: Any) -> None:
    page = _event_page(wrap(_ld()))
    facts = parse_event_page(page, slug="quill-study-jam", chapter="GDG Quill")
    assert facts is not None and facts.title == "Quill Cloud Study Jam"


def test_an_event_subtype_and_a_type_list_are_events() -> None:
    for kind in ("EducationEvent", ["Event", "SocialEvent"]):
        facts = parse_event_page(_event_page(_ld(**{"@type": kind})), slug="s", chapter="GDG Quill")
        assert facts is not None


def test_a_page_without_an_event_block_is_none() -> None:
    assert parse_event_page(b"<html></html>", slug="s", chapter="c") is None
    assert parse_event_page(b"", slug="s", chapter="c") is None
    only_org = _event_page({"@type": "Organization", "name": "Quill"})
    assert parse_event_page(only_org, slug="s", chapter="c") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": None},
        {"name": ""},
        {"name": "   "},
        {"name": 7},
        {"startDate": None},
        {"startDate": ""},
        {"startDate": "next friday"},
        {"startDate": 1760000000},
        # no offset: the page does not say which clock this is, and a guess would move the event
        {"startDate": "2026-11-07T10:00:00"},
        {"startDate": "2026-11-07"},
    ],
)
def test_an_event_without_a_readable_start_or_title_is_not_built(overrides: dict[str, Any]) -> None:
    page = _event_page(_ld(**overrides))
    assert parse_event_page(page, slug="quill-study-jam", chapter="GDG Quill") is None


@pytest.mark.parametrize(
    "end",
    ["2026-11-07T09:00:00+05:30", "2026-11-07T10:00:00+05:30", "garbage", "2026-11-07T13:00:00", 5],
)
def test_an_unusable_end_is_none_and_the_event_still_stands(end: object) -> None:
    facts = _facts(endDate=end)
    assert facts.ends_at is None
    assert facts.starts_at == datetime(2026, 11, 7, 10, 0, tzinfo=IST)


def test_a_missing_end_is_none() -> None:
    assert _facts(endDate=None).ends_at is None


def test_utc_and_other_offsets_are_kept_as_given() -> None:
    assert _facts(startDate="2026-11-07T04:30:00Z").starts_at == datetime(
        2026, 11, 7, 4, 30, tzinfo=UTC
    )
    assert _facts(startDate="2026-11-06T21:00:00-08:00").starts_at == datetime(
        2026, 11, 7, 10, 30, tzinfo=IST
    )


def test_event_text_is_flattened_and_bounded() -> None:
    facts = _facts(
        name="<b>Quill</b>\n  Study &amp; Jam\u0000 " + "x" * 400,
        description="<p>First</p><p>Second</p>" + "y" * 900,
        location={
            "@type": "Place",
            "name": "<i>Quill</i> Hall " + "z" * 300,
            "address": {"@type": "PostalAddress", "addressLocality": "Bengaluru " + "w" * 300},
        },
    )
    assert facts.title.startswith("Quill Study & Jam")
    assert len(facts.title) <= gdg.TITLE_MAX
    assert "<" not in facts.title and "\u0000" not in facts.title
    assert facts.snippet is not None and facts.snippet.startswith("First Second")
    assert len(facts.snippet) <= gdg.SNIPPET_MAX
    assert facts.venue_name is not None and len(facts.venue_name) <= gdg.VENUE_MAX
    assert facts.locality is not None and len(facts.locality) <= gdg.LOCALITY_MAX
    assert facts.address is not None and len(facts.address) <= gdg.ADDRESS_MAX


def test_the_organiser_is_the_chapter_whatever_the_page_says() -> None:
    facts = parse_event_page(
        _event_page(_ld(organizer={"@type": "Person", "name": "Quill Person"})),
        slug="quill-study-jam",
        chapter="  GDG\nQuill  ",
    )
    assert facts is not None
    assert facts.organiser_name == "GDG Quill"
    assert facts.hosts == ()


def test_parse_event_page_refuses_a_bad_slug() -> None:
    with pytest.raises(ValueError):
        parse_event_page(_event_page(_ld()), slug="../api", chapter="GDG Quill")


def test_parse_event_page_survives_broken_json_and_a_json_bomb() -> None:
    broken = b'<html><script type="application/ld+json">{"@type": "Event", </script></html>'
    assert parse_event_page(broken, slug="s", chapter="c") is None
    deep = "[" * 200_000 + "]" * 200_000
    bomb = f'<html><script type="application/ld+json">{deep}</script></html>'.encode()
    assert parse_event_page(bomb, slug="s", chapter="c") is None
    assert parse_event_page(b"\xff\xfe\x00<script", slug="s", chapter="c") is None


def test_a_broken_block_does_not_hide_a_good_one() -> None:
    page = (
        b'<html><script type="application/ld+json">{oops</script>'
        b'<script type="application/ld+json">' + json.dumps(_ld()).encode() + b"</script></html>"
    )
    facts = parse_event_page(page, slug="quill-study-jam", chapter="GDG Quill")
    assert facts is not None and facts.title == "Quill Cloud Study Jam"


def test_parsing_twice_gives_the_same_result() -> None:
    data = _read(DEVFEST)
    first = parse_event_page(data, slug=DEVFEST_SLUG, chapter="GDG Cloud Bengaluru")
    assert first == parse_event_page(data, slug=DEVFEST_SLUG, chapter="GDG Cloud Bengaluru")
    assert parse_chapter(_read(CLOUD)) == parse_chapter(_read(CLOUD))


# ── the page fetch ───────────────────────────────────────────────────────────


def _client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport)


async def test_fetch_page_requests_exactly_the_page_and_follows_nothing() -> None:
    seen: list[httpx.Request] = []
    url = chapter_url("gdg-bangalore")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, content=b"<html></html>")
        return httpx.Response(302, headers={"Location": f"{BASE}/api/event_slim/for_chapter/793/"})

    async with _client(httpx.MockTransport(handler)) as client:
        assert await fetch_page(client, url) == b"<html></html>"
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_page(client, url)
    assert [str(r.url) for r in seen] == [url, url]
    assert all(r.headers["User-Agent"] == USER_AGENT for r in seen)
    assert all(r.url.host == "gdg.community.dev" for r in seen)


async def test_fetch_page_reads_an_event_page() -> None:
    url = event_page_url("google-gdg-quill-presents-x")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(200, content=b"<html>event</html>")

    async with _client(httpx.MockTransport(handler)) as client:
        assert await fetch_page(client, url) == b"<html>event</html>"


@pytest.mark.parametrize(
    "url",
    [
        "https://gdg.community.dev/api/event_slim/for_chapter/793/",
        "https://gdg.community.dev/gql/",
        "https://gdg.community.dev/accounts/login/",
        "https://gdg.community.dev/u/someone/",
        "https://evil.dev/gdg-bangalore/",
        "http://gdg.community.dev/gdg-bangalore/",
        "https://user@gdg.community.dev/gdg-bangalore/",
        "https://gdg.community.dev/gdg-bangalore/../api/x",
        # allowed by robots.txt, but not a chapter or an event page
        "https://gdg.community.dev/robots.txt",
        "https://gdg.community.dev/sitemap.xml",
        "https://gdg.community.dev/",
        "https://gdg.community.dev/gdg-bangalore",
        "https://gdg.community.dev/events/",
        "https://gdg.community.dev/events/details/",
        "https://gdg.community.dev/events/details/a/b/",
        "https://gdg.community.dev/gdg-bangalore/past-events/",
        "https://gdg.community.dev/gdg-bangalore/?page=2",
        "https://gdg.community.dev/gdg-bangalore/#events",
    ],
)
async def test_fetch_page_refuses_every_other_address_without_a_request(url: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await fetch_page(client, url)
    assert calls == []


async def test_fetch_page_treats_a_body_past_the_cap_as_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_BYTES + 1))

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="larger than"):
            await fetch_page(client, chapter_url("gdg-bangalore"))


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
async def test_fetch_page_raises_on_a_non_2xx(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_page(client, chapter_url("gdg-bangalore"))
