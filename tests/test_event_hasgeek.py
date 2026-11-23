"""Hasgeek by link: the URL pattern, the schedule parser and the import.

Hasgeek's terms forbid crawling, so it is never read on a schedule. A link a person
pasted is matched exactly and only that project's published iCal schedule is read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from domain.event_urls import PastedEvent, parse_event_url
from domain.ical import calendar_property
from services.events import hasgeek, user
from storage.models import Event
from tests.test_event_user_api import (  # noqa: F401 - fixtures used by name
    Factory,
    add,
    factory,
    me,
    quiet_outside,
    rows_of,
    run_import,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "events" / "hasgeek_schedule.ics"
KEY = "bengalurusystemsmeetup/women-in-systems2"
LINK = f"https://hasgeek.com/{KEY}/"


def schedule() -> bytes:
    return FIXTURE.read_bytes()


def two_sessions() -> bytes:
    return (
        b"BEGIN:VCALENDAR\r\nNAME:Rootconf Mini\r\n"
        b"BEGIN:VEVENT\r\nUID:session/b@hasgeek.com\r\nSUMMARY:Talk two\r\n"
        b"DTSTART:20261205T080000Z\r\nDTEND:20261205T100000Z\r\nEND:VEVENT\r\n"
        b"BEGIN:VEVENT\r\nUID:session/a@hasgeek.com\r\nSUMMARY:Talk one\r\n"
        b"DTSTART:20261205T040000Z\r\nDTEND:20261205T050000Z\r\n"
        b"LOCATION:Bengaluru - https://hasgeek.com/rootconf/mini/\r\nEND:VEVENT\r\n"
        b"END:VCALENDAR\r\n"
    )


# ── the link ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("link", [LINK, LINK.rstrip("/"), f"{LINK}?utm=x", f"{LINK}#top"])
def test_hasgeek_link_recognised(link: str) -> None:
    assert parse_event_url(link) == PastedEvent(source="hasgeek", key=KEY)


@pytest.mark.parametrize(
    "link",
    [
        f"http://hasgeek.com/{KEY}/",
        f"https://www.hasgeek.com/{KEY}/",
        "https://hasgeek.com/bengalurusystemsmeetup/",
        f"https://hasgeek.com/{KEY}/schedule",
        f"https://hasgeek.com/{KEY}/schedule/ical",
        "https://hasgeek.com/account/x/",
        "https://hasgeek.com/about/policy/",
        "https://hasgeek.com/static/a/",
        "https://hasgeek.com/api/1/",
        "https://hasgeek.com/Org/Proj/",
        f"https://user@hasgeek.com/{KEY}/",
        f"https://hasgeek.com:443/{KEY}/",
    ],
)
def test_hasgeek_link_rejected(link: str) -> None:
    assert parse_event_url(link) is None


def test_schedule_url_is_built_from_the_key_alone() -> None:
    assert hasgeek.schedule_url(KEY) == f"https://hasgeek.com/{KEY}/schedule/ical"
    for bad in ("org", "org/proj/extra", "../x/y", "account/x", "org/pro j"):
        with pytest.raises(ValueError):
            hasgeek.schedule_url(bad)


# ── the schedule ─────────────────────────────────────────────────────────────


def test_calendar_property_reads_outside_any_event() -> None:
    assert calendar_property(schedule(), "NAME") == "Women in Systems"
    assert calendar_property(schedule(), "UID") is None  # only inside a VEVENT
    assert calendar_property(b"not a calendar", "NAME") is None


def test_parse_schedule_fixture() -> None:
    facts = hasgeek.parse_schedule(schedule(), KEY)
    assert facts is not None
    assert facts.source == "hasgeek"
    assert facts.external_id == KEY
    assert facts.url == LINK
    assert facts.title == "Women in Systems"
    assert facts.starts_at == datetime(2026, 11, 28, 4, 30, tzinfo=UTC)
    assert facts.ends_at == datetime(2026, 11, 28, 8, 0, tzinfo=UTC)
    assert facts.locality == "Bengaluru"
    assert facts.organiser_name == "Bengaluru Systems Meetup"
    assert facts.snippet is not None
    assert "Join Women in Systems" in facts.snippet
    assert "#" not in facts.snippet  # Markdown heading marks are not text


def test_parse_schedule_multi_session() -> None:
    facts = hasgeek.parse_schedule(two_sessions(), "rootconf/mini")
    assert facts is not None
    assert facts.title == "Rootconf Mini"
    assert facts.starts_at == datetime(2026, 12, 5, 4, 0, tzinfo=UTC)
    assert facts.ends_at == datetime(2026, 12, 5, 10, 0, tzinfo=UTC)
    assert facts.locality == "Bengaluru"
    assert facts.organiser_name == "rootconf"  # no ORGANIZER: the org segment


def test_parse_schedule_empty() -> None:
    assert hasgeek.parse_schedule(b"BEGIN:VCALENDAR\r\nNAME:X\r\nEND:VCALENDAR\r\n", KEY) is None
    assert hasgeek.parse_schedule(b"", KEY) is None


# ── the import ───────────────────────────────────────────────────────────────


async def test_import_hasgeek_link(
    factory: Factory,  # noqa: F811
    me: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = AsyncMock(return_value=schedule())
    monkeypatch.setattr(hasgeek, "fetch_schedule", fetch)

    result = await run_import(factory, me, LINK)

    assert result["event"]["title"] == "Women in Systems"
    assert result["event"]["source"] == "hasgeek"
    fetch.assert_awaited_once()
    assert fetch.await_args is not None
    assert fetch.await_args.args[1] == KEY
    stored = await rows_of(factory, Event, Event.source == "hasgeek")
    assert [row.external_id for row in stored] == [KEY]

    again = await run_import(factory, me, LINK.rstrip("/"))
    assert again["event"]["id"] == result["event"]["id"]
    fetch.assert_awaited_once()


async def test_import_hasgeek_empty_schedule(
    factory: Factory,  # noqa: F811
    me: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = b"BEGIN:VCALENDAR\r\nNAME:Soon\r\nEND:VCALENDAR\r\n"
    monkeypatch.setattr(hasgeek, "fetch_schedule", AsyncMock(return_value=empty))

    result = await run_import(factory, me, LINK)

    assert result == {"needs_details": True, "reason": "not_readable"}
    assert await rows_of(factory, Event, Event.source == "hasgeek") == []


def test_hasgeek_has_its_own_breaker() -> None:
    assert hasgeek.HASGEEK_KEY == "events:hasgeek"
    assert user.HASGEEK_KEY == hasgeek.HASGEEK_KEY
