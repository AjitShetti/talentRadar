"""
tests/test_outreach_touches.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Touches drive the follow-up clock: recording one, the due list, the daily
reach count, the Outreach page payload, the daily target and the retention
prune.

The services run against in-memory SQLite (``outreach_db``). Every person and
company here is fictional. ``NOW`` is 10:30 IST on 15 October 2026, so "today"
in India is 15 October and "tomorrow" begins at 18:30 UTC.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.outreach import (
    MAX_DAILY_TARGET,
    MAX_TOUCHES_PER_THREAD,
    PersonClock,
    StoredState,
    aware,
    effective_state,
    ist_today,
    thread_stage,
)
from services.outreach import touches as touches_module
from services.outreach.errors import Conflict, Invalid, NotFound
from services.outreach.people import add_person, update_person
from services.outreach.retention import MAX_THREADS_PER_RUN, prune_outreach, prune_statement
from services.outreach.threads import create_thread, update_thread
from services.outreach.touches import (
    conversations_started,
    due_items,
    get_target,
    home,
    reached_today,
    record_touch,
    set_target,
)
from storage.models import (
    OutreachPerson,
    OutreachSettings,
    OutreachThread,
    OutreachTouch,
    User,
)
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 10:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)

HOME_KEYS = {"target", "conversations_started", "due", "threads"}
DUE_KEYS = {
    "thread_id",
    "person_id",
    "kind",
    "due_at",
    "company_name",
    "person_name",
    "follow_up_number",
    "last_outbound_at",
    "next_step",
}


def days(n: float) -> timedelta:
    return timedelta(days=n)


def _dialect(url: str) -> Dialect:
    """The SQL dialect behind a URL, without connecting to anything."""
    return create_async_engine(url).dialect


# --- helpers -------------------------------------------------------------------


async def _open_thread(
    factory: Factory, user: User, company_name: str, *people: str
) -> tuple[str, list[str]]:
    """The user's thread with a new fictional company and the named people in it."""
    async with factory() as session:
        company = await make_company(session, company_name)
        contacts = [await make_contact(session, company, "founder", name) for name in people]
    summary, _ = await create_thread(
        str(user.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    thread_id = str(summary["id"])
    person_ids: list[str] = []
    for contact in contacts:
        detail = await add_person(str(user.id), thread_id, contact_id=str(contact.id))
        person_ids.append(next(p["id"] for p in detail["people"] if p["name"] == contact.name))
    return thread_id, person_ids


async def _start(factory: Factory, *people: str) -> tuple[User, str, list[str]]:
    async with factory() as session:
        user = await make_user(session)
    thread_id, person_ids = await _open_thread(factory, user, "Tessel AI", *people)
    return user, thread_id, person_ids


async def _touch(
    user: User,
    thread_id: str,
    person_id: str | None,
    *,
    now: datetime,
    direction: str = "out",
    channel: str = "linkedin",
    **kw: Any,
) -> dict[str, Any]:
    return await record_touch(
        str(user.id),
        thread_id,
        person_id=person_id,
        channel=channel,
        direction=direction,
        now=now,
        **kw,
    )


def _person(detail: dict[str, Any], person_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(p for p in detail["people"] if p["id"] == person_id)
    return found


def _at(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


async def _thread_row(factory: Factory, thread_id: str) -> OutreachThread:
    async with factory() as session:
        return (
            await session.execute(
                select(OutreachThread).where(OutreachThread.id == uuid.UUID(thread_id))
            )
        ).scalar_one()


async def _person_row(factory: Factory, person_id: str) -> OutreachPerson:
    async with factory() as session:
        return (
            await session.execute(
                select(OutreachPerson).where(OutreachPerson.id == uuid.UUID(person_id))
            )
        ).scalar_one()


async def _count(factory: Factory, model: type[Any]) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _set_updated(factory: Factory, thread_id: str, at: datetime) -> None:
    async with factory() as session:
        await session.execute(
            update(OutreachThread)
            .where(OutreachThread.id == uuid.UUID(thread_id))
            .values(updated_at=at)
        )
        await session.commit()


# --- the clock moves -----------------------------------------------------------


async def test_first_out_moves_the_person_and_counts_a_reach(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _set_updated(outreach_db, thread_id, NOW - days(30))
    assert await reached_today(str(user.id), now=NOW) == 0

    detail = await _touch(user, thread_id, asha, now=NOW, note="Intro, mentioned the Go work")

    person = _person(detail, asha)
    assert person["state"] == "waiting"
    assert _at(person["first_sent_at"]) == NOW
    assert _at(person["last_outbound_at"]) == NOW
    assert _at(person["next_follow_up_at"]) == NOW + days(5)
    assert person["follow_ups_sent"] == 0
    assert [(t["kind"], t["direction"], t["person_name"]) for t in detail["touches"]] == [
        ("first", "out", "Asha Verma")
    ]
    assert detail["touches"][0]["note"] == "Intro, mentioned the Go work"
    assert detail["stage"] == "waiting"
    assert await reached_today(str(user.id), now=NOW) == 1
    assert aware((await _thread_row(outreach_db, thread_id)).updated_at) == NOW


async def test_double_first_out_is_a_follow_up_not_a_second_reach(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")

    await _touch(user, thread_id, asha, now=NOW)
    detail = await _touch(user, thread_id, asha, now=NOW + timedelta(minutes=3))

    assert [t["kind"] for t in detail["touches"]] == ["follow_up", "first"]
    person = _person(detail, asha)
    assert person["state"] == "waiting"
    assert person["follow_ups_sent"] == 1
    assert await reached_today(str(user.id), now=NOW + timedelta(minutes=5)) == 1


async def test_out_after_a_reply_neither_reaches_nor_moves_the_person_back(
    outreach_db: Factory,
) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(2))
    await _touch(user, thread_id, asha, now=NOW - days(1), direction="in")

    detail = await _touch(user, thread_id, asha, now=NOW)

    assert detail["touches"][0]["kind"] == "other"
    person = _person(detail, asha)
    assert person["state"] == "replied"
    assert person["next_follow_up_at"] is None
    assert await reached_today(str(user.id), now=NOW) == 0


async def test_reply_with_next_step(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(2))
    today = ist_today(NOW)

    detail = await _touch(
        user,
        thread_id,
        asha,
        now=NOW,
        direction="in",
        note="Asked for my resume",
        next_step="Send resume",
        next_step_at=today,
    )

    person = _person(detail, asha)
    assert person["state"] == "replied"
    assert _at(person["replied_at"]) == NOW
    assert person["next_follow_up_at"] is None
    assert detail["stage"] == "replied"
    assert detail["next_step"] == "Send resume"
    assert detail["next_step_at"] == today.isoformat()
    assert (detail["touches"][0]["kind"], detail["touches"][0]["note"]) == (
        "reply",
        "Asked for my resume",
    )
    items = await due_items(str(user.id), now=NOW)
    assert [(i.kind, i.next_step, i.thread_id) for i in items] == [
        ("next_step", "Send resume", thread_id)
    ]
    assert await conversations_started(str(user.id)) == 1


async def test_a_next_step_that_is_not_due_yet_is_not_listed(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(2))
    await _touch(
        user,
        thread_id,
        asha,
        now=NOW,
        direction="in",
        next_step="Call on Friday",
        next_step_at=ist_today(NOW) + days(1),
    )

    assert await due_items(str(user.id), now=NOW) == []
    # It falls due at 00:00 IST on its day.
    assert len(await due_items(str(user.id), now=datetime(2026, 10, 15, 18, 30, tzinfo=UTC))) == 1


async def test_thread_level_github_comment(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")

    first = await _touch(user, thread_id, None, now=NOW, channel="github", note="Opened a PR")
    second = await _touch(
        user, thread_id, None, now=NOW + timedelta(hours=1), channel="github", note="Replied"
    )

    assert first["touches"][0]["kind"] == "first"
    assert first["touches"][0]["person_id"] is None
    assert second["touches"][0]["kind"] == "other"
    # Nobody's clock moved.
    person = _person(second, asha)
    assert person["state"] == "planned"
    assert person["next_follow_up_at"] is None
    assert second["stage"] == "planned"
    assert await reached_today(str(user.id), now=NOW + timedelta(hours=2)) == 1


async def test_a_thread_level_reply_is_logged_and_changes_no_person(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, None, now=NOW - days(1), channel="github")

    detail = await _touch(user, thread_id, None, now=NOW, channel="github", direction="in")

    assert detail["touches"][0]["kind"] == "other"
    assert _person(detail, asha)["state"] == "planned"
    assert await conversations_started(str(user.id)) == 0


async def test_reached_today_uses_ist_day(outreach_db: Factory) -> None:
    user, thread_id, (asha, vikram) = await _start(outreach_db, "Asha Verma", "Vikram Rao")
    late = datetime(2026, 10, 14, 18, 20, tzinfo=UTC)  # 23:50 IST, 14 October
    early = datetime(2026, 10, 14, 18, 40, tzinfo=UTC)  # 00:10 IST, 15 October

    await _touch(user, thread_id, asha, now=NOW, happened_at=late)
    await _touch(user, thread_id, vikram, now=NOW, happened_at=early)

    assert await reached_today(str(user.id), now=NOW) == 1
    # Asked from the evening of the 14th, only the 23:50 touch has happened.
    assert await reached_today(str(user.id), now=datetime(2026, 10, 14, 18, 25, tzinfo=UTC)) == 1
    # Another user reached no one.
    async with outreach_db() as session:
        other = await make_user(session)
    assert await reached_today(str(other.id), now=NOW) == 0


async def test_reached_today_counts_first_messages_only(outreach_db: Factory) -> None:
    user, thread_id, (asha, vikram) = await _start(outreach_db, "Asha Verma", "Vikram Rao")

    await _touch(user, thread_id, asha, now=NOW - timedelta(hours=3))
    await _touch(user, thread_id, asha, now=NOW - timedelta(hours=2))  # a follow-up
    await _touch(user, thread_id, vikram, now=NOW - timedelta(hours=1))
    await _touch(user, thread_id, vikram, now=NOW, direction="in")  # a reply

    assert await reached_today(str(user.id), now=NOW) == 2


# --- validation ----------------------------------------------------------------


async def test_touch_validation(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    _, (stranger,) = await _open_thread(outreach_db, user, "Ledgerline", "Meera Pillai")

    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, channel="carrier_pigeon")
    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, direction="sideways")
    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, happened_at=NOW + timedelta(minutes=1))
    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, happened_at=NOW - days(31))
    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, body="x" * 2001)
    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, note="x" * 501)
    with pytest.raises(Invalid):
        await _touch(user, thread_id, asha, now=NOW, next_step="x" * 141)
    with pytest.raises(NotFound):
        await _touch(user, thread_id, stranger, now=NOW)  # a person in another thread
    with pytest.raises(NotFound):
        await _touch(user, thread_id, "not-a-uuid", now=NOW)
    with pytest.raises(NotFound):
        await _touch(user, str(uuid.uuid4()), asha, now=NOW)

    # Nothing was written, and the person has not moved.
    assert await _count(outreach_db, OutreachTouch) == 0
    assert (await _person_row(outreach_db, asha)).state == "planned"
    assert (await _person_row(outreach_db, stranger)).state == "planned"

    # The limits themselves are allowed.
    detail = await _touch(
        user,
        thread_id,
        asha,
        now=NOW,
        happened_at=NOW - days(30),
        body="x" * 2000,
        note="n" * 500,
        next_step="s" * 140,
    )
    assert len(detail["touches"][0]["body"]) == 2000


async def test_text_is_cleaned_and_a_blank_note_is_nothing(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")

    detail = await _touch(
        user,
        thread_id,
        asha,
        now=NOW,
        note="  \t  ",
        body="Hi Asha,\x00\r\n\r\n\r\n\r\nThanks\x07  a lot",
    )

    assert detail["touches"][0]["note"] is None
    assert detail["touches"][0]["body"] == "Hi Asha,\n\nThanks a lot"


async def test_another_users_thread_is_a_404_and_untouched(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    async with outreach_db() as session:
        intruder = await make_user(session)

    with pytest.raises(NotFound):
        await _touch(intruder, thread_id, asha, now=NOW)
    with pytest.raises(NotFound):
        await _touch(intruder, thread_id, None, now=NOW, channel="github")

    assert await _count(outreach_db, OutreachTouch) == 0
    assert (await _person_row(outreach_db, asha)).state == "planned"
    assert (await _thread_row(outreach_db, thread_id)).user_id == user.id


async def test_the_sixty_first_touch_is_a_conflict(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    async with outreach_db() as session:
        session.add_all(
            OutreachTouch(
                id=uuid.uuid4(),
                thread_id=uuid.UUID(thread_id),
                person_id=None,
                user_id=user.id,
                channel="other",
                direction="out",
                kind="other",
                happened_at=NOW - days(2),
            )
            for _ in range(MAX_TOUCHES_PER_THREAD - 1)
        )
        await session.commit()

    detail = await _touch(user, thread_id, asha, now=NOW)  # the 60th
    assert len(detail["touches"]) == MAX_TOUCHES_PER_THREAD

    with pytest.raises(Conflict):
        await _touch(user, thread_id, asha, now=NOW + timedelta(minutes=1))
    assert await _count(outreach_db, OutreachTouch) == MAX_TOUCHES_PER_THREAD
    assert (await _person_row(outreach_db, asha)).follow_ups_sent == 0


# --- backdating and the first reply --------------------------------------------


async def test_a_backdated_out_is_stored_but_never_moves_the_clock_back(
    outreach_db: Factory,
) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(5))
    before = await _touch(user, thread_id, asha, now=NOW - days(1))
    person = _person(before, asha)
    assert person["follow_ups_sent"] == 1
    assert _at(person["last_outbound_at"]) == NOW - days(1)
    assert _at(person["next_follow_up_at"]) == NOW + days(6)

    detail = await _touch(user, thread_id, asha, now=NOW, happened_at=NOW - days(3))

    assert len(detail["touches"]) == 3
    backdated = next(t for t in detail["touches"] if _at(t["happened_at"]) == NOW - days(3))
    assert backdated["kind"] == "other"
    person = _person(detail, asha)
    assert person["follow_ups_sent"] == 1
    assert _at(person["last_outbound_at"]) == NOW - days(1)
    assert _at(person["next_follow_up_at"]) == NOW + days(6)
    assert _at(person["first_sent_at"]) == NOW - days(5)
    assert [t["happened_at"] for t in detail["touches"]] == sorted(
        (t["happened_at"] for t in detail["touches"]), reverse=True
    )


async def test_a_backdated_reply_still_marks_the_person_replied(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(5))
    await _touch(user, thread_id, asha, now=NOW - days(1))

    detail = await _touch(
        user, thread_id, asha, now=NOW, direction="in", happened_at=NOW - days(3), note="Late log"
    )

    person = _person(detail, asha)
    assert person["state"] == "replied"
    assert _at(person["replied_at"]) == NOW - days(3)
    assert person["next_follow_up_at"] is None
    assert next(t for t in detail["touches"] if t["note"] == "Late log")["kind"] == "reply"
    assert await conversations_started(str(user.id)) == 1


async def test_replied_at_keeps_the_first_reply(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(4))
    await _touch(user, thread_id, asha, now=NOW - days(2), direction="in")

    detail = await _touch(user, thread_id, asha, now=NOW, direction="in", channel="email")
    call = await _touch(user, thread_id, asha, now=NOW, direction="out", channel="call")

    assert _at(_person(detail, asha)["replied_at"]) == NOW - days(2)
    assert _at(_person(call, asha)["replied_at"]) == NOW - days(2)
    assert detail["stage"] == "replied"
    assert (await _person_row(outreach_db, asha)).state == "replied"
    # Three replies are still one conversation.
    assert await conversations_started(str(user.id)) == 1


async def test_snooze_is_refused_once_both_follow_ups_are_spent(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=NOW - days(14))
    await _touch(user, thread_id, asha, now=NOW - days(9))
    detail = await _touch(user, thread_id, asha, now=NOW - days(2))
    assert _person(detail, asha)["follow_ups_sent"] == 2

    with pytest.raises(Invalid):
        await update_person(str(user.id), thread_id, asha, snooze_days=3)


# --- the due list --------------------------------------------------------------


async def test_due_items_are_ordered_and_only_due(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        stranger = await make_user(session)
    t_asha, (asha,) = await _open_thread(outreach_db, user, "Tessel AI", "Asha Verma")
    t_vikram, (vikram,) = await _open_thread(outreach_db, user, "Ledgerline", "Vikram Rao")
    t_meera, (meera,) = await _open_thread(outreach_db, user, "Northwind Labs", "Meera Pillai")
    t_dev, (dev,) = await _open_thread(outreach_db, user, "Quillstone", "Dev Menon")
    t_isha, (isha,) = await _open_thread(outreach_db, user, "Brightwell", "Isha Kapoor")
    t_closed, (closed,) = await _open_thread(outreach_db, user, "Halcyon Works", "Tara Joshi")
    t_dropped, (dropped,) = await _open_thread(outreach_db, user, "Marlowe Labs", "Kiran Shah")
    t_other, (other,) = await _open_thread(outreach_db, stranger, "Tessel AI Two", "Nila Rao")

    await _touch(user, t_asha, asha, now=NOW - days(7))  # due 8 Oct
    await _touch(user, t_vikram, vikram, now=NOW - days(6))  # due 9 Oct
    await _touch(user, t_meera, meera, now=NOW - days(3))
    await _touch(
        user,
        t_meera,
        meera,
        now=NOW,
        direction="in",
        next_step="Call with Vikram",
        next_step_at=ist_today(NOW),
    )
    await _touch(user, t_dev, dev, now=NOW - days(4))  # due 16 Oct 10:30 IST: tomorrow
    # Due 21:00 IST today. Later than "now", but the same IST day, so already due.
    await _touch(
        user,
        t_isha,
        isha,
        now=datetime(2026, 10, 10, 16, 0, tzinfo=UTC),
        happened_at=datetime(2026, 10, 10, 15, 30, tzinfo=UTC),
    )
    await _touch(user, t_closed, closed, now=NOW - days(8))
    await update_thread(str(user.id), t_closed, {"close_outcome": "not_now"})
    await _touch(user, t_dropped, dropped, now=NOW - days(8))
    await update_person(str(user.id), t_dropped, dropped, state="dropped")
    await _touch(stranger, t_other, other, now=NOW - days(9))

    items = await due_items(str(user.id), now=NOW)

    assert [(i.kind, i.thread_id) for i in items] == [
        ("next_step", t_meera),
        ("follow_up", t_asha),
        ("follow_up", t_vikram),
        ("follow_up", t_isha),
    ]
    step, first, _second, late = items
    assert (step.company_name, step.person_id, step.person_name, step.next_step) == (
        "Northwind Labs",
        None,
        None,
        "Call with Vikram",
    )
    assert step.follow_up_number is None
    assert (first.person_id, first.person_name, first.company_name) == (
        asha,
        "Asha Verma",
        "Tessel AI",
    )
    assert first.follow_up_number == 1
    assert first.last_outbound_at == NOW - days(7)
    assert first.due_at == NOW - days(2)
    assert first.next_step is None
    assert late.person_name == "Isha Kapoor"
    assert set(first.to_dict()) == DUE_KEYS
    # Nothing leaks across users.
    assert [i.person_name for i in await due_items(str(stranger.id), now=NOW)] == ["Nila Rao"]


async def test_a_follow_up_due_today_is_due_from_midnight_ist(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    # Due 15 October 22:00 IST (16:30 UTC).
    await _touch(
        user,
        thread_id,
        asha,
        now=datetime(2026, 10, 10, 17, 0, tzinfo=UTC),
        happened_at=datetime(2026, 10, 10, 16, 30, tzinfo=UTC),
    )
    before_midnight = datetime(2026, 10, 14, 18, 29, tzinfo=UTC)  # 23:59 IST, 14 October
    midnight = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)  # 00:00 IST, 15 October

    assert await due_items(str(user.id), now=before_midnight) == []
    assert [i.person_name for i in await due_items(str(user.id), now=midnight)] == ["Asha Verma"]
    # ... and the thread row agrees with the list.
    summary = (await home(str(user.id), now=midnight))["threads"][0]
    assert summary["due"] is True
    assert (await home(str(user.id), now=before_midnight))["threads"][0]["due"] is False


async def test_a_snoozed_follow_up_leaves_the_due_list(outreach_db: Factory) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    await _touch(user, thread_id, asha, now=datetime.now(UTC) - days(6))
    assert len(await due_items(str(user.id))) == 1

    await update_person(str(user.id), thread_id, asha, snooze_days=3)

    assert await due_items(str(user.id)) == []


# --- the page ------------------------------------------------------------------


async def test_home_shape_and_default_target(outreach_db: Factory) -> None:
    user, thread_id, (asha, vikram) = await _start(outreach_db, "Asha Verma", "Vikram Rao")
    uid = str(user.id)

    empty = await home(uid, now=NOW)
    assert set(empty) == HOME_KEYS
    assert empty["target"] == {"daily": 3, "reached_today": 0}
    assert empty["conversations_started"] == 0
    assert empty["due"] == []
    assert [t["id"] for t in empty["threads"]] == [thread_id]

    await _touch(user, thread_id, asha, now=NOW - days(7))
    await _touch(user, thread_id, vikram, now=NOW - timedelta(hours=1))
    await _touch(user, thread_id, vikram, now=NOW, direction="in")
    page = await home(uid, now=NOW)
    assert page["target"] == {"daily": 3, "reached_today": 1}
    assert page["conversations_started"] == 1
    assert [d["kind"] for d in page["due"]] == ["follow_up"]
    assert set(page["due"][0]) == DUE_KEYS
    assert page["threads"][0]["stage"] == "replied"
    assert page["threads"][0]["due"] is True

    assert await get_target(uid) == 3
    assert await set_target(uid, 5) == 5
    assert await get_target(uid) == 5
    assert (await home(uid, now=NOW))["target"] == {"daily": 5, "reached_today": 1}
    assert await set_target(uid, 0) == 0
    assert await set_target(uid, MAX_DAILY_TARGET) == MAX_DAILY_TARGET
    for bad in (11, -1):
        with pytest.raises(Invalid):
            await set_target(uid, bad)
    assert await get_target(uid) == MAX_DAILY_TARGET
    # The target is one row per user, and another user keeps the default.
    assert await _count(outreach_db, OutreachSettings) == 1
    async with outreach_db() as session:
        other = await make_user(session)
    assert await get_target(str(other.id)) == 3


async def test_home_lists_only_the_users_threads_due_first(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        stranger = await make_user(session)
    t_quiet, (quiet,) = await _open_thread(outreach_db, user, "Tessel AI", "Asha Verma")
    t_due, (due,) = await _open_thread(outreach_db, user, "Ledgerline", "Vikram Rao")
    t_closed, _ = await _open_thread(outreach_db, user, "Northwind Labs")
    await _open_thread(outreach_db, stranger, "Quillstone", "Dev Menon")
    await _touch(user, t_quiet, quiet, now=NOW - days(1))
    await _touch(user, t_due, due, now=NOW - days(7))
    await update_thread(str(user.id), t_closed, {"close_outcome": "other"})

    page = await home(str(user.id), now=NOW)

    assert [t["id"] for t in page["threads"]] == [t_due, t_quiet, t_closed]
    assert [t["company_name"] for t in page["threads"]] == [
        "Ledgerline",
        "Tessel AI",
        "Northwind Labs",
    ]
    assert page["threads"][2]["stage"] == "closed"


async def test_set_target_survives_a_lost_insert_race(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        session.add(OutreachSettings(user_id=user.id, daily_target=4))
        await session.commit()

    real_get = AsyncSession.get
    calls = 0

    async def blind_first_get(self: AsyncSession, entity: Any, ident: Any, **kw: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1 and entity is OutreachSettings:
            return None  # the other request's row is not visible yet
        return await real_get(self, entity, ident, **kw)

    monkeypatch.setattr(AsyncSession, "get", blind_first_get)

    assert await set_target(str(user.id), 6) == 6
    assert await get_target(str(user.id)) == 6
    assert await _count(outreach_db, OutreachSettings) == 1


async def test_set_target_for_a_user_that_does_not_exist_is_not_found(
    outreach_db: Factory,
) -> None:
    with pytest.raises(NotFound):
        await set_target(str(uuid.uuid4()), 4)
    assert await _count(outreach_db, OutreachSettings) == 0


def test_the_thread_lock_is_a_for_update() -> None:
    """Two simultaneous "I sent it" clicks must queue, not both start the clock."""
    statement = touches_module.locked_thread_statement(uuid.uuid4(), uuid.uuid4())
    sql = str(statement.compile(dialect=_dialect("postgresql+asyncpg://u:p@localhost/db")))
    assert "FOR UPDATE" in sql


async def test_recording_a_touch_takes_the_thread_lock(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, thread_id, (asha,) = await _start(outreach_db, "Asha Verma")
    locked: list[tuple[uuid.UUID, uuid.UUID]] = []
    build = touches_module.locked_thread_statement

    def spy(user_id: uuid.UUID, thread: uuid.UUID) -> Any:
        locked.append((user_id, thread))
        return build(user_id, thread)

    monkeypatch.setattr(touches_module, "locked_thread_statement", spy)

    await _touch(user, thread_id, asha, now=NOW)

    assert locked == [(user.id, uuid.UUID(thread_id))]


# --- retention -----------------------------------------------------------------


async def _seed_thread(
    factory: Factory,
    user: User,
    name: str,
    *,
    idle_days: float,
    closed: bool = False,
    people: tuple[tuple[str, int, float | None], ...] = (),
    touched_days_ago: float | None = None,
    updated_days_ago: float | None = None,
    with_touches: bool = True,
) -> uuid.UUID:
    """A name-only thread last touched ``idle_days`` ago.

    ``people`` are ``(state, follow_ups_sent, last_outbound_days_ago)``. Each person gets
    one touch. ``touched_days_ago`` and ``updated_days_ago`` override ``idle_days`` for
    the touches and for ``updated_at``.
    """
    touched = idle_days if touched_days_ago is None else touched_days_ago
    updated = idle_days if updated_days_ago is None else updated_days_ago
    thread_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            OutreachThread(
                id=thread_id,
                user_id=user.id,
                company_name=name,
                source="typed",
                closed_at=NOW - days(idle_days + 5) if closed else None,
                outcome="not_now" if closed else None,
                created_at=NOW - days(idle_days + 30),
                updated_at=NOW - days(updated),
            )
        )
        await session.flush()
        for index, (state, sent, last_out) in enumerate(people):
            person_id = uuid.uuid4()
            session.add(
                OutreachPerson(
                    id=person_id,
                    thread_id=thread_id,
                    name=f"{name} person {index}",
                    kind="referral",
                    state=state,
                    follow_ups_sent=sent,
                    last_outbound_at=None if last_out is None else NOW - days(last_out),
                    created_at=NOW - days(idle_days + 30),
                )
            )
            await session.flush()
            if with_touches:
                session.add(
                    OutreachTouch(
                        id=uuid.uuid4(),
                        thread_id=thread_id,
                        person_id=person_id,
                        user_id=user.id,
                        channel="linkedin",
                        direction="out",
                        kind="first",
                        happened_at=NOW - days(touched),
                    )
                )
        if not people and with_touches:
            session.add(
                OutreachTouch(
                    id=uuid.uuid4(),
                    thread_id=thread_id,
                    person_id=None,
                    user_id=user.id,
                    channel="github",
                    direction="out",
                    kind="first",
                    happened_at=NOW - days(touched),
                )
            )
        await session.commit()
    return thread_id


async def _names(factory: Factory) -> set[str]:
    async with factory() as session:
        rows = await session.execute(select(OutreachThread.company_name))
        return {row[0] for row in rows}


async def _prune(factory: Factory) -> int:
    async with factory() as session:
        return await prune_outreach(session, now=NOW)


async def test_prune_outreach(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    cold = (("waiting", 2, 366.0),)
    await _seed_thread(outreach_db, user, "Closed 366", idle_days=366, closed=True, people=cold)
    await _seed_thread(outreach_db, user, "Cold 366", idle_days=366, people=cold)
    await _seed_thread(outreach_db, user, "Closed 300", idle_days=300, closed=True, people=cold)
    await _seed_thread(
        outreach_db, user, "Waiting 400", idle_days=400, people=(("waiting", 1, 400.0),)
    )
    assert await _count(outreach_db, OutreachThread) == 4
    assert await _count(outreach_db, OutreachPerson) == 4
    assert await _count(outreach_db, OutreachTouch) == 4

    deleted = await _prune(outreach_db)

    assert deleted == 2
    assert await _names(outreach_db) == {"Closed 300", "Waiting 400"}
    # Their people and touches went with them; the others kept theirs.
    assert await _count(outreach_db, OutreachPerson) == 2
    async with outreach_db() as session:
        rows = await session.execute(
            select(OutreachThread.company_name, func.count(OutreachTouch.id))
            .join(OutreachTouch, OutreachTouch.thread_id == OutreachThread.id)
            .group_by(OutreachThread.company_name)
        )
        assert {name: count for name, count in rows} == {"Closed 300": 1, "Waiting 400": 1}
    # Nothing else was touched, and a second pass finds nothing.
    assert await _prune(outreach_db) == 0
    assert await _count(outreach_db, User) == 1


async def test_prune_uses_the_same_idea_of_cold_as_the_clock(outreach_db: Factory) -> None:
    """The SQL and ``domain.outreach`` must agree on which idle threads are cold."""
    async with outreach_db() as session:
        user = await make_user(session)
    spent = ("waiting", 2, 400.0)
    cases: dict[str, tuple[tuple[tuple[str, int, float | None], ...], bool]] = {
        "cold only": ((spent,), True),
        "cold and dropped": ((spent, ("dropped", 0, None)), True),
        "cold and planned": ((spent, ("planned", 0, None)), True),
        "two cold": ((spent, spent), True),
        "cold and replied": ((spent, ("replied", 0, 400.0)), False),
        "cold and still waiting": ((spent, ("waiting", 1, 400.0)), False),
        "cold and fresh waiting": ((spent, ("waiting", 0, 400.0)), False),
        "nudged twice, not yet ten days": ((("waiting", 2, 5.0),), False),
        "nudged once": ((("waiting", 1, 400.0),), False),
        "never written to": ((("planned", 0, None),), False),
        "only dropped": ((("dropped", 0, None),), False),
        "nobody in it": ((), False),
    }
    for name, (people, expected) in cases.items():
        clocks = [
            PersonClock(
                state=cast("StoredState", state),
                first_sent_at=None,
                last_outbound_at=None if last_out is None else NOW - days(last_out),
                follow_ups_sent=sent,
                next_follow_up_at=None,
                replied_at=None,
            )
            for state, sent, last_out in people
        ]
        stage = thread_stage([effective_state(c, NOW) for c in clocks], closed=False)
        assert (stage == "cold") is expected, name
        await _seed_thread(outreach_db, user, name, idle_days=400, people=people)

    await _prune(outreach_db)

    assert await _names(outreach_db) == {name for name, (_, gone) in cases.items() if not gone}


async def test_prune_needs_the_thread_to_have_been_idle_for_a_year(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    spent = (("waiting", 2, 400.0),)
    await _seed_thread(outreach_db, user, "Just a year", idle_days=365, closed=True, people=spent)
    await _seed_thread(outreach_db, user, "Just under", idle_days=364, closed=True, people=spent)
    await _seed_thread(
        outreach_db,
        user,
        "Touched lately",
        idle_days=400,
        closed=True,
        people=spent,
        touched_days_ago=10,
    )
    await _seed_thread(
        outreach_db,
        user,
        "Edited lately",
        idle_days=400,
        closed=True,
        people=spent,
        updated_days_ago=10,
    )
    await _seed_thread(
        outreach_db, user, "No touches", idle_days=400, closed=True, with_touches=False
    )

    deleted = await _prune(outreach_db)

    assert deleted == 2
    assert await _names(outreach_db) == {"Just under", "Touched lately", "Edited lately"}


async def test_prune_deletes_at_most_five_hundred_threads_a_run(outreach_db: Factory) -> None:
    assert MAX_THREADS_PER_RUN == 500
    async with outreach_db() as session:
        user = await make_user(session)
        session.add_all(
            OutreachThread(
                id=uuid.uuid4(),
                user_id=user.id,
                company_name=f"Old {index}",
                source="typed",
                closed_at=NOW - days(500),
                outcome="other",
                created_at=NOW - days(600),
                updated_at=NOW - days(500) - timedelta(minutes=index),
            )
            for index in range(MAX_THREADS_PER_RUN + 1)
        )
        await session.commit()

    assert await _prune(outreach_db) == MAX_THREADS_PER_RUN
    # The one left is the most recently active.
    assert await _names(outreach_db) == {"Old 0"}
    assert await _prune(outreach_db) == 1
    assert await _count(outreach_db, OutreachThread) == 0


def test_the_prune_is_portable_sql() -> None:
    """Plain SQLAlchemy expressions with the cutoff bound from Python: no Postgres-only
    interval function, so the same statement runs on SQLite and on Postgres."""
    statement = prune_statement(NOW)
    for url in ("postgresql+asyncpg://u:p@localhost/db", "sqlite+aiosqlite://"):
        dialect = _dialect(url)
        sql = str(statement.compile(dialect=dialect))
        assert "make_interval" not in sql
        assert "interval" not in sql.lower()
        assert sql.startswith("DELETE FROM outreach_threads")
        assert "LIMIT" in sql


# --- prune_stale_jobs ----------------------------------------------------------


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount

    def scalar(self) -> int:
        return 0


class _FakeSession:
    """Records the SQL it is given; the one statement named by ``fail_on`` raises."""

    def __init__(self, rowcount: int = 0, fail_on: str | None = None) -> None:
        self.rowcount = rowcount
        self.fail_on = fail_on
        self.statements: list[str] = []
        self.rollbacks = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_on is not None and self.fail_on in sql:
            raise RuntimeError("relation does not exist")
        return _FakeResult(self.rowcount)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_the_nightly_prune_runs_the_outreach_prune_after_the_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import storage.database as database
    from services import job_retention

    session = _FakeSession(rowcount=4)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_outreach_threads"] == 4
    news = next(i for i, s in enumerate(session.statements) if "DELETE FROM company_news" in s)
    outreach = [i for i, s in enumerate(session.statements) if "DELETE FROM outreach_threads" in s]
    assert len(outreach) == 1
    assert outreach[0] > news


async def test_a_failed_outreach_prune_does_not_lose_the_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import storage.database as database
    from services import job_retention

    # A database that predates migration 017 has no such table.
    session = _FakeSession(rowcount=3, fail_on="outreach_threads")
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)

    summary = await job_retention.prune_stale_jobs()

    assert summary["ran"] is True
    assert summary["deleted_outreach_threads"] == 0
    assert summary["deleted_news"] == 3
    assert session.rollbacks == 1
