"""
tests/test_event_source_contracts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Events source contracts: the recorded payloads under
``tests/fixtures/events/`` still parse the way the parsers expect, and they
carry nobody's real name or address.

The recordings are replayed offline. They are re-made with
``python scripts/capture_fixtures.py --events``, which reads the curated lists
in ``data/events/sources.json`` and pseudonymises people on the way in; review
the diff when you do. A recording that suddenly parses to zero events is the
change this file exists to catch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.fixture_privacy import PUBLIC_ORGANISATIONS, event_people, pseudonymise_ics
from services.events.luma import feed_url, parse_feed

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "events"
SOURCES = ROOT / "data" / "events" / "sources.json"
LUMA_FIXTURES = sorted(FIXTURES.glob("luma_*.ics"))

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
