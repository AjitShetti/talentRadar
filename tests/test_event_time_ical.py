"""
tests/test_event_time_ical.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pure rules behind Events: the IST day boundary, the facts a source parser
hands over, and the iCal subset read from Luma and written for a download.
No database, no network.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from domain.event_facts import EventFacts, SourceScope, flatten
from domain.event_time import (
    DEFAULT_DURATION,
    IST,
    effective_end,
    is_today,
    ist_date,
    when_label,
    window,
)
from domain.ical import MAX_ICAL_BYTES, IcalEvent, build_ics, parse_ical


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ── event_time ───────────────────────────────────────────────────────────────


def test_ist_is_a_fixed_five_thirty_offset():
    assert IST.utcoffset(None) == timedelta(hours=5, minutes=30)
    assert timedelta(hours=2) == DEFAULT_DURATION


def test_ist_date_reads_the_calendar_day_in_india():
    assert ist_date(_utc(2026, 10, 15, 18, 29)).isoformat() == "2026-10-15"
    assert ist_date(_utc(2026, 10, 15, 18, 30)).isoformat() == "2026-10-16"


def test_today_window_uses_ist_midnight():
    # 23:50 IST on Thursday 15 Oct: an event at 00:30 IST on Friday is tomorrow.
    event_start = _utc(2026, 10, 15, 19, 0)
    late_thursday = _utc(2026, 10, 15, 18, 20)
    assert not is_today(event_start, None, late_thursday)
    # 00:10 IST on Friday: the same event is today, though the UTC date is unchanged.
    early_friday = _utc(2026, 10, 15, 18, 40)
    assert is_today(event_start, None, early_friday)


def test_is_today_is_false_once_the_event_has_ended():
    start = _utc(2026, 10, 15, 4, 30)  # 10:00 IST
    end = _utc(2026, 10, 15, 5, 30)  # 11:00 IST
    assert is_today(start, end, _utc(2026, 10, 15, 5, 0))
    assert not is_today(start, end, _utc(2026, 10, 15, 6, 30))  # 12:00 IST
    # No stated end: it is assumed to run two hours, to 12:00 IST.
    assert is_today(start, None, _utc(2026, 10, 15, 6, 29))
    assert not is_today(start, None, _utc(2026, 10, 15, 6, 31))


def test_is_today_is_false_for_another_day():
    now = _utc(2026, 10, 15, 10, 0)
    assert not is_today(_utc(2026, 10, 16, 10, 0), None, now)
    assert not is_today(_utc(2026, 10, 14, 10, 0), _utc(2026, 10, 14, 12, 0), now)


def test_window_today_runs_from_ist_midnight_to_the_next():
    start, end = window("today", _utc(2026, 10, 15, 10, 0))
    assert start == _utc(2026, 10, 14, 18, 30)
    assert end == _utc(2026, 10, 15, 18, 30)
    assert start.utcoffset() == timedelta(0)


def test_window_today_after_ist_midnight_but_before_utc_midnight():
    # 00:10 IST Friday is still Thursday in UTC; the window is Friday's.
    start, end = window("today", _utc(2026, 10, 15, 18, 40))
    assert start == _utc(2026, 10, 15, 18, 30)
    assert end == _utc(2026, 10, 16, 18, 30)


def test_window_week_and_month_run_from_now():
    now = _utc(2026, 10, 15, 10, 0)
    assert window("week", now) == (now, now + timedelta(days=7))
    assert window("month", now) == (now, now + timedelta(days=30))


def test_window_weekend_from_a_thursday_starts_saturday_midnight_ist():
    start, end = window("weekend", _utc(2026, 10, 15, 10, 0))  # Thursday
    assert start == _utc(2026, 10, 16, 18, 30)  # Sat 17 Oct 00:00 IST
    assert end == _utc(2026, 10, 18, 18, 30)  # Mon 19 Oct 00:00 IST


def test_window_weekend_from_a_monday_looks_ahead_to_saturday():
    start, end = window("weekend", _utc(2026, 10, 12, 10, 0))  # Monday
    assert start == _utc(2026, 10, 16, 18, 30)
    assert end == _utc(2026, 10, 18, 18, 30)


def test_window_weekend_starts_now_when_it_is_already_the_weekend():
    saturday = _utc(2026, 10, 17, 10, 0)  # 15:30 IST Saturday
    assert window("weekend", saturday) == (saturday, _utc(2026, 10, 18, 18, 30))
    sunday = _utc(2026, 10, 18, 10, 0)  # 15:30 IST Sunday
    assert window("weekend", sunday) == (sunday, _utc(2026, 10, 18, 18, 30))


def test_window_weekend_follows_the_ist_day_not_the_utc_day():
    # 23:00 UTC Friday is already 04:30 IST Saturday.
    friday_night_utc = _utc(2026, 10, 16, 23, 0)
    assert window("weekend", friday_night_utc) == (friday_night_utc, _utc(2026, 10, 18, 18, 30))
    # 18:00 UTC Friday is 23:30 IST Friday: the weekend is still ahead.
    friday_evening_utc = _utc(2026, 10, 16, 18, 0)
    assert window("weekend", friday_evening_utc)[0] == _utc(2026, 10, 16, 18, 30)


def test_window_rejects_an_unknown_name():
    with pytest.raises(ValueError):
        window("fortnight", _utc(2026, 10, 15, 10, 0))  # type: ignore[arg-type]


def test_effective_end_assumes_two_hours_when_unstated():
    start = _utc(2026, 10, 15, 13, 0)
    assert effective_end(start, None) == start + timedelta(hours=2)


def test_effective_end_keeps_a_stated_end():
    start = _utc(2026, 10, 15, 13, 0)
    end = _utc(2026, 10, 15, 14, 30)
    assert effective_end(start, end) == end


def test_effective_end_ignores_an_end_that_is_not_after_the_start():
    start = _utc(2026, 10, 15, 13, 0)
    assert effective_end(start, start) == start + DEFAULT_DURATION
    assert effective_end(start, start - timedelta(hours=1)) == start + DEFAULT_DURATION


def test_when_label_same_day():
    label = when_label(_utc(2026, 10, 15, 13, 0), _utc(2026, 10, 15, 14, 30))
    assert label == "Thu 15 Oct · 18:30–20:00 IST"


def test_when_label_ending_on_another_day():
    label = when_label(_utc(2026, 10, 24, 4, 0), _utc(2026, 10, 25, 11, 30))
    assert label == "Sat 24 Oct 09:30 – Sun 25 Oct 17:00 IST"


def test_when_label_does_not_invent_an_end():
    assert when_label(_utc(2026, 10, 15, 13, 0), None) == "Thu 15 Oct · 18:30 IST"


def test_when_label_day_follows_ist_not_utc():
    # 19:00 UTC Thursday is 00:30 IST Friday, and the day number is not padded.
    assert when_label(_utc(2026, 11, 5, 19, 0), None) == "Fri 6 Nov · 00:30 IST"


# ── event_facts ──────────────────────────────────────────────────────────────


def test_flatten_strips_tags_unescapes_and_collapses_whitespace():
    assert flatten("<p>Hi &amp; bye</p>\n\n x", 600) == "Hi & bye x"


def test_flatten_never_returns_an_empty_string():
    assert flatten("   ", 10) is None
    assert flatten("", 10) is None
    assert flatten(None, 10) is None
    assert flatten("<p></p><br/>", 10) is None
    assert flatten("&nbsp;", 10) is None
    assert flatten("text", 0) is None


def test_flatten_bounds_a_long_text_at_a_word():
    out = flatten("word " * 140, 600)  # 700 characters
    assert out is not None
    assert out.endswith("…")
    assert len(out) <= 600
    assert out[:-1].split(" ")[-1] == "word"  # no word was cut in half


def test_flatten_cuts_at_a_word_boundary_not_inside_a_word():
    out = flatten("alpha beta gamma delta", 14)
    assert out == "alpha beta…"


def test_flatten_hard_cuts_a_single_long_token():
    out = flatten("x" * 50, 10)
    assert out == "x" * 9 + "…"


def test_flatten_leaves_a_short_text_alone():
    assert flatten("Short and sweet", 600) == "Short and sweet"
    assert flatten("exactly ten", 11) == "exactly ten"


def test_flatten_treats_block_tags_as_word_breaks_but_inline_tags_as_nothing():
    assert flatten("<p>One</p><p>Two</p>", 100) == "One Two"
    assert flatten("a<br>b", 100) == "a b"
    assert flatten("<b>Bold</b>ly <a href='x'>go</a>", 100) == "Boldly go"


def test_flatten_keeps_a_bare_less_than_sign():
    assert flatten("Under <5 min and >3 hrs", 100) == "Under <5 min and >3 hrs"


def test_flatten_removes_control_characters_a_database_would_refuse():
    assert flatten("a\x00b\x07c &#0; d", 100) == "abc \ufffd d"


def test_flatten_is_linear_on_unclosed_angle_brackets():
    # Thousands of "<a" with no closing ">" must not backtrack quadratically.
    assert flatten("<a " * 20000, 50) is not None


def test_source_scope_and_event_facts_are_frozen_values():
    scope = SourceScope(key="luma:discover", india=True, trusted_tech=False)
    with pytest.raises(FrozenInstanceError):
        scope.india = False  # type: ignore[misc]

    facts = EventFacts(
        source="luma",
        external_id="evt-abc",
        url="https://luma.com/abc",
        title="Swift Bengaluru",
        starts_at=_utc(2026, 10, 15, 13, 0),
    )
    assert facts.ends_at is None
    assert facts.hosts == ()
    assert facts.taxonomy == {}
    assert facts.address_withheld is False
    assert facts.is_online is False
    assert facts.is_free is None
    assert facts.is_cancelled is False
    assert facts.timezone is None
    with pytest.raises(FrozenInstanceError):
        facts.title = "other"  # type: ignore[misc]


def test_event_facts_do_not_share_one_taxonomy_dict():
    base = {
        "source": "gdg",
        "external_id": "x",
        "url": "https://gdg.community.dev/e/",
        "title": "t",
        "starts_at": _utc(2026, 10, 15, 13, 0),
    }
    a, b = EventFacts(**base), EventFacts(**base)  # type: ignore[arg-type]
    assert a.taxonomy is not b.taxonomy


# ── ical: parsing ────────────────────────────────────────────────────────────


def _calendar(*events: str) -> bytes:
    body = "\r\n".join(events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Luma//EN\r\n{body}\r\nEND:VCALENDAR\r\n".encode()


def _vevent(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VEVENT", *lines, "END:VEVENT"])


def test_parse_ical_reads_a_luma_style_event():
    data = _calendar(
        _vevent(
            "UID:evt-a1B2c3@events.lu.ma",
            "DTSTAMP:20261001T000000Z",
            "DTSTART:20261015T130000Z",
            "DTEND:20261015T143000Z",
            "SUMMARY:Swift Bengaluru × Okta",
            "LOCATION:https://luma.com/swift-okta",
            "GEO:12.98;77.64",
            "STATUS:TENTATIVE",
            'ORGANIZER;CN="Team Lossfunk":MAILTO:x@y',
        )
    )
    (event,) = parse_ical(data)
    assert event == IcalEvent(
        uid="evt-a1B2c3@events.lu.ma",
        summary="Swift Bengaluru × Okta",
        description=None,
        location="https://luma.com/swift-okta",
        dtstart=_utc(2026, 10, 15, 13, 0),
        dtend=_utc(2026, 10, 15, 14, 30),
        geo=(12.98, 77.64),
        status="TENTATIVE",
        organizer_cn="Team Lossfunk",
    )


def test_parse_ical_unfolds_a_summary_spanning_two_lines():
    data = _calendar(
        _vevent(
            "UID:e1",
            "DTSTART:20261015T130000Z",
            "SUMMARY:A very long title that the feed folded onto a second li",
            " ne for us",
        )
    )
    assert (
        parse_ical(data)[0].summary
        == "A very long title that the feed folded onto a second line for us"
    )


def test_parse_ical_unfolds_with_a_tab_and_bare_newlines():
    data = b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\nDTSTART:20261015T130000Z\nSUMMARY:Tab\n\t fold\nEND:VEVENT\nEND:VCALENDAR\n"
    assert parse_ical(data)[0].summary == "Tab fold"


def test_parse_ical_unescapes_text():
    data = _calendar(
        _vevent(
            "UID:e1",
            "DTSTART:20261015T130000Z",
            r"SUMMARY:Rust\, Go \; Zig",
            r"DESCRIPTION:Line one\nLine two\NLine three \\ done",
            r"LOCATION:Hall A\, 2nd floor",
        )
    )
    (event,) = parse_ical(data)
    assert event.summary == "Rust, Go ; Zig"
    assert event.description == "Line one\nLine two\nLine three \\ done"
    assert event.location == "Hall A, 2nd floor"


def test_parse_ical_escaped_backslash_before_n_is_not_a_newline():
    data = _calendar(_vevent("UID:e1", "DTSTART:20261015T130000Z", r"SUMMARY:C:\\new"))
    assert parse_ical(data)[0].summary == "C:\\new"


def test_parse_ical_dtstart_in_utc():
    data = _calendar(_vevent("UID:e1", "DTSTART:20261015T130000Z", "SUMMARY:x"))
    assert parse_ical(data)[0].dtstart == _utc(2026, 10, 15, 13, 0)


def test_parse_ical_dtstart_with_tzid_asia_kolkata():
    data = _calendar(_vevent("UID:e1", "DTSTART;TZID=Asia/Kolkata:20261015T183000", "SUMMARY:x"))
    assert parse_ical(data)[0].dtstart == _utc(2026, 10, 15, 13, 0)


def test_parse_ical_floating_dtstart_is_taken_as_ist():
    data = _calendar(_vevent("UID:e1", "DTSTART:20261015T183000", "SUMMARY:x"))
    assert parse_ical(data)[0].dtstart == _utc(2026, 10, 15, 13, 0)


def test_parse_ical_date_value_is_midnight_ist():
    data = _calendar(
        _vevent(
            "UID:e1",
            "DTSTART;VALUE=DATE:20261015",
            "DTEND;VALUE=DATE:20261016",
            "SUMMARY:x",
        )
    )
    (event,) = parse_ical(data)
    assert event.dtstart == _utc(2026, 10, 14, 18, 30)
    assert event.dtend == _utc(2026, 10, 15, 18, 30)


def test_parse_ical_returns_aware_utc_datetimes():
    data = _calendar(_vevent("UID:e1", "DTSTART;TZID=Asia/Kolkata:20261015T183000", "SUMMARY:x"))
    assert parse_ical(data)[0].dtstart.utcoffset() == timedelta(0)


def test_parse_ical_skips_a_timezone_it_cannot_resolve():
    # No tz database here, so a TZID we do not know is not guessed at.
    data = _calendar(
        _vevent("UID:e1", "DTSTART;TZID=America/New_York:20261015T183000", "SUMMARY:x"),
        _vevent("UID:e2", "DTSTART:20261015T130000Z", "SUMMARY:y"),
    )
    assert [e.uid for e in parse_ical(data)] == ["e2"]


def test_parse_ical_skips_an_unparseable_dtstart():
    data = _calendar(
        _vevent("UID:e1", "DTSTART:not-a-date", "SUMMARY:x"),
        _vevent("UID:e2", "DTSTART:20261315T130000Z", "SUMMARY:x"),
        _vevent("UID:e3", "DTSTART:20261015T130000Z", "SUMMARY:ok"),
        _vevent("UID:e4", "DTSTART:٢٠٢٦١٠١٥T١٣٠٠٠٠Z", "SUMMARY:non-ASCII digits"),
    )
    assert [e.uid for e in parse_ical(data)] == ["e3"]


def test_parse_ical_geo_status_and_organizer():
    data = _calendar(
        _vevent(
            "UID:e1",
            "DTSTART:20261015T130000Z",
            "SUMMARY:x",
            "GEO:12.98;77.64",
            "STATUS:CANCELLED",
            'ORGANIZER;CN="Team Lossfunk":MAILTO:x@y',
        )
    )
    (event,) = parse_ical(data)
    assert event.geo == (12.98, 77.64)
    assert event.status == "CANCELLED"
    assert event.organizer_cn == "Team Lossfunk"


def test_parse_ical_organizer_cn_unquoted_and_absent():
    data = _calendar(
        _vevent("UID:e1", "DTSTART:20261015T130000Z", "ORGANIZER;CN=Plain Name:MAILTO:x@y"),
        _vevent("UID:e2", "DTSTART:20261015T130000Z", "ORGANIZER:MAILTO:x@y"),
        _vevent("UID:e3", "DTSTART:20261015T130000Z"),
    )
    assert [e.organizer_cn for e in parse_ical(data)] == ["Plain Name", None, None]


def test_parse_ical_quoted_parameter_may_hold_a_colon_and_a_semicolon():
    data = _calendar(
        _vevent("UID:e1", "DTSTART:20261015T130000Z", 'ORGANIZER;CN="Acme: Labs; India":MAILTO:x@y')
    )
    assert parse_ical(data)[0].organizer_cn == "Acme: Labs; India"


def test_parse_ical_bad_geo_is_none():
    for geo in ("GEO:abc;def", "GEO:12.98", "GEO:95;77", "GEO:12;181", "GEO:nan;77", "GEO:"):
        data = _calendar(_vevent("UID:e1", "DTSTART:20261015T130000Z", geo))
        assert parse_ical(data)[0].geo is None, geo


def test_parse_ical_skips_events_missing_uid_or_dtstart():
    data = _calendar(
        _vevent("UID:no-start", "SUMMARY:no start"),
        _vevent("DTSTART:20261015T130000Z", "SUMMARY:no uid"),
        _vevent("UID:", "DTSTART:20261015T130000Z", "SUMMARY:empty uid"),
        _vevent("UID:ok", "DTSTART:20261015T130000Z", "SUMMARY:fine"),
    )
    assert [e.uid for e in parse_ical(data)] == ["ok"]


def test_parse_ical_keeps_event_order_and_reads_every_event():
    data = _calendar(
        *[_vevent(f"UID:e{i}", f"DTSTART:2026101{i}T130000Z", f"SUMMARY:n{i}") for i in range(5)]
    )
    assert [e.uid for e in parse_ical(data)] == [f"e{i}" for i in range(5)]


def test_parse_ical_ignores_properties_of_a_nested_alarm():
    data = _calendar(
        _vevent(
            "UID:e1",
            "DTSTART:20261015T130000Z",
            "SUMMARY:Real title",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:Alarm text",
            "SUMMARY:Alarm summary",
            "END:VALARM",
        )
    )
    (event,) = parse_ical(data)
    assert event.summary == "Real title"
    assert event.description is None


def test_parse_ical_ignores_events_outside_a_vevent():
    data = b"BEGIN:VCALENDAR\r\nUID:stray\r\nDTSTART:20261015T130000Z\r\nEND:VCALENDAR\r\n"
    assert parse_ical(data) == []


def test_parse_ical_an_end_that_is_not_after_the_start_is_dropped():
    data = _calendar(
        _vevent("UID:e1", "DTSTART:20261015T130000Z", "DTEND:20261015T120000Z"),
        _vevent("UID:e2", "DTSTART:20261015T130000Z", "DTEND:garbage"),
    )
    assert [e.dtend for e in parse_ical(data)] == [None, None]


def test_parse_ical_handles_a_utf8_bom_and_non_ascii():
    data = b"\xef\xbb\xbf" + _calendar(
        _vevent("UID:e1", "DTSTART:20261015T130000Z", "SUMMARY:ಕನ್ನಡ meetup — ಬೆಂಗಳೂರು")
    )
    assert parse_ical(data)[0].summary == "ಕನ್ನಡ meetup — ಬೆಂಗಳೂರು"


def test_parse_ical_oversize_input_is_empty():
    assert parse_ical(b"x" * (MAX_ICAL_BYTES + 1)) == []
    assert MAX_ICAL_BYTES == 1_048_576


def test_parse_ical_malformed_input_is_empty_and_never_raises():
    assert parse_ical(b"\xff\xfe garbage") == []
    assert parse_ical(b"") == []
    assert parse_ical(b"BEGIN:VEVENT") == []
    assert parse_ical(b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\n") == []
    assert parse_ical(b"END:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR") == []
    assert parse_ical(b"\x00\x00\x00") == []


def test_parse_ical_year_one_does_not_overflow():
    data = _calendar(_vevent("UID:e1", "DTSTART:00010101T000000", "SUMMARY:x"))
    assert isinstance(parse_ical(data), list)


# ── ical: building ───────────────────────────────────────────────────────────

_STAMP = _utc(2026, 10, 1, 9, 0)


def _build(**overrides: object) -> str:
    args: dict[str, object] = {
        "uid": "evt-abc@events.lu.ma",
        "title": "Swift Bengaluru × Okta",
        "starts_at": _utc(2026, 10, 15, 13, 0),
        "ends_at": _utc(2026, 10, 15, 14, 30),
        "location": "Okta, Indiranagar",
        "url": "https://luma.com/swift-okta",
        "description": "A short evening.\nBring a laptop.",
        "cancelled": False,
        "stamp": _STAMP,
    }
    args.update(overrides)
    return build_ics(**args)  # type: ignore[arg-type]


def test_build_ics_round_trips_uid_summary_start_and_end():
    (event,) = parse_ical(_build().encode("utf-8"))
    assert event.uid == "evt-abc@events.lu.ma"
    assert event.summary == "Swift Bengaluru × Okta"
    assert event.dtstart == _utc(2026, 10, 15, 13, 0)
    assert event.dtend == _utc(2026, 10, 15, 14, 30)
    assert event.location == "Okta, Indiranagar"
    assert event.description == "A short evening.\nBring a laptop."
    assert event.status is None


def test_build_ics_frame_and_times_in_utc():
    out = _build()
    lines = out.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR"
    assert "VERSION:2.0" in lines
    assert "PRODID:-//TalentRadar//Events//EN" in lines
    assert "DTSTART:20261015T130000Z" in lines
    assert "DTEND:20261015T143000Z" in lines
    assert "DTSTAMP:20261001T090000Z" in lines
    assert "URL:https://luma.com/swift-okta" in lines
    assert lines[-2:] == ["END:VCALENDAR", ""]


def test_build_ics_converts_an_ist_aware_time_to_utc():
    out = _build(starts_at=datetime(2026, 10, 15, 18, 30, tzinfo=IST))
    assert "DTSTART:20261015T130000Z" in out.split("\r\n")


def test_build_ics_every_line_is_crlf_terminated_and_at_most_75_octets():
    long_title = "Ünïcödé headline ✨ " * 30
    long_desc = ("Bring your laptop, charger; and a friend. " * 40 + "\n") * 3
    out = _build(title=long_title, description=long_desc, location="L" * 400)
    assert out.endswith("\r\n")
    assert "\n" not in out.replace("\r\n", "")
    for line in out.split("\r\n")[:-1]:
        assert len(line.encode("utf-8")) <= 75, line


def test_build_ics_folding_never_splits_a_multibyte_character_and_round_trips():
    title = ("ಕನ್ನಡ ✨ " * 40).strip()
    (event,) = parse_ical(_build(title=title).encode("utf-8"))
    assert event.summary == title


def test_build_ics_escapes_text_so_a_title_cannot_inject_properties():
    hostile = "Talk\r\nBEGIN:VEVENT\r\nUID:evil\r\nEND:VEVENT"
    out = _build(title=hostile, description=hostile, location=hostile)
    events = parse_ical(out.encode("utf-8"))
    assert [e.uid for e in events] == ["evt-abc@events.lu.ma"]
    assert out.split("\r\n").count("BEGIN:VEVENT") == 1
    # The injected lines survive only as escaped text inside the one SUMMARY.
    assert events[0].summary == "Talk\nBEGIN:VEVENT\nUID:evil\nEND:VEVENT"


def test_build_ics_escapes_commas_semicolons_and_backslashes():
    (event,) = parse_ical(_build(title="a, b; c \\ d").encode("utf-8"))
    assert event.summary == "a, b; c \\ d"
    assert r"SUMMARY:a\, b\; c \\ d" in _build(title="a, b; c \\ d")


def test_build_ics_omits_absent_location_and_description():
    out = _build(location=None, description=None)
    assert "LOCATION" not in out
    assert "DESCRIPTION" not in out


def test_build_ics_marks_a_cancelled_event():
    (event,) = parse_ical(_build(cancelled=True).encode("utf-8"))
    assert event.status == "CANCELLED"


def test_build_ics_url_cannot_break_a_line():
    out = _build(url="https://example.com/a\r\nBEGIN:VEVENT b")
    lines = out.split("\r\n")
    assert lines.count("BEGIN:VEVENT") == 1
    assert "URL:https://example.com/aBEGIN:VEVENTb" in lines
    assert len(parse_ical(out.encode("utf-8"))) == 1


def test_build_ics_treats_a_naive_datetime_as_utc():
    out = _build(starts_at=datetime(2026, 10, 15, 13, 0), ends_at=datetime(2026, 10, 15, 14, 0))
    assert "DTSTART:20261015T130000Z" in out.split("\r\n")
