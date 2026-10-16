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
    PLACEHOLDER_EMAIL,
    PUBLIC_ORGANISATIONS,
    bare_json_page,
    emails_in,
    embedded_json,
    event_people,
    gdg_chapter_people,
    person_names,
    personal_keys,
    pseudonymise_ics,
    reduce_gdg_chapter,
    reduce_gdg_event,
    scrub_gdg_slug,
)
from services.events import gdg
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
