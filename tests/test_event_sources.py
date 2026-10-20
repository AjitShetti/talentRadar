"""
tests/test_event_sources.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The curated source list (``data/events/sources.json``) and what each entry
vouches for: :func:`services.events.sources.load_sources` and
:func:`services.events.sources.scope_for`. No database, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domain.event_facts import SourceScope
from services.events import gdg, luma
from services.events.sources import (
    DEFAULT_PATH,
    GdgChapter,
    LumaFeed,
    SourceList,
    load_sources,
    scope_for,
)

REPO = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── the checked-in list ──────────────────────────────────────────────────────


def test_default_path_is_anchored_on_the_repository() -> None:
    assert DEFAULT_PATH == REPO / "data" / "events" / "sources.json"
    assert DEFAULT_PATH.is_absolute()
    assert DEFAULT_PATH.is_file()


def test_the_checked_in_list_loads_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = load_sources()
    monkeypatch.chdir(tmp_path)
    elsewhere = load_sources()
    assert elsewhere == here
    assert here.luma and here.gdg


def test_the_checked_in_list_is_usable() -> None:
    sources = load_sources()
    discover = [feed for feed in sources.luma if feed.kind == "discover"]
    calendars = [feed for feed in sources.luma if feed.kind == "calendar"]
    assert discover and len(calendars) >= 2
    assert sources.luma == (*discover, *calendars)  # the city feed first
    for feed in sources.luma:
        assert feed.name
        assert feed_url_for(feed)  # every id builds an address
    for chapter in sources.gdg:
        assert chapter.name
        assert gdg.chapter_url(chapter.slug)
    # A city feed lists every kind of event; only a curated tech calendar is trusted as tech.
    assert all(feed.india and not feed.trusted_tech for feed in discover)
    assert all(feed.india and feed.trusted_tech for feed in calendars)
    assert all(
        ident.isdigit() for ident in sources.eventbrite_organizers + sources.eventbrite_venues
    )


def feed_url_for(feed: LumaFeed) -> str:
    return luma.feed_url(feed.kind, feed.id)


# ── scope_for ────────────────────────────────────────────────────────────────


def test_a_luma_scope_is_named_for_the_calendar() -> None:
    feed = LumaFeed("calendar", "cal-abcdef123", "Quill Rust Collective", True, True)
    assert scope_for(feed) == SourceScope(
        key="Quill Rust Collective", india=True, trusted_tech=True
    )
    city = LumaFeed("discover", "discplace-abcdef123", "Bengaluru", False, True)
    assert scope_for(city) == SourceScope(key="Bengaluru", india=True, trusted_tech=False)
    abroad = LumaFeed("calendar", "cal-abcdef123", "Quill Online", False, False)
    assert scope_for(abroad) == SourceScope(key="Quill Online", india=False, trusted_tech=False)


def test_a_gdg_chapter_is_india_and_tech() -> None:
    scope = scope_for(GdgChapter("gdg-quill", "GDG Quill"))
    assert scope.india is True
    assert scope.trusted_tech is True
    assert scope_for("gdg") == SourceScope(key="gdg", india=True, trusted_tech=True)


@pytest.mark.parametrize("name", ["eventbrite", "something-new", ""])
def test_everything_else_is_unvouched(name: str) -> None:
    # Eventbrite is not India-wide and not all tech: India comes from the timezone, tech from the
    # category, both read per event.
    assert scope_for(name) == SourceScope(key=name, india=False, trusted_tech=False)


# ── reading the file ─────────────────────────────────────────────────────────


def test_a_good_file_round_trips(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "luma": {
                "discover": [
                    {
                        "id": "discplace-AAAAAA1",
                        "name": "Quillville",
                        "trusted_tech": False,
                        "india": True,
                    }
                ],
                "calendars": [
                    {
                        "id": "cal-BBBBBB22",
                        "name": "Rust Quill",
                        "trusted_tech": True,
                        "india": True,
                    }
                ],
            },
            "gdg": {"chapters": [{"slug": "gdg-quillville", "name": "GDG Quillville"}]},
            "eventbrite": {"organizers": ["12345"], "venues": ["67890"]},
        },
    )
    assert load_sources(path) == SourceList(
        luma=(
            LumaFeed("discover", "discplace-AAAAAA1", "Quillville", False, True),
            LumaFeed("calendar", "cal-BBBBBB22", "Rust Quill", True, True),
        ),
        gdg=(GdgChapter("gdg-quillville", "GDG Quillville"),),
        eventbrite_organizers=("12345",),
        eventbrite_venues=("67890",),
    )


def test_a_missing_or_broken_file_is_an_empty_list(tmp_path: Path) -> None:
    empty = SourceList((), (), (), ())
    assert load_sources(tmp_path / "nope.json") == empty
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_sources(broken) == empty
    assert load_sources(_write(tmp_path, ["a", "list"])) == empty
    assert load_sources(_write(tmp_path, {"luma": "text", "gdg": 3, "eventbrite": None})) == empty


def test_a_bad_entry_is_skipped_and_the_rest_kept(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "luma": {
                "discover": [
                    {"id": "discplace-AAAAAA1", "name": "Quillville", "india": True},
                    {"id": "cal-CCCCCC33", "name": "Wrong kind", "india": True},  # a calendar id
                    {"id": "discplace-x", "name": "Too short", "india": True},
                    "discplace-DDDDDD44",
                    {"name": "No id"},
                ],
                "calendars": [
                    {
                        "id": "cal-BBBBBB22",
                        "name": "Rust Quill",
                        "trusted_tech": True,
                        "india": True,
                    },
                    {
                        "id": "cal-BBBBBB22",
                        "name": "Same again",
                        "trusted_tech": True,
                        "india": True,
                    },
                    {"id": "cal-EEEEEE55", "name": "  ", "india": True},  # no usable name
                    {"id": "discplace-FFFFFF66", "name": "Wrong kind", "india": True},
                    {"id": "cal-GGGGGG77&entity=x", "name": "Smuggled", "india": True},
                    7,
                ],
            },
            "gdg": {
                "chapters": [
                    {"slug": "gdg-quillville", "name": "GDG Quillville"},
                    {"slug": "gdg-quillville", "name": "Again"},
                    {"slug": "api/event_slim", "name": "Not a chapter"},
                    {"slug": "events", "name": "The events index"},
                    {"slug": "gdg-nameless"},
                ]
            },
            "eventbrite": {
                "organizers": ["123", "12a", "", 456, {"id": "789"}, {"id": 3.5}, "123", None],
                "venues": [{"id": "55"}, "../events/1"],
            },
        },
    )
    sources = load_sources(path)
    assert [(feed.kind, feed.id) for feed in sources.luma] == [
        ("discover", "discplace-AAAAAA1"),
        ("calendar", "cal-BBBBBB22"),
    ]
    assert [chapter.slug for chapter in sources.gdg] == ["gdg-quillville"]
    assert sources.eventbrite_organizers == ("123", "456", "789")
    assert sources.eventbrite_venues == ("55",)


def test_flags_are_true_only_for_a_real_true(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "luma": {
                "calendars": [
                    {"id": "cal-BBBBBB22", "name": "One", "trusted_tech": "true", "india": 1},
                    {"id": "cal-CCCCCC33", "name": "Two"},
                ]
            }
        },
    )
    one, two = load_sources(path).luma
    assert (one.trusted_tech, one.india) == (False, False)
    assert (two.trusted_tech, two.india) == (False, False)


def test_a_name_is_flattened_and_bounded(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "luma": {
                "calendars": [
                    {
                        "id": "cal-BBBBBB22",
                        "name": "<b>Rust</b>\n  Quill " + "x" * 400,
                        "india": True,
                    }
                ]
            }
        },
    )
    (feed,) = load_sources(path).luma
    assert "<" not in feed.name and "\n" not in feed.name
    assert feed.name.startswith("Rust Quill")
    assert len(feed.name) <= 100
