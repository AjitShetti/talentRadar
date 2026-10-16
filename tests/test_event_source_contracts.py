"""
tests/test_event_source_contracts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Events source contracts: the recorded payloads under
``tests/fixtures/events/`` still parse the way the parsers expect, and they
carry nobody's real name or address.

The recordings are replayed offline. They are re-made with
``python scripts/capture_fixtures.py --events``, which reads the curated lists
in ``data/events/sources.json`` and pseudonymises people on the way in (a GDG
page is also cut down to the one JSON block its parser reads); review the diff
when you do. A recording that suddenly parses to zero events is the change
this file exists to catch.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import capture_fixtures
from scripts.fixture_privacy import (
    PLACEHOLDER_CONTINUATION,
    PLACEHOLDER_EMAIL,
    PUBLIC_ORGANISATIONS,
    bare_json_page,
    emails_in,
    embedded_json,
    event_people,
    eventbrite_people,
    gdg_chapter_people,
    person_names,
    personal_keys,
    pseudonymise_ics,
    reduce_eventbrite_event,
    reduce_eventbrite_list,
    reduce_gdg_chapter,
    reduce_gdg_event,
    scrub_gdg_slug,
)
from services.events import eventbrite, gdg
from services.events.luma import feed_url, parse_feed

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "events"
SOURCES = ROOT / "data" / "events" / "sources.json"
LUMA_FIXTURES = sorted(FIXTURES.glob("luma_*.ics"))
GDG_CHAPTER_FIXTURES = sorted(FIXTURES.glob("gdg_chapter_*.html.gz"))
GDG_EVENT_FIXTURES = sorted(FIXTURES.glob("gdg_event_*.html.gz"))
GDG_FIXTURES = GDG_CHAPTER_FIXTURES + GDG_EVENT_FIXTURES

_FOLD = re.compile(r"\r?\n[ \t]")
_MAILTO = re.compile(r"mailto:\s*([^\s;\"]+)", re.IGNORECASE)
_PSEUDONYM = re.compile(r"Host \d+ Quill")


def _unfolded(path: Path) -> str:
    return _FOLD.sub("", path.read_text(encoding="utf-8"))


# ── Luma ─────────────────────────────────────────────────────────────────────


def test_the_luma_recordings_are_all_there() -> None:
    names = {path.name for path in LUMA_FIXTURES}
    assert "luma_discover_bengaluru.ics" in names
    assert len([n for n in names if n.startswith("luma_calendar_")]) >= 2


@pytest.mark.parametrize("path", LUMA_FIXTURES, ids=lambda p: p.name)
def test_luma_fixtures_parse(path: Path) -> None:
    data = path.read_bytes()
    assert data.count(b"BEGIN:VEVENT") >= 1
    events = parse_feed(data)
    assert len(events) >= 1
    for event in events:
        assert event.title
        assert event.url.startswith("https://luma.com/")
        assert event.starts_at.tzinfo is not None
        assert event.external_id
    again = parse_feed(data)
    assert [e.external_id for e in events] == [e.external_id for e in again]
    assert len({e.external_id for e in events}) == len(events), "a UID repeats within one feed"


@pytest.mark.parametrize("path", LUMA_FIXTURES, ids=lambda p: p.name)
def test_luma_fixtures_keep_every_event_the_feed_had(path: Path) -> None:
    """The parser drops only what it must: a recorded event does not vanish silently."""
    data = path.read_bytes()
    assert len(parse_feed(data)) == data.count(b"BEGIN:VEVENT")


def test_the_curated_luma_ids_build_valid_feed_urls() -> None:
    luma = json.loads(SOURCES.read_text(encoding="utf-8"))["luma"]
    assert luma["discover"][0]["id"] == "discplace-G0tGUVYwl7T17Sb"
    assert len(luma["calendars"]) >= 2
    for entry in luma["discover"]:
        feed_url("discover", entry["id"])
    for entry in luma["calendars"]:
        feed_url("calendar", entry["id"])
    for entry in luma["discover"] + luma["calendars"]:
        assert set(entry) == {"id", "name", "trusted_tech", "india", "checked"}
        assert isinstance(entry["trusted_tech"], bool) and isinstance(entry["india"], bool)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["checked"])


# ── nobody's real contact details or name ────────────────────────────────────


def test_fixtures_carry_no_mailto_other_than_example_com() -> None:
    fixtures = sorted(FIXTURES.glob("*.ics"))
    assert fixtures, "no recorded .ics fixtures"
    for path in fixtures:
        for address in _MAILTO.findall(_unfolded(path)):
            assert address.lower().endswith("@example.com"), f"{path.name}: {address}"


@pytest.mark.parametrize("path", LUMA_FIXTURES, ids=lambda p: p.name)
def test_fixtures_name_only_pseudonyms_and_public_organisations(path: Path) -> None:
    """Every organiser and every "Hosted by" name is a pseudonym or a public organisation."""
    names = event_people(_unfolded(path))
    assert names, "the recording names no host or organiser, so it proves nothing"
    for name in names:
        assert _PSEUDONYM.fullmatch(name) or name.casefold() in PUBLIC_ORGANISATIONS, name


# ── the pseudonymiser behind them ────────────────────────────────────────────

_SAMPLE = (
    "BEGIN:VCALENDAR\n"
    "VERSION:2.0\n"
    "BEGIN:VEVENT\n"
    'ORGANIZER;CN="Ada Quill":MAILTO:ada.quill@example.org\n'
    "UID:evt-Sample00000001@events.lu.ma\n"
    "SUMMARY:An evening with ADA QUILL on Rust\n"
    "DESCRIPTION:Get up-to-date information at: https://luma.com/cyrus-talk\\n\\nAdd\n"
    " ress:\\nQuill Hall\\nBengaluru\\, India\\n\\nTalks from Ada Quill\\, then Ben Fi\n"
    " ctional joins. Say hi to Cyrus too. Vedantic ideas welcome.\\nVedant Fictional opens\n"
    " the doors.\\n\\nHosted by Sw\n"
    " ift Bengaluru\\, Ada Quill\\, Ben Fictional\\, Cyrus Fictional & 4 others\n"
    "LOCATION:Quill Hall\\, Bengaluru\n"
    "END:VEVENT\n"
    "BEGIN:VEVENT\n"
    'ORGANIZER;CN="Swift Bengaluru":mailto:Calendar-Invite@Lu.Ma\n'
    "UID:evt-Sample00000002@events.lu.ma\n"
    "SUMMARY:Second\n"
    "DESCRIPTION:Hosted by Vedant Fictional\n"
    "END:VEVENT\n"
    "END:VCALENDAR\n"
)


def _unescaped(text: str) -> str:
    return _FOLD.sub("", text)


def test_pseudonymise_renames_every_host_and_organiser_everywhere() -> None:
    out = _unescaped(pseudonymise_ics(_SAMPLE))
    for real in ("Ada Quill", "Ben Fictional", "Cyrus Fictional", "Vedant Fictional"):
        assert real.casefold() not in out.casefold(), real
    assert "Swift Bengaluru" in out  # a public community stays
    assert "& 4 others" in out
    assert "Quill Hall" in out  # a venue stays
    # the name inside the title, in other case, and split across a fold, are renamed too
    summary = next(line for line in out.splitlines() if line.startswith("SUMMARY:An evening"))
    assert _PSEUDONYM.search(summary)


def test_pseudonymise_gives_one_person_one_pseudonym() -> None:
    out = _unescaped(pseudonymise_ics(_SAMPLE))
    organiser = re.search(r'ORGANIZER;CN="([^"]+)"', out)
    assert organiser is not None
    hosted = next(line for line in out.splitlines() if "Hosted by Swift" in line)
    assert organiser.group(1) in hosted
    assert len(set(_PSEUDONYM.findall(out))) == 4  # Ada, Ben, Cyrus and Vedant, nobody else


def test_pseudonymise_renames_a_first_name_mentioned_alone() -> None:
    out = _unescaped(pseudonymise_ics(_SAMPLE))
    assert "Cyrus" not in out
    assert "Vedant" not in out.replace("Vedantic", "")
    assert "Vedantic ideas" in out  # a word that merely contains a name is left alone


def test_pseudonymise_renames_a_name_that_opens_a_line() -> None:
    """A line break is the two characters backslash-n, which must not glue to the name after it."""
    out = _unescaped(pseudonymise_ics(_SAMPLE))
    assert "Vedant Fictional" not in out
    assert "\\nHost " in out  # the escape is kept; only the name changed


def test_pseudonymise_scrubs_a_name_from_a_link_path_and_keeps_the_link_valid() -> None:
    out = _unescaped(pseudonymise_ics(_SAMPLE))
    assert "https://luma.com/host-talk" in out
    assert "cyrus" not in out.lower()


def test_pseudonymise_replaces_every_mailto_with_the_example_address() -> None:
    out = pseudonymise_ics(_SAMPLE)
    addresses = _MAILTO.findall(_unescaped(out))
    assert addresses == ["organiser@example.com", "organiser@example.com"]


def test_pseudonymise_takes_extra_names_for_people_only_the_prose_mentions() -> None:
    text = _SAMPLE.replace("Say hi to Cyrus too.", "Keynote by Eve Mentioned of Quill Labs.")
    kept = _unescaped(pseudonymise_ics(text))
    assert "Eve Mentioned" in kept
    renamed = _unescaped(pseudonymise_ics(text, extra_names=("eve mentioned",)))
    assert "Eve Mentioned".casefold() not in renamed.casefold()
    assert "Quill Labs" in renamed


def test_pseudonymise_is_deterministic_and_idempotent() -> None:
    once = pseudonymise_ics(_SAMPLE)
    assert pseudonymise_ics(_SAMPLE) == once
    assert pseudonymise_ics(once) == once


def test_pseudonymise_folds_lines_to_75_octets_without_splitting_a_character() -> None:
    long_name = "Ünïcödé " * 3 + "Fictional"
    text = (
        "BEGIN:VEVENT\n"
        f'ORGANIZER;CN="{long_name}":MAILTO:a@b.c\n'
        f"DESCRIPTION:{'é' * 120} Hosted by {long_name}\n"
        "END:VEVENT\n"
    )
    out = pseudonymise_ics(text)
    out.encode("utf-8")  # no lone surrogate
    for line in out.splitlines():
        assert len(line.encode("utf-8")) <= 75, line
    assert "é" * 120 in _unescaped(out)


# ── GDG ──────────────────────────────────────────────────────────────────────

_SLUG = re.compile(r"[a-z0-9-]{1,160}")
_ANY_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PROFILE_LINK = re.compile(
    r"(?<![\w.-])(?:linkedin\.com/in/|twitter\.com/|x\.com/|instagram\.com/|facebook\.com/)"
)
_FIXTURE_NAME = re.compile(r"gdg_(?:chapter|event)_(?P<slug>[a-z0-9-]{1,160})\.html\.gz")


def _gdg_slug(path: Path) -> str:
    match = _FIXTURE_NAME.fullmatch(path.name)
    assert match is not None, path.name
    return match.group("slug")


def _gdg_text(path: Path) -> str:
    return gzip.decompress(path.read_bytes()).decode("utf-8")


def _gdg_chapters() -> list[dict[str, Any]]:
    chapters = json.loads(SOURCES.read_text(encoding="utf-8"))["gdg"]["chapters"]
    assert isinstance(chapters, list)
    return chapters


def test_the_gdg_recordings_are_all_there() -> None:
    names = {path.name for path in GDG_FIXTURES}
    assert "gdg_chapter_gdg-bangalore.html.gz" in names
    assert len(GDG_EVENT_FIXTURES) >= 2
    assert len(GDG_CHAPTER_FIXTURES) >= 2


@pytest.mark.parametrize("path", GDG_CHAPTER_FIXTURES, ids=lambda p: p.name)
def test_gdg_chapter_fixtures_parse(path: Path) -> None:
    data = gzip.decompress(path.read_bytes())
    listings = gdg.parse_chapter(data)
    for listing in listings:
        assert listing.title
        assert listing.starts_at.tzinfo is not None
        assert _SLUG.fullmatch(listing.slug)
        assert listing.url == gdg.event_page_url(listing.slug)
        gdg.assert_allowed(listing.url)
    assert listings == gdg.parse_chapter(data)
    assert len({listing.slug for listing in listings}) == len(listings)


def test_a_recorded_chapter_has_upcoming_events() -> None:
    """At least one recording proves the listing path; the others may be empty on the day."""
    counts = {
        path.name: len(gdg.parse_chapter(gzip.decompress(path.read_bytes())))
        for path in GDG_CHAPTER_FIXTURES
    }
    assert max(counts.values()) >= 1, counts


@pytest.mark.parametrize("path", GDG_EVENT_FIXTURES, ids=lambda p: p.name)
def test_gdg_event_fixtures_parse(path: Path) -> None:
    slug = _gdg_slug(path)
    facts = gdg.parse_event_page(gzip.decompress(path.read_bytes()), slug=slug, chapter="GDG Quill")
    assert facts is not None, "the recorded event page no longer yields an event"
    assert facts.title
    assert facts.url == gdg.event_page_url(slug)
    assert facts.starts_at.tzinfo is not None
    assert facts.source == "gdg" and facts.external_id == slug


def test_gdg_fixtures_parse() -> None:
    """Every chapter and every event recording is read; every address passes the guard."""
    assert GDG_CHAPTER_FIXTURES and GDG_EVENT_FIXTURES
    for path in GDG_CHAPTER_FIXTURES:
        gdg.assert_allowed(gdg.chapter_url(_gdg_slug(path)))
        for listing in gdg.parse_chapter(gzip.decompress(path.read_bytes())):
            gdg.assert_allowed(listing.url)
    for path in GDG_EVENT_FIXTURES:
        facts = gdg.parse_event_page(
            gzip.decompress(path.read_bytes()), slug=_gdg_slug(path), chapter="GDG Quill"
        )
        assert facts is not None and facts.title and facts.url and facts.starts_at
        gdg.assert_allowed(facts.url)


def test_a_recorded_listing_and_its_recorded_page_agree() -> None:
    pages = {_gdg_slug(path): path for path in GDG_EVENT_FIXTURES}
    checked = 0
    for chapter in GDG_CHAPTER_FIXTURES:
        for listing in gdg.parse_chapter(gzip.decompress(chapter.read_bytes())):
            if listing.slug not in pages:
                continue
            facts = gdg.parse_event_page(
                gzip.decompress(pages[listing.slug].read_bytes()),
                slug=listing.slug,
                chapter="GDG Quill",
            )
            assert facts is not None
            assert facts.starts_at == listing.starts_at
            assert facts.title == listing.title
            checked += 1
    assert checked >= 1, "no recorded listing has its page recorded, so nothing ties them"


def test_the_curated_gdg_chapters_build_valid_pages() -> None:
    chapters = _gdg_chapters()
    assert any(entry["slug"] == "gdg-bangalore" for entry in chapters)
    assert len({entry["slug"] for entry in chapters}) == len(chapters)
    for entry in chapters:
        assert set(entry) == {"slug", "name", "checked"}
        assert entry["name"] and entry["name"].strip() == entry["name"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["checked"])
        gdg.assert_allowed(gdg.chapter_url(entry["slug"]))
        assert "campus" not in entry["slug"], "GDG on Campus chapters are student events"


def test_every_recorded_chapter_is_a_curated_one() -> None:
    curated = {entry["slug"] for entry in _gdg_chapters()}
    assert {_gdg_slug(path) for path in GDG_CHAPTER_FIXTURES} <= curated


# ── no real person in a GDG recording ────────────────────────────────────────


@pytest.mark.parametrize("path", GDG_FIXTURES, ids=lambda p: p.name)
def test_gdg_fixtures_are_a_bare_page_around_the_json_the_parsers_read(path: Path) -> None:
    html = gzip.decompress(path.read_bytes())
    assert html.startswith(b"<html><body>") and html.endswith(b"</body></html>")
    assert len(embedded_json(html)) == 1, "the recording keeps one JSON block and nothing else"
    for markup in (b"<img", b"<a ", b"<meta", b"<link", b"<footer", b"<head"):
        assert markup not in html, f"{path.name}: {markup!r}"


def test_gdg_fixtures_carry_no_email_other_than_example_com() -> None:
    assert GDG_FIXTURES, "no recorded gdg fixtures"
    for path in GDG_FIXTURES:
        text = _gdg_text(path)
        for address in _ANY_EMAIL.findall(text):
            assert address == PLACEHOLDER_EMAIL, f"{path.name}: {address}"
        assert "mailto:" not in text.lower(), path.name


@pytest.mark.parametrize("path", GDG_FIXTURES, ids=lambda p: p.name)
def test_gdg_fixtures_name_no_person_outside_the_pseudonyms(path: Path) -> None:
    for blob in embedded_json(gzip.decompress(path.read_bytes())):
        for name in person_names(blob):
            assert _PSEUDONYM.fullmatch(name), f"{path.name}: {name}"
        assert personal_keys(blob) == [], path.name
        assert set(emails_in(blob)) <= {PLACEHOLDER_EMAIL}, path.name


@pytest.mark.parametrize("path", GDG_FIXTURES, ids=lambda p: p.name)
def test_gdg_fixtures_carry_no_profile_link_or_photo(path: Path) -> None:
    text = _gdg_text(path).lower()
    assert _PROFILE_LINK.search(text) is None, path.name
    for key in ("avatar", "sameas", '"photo"', '"image"', "chapterteam", "first_name", "cropped_"):
        assert key not in text, f"{path.name}: {key}"


# ── the reducers behind them ─────────────────────────────────────────────────

_EVENT_URL = "https://gdg.community.dev/events/details/"
_RAW_UPCOMING: dict[str, Any] = {
    "allows_cohosting": False,
    "chapter_id": 7,
    "cohost_registration_url": f"{_EVENT_URL}google-gdg-quill-presents-android-night-with-cyrus/",
    "cropped_banner_url": "https://img.example/banner.png",
    "description": "<p>Long text naming Ben Fictional and ben.fictional@example.org.</p>",
    "description_short": "Talk by Cyrus Fictional. Cyrus opens the doors. Mail ben.fictional@example.org.",
    "event_type_title": "In-person Event",
    "start_date": "2026-11-14T12:30:00Z",
    "title": "Android Night with CYRUS FICTIONAL",
    "url": f"{_EVENT_URL}google-gdg-quill-presents-android-night-with-cyrus/",
}
_RAW_PAST: dict[str, Any] = dict(
    _RAW_UPCOMING,
    title="Quill Cloud Day",
    start_date="2026-08-01T04:00:00Z",
    description_short="Hosted by Vedant Fictional.",
    url=f"{_EVENT_URL}google-gdg-quill-presents-cloud-day/",
)
_RAW_CHAPTER: dict[str, Any] = {
    "props": {
        "pageProps": {
            "chapterSlug": "gdg-quill",
            "chapterData": {
                "id": 7,
                "title": "GDG Quill",
                "city": "Bengaluru",
                "state": "Karnataka",
                "country": "IN",
                "country_name": "India",
                "chapter_location": "Bengaluru, Karnataka (IN)",
                "is_city": True,
                "is_active": True,
                "latitude": 12.97,
                "longitude": 77.59,
                "url": "https://gdg.community.dev/gdg-quill/",
                "linkedin_page": "https://www.linkedin.com/company/gdgquill",
                "twitter_handle": "gdgquill",
                "instagram_handle": "gdgquill",
                "description": "Write to the organiser at ben.fictional@example.org",
                "logo": "https://img.example/logo.png",
            },
            "slateJSON": [{"text": "Cyrus Fictional is our lead"}],
            "customBlockData": {"speakers": ["Vedant Fictional"]},
            "prerenderData": {
                "chapterTeam": [
                    {
                        "title": "GDG Quill Organizer",
                        "user": {
                            "first_name": "Cyrus",
                            "last_name": "Fictional",
                            "avatar": {"url": "https://img.example/cyrus.jpg"},
                            "cropped_avatar_url": "https://img.example/cyrus-250.jpg",
                        },
                    },
                    {
                        "title": "Developer",
                        "user": {"first_name": "Vedant", "last_name": "Fictional"},
                    },
                ],
                "upcomingEvents": {
                    "count": 1,
                    "pagination": {"current_page": 1},
                    "links": {"next": None},
                    "results": [_RAW_UPCOMING],
                },
                "pastEvents": {"count": 40, "results": [_RAW_PAST]},
                "globalSponsors": {"data": [{"name": "Quill Cloud"}]},
            },
        }
    },
    "query": {"fbclid": "tracking-value", "chapterslug": "gdg-quill"},
    "buildId": "abc",
}
_RAW_EVENT_LD: dict[str, Any] = {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Android Night",
    "startDate": "2026-11-14T18:00:00+05:30",
    "endDate": "2026-11-14T21:00:00+05:30",
    "description": "Cyrus Fictional hosts. Ask cyrus@example.org. Vedant joins as a speaker.",
    "image": ["https://img.example/banner.png"],
    "organizer": [
        {
            "@type": "Person",
            "name": "Cyrus Fictional",
            "url": "https://www.linkedin.com/in/cyrus-fictional",
            "email": "cyrus@example.org",
            "image": "https://img.example/cyrus.jpg",
            "sameAs": ["https://twitter.com/cyrusfictional"],
        },
        {
            "@type": "Organization",
            "name": "Google Developer Groups",
            "url": "https://gdg.community.dev",
        },
    ],
    "performer": {"@type": "Person", "name": "Vedant Fictional"},
    "attendee": [{"name": "Dana Guest"}],
    "location": {
        "@type": "Place",
        "name": "Quill Hall",
        "telephone": "+91 80 0000 0000",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "12 Quill Road",
            "addressLocality": "Bengaluru",
            "postalCode": "560038",
            "addressCountry": "US",
        },
    },
    "eventStatus": "https://schema.org/EventScheduled",
    "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    "offers": {"@type": "Offer", "url": "https://forms.example/register", "price": "0"},
    "aggregateRating": {"ratingValue": 5},
}
_ANDROID_SLUG = "google-gdg-quill-presents-android-night-with-host"  # "cyrus" is a person's name
_CLOUD_SLUG = "google-gdg-quill-presents-cloud-day"


def _dump(node: object) -> str:
    return json.dumps(node, ensure_ascii=False)


def _reduced_chapter(**kwargs: Any) -> dict[str, Any]:
    return reduce_gdg_chapter(_RAW_CHAPTER, **kwargs)


def _results(reduced: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = reduced["props"]["pageProps"]["prerenderData"][kind]["results"]
    return results


def test_reduce_chapter_keeps_the_listing_and_drops_every_person() -> None:
    reduced = _reduced_chapter()
    text = _dump(reduced).casefold()
    for gone in (
        "cyrus",
        "fictional",
        "vedant",
        "ben.",
        "avatar",
        "chapterteam",
        "linkedin",
        "twitter",
        "instagram",
        "fbclid",
        "slatejson",
        "customblockdata",
        "globalsponsors",
        "logo",
        "banner",
    ):
        assert gone not in text, gone
    page = reduced["props"]["pageProps"]
    assert page["chapterSlug"] == "gdg-quill"
    assert page["chapterData"]["title"] == "GDG Quill"
    assert page["chapterData"]["city"] == "Bengaluru"
    assert set(_results(reduced, "upcomingEvents")[0]) == {
        "chapter_id",
        "title",
        "start_date",
        "url",
        "event_type_title",
        "description_short",
    }
    assert page["prerenderData"]["upcomingEvents"]["count"] == 1
    assert _results(reduced, "pastEvents")[0]["title"] == "Quill Cloud Day"


def test_reduce_chapter_gives_a_person_one_pseudonym_wherever_the_text_names_them() -> None:
    reduced = _reduced_chapter()
    upcoming = _results(reduced, "upcomingEvents")[0]
    assert upcoming["title"] == "Android Night with Host 1 Quill"  # other case, same person
    assert upcoming["description_short"] == (
        f"Talk by Host 1 Quill. Host 1 opens the doors. Mail {PLACEHOLDER_EMAIL}."
    )
    assert _results(reduced, "pastEvents")[0]["description_short"] == "Hosted by Host 2 Quill."


def test_reduce_chapter_keeps_a_link_valid_when_a_person_is_named_in_it() -> None:
    reduced = _reduced_chapter()
    assert _results(reduced, "upcomingEvents")[0]["url"] == f"{_EVENT_URL}{_ANDROID_SLUG}/"
    assert _results(reduced, "pastEvents")[0]["url"] == f"{_EVENT_URL}{_CLOUD_SLUG}/"


def test_scrub_gdg_slug_replaces_name_parts_and_nothing_else() -> None:
    people = ["Cyrus Fictional"]
    assert scrub_gdg_slug("fireside-with-cyrus-fictional", people) == "fireside-with-host-host"
    assert scrub_gdg_slug("cyrus", people) == "host"
    assert scrub_gdg_slug("cyrusbar-and-fictionalist", people) == "cyrusbar-and-fictionalist"
    assert scrub_gdg_slug("google-gdg-cloud-day", people) == "google-gdg-cloud-day"
    assert scrub_gdg_slug("fireside-with-cyrus", []) == "fireside-with-cyrus"
    assert scrub_gdg_slug("Fireside-CYRUS", people) == "Fireside-host"


def test_reduce_chapter_takes_extra_names_for_people_only_the_prose_mentions() -> None:
    raw = json.loads(_dump(_RAW_CHAPTER))
    _results(raw, "upcomingEvents")[0]["description_short"] = (
        "Keynote by Eve Mentioned of Quill Labs."
    )
    kept = _dump(reduce_gdg_chapter(raw))
    assert "Eve Mentioned" in kept
    renamed = _dump(reduce_gdg_chapter(raw, redact=("eve mentioned",)))
    assert "eve mentioned" not in renamed.casefold()
    assert "Quill Labs" in renamed


def test_reduce_chapter_is_deterministic_idempotent_and_never_mutates_its_input() -> None:
    before = _dump(_RAW_CHAPTER)
    once = _reduced_chapter()
    assert _dump(_RAW_CHAPTER) == before
    assert _reduced_chapter() == once
    assert reduce_gdg_chapter(once) == once


def test_reduce_chapter_without_the_expected_json_is_an_error_not_an_empty_page() -> None:
    page: dict[str, Any] = {"prerenderData": {"upcomingEvents": {"results": []}}}
    assert reduce_gdg_chapter({"props": {"pageProps": page}})["props"]["pageProps"]
    broken_inputs: list[Any] = [
        None,
        [],
        {},
        "text",
        {"props": {}},
        {"props": {"pageProps": {}}},
        {"props": {"pageProps": {"prerenderData": {}}}},
        {"props": {"pageProps": {"prerenderData": {"upcomingEvents": {}}}}},
        {"props": {"pageProps": {"prerenderData": {"upcomingEvents": {"results": "x"}}}}},
    ]
    for broken in broken_inputs:
        with pytest.raises(ValueError):
            reduce_gdg_chapter(broken)


def test_the_people_of_a_chapter_are_its_team() -> None:
    assert gdg_chapter_people(_RAW_CHAPTER) == ["Cyrus Fictional", "Vedant Fictional"]
    assert gdg_chapter_people({}) == []
    assert (
        gdg_chapter_people({"props": {"pageProps": {"prerenderData": {"chapterTeam": "x"}}}}) == []
    )


def test_reduced_chapter_still_parses_as_a_chapter_page() -> None:
    page = bare_json_page(
        _reduced_chapter(), script_type="application/json", script_id="__NEXT_DATA__"
    )
    listings = gdg.parse_chapter(page)
    assert [item.slug for item in listings] == [_ANDROID_SLUG]
    assert listings[0].title == "Android Night with Host 1 Quill"
    assert embedded_json(page) == [_reduced_chapter()]


def test_reduce_event_keeps_what_the_parser_reads_and_pseudonymises_every_person() -> None:
    reduced = reduce_gdg_event(_RAW_EVENT_LD)
    text = _dump(reduced).casefold()
    for gone in (
        "cyrus",
        "fictional",
        "vedant",
        "dana",
        "example.org",
        "linkedin",
        "twitter",
        "sameas",
        "image",
        "telephone",
        "aggregaterating",
        "attendee",
        "price",
    ):
        assert gone not in text, gone
    assert reduced["name"] == "Android Night"
    assert reduced["startDate"] == "2026-11-14T18:00:00+05:30"
    assert reduced["location"]["name"] == "Quill Hall"
    assert reduced["location"]["address"]["postalCode"] == "560038"
    assert reduced["location"]["address"]["addressCountry"] == "US"  # as recorded; never trusted
    assert reduced["eventStatus"].endswith("EventScheduled")
    assert reduced["offers"] == {"@type": "Offer", "url": "https://forms.example/register"}
    assert reduced["description"] == (
        f"Host 1 Quill hosts. Ask {PLACEHOLDER_EMAIL}. Host 2 joins as a speaker."
    )
    assert person_names(reduced) == ["Host 1 Quill", "Host 2 Quill"]
    # an organisation is not a person: its name and address stay
    assert reduced["organizer"] == [
        {"@type": "Person", "name": "Host 1 Quill"},
        {
            "@type": "Organization",
            "name": "Google Developer Groups",
            "url": "https://gdg.community.dev",
        },
    ]
    assert reduced["performer"] == {"@type": "Person", "name": "Host 2 Quill"}
    assert personal_keys(reduced) == []


def test_reduce_event_also_renames_the_people_a_caller_already_knows() -> None:
    plain = dict(_RAW_EVENT_LD, organizer=[], performer=[], attendee=[])
    reduced = reduce_gdg_event(plain, redact=("Cyrus Fictional", "Vedant Fictional"))
    assert reduced["description"] == (
        f"Host 1 Quill hosts. Ask {PLACEHOLDER_EMAIL}. Host 2 joins as a speaker."
    )


def test_reduce_event_is_deterministic_idempotent_and_never_mutates_its_input() -> None:
    before = _dump(_RAW_EVENT_LD)
    once = reduce_gdg_event(_RAW_EVENT_LD)
    assert _dump(_RAW_EVENT_LD) == before
    assert reduce_gdg_event(_RAW_EVENT_LD) == once
    assert reduce_gdg_event(once) == once


def test_reduce_event_drops_the_join_link_of_a_virtual_location() -> None:
    online = dict(
        _RAW_EVENT_LD, location={"@type": "VirtualLocation", "url": "https://meet.example/x"}
    )
    assert reduce_gdg_event(online)["location"] == {"@type": "VirtualLocation"}


def test_reduce_event_rejects_a_block_that_is_not_an_event() -> None:
    broken_inputs: list[Any] = [None, [], {}, {"@type": "Organization", "name": "x"}, "text"]
    for broken in broken_inputs:
        with pytest.raises(ValueError):
            reduce_gdg_event(broken)


def test_reduced_event_still_parses_as_an_event_page() -> None:
    page = bare_json_page(reduce_gdg_event(_RAW_EVENT_LD), script_type="application/ld+json")
    facts = gdg.parse_event_page(page, slug=_ANDROID_SLUG, chapter="GDG Quill")
    assert facts is not None
    assert facts.title == "Android Night"
    assert facts.locality == "Bengaluru"
    assert facts.address == "12 Quill Road, Bengaluru, 560038"


def test_bare_json_page_cannot_close_its_own_script_early() -> None:
    page = bare_json_page({"text": "</script><b>x</b>"}, script_type="application/json")
    assert page.count(b"</script>") == 1
    assert embedded_json(page) == [{"text": "</script><b>x</b>"}]


def test_the_privacy_checks_catch_a_real_person() -> None:
    """The contract checks above must not pass vacuously."""
    leaky = {
        "@type": "Event",
        "organizer": {"@type": "Person", "name": "Real Name"},
        "performer": [{"name": "Other Person"}, {"@type": "Organization", "name": "Quill Org"}],
        "chapterTeam": [{"user": {"first_name": "Real", "avatar": {"url": "x"}}}],
        "note": "mail real.name@gmail.com",
    }
    assert person_names(leaky) == ["Real Name", "Other Person", "Real"]
    assert {"chapterTeam", "avatar", "first_name", "user"} <= set(personal_keys(leaky))
    assert emails_in(leaky) == ["real.name@gmail.com"]
    assert person_names({"@type": "Organization", "name": "Quill Org"}) == []
    assert person_names({"user": {"first_name": "Ada", "last_name": "Lovelace"}}) == [
        "Ada Lovelace"
    ]


# ── the capture, replayed ────────────────────────────────────────────────────


def _raw_chapter_page(slug: str = "gdg-quill") -> bytes:
    blob = json.loads(_dump(_RAW_CHAPTER))
    blob["props"]["pageProps"]["chapterSlug"] = slug
    return (
        '<html><head><title>GDG Quill</title></head><body><img src="https://img.example/x.png">'
        '<script type="application/json" id="event_tags">["a"]</script>'
        f'<script type="application/json" id="__NEXT_DATA__">{_dump(blob)}</script>'
        "<footer>Ask ben.fictional@example.org</footer></body></html>"
    ).encode()


def _raw_event_page() -> bytes:
    return (
        '<html><head><script type="application/ld+json">'
        f"{_dump(_RAW_EVENT_LD)}</script></head><body>Cyrus Fictional"
        '<script type="application/json" id="__NEXT_DATA__">{"props": {"eventData": '
        '{"speakers": [{"first_name": "Dana"}]}}}</script></body></html>'
    ).encode()


async def _run_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    chapters: list[dict[str, str]] | None = None,
    redact: tuple[str, ...] = (),
) -> tuple[int, list[float]]:
    sources = tmp_path / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "luma": {"discover": [], "calendars": []},
                "gdg": {
                    "chapters": chapters
                    or [{"slug": "gdg-quill", "name": "GDG Quill", "checked": "2026-10-16"}]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(capture_fixtures, "EVENT_SOURCES", sources)
    monkeypatch.setattr(capture_fixtures, "EVENTS_DIR", tmp_path / "out")
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        code = await capture_fixtures.capture_events_gdg(redact, client=client)
    return code, pauses


def _serve(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/gdg-quill/":
        return httpx.Response(200, content=_raw_chapter_page())
    assert request.url.path.startswith("/events/details/")
    return httpx.Response(200, content=_raw_event_page())


async def test_capture_gdg_records_scrubbed_pages_politely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _serve(request)

    code, pauses = await _run_capture(tmp_path, monkeypatch, handler)
    out = tmp_path / "out"

    assert code == 0
    # the chapter page, the upcoming event's page, then the newest past event's page; the real
    # addresses are requested even though a person's name in one is not kept
    assert [r.url.path for r in seen] == [
        "/gdg-quill/",
        "/events/details/google-gdg-quill-presents-android-night-with-cyrus/",
        f"/events/details/{_CLOUD_SLUG}/",
    ]
    assert pauses == [
        gdg.CRAWL_DELAY_S,
        gdg.CRAWL_DELAY_S,
    ]  # between requests, not before the first
    for request in seen:
        assert request.headers["User-Agent"].startswith("TalentRadar/1.0")
        gdg.assert_allowed(str(request.url))
    assert sorted(path.name for path in out.iterdir()) == [
        "gdg_chapter_gdg-quill.html.gz",
        f"gdg_event_{_ANDROID_SLUG}.html.gz",
        f"gdg_event_{_CLOUD_SLUG}.html.gz",
    ]
    for path in out.iterdir():
        html = gzip.decompress(path.read_bytes())
        text = html.decode("utf-8")
        for gone in (
            "Cyrus",
            "Vedant",
            "Dana",
            "Fictional",
            "example.org",
            "img.example",
            "footer",
        ):
            assert gone not in text, f"{path.name}: {gone}"
        assert html.startswith(b"<html><body>") and html.endswith(b"</body></html>")
        (blob,) = embedded_json(html)
        assert personal_keys(blob) == []
        assert all(_PSEUDONYM.fullmatch(name) for name in person_names(blob))

    chapter = gzip.decompress((out / "gdg_chapter_gdg-quill.html.gz").read_bytes())
    listings = gdg.parse_chapter(chapter)
    assert [(item.slug, item.title) for item in listings] == [
        (_ANDROID_SLUG, "Android Night with Host 1 Quill")
    ]
    event = gzip.decompress((out / f"gdg_event_{_ANDROID_SLUG}.html.gz").read_bytes())
    facts = gdg.parse_event_page(event, slug=_ANDROID_SLUG, chapter="GDG Quill")
    assert facts is not None and facts.locality == "Bengaluru"
    # the team known from the chapter page is renamed in the event page too
    assert (
        facts.snippet == f"Host 1 Quill hosts. Ask {PLACEHOLDER_EMAIL}. Host 2 joins as a speaker."
    )


async def test_capture_gdg_is_reproducible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_capture(tmp_path, monkeypatch, _serve)
    first = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    assert first
    await _run_capture(tmp_path, monkeypatch, _serve)
    assert {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()} == first


async def test_capture_gdg_applies_redact_to_names_no_rule_can_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gdg-quill/":
            page = _raw_chapter_page().replace(b"Quill Cloud Day", b"Quill Day with Eve Mentioned")
            return httpx.Response(200, content=page)
        return _serve(request)

    code, _ = await _run_capture(tmp_path, monkeypatch, handler, redact=("Eve Mentioned",))
    assert code == 0
    text = _gdg_text(tmp_path / "out" / "gdg_chapter_gdg-quill.html.gz")
    assert "Eve Mentioned" not in text and "Quill Day with Host" in text


async def test_capture_gdg_writes_nothing_for_a_page_without_the_expected_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html><body>Please enable JavaScript</body></html>")

    code, pauses = await _run_capture(tmp_path, monkeypatch, handler)
    assert code == 1
    assert pauses == []  # nothing left to fetch once the only chapter failed
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []


async def test_capture_gdg_survives_a_failed_chapter_and_still_records_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chapters = [
        {"slug": "gdg-gone", "name": "GDG Gone", "checked": "2026-10-16"},
        {"slug": "gdg-quill", "name": "GDG Quill", "checked": "2026-10-16"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gdg-gone/":
            return httpx.Response(404)
        return _serve(request)

    code, _ = await _run_capture(tmp_path, monkeypatch, handler, chapters=chapters)
    assert code == 1  # not every planned file was written
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert "gdg_chapter_gdg-quill.html.gz" in names
    assert "gdg_chapter_gdg-gone.html.gz" not in names


# ── Eventbrite ───────────────────────────────────────────────────────────────

EVENTBRITE_EVENT = FIXTURES / "eventbrite_event.json"
EVENTBRITE_LIST = FIXTURES / "eventbrite_organizer_events.json"
EVENTBRITE_FIXTURES = [EVENTBRITE_EVENT, EVENTBRITE_LIST]
_HAND_WRITTEN = "hand-written from the documented v3 shapes; re-record with a token"
_FICTIONAL_ORGANISERS = frozenset({"quill tech meetups"})
_EVENTBRITE_PAGE = re.compile(r"https://(?:www\.)?eventbrite\.(?:com|co\.in)/e/\S+")


def _eventbrite_blob(path: Path) -> dict[str, Any]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(blob, dict)
    return blob


def test_the_eventbrite_recordings_are_all_there() -> None:
    assert EVENTBRITE_EVENT.is_file() and EVENTBRITE_LIST.is_file()


def test_eventbrite_fixtures_parse() -> None:
    event = eventbrite.parse_event(_eventbrite_blob(EVENTBRITE_EVENT))
    assert event is not None, "the recorded event no longer yields an event"
    assert event.source == "eventbrite" and event.external_id.isdigit()
    assert event.starts_at.tzinfo is not None and event.title
    assert _EVENTBRITE_PAGE.fullmatch(event.url)

    events, continuation = eventbrite.parse_event_list(_eventbrite_blob(EVENTBRITE_LIST))
    assert len(events) >= 1, "the recorded list page no longer yields an event"
    assert continuation is not None, "the recorded list page shows no next page"
    assert len({e.external_id for e in events}) == len(events)
    for each in events:
        assert each.external_id.isdigit() and each.title
        assert each.starts_at.tzinfo is not None
        assert _EVENTBRITE_PAGE.fullmatch(each.url)
        assert each.taxonomy.get("category"), "the recording keeps the category id"
    # the two recordings describe the same world: the single event is one the list names
    assert event.external_id in {e.external_id for e in events}
    assert eventbrite.parse_event_list(_eventbrite_blob(EVENTBRITE_LIST)) == (events, continuation)


@pytest.mark.parametrize("path", EVENTBRITE_FIXTURES, ids=lambda p: p.name)
def test_eventbrite_fixtures_say_so_when_they_are_hand_written(path: Path) -> None:
    """Until a token records them, the comment says the shape is the documented one."""
    comment = _eventbrite_blob(path).get("_comment")
    assert comment in (None, _HAND_WRITTEN)


@pytest.mark.parametrize("path", EVENTBRITE_FIXTURES, ids=lambda p: p.name)
def test_eventbrite_fixtures_name_only_fictional_or_pseudonymised_organisers(path: Path) -> None:
    names = eventbrite_people(_eventbrite_blob(path))
    assert names, "the recording names no organiser, so it proves nothing"
    for name in names:
        assert (
            _PSEUDONYM.fullmatch(name)
            or name.casefold() in PUBLIC_ORGANISATIONS
            or name.casefold() in _FICTIONAL_ORGANISERS
        ), name


@pytest.mark.parametrize("path", EVENTBRITE_FIXTURES, ids=lambda p: p.name)
def test_eventbrite_fixtures_carry_no_credential_email_or_profile_link(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    blob = _eventbrite_blob(path)
    for address in _ANY_EMAIL.findall(text):
        assert address == PLACEHOLDER_EMAIL, f"{path.name}: {address}"
    lowered = text.lower()
    for word in ("bearer", "authorization", "access_token", "oauth", "mailto:"):
        assert word not in lowered, f"{path.name}: {word}"
    assert _PROFILE_LINK.search(lowered) is None, path.name
    assert personal_keys(blob) == [], path.name


def test_the_curated_eventbrite_block_is_well_formed() -> None:
    block = json.loads(SOURCES.read_text(encoding="utf-8"))["eventbrite"]
    assert isinstance(block["organizers"], list) and isinstance(block["venues"], list)
    assert isinstance(block["status"], str) and block["status"]
    assert block.get("organizer_endpoint", "unavailable") == "unavailable"
    for entry in block["organizers"] + block["venues"]:
        assert set(entry) == {"id", "name", "checked"}
        assert re.fullmatch(r"[0-9]{1,32}", entry["id"])
        assert entry["name"] and entry["name"].strip() == entry["name"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["checked"])
    if not block["organizers"] and not block["venues"]:
        assert block["status"] == "needs token and vetted organiser ids"


# ── the Eventbrite reducers ──────────────────────────────────────────────────

_RAW_EB_EVENT: dict[str, Any] = {
    "name": {"text": "Rust Night with ADA QUILL", "html": "<p>Rust Night with Ada Quill</p>"},
    "description": {
        "text": "Mail ada.quill@example.org",
        "html": "<p>Mail ada.quill@example.org</p>",
    },
    "id": "9001",
    "url": "https://www.eventbrite.com/e/rust-night-with-ada-quill-tickets-9001",
    "vanity_url": "https://ada-quill.eventbrite.com",
    "start": {
        "timezone": "Asia/Kolkata",
        "local": "2026-11-21T18:00:00",
        "utc": "2026-11-21T12:30:00Z",
    },
    "end": {
        "timezone": "Asia/Kolkata",
        "local": "2026-11-21T20:00:00",
        "utc": "2026-11-21T14:30:00Z",
    },
    "status": "live",
    "online_event": False,
    "is_free": True,
    "summary": "Ada Quill talks about Rust. Ask ada.quill@example.org.",
    "organizer_id": "5",
    "venue_id": "6",
    "category_id": "102",
    "subcategory_id": "2004",
    "format_id": "2",
    "logo": {
        "url": "https://img.example/logo.png",
        "original": {"url": "https://img.example/o.png"},
    },
    "venue": {
        "id": "6",
        "name": "Quill Hall",
        "age_restriction": None,
        "latitude": "12.9352",
        "longitude": "77.6245",
        "address": {
            "address_1": "1 Quill Lane",
            "city": "Bengaluru",
            "latitude": "12.9352",
            "longitude": "77.6245",
            "localized_address_display": "1 Quill Lane, Koramangala, Bengaluru",
            "localized_multi_line_address_display": ["1 Quill Lane"],
        },
    },
    "organizer": {
        "id": "5",
        "name": "Ada Quill",
        "description": {"text": "Ada runs this.", "html": "<p>Ada runs this.</p>"},
        "website": "https://ada.example",
        "url": "https://www.eventbrite.com/o/ada-quill-5",
        "twitter": "@ada",
        "facebook": "https://facebook.com/ada",
    },
    "format": {
        "id": "2",
        "name": "Seminar or Talk",
        "resource_uri": "https://www.eventbriteapi.com/v3/formats/2/",
    },
}


def _raw_eb_event(**changes: Any) -> dict[str, Any]:
    event: dict[str, Any] = json.loads(_dump(_RAW_EB_EVENT))
    event.update(changes)
    return event


def _raw_eb_list(count: int = 5, *, more: bool = True) -> dict[str, Any]:
    events = []
    for number in range(count):
        event = _raw_eb_event(id=str(9001 + number))
        event["url"] = (
            f"https://www.eventbrite.com/e/rust-night-with-ada-quill-tickets-{9001 + number}"
        )
        events.append(event)
    pagination: dict[str, Any] = {
        "object_count": count,
        "page_number": 1,
        "page_size": count,
        "page_count": 2,
        "has_more_items": more,
    }
    if more:
        pagination["continuation"] = "REALCURSORdoNOTcommit"
    return {"pagination": pagination, "events": events}


def test_reduce_eventbrite_event_keeps_what_the_parser_reads_and_no_person() -> None:
    reduced = reduce_eventbrite_event(_RAW_EB_EVENT)
    text = _dump(reduced)
    for gone in (
        "Ada",
        "ADA",
        "ada",
        "example.org",
        "img.example",
        "twitter",
        "facebook",
        "description",
        "vanity_url",
        "logo",
        "organizer_id",
        "venue_id",
        "age_restriction",
        "localized_multi_line",
        "address_1",
        "html",
    ):
        assert gone not in text, gone
    assert reduced["organizer"] == {"name": "Host 1 Quill"}
    assert eventbrite_people(reduced) == ["Host 1 Quill"]
    assert personal_keys(reduced) == []
    assert set(emails_in(reduced)) <= {PLACEHOLDER_EMAIL}

    facts = eventbrite.parse_event(reduced)
    assert facts is not None
    assert facts.title == "Rust Night with Host 1 Quill"
    assert facts.snippet == f"Host 1 Quill talks about Rust. Ask {PLACEHOLDER_EMAIL}."
    assert facts.external_id == "9001" and facts.url.endswith("-tickets-9001")
    assert (facts.venue_name, facts.locality) == ("Quill Hall", "Bengaluru")
    assert (facts.lat, facts.lng) == (12.9352, 77.6245)
    assert facts.taxonomy == {"category": "102", "subcategory": "2004", "format": "2"}
    assert facts.is_free is True and facts.timezone == "Asia/Kolkata"
    assert facts.organiser_name == "Host 1 Quill" and facts.organiser_url is None


def test_reduce_eventbrite_event_scrubs_a_name_from_the_link_and_keeps_it_valid() -> None:
    reduced = reduce_eventbrite_event(_RAW_EB_EVENT)
    assert reduced["url"] == "https://www.eventbrite.com/e/rust-night-with-host-host-tickets-9001"
    assert eventbrite.parse_event(reduced) is not None


def test_reduce_eventbrite_event_keeps_a_public_organisation_and_its_pages() -> None:
    event = _raw_eb_event()
    event["organizer"] = {**event["organizer"], "name": "Swift Bengaluru"}
    event["name"] = {"text": "Swift Bengaluru Meetup"}
    reduced = reduce_eventbrite_event(event)
    assert reduced["organizer"] == {
        "name": "Swift Bengaluru",
        "website": "https://ada.example",
        "url": "https://www.eventbrite.com/o/ada-quill-5",
    }
    assert reduced["name"] == {"text": "Swift Bengaluru Meetup"}


def test_reduce_eventbrite_event_takes_extra_names_for_people_only_the_prose_mentions() -> None:
    event = _raw_eb_event(summary="Keynote by Eve Mentioned of Quill Labs.")
    assert "Eve Mentioned" in _dump(reduce_eventbrite_event(event))
    renamed = _dump(reduce_eventbrite_event(event, redact=("Eve Mentioned",)))
    assert "Eve Mentioned" not in renamed and "Quill Labs" in renamed


def test_reduce_eventbrite_event_keeps_an_online_event_without_a_venue() -> None:
    reduced = reduce_eventbrite_event(_raw_eb_event(venue=None, venue_id=None, online_event=True))
    assert reduced["venue"] is None and reduced["online_event"] is True
    facts = eventbrite.parse_event(reduced)
    assert facts is not None and facts.is_online and facts.venue_name is None


def test_reduce_eventbrite_event_is_deterministic_idempotent_and_never_mutates_its_input() -> None:
    snapshot = json.loads(_dump(_RAW_EB_EVENT))
    once = reduce_eventbrite_event(_RAW_EB_EVENT)
    assert snapshot == _RAW_EB_EVENT
    assert reduce_eventbrite_event(_RAW_EB_EVENT) == once
    assert reduce_eventbrite_event(once) == once


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"id": 5}, {"name": {"text": "x"}}])
def test_reduce_eventbrite_event_rejects_something_that_is_not_an_event(payload: Any) -> None:
    with pytest.raises(ValueError):
        reduce_eventbrite_event(payload)


def test_reduce_eventbrite_list_shares_pseudonyms_limits_and_replaces_the_cursor() -> None:
    reduced = reduce_eventbrite_list(_raw_eb_list(5), limit=3)
    assert len(reduced["events"]) == 3
    assert reduced["pagination"] == {
        "object_count": 5,
        "page_number": 1,
        "page_size": 5,
        "page_count": 2,
        "continuation": PLACEHOLDER_CONTINUATION,
        "has_more_items": True,
    }
    text = _dump(reduced)
    assert "REALCURSOR" not in text and "Ada" not in text
    assert eventbrite_people(reduced) == ["Host 1 Quill"]
    events, continuation = eventbrite.parse_event_list(reduced)
    assert [e.external_id for e in events] == ["9001", "9002", "9003"]
    assert continuation == PLACEHOLDER_CONTINUATION


def test_reduce_eventbrite_list_gives_each_organiser_one_pseudonym_across_events() -> None:
    page = _raw_eb_list(3)
    page["events"][1]["organizer"] = {**page["events"][1]["organizer"], "name": "Ben Fictional"}
    reduced = reduce_eventbrite_list(page)
    assert eventbrite_people(reduced) == ["Host 1 Quill", "Host 2 Quill"]
    names = [e["organizer"]["name"] for e in reduced["events"]]
    assert names == ["Host 1 Quill", "Host 2 Quill", "Host 1 Quill"]


def test_reduce_eventbrite_list_without_more_items_has_no_cursor() -> None:
    reduced = reduce_eventbrite_list(_raw_eb_list(2, more=False))
    assert reduced["pagination"]["has_more_items"] is False
    assert "continuation" not in reduced["pagination"]
    assert eventbrite.parse_event_list(reduced)[1] is None


def test_reduce_eventbrite_list_is_idempotent_and_never_mutates_its_input() -> None:
    page = _raw_eb_list(4)
    snapshot = json.loads(_dump(page))
    once = reduce_eventbrite_list(page)
    assert page == snapshot
    assert reduce_eventbrite_list(once) == once


@pytest.mark.parametrize("payload", [None, [], {}, {"events": None}, {"events": {}}, "x"])
def test_reduce_eventbrite_list_rejects_a_page_without_events(payload: Any) -> None:
    with pytest.raises(ValueError):
        reduce_eventbrite_list(payload)


# ── the Eventbrite capture, replayed ─────────────────────────────────────────

_EB_TOKEN = "CAPTURETOKEN0123456789"
_EB_ORGANIZER = {"id": "98765432101", "name": "Quill Tech Meetups", "checked": "2026-10-16"}
_EB_VENUE = {"id": "45678901234", "name": "Quill Commons", "checked": "2026-10-16"}


def _eb_served(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/v3/organizers/") or path.startswith("/v3/venues/"):
        return httpx.Response(200, json=_raw_eb_list(4))
    assert path == "/v3/events/9001/", path
    return httpx.Response(200, json=_raw_eb_event())


async def _run_eventbrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    *,
    organizers: list[dict[str, str]] | None = None,
    venues: list[dict[str, str]] | None = None,
    token: str | None = _EB_TOKEN,
    redact: tuple[str, ...] = (),
) -> tuple[int, list[float]]:
    sources = tmp_path / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "eventbrite": {
                    "organizers": [_EB_ORGANIZER] if organizers is None else organizers,
                    "venues": [] if venues is None else venues,
                    "status": "needs token and vetted organiser ids",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(capture_fixtures, "EVENT_SOURCES", sources)
    monkeypatch.setattr(capture_fixtures, "EVENTS_DIR", tmp_path / "out")
    monkeypatch.setattr(capture_fixtures, "_eventbrite_token", lambda: None)
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        code = await capture_fixtures.capture_events_eventbrite(redact, client=client, token=token)
    return code, pauses


def _seed_hand_written(tmp_path: Path) -> dict[str, bytes]:
    """The committed fixtures, copied to where the capture writes, to see them survive."""
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    seeded = {}
    for path in EVENTBRITE_FIXTURES:
        (out / path.name).write_bytes(path.read_bytes())
        seeded[path.name] = path.read_bytes()
    return seeded


def _untouched(tmp_path: Path, seeded: dict[str, bytes]) -> bool:
    out = tmp_path / "out"
    return {p.name: p.read_bytes() for p in out.iterdir()} == seeded


async def test_capture_eventbrite_skips_cleanly_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _seed_hand_written(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _eb_served(request)

    for blank in (None, ""):
        code, pauses = await _run_eventbrite(tmp_path, monkeypatch, handler, token=blank)
        assert code == 0
        assert seen == [] and pauses == []  # no request at all
        assert _untouched(tmp_path, seeded)  # the hand-written fixtures are not overwritten
        assert "SKIPPED" in capsys.readouterr().out


async def test_capture_eventbrite_skips_when_no_organiser_or_venue_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _seed_hand_written(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _eb_served(request)

    code, _ = await _run_eventbrite(tmp_path, monkeypatch, handler, organizers=[], venues=[])
    out = capsys.readouterr().out
    assert code == 0 and seen == [] and _untouched(tmp_path, seeded)
    assert "SKIPPED" in out and "needs token and vetted organiser ids" in out
    assert _EB_TOKEN not in out


async def test_capture_eventbrite_records_scrubbed_fixtures_through_the_api_politely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _eb_served(request)

    code, pauses = await _run_eventbrite(tmp_path, monkeypatch, handler)
    out_dir = tmp_path / "out"

    assert code == 0
    assert [r.url.path for r in seen] == [
        f"/v3/organizers/{_EB_ORGANIZER['id']}/events/",
        "/v3/events/9001/",
    ]
    assert pauses == [capture_fixtures._EVENTBRITE_GAP_S]  # between requests, not before the first
    for request in seen:
        assert request.url.host == "www.eventbriteapi.com"
        assert request.headers["Authorization"] == f"Bearer {_EB_TOKEN}"
        assert request.headers["User-Agent"].startswith("TalentRadar/1.0")
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "eventbrite_event.json",
        "eventbrite_organizer_events.json",
    ]
    for path in out_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        assert _EB_TOKEN not in text and "_comment" not in text
        for gone in ("Ada", "ada.quill", "example.org", "img.example", "REALCURSOR", "twitter"):
            assert gone not in text, f"{path.name}: {gone}"
        assert text.endswith("\n") and "\r" not in text
    assert _EB_TOKEN not in capsys.readouterr().out

    event = eventbrite.parse_event(
        json.loads((out_dir / "eventbrite_event.json").read_text("utf-8"))
    )
    assert event is not None and event.external_id == "9001"
    assert event.organiser_name == "Host 1 Quill"
    events, continuation = eventbrite.parse_event_list(
        json.loads((out_dir / "eventbrite_organizer_events.json").read_text("utf-8"))
    )
    assert [e.external_id for e in events] == ["9001", "9002", "9003"]
    assert continuation == PLACEHOLDER_CONTINUATION


async def test_capture_eventbrite_is_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_eventbrite(tmp_path, monkeypatch, _eb_served)
    first = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    assert first
    await _run_eventbrite(tmp_path, monkeypatch, _eb_served)
    assert {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()} == first


async def test_capture_eventbrite_applies_redact_to_names_no_rule_can_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v3/events/"):
            return _eb_served(request)
        page = _raw_eb_list(2)
        page["events"][0]["summary"] = "Keynote by Eve Mentioned."
        return httpx.Response(200, json=page)

    code, _ = await _run_eventbrite(tmp_path, monkeypatch, handler, redact=("Eve Mentioned",))
    assert code == 0
    text = (tmp_path / "out" / "eventbrite_organizer_events.json").read_text(encoding="utf-8")
    assert "Eve Mentioned" not in text and "Keynote by Host" in text


async def test_capture_eventbrite_falls_back_to_the_venue_when_the_organiser_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.startswith("/v3/organizers/"):
            return httpx.Response(403, json={"error": "NOT_AUTHORIZED"})
        return _eb_served(request)

    code, pauses = await _run_eventbrite(tmp_path, monkeypatch, handler, venues=[_EB_VENUE])
    out = capsys.readouterr().out
    assert code == 0
    assert seen == [
        f"/v3/organizers/{_EB_ORGANIZER['id']}/events/",
        f"/v3/venues/{_EB_VENUE['id']}/events/",
        "/v3/events/9001/",
    ]
    assert len(pauses) == 2
    assert '"organizer_endpoint": "unavailable"' in out  # the finding to record in sources.json
    assert _EB_TOKEN not in out
    assert (tmp_path / "out" / "eventbrite_organizer_events.json").is_file()


async def test_capture_eventbrite_does_not_fall_back_on_a_server_error_only_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v3/organizers/"):
            return httpx.Response(500)
        return _eb_served(request)

    await _run_eventbrite(tmp_path, monkeypatch, handler, venues=[_EB_VENUE])
    assert '"organizer_endpoint": "unavailable"' not in capsys.readouterr().out


async def test_capture_eventbrite_refused_everywhere_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seed_hand_written(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    code, _ = await _run_eventbrite(tmp_path, monkeypatch, handler)
    assert code == 1 and _untouched(tmp_path, seeded)


async def test_capture_eventbrite_writes_neither_file_when_the_event_call_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seed_hand_written(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v3/events/"):
            return httpx.Response(404)
        return _eb_served(request)

    code, _ = await _run_eventbrite(tmp_path, monkeypatch, handler)
    assert code == 1
    assert _untouched(
        tmp_path, seeded
    )  # the list was good, but both are written together or not at all


@pytest.mark.parametrize(
    "page",
    [
        {"events": [], "pagination": {}},
        {"events": [{"id": "x"}]},
        {"nothing": True},
        {"events": "x"},
    ],
    ids=["empty", "unreadable", "no-events", "wrong-type"],
)
async def test_capture_eventbrite_writes_nothing_for_a_page_that_parses_to_no_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: dict[str, Any]
) -> None:
    seeded = _seed_hand_written(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    code, pauses = await _run_eventbrite(tmp_path, monkeypatch, handler)
    assert code == 1 and pauses == [] and _untouched(tmp_path, seeded)


async def test_capture_eventbrite_writes_nothing_for_an_event_that_does_not_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seed_hand_written(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v3/events/"):
            return httpx.Response(200, json=_raw_eb_event(url="https://evil.example/e/x-9001"))
        return _eb_served(request)

    code, _ = await _run_eventbrite(tmp_path, monkeypatch, handler)
    assert code == 1 and _untouched(tmp_path, seeded)


async def test_capture_eventbrite_never_prints_the_token_even_when_a_call_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "INVALID_AUTH"})

    code, _ = await _run_eventbrite(tmp_path, monkeypatch, handler)
    captured = capsys.readouterr()
    assert code == 1 and _EB_TOKEN not in captured.out + captured.err


async def test_capture_events_runs_the_eventbrite_part_alone_and_skips_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seed_hand_written(tmp_path)
    monkeypatch.setattr(capture_fixtures, "EVENTS_DIR", tmp_path / "out")
    monkeypatch.setattr(capture_fixtures, "_eventbrite_token", lambda: None)
    assert await capture_fixtures.capture_events(sources=["eventbrite"]) == 0
    assert _untouched(tmp_path, seeded)


def test_events_source_accepts_eventbrite(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[tuple[Any, Any]] = []

    async def fake(redact: Any = (), sources: Any = ()) -> int:
        got.append((redact, sources))
        return 0

    monkeypatch.setattr(capture_fixtures, "capture_events", fake)
    monkeypatch.setattr(
        "sys.argv", ["capture_fixtures.py", "--events", "--events-source", "eventbrite"]
    )
    assert capture_fixtures.main() == 0
    assert got == [([], ["eventbrite"])]


def test_the_token_comes_from_the_environment_then_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVENTBRITE_TOKEN", f"  {_EB_TOKEN}  ")
    assert capture_fixtures._eventbrite_token() == _EB_TOKEN
    monkeypatch.setenv("EVENTBRITE_TOKEN", "   ")
    monkeypatch.setattr(
        "config.settings.get_settings",
        lambda: type("S", (), {"eventbrite_token": "FROMSETTINGS"})(),
    )
    assert capture_fixtures._eventbrite_token() == "FROMSETTINGS"
    monkeypatch.setattr(
        "config.settings.get_settings", lambda: type("S", (), {"eventbrite_token": None})()
    )
    assert capture_fixtures._eventbrite_token() is None
