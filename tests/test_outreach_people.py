"""
tests/test_outreach_people.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
People in a thread: adding, snoozing, dropping and removing them, the channels
each person gets, and the full thread detail the drawer renders.

The service runs against in-memory SQLite (``outreach_db``). ``channels_for`` and
``first_messages_last_7_days`` are pure functions of rows, so they are tested on
rows that were never stored. Every person and company here is fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.outreach import MAX_PEOPLE_PER_THREAD, pacing_note
from services.outreach.errors import Conflict, Invalid, NotFound
from services.outreach.people import (
    add_person,
    channels_for,
    first_messages_last_7_days,
    get_thread,
    remove_person,
    update_person,
)
from services.outreach.threads import create_thread
from storage.models import (
    Company,
    CompanyContact,
    OssIssueSnapshot,
    OutreachPerson,
    OutreachThread,
    OutreachTouch,
    Profile,
    User,
)
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 11:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)

SUMMARY_KEYS = {
    "id",
    "company_id",
    "company_name",
    "company_summary",
    "stage",
    "outcome",
    "people_count",
    "lead",
    "due",
    "next_due_at",
    "last_touch_at",
    "job",
    "application",
    "source",
    "created_at",
}
DETAIL_KEYS = SUMMARY_KEYS | {
    "note",
    "next_step",
    "next_step_at",
    "closed_at",
    "company",
    "people",
    "suggested_people",
    "touches",
    "issues",
    "careers_url",
    "linkedin_links",
    "pacing_note",
}
PERSON_KEYS = {
    "id",
    "contact_id",
    "name",
    "title",
    "kind",
    "state",
    "verified",
    "user_saved",
    "source_url",
    "source_host",
    "channels",
    "first_sent_at",
    "last_outbound_at",
    "follow_ups_sent",
    "next_follow_up_at",
    "replied_at",
    "draft",
}
CHANNEL_KEYS = {"channel", "url", "label", "direct", "source_host"}
SUGGESTED_KEYS = {
    "contact_id",
    "name",
    "title",
    "kind",
    "verified",
    "user_saved",
    "source_host",
    "reason",
    "score",
}
TOUCH_KEYS = {
    "id",
    "person_id",
    "person_name",
    "channel",
    "direction",
    "kind",
    "note",
    "body",
    "happened_at",
}
COMPANY_KEYS = {"stage", "sector", "area", "last_funding_at", "linkedin_url"}

LINKEDIN_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="


# --- helpers -------------------------------------------------------------------


async def _open(user: User, company: Company) -> str:
    summary, _ = await create_thread(
        str(user.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    return str(summary["id"])


async def _typed_thread(user: User, name: str = "Acme Robotics") -> str:
    summary, _ = await create_thread(
        str(user.id), company_id=None, company_name=name, job_id=None, source="typed"
    )
    return str(summary["id"])


async def _store_person(
    factory: Factory, thread_id: str, name: str, state: str = "planned", **kw: Any
) -> OutreachPerson:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "thread_id": uuid.UUID(thread_id),
        "name": name,
        "kind": "founder",
        "state": state,
        "follow_ups_sent": 0,
        "created_at": NOW - timedelta(days=20),
    }
    values.update(kw)
    async with factory() as session:
        person = OutreachPerson(**values)
        session.add(person)
        await session.commit()
    return person


async def _waiting(factory: Factory, thread_id: str, name: str, **kw: Any) -> OutreachPerson:
    sent = NOW - timedelta(days=6)
    values: dict[str, Any] = {
        "first_sent_at": sent,
        "last_outbound_at": sent,
        "next_follow_up_at": NOW - timedelta(days=1),
    }
    values.update(kw)
    return await _store_person(factory, thread_id, name, "waiting", **values)


async def _store_touch(
    factory: Factory,
    thread_id: str,
    user_id: uuid.UUID,
    person_id: uuid.UUID | None,
    at: datetime,
    **kw: Any,
) -> OutreachTouch:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "thread_id": uuid.UUID(thread_id),
        "person_id": person_id,
        "user_id": user_id,
        "channel": "linkedin",
        "direction": "out",
        "kind": "first",
        "happened_at": at,
    }
    values.update(kw)
    async with factory() as session:
        touch = OutreachTouch(**values)
        session.add(touch)
        await session.commit()
    return touch


async def _count(factory: Factory, model: type[Any]) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _person_row(factory: Factory, person_id: str) -> OutreachPerson:
    async with factory() as session:
        return (
            await session.execute(
                select(OutreachPerson).where(OutreachPerson.id == uuid.UUID(person_id))
            )
        ).scalar_one()


async def _nameless_contact(
    session: AsyncSession, company: Company, kind: str, **kw: Any
) -> CompanyContact:
    """A shared inbox or a careers page: a contact row with no name."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "company_id": company.id,
        "kind": kind,
        "source_kind": "curated",
    }
    values.update(kw)
    contact = CompanyContact(**values)
    session.add(contact)
    await session.commit()
    return contact


def _contact(**kw: Any) -> CompanyContact:
    """A contact row that is never stored: ``channels_for`` only reads it."""
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "company_id": uuid.uuid4(),
        "user_id": None,
        "kind": "founder",
        "name": "Asha Rao",
        "source_url": "https://tessel.example/team/asha-rao",
        "source_kind": "curated",
        "verified": False,
    }
    values.update(kw)
    return CompanyContact(**values)


def _person(**kw: Any) -> OutreachPerson:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "name": "Asha Rao",
        "kind": "founder",
        "state": "planned",
    }
    values.update(kw)
    return OutreachPerson(**values)


def _by_channel(channels: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [c for c in channels if c["channel"] == name]


# --- adding a person ---------------------------------------------------------------


async def test_add_curated_person_snapshots_and_is_idempotent(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
        founder = await make_contact(
            session, company, "founder", "Asha Rao", title="Co-founder and CEO"
        )
    thread_id = await _open(user, company)

    detail = await add_person(str(user.id), thread_id, contact_id=str(founder.id))

    [person] = detail["people"]
    assert (person["name"], person["title"], person["kind"]) == (
        "Asha Rao",
        "Co-founder and CEO",
        "founder",
    )
    assert person["contact_id"] == str(founder.id)
    assert person["state"] == "planned"
    assert detail["people_count"] == 1
    row = await _person_row(outreach_db, person["id"])
    assert (row.name, row.title, row.kind) == ("Asha Rao", "Co-founder and CEO", "founder")

    with pytest.raises(Conflict, match="Already in this thread"):
        await add_person(str(user.id), thread_id, contact_id=str(founder.id))
    assert await _count(outreach_db, OutreachPerson) == 1


async def test_add_contact_must_belong_to_the_company_and_be_visible(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Tessel AI")
        elsewhere = await make_company(session, "Ledgerline")
        foreign = await make_contact(session, elsewhere, "founder", "Vikram Nair")
        hidden = await make_contact(
            session,
            company,
            "referral",
            "Meera Pillai",
            user_id=other.id,
            source_url=None,
            source_kind="user",
        )
        mine = await make_contact(
            session,
            company,
            "referral",
            "Ila Quill",
            user_id=user.id,
            source_url=None,
            source_kind="user",
        )
        careers = await _nameless_contact(
            session, company, "careers_page", source_url="https://t.example/c"
        )
    thread_id = await _open(user, company)

    for not_allowed in (foreign.id, hidden.id, uuid.uuid4()):
        with pytest.raises(NotFound):
            await add_person(str(user.id), thread_id, contact_id=str(not_allowed))
    with pytest.raises(NotFound):
        await add_person(str(user.id), thread_id, contact_id="not-a-uuid")
    with pytest.raises(Invalid):
        await add_person(str(user.id), thread_id, contact_id=str(careers.id))
    assert await _count(outreach_db, OutreachPerson) == 0

    detail = await add_person(str(user.id), thread_id, contact_id=str(mine.id))
    assert [p["name"] for p in detail["people"]] == ["Ila Quill"]
    assert detail["people"][0]["user_saved"] is True


async def test_add_person_to_name_only_thread(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
        contact = await make_contact(session, company, "founder", "Asha Rao")
    thread_id = await _typed_thread(user)

    detail = await add_person(
        str(user.id),
        thread_id,
        contact_id=None,
        name="  Nadia   Verma ",
        title="Staff Engineer",
        email="Nadia.Verma@Example.com",
        linkedin_url="https://in.linkedin.com/in/nadia-verma/?utm_source=share",
    )

    [person] = detail["people"]
    row = await _person_row(outreach_db, person["id"])
    assert row.contact_id is None
    assert (row.name, row.title, row.kind) == ("Nadia Verma", "Staff Engineer", "referral")
    assert row.email == "nadia.verma@example.com"
    assert row.linkedin_url == "https://www.linkedin.com/in/nadia-verma"
    assert person["user_saved"] is True
    assert person["source_url"] is None
    # What was typed is the thread's alone: nothing lands in the shared table.
    assert await _count(outreach_db, CompanyContact) == 1

    bad: list[dict[str, Any]] = [
        {"name": "Ravi Iyer", "email": "not-an-email"},
        {"name": "Ravi Iyer", "email": "ravi@example"},
        {"name": "Ravi Iyer", "linkedin_url": "https://example.com/in/ravi-iyer"},
        {"name": "Ravi Iyer", "linkedin_url": "https://linkedin.com.evil.example/in/ravi"},
        {"name": "Ravi Iyer", "linkedin_url": "javascript:alert(1)"},
        {"name": "Ravi Iyer", "linkedin_url": "https://www.linkedin.com/company/tessel-ai"},
        {"name": "x" * 129},
        {"name": "   "},
        {"title": "Engineer"},
    ]
    for fields in bad:
        with pytest.raises(Invalid):
            await add_person(str(user.id), thread_id, contact_id=None, **fields)
    # A contact id means nothing where there is no company to hold contacts.
    with pytest.raises(Invalid):
        await add_person(str(user.id), thread_id, contact_id=str(contact.id))
    assert await _count(outreach_db, OutreachPerson) == 1


async def test_typed_person_in_a_company_thread_is_saved_as_a_private_contact(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    other_thread_id = await _open(other, company)

    detail = await add_person(
        str(user.id),
        thread_id,
        contact_id=None,
        name="Rhea Kapoor",
        title="Engineering Manager",
        email="rhea@example.com",
    )

    [person] = detail["people"]
    async with outreach_db() as session:
        contact = (await session.execute(select(CompanyContact))).scalar_one()
    assert (contact.user_id, contact.company_id) == (user.id, company.id)
    assert (contact.kind, contact.source_kind, contact.source_url) == ("referral", "user", None)
    assert (contact.name, contact.title, contact.email) == (
        "Rhea Kapoor",
        "Engineering Manager",
        "rhea@example.com",
    )
    assert person["contact_id"] == str(contact.id)
    assert person["user_saved"] is True
    [email] = _by_channel(person["channels"], "email")
    assert email["url"] == "mailto:rhea@example.com"
    assert email["source_host"] is None

    # It is private: another user's thread at the same company never offers her.
    theirs = await get_thread(str(other.id), other_thread_id, now=NOW)
    assert theirs["suggested_people"] == []
    with pytest.raises(NotFound):
        await add_person(str(other.id), other_thread_id, contact_id=str(contact.id))

    # Typing the same name again (any case) is the same person.
    with pytest.raises(Conflict, match="Already in this thread"):
        await add_person(str(user.id), thread_id, contact_id=None, name="rhea  KAPOOR")
    assert await _count(outreach_db, OutreachPerson) == 1


async def test_removing_a_typed_person_keeps_the_contact_and_retyping_reuses_it(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)

    first = await add_person(
        str(user.id), thread_id, contact_id=None, name="Rhea Kapoor", title="Engineering Manager"
    )
    contact_id = first["people"][0]["contact_id"]
    removed = await remove_person(str(user.id), thread_id, first["people"][0]["id"])

    # She was saved as a contact, so she stays one, and is offered like any other.
    assert await _count(outreach_db, CompanyContact) == 1
    assert [p["contact_id"] for p in removed["suggested_people"]] == [contact_id]

    again = await add_person(
        str(user.id),
        thread_id,
        contact_id=None,
        name="  rhea   KAPOOR ",
        email="rhea@example.com",
        linkedin_url="https://www.linkedin.com/in/rhea-kapoor",
    )

    assert await _count(outreach_db, CompanyContact) == 1
    [person] = again["people"]
    assert person["contact_id"] == contact_id
    assert (person["name"], person["title"], person["user_saved"]) == (
        "Rhea Kapoor",
        "Engineering Manager",
        True,
    )
    assert again["suggested_people"] == []
    # Details typed this time fill the gaps in her own private contact.
    async with outreach_db() as session:
        contact = (await session.execute(select(CompanyContact))).scalar_one()
    assert (contact.email, contact.linkedin_url) == (
        "rhea@example.com",
        "https://www.linkedin.com/in/rhea-kapoor",
    )
    assert [c["channel"] for c in person["channels"]] == ["linkedin", "email"]

    # Typing different details later does not overwrite what she already has.
    await remove_person(str(user.id), thread_id, person["id"])
    later = await add_person(
        str(user.id), thread_id, contact_id=None, name="Rhea Kapoor", email="other@example.com"
    )
    assert later["people"][0]["contact_id"] == contact_id
    async with outreach_db() as session:
        contact = (await session.execute(select(CompanyContact))).scalar_one()
    assert contact.email == "rhea@example.com"


async def test_typed_name_matching_a_public_contact_links_it_and_leaves_it_alone(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Tessel AI")
        elsewhere = await make_company(session, "Ledgerline")
        founder = await make_contact(session, company, "founder", "Asha Rao", title="CEO")
        await make_contact(session, elsewhere, "founder", "Dev Menon")
        await make_contact(
            session,
            company,
            "referral",
            "Dev Menon",
            user_id=other.id,
            source_url=None,
            source_kind="user",
        )
    thread_id = await _open(user, company)

    detail = await add_person(
        str(user.id),
        thread_id,
        contact_id=None,
        name="asha  rao",
        title="Someone else",
        email="asha.personal@example.com",
        linkedin_url="https://www.linkedin.com/in/asha-rao",
    )

    [person] = detail["people"]
    assert person["contact_id"] == str(founder.id)
    assert (person["name"], person["title"], person["kind"]) == ("Asha Rao", "CEO", "founder")
    assert person["user_saved"] is False
    assert detail["suggested_people"] == []
    async with outreach_db() as session:
        rows = (await session.execute(select(CompanyContact))).scalars().all()
        stored = next(c for c in rows if c.id == founder.id)
    assert len(rows) == 3
    # The public row is shared: nothing the user typed was written onto it.
    assert (stored.user_id, stored.email, stored.linkedin_url, stored.title) == (
        None,
        None,
        None,
        "CEO",
    )
    # Typing the name of a person who is already in the thread is still a conflict.
    with pytest.raises(Conflict, match="Already in this thread"):
        await add_person(str(user.id), thread_id, contact_id=None, name="Asha Rao")

    # Another company's contact, and another user's private one, are not matches.
    await add_person(str(user.id), thread_id, contact_id=None, name="Dev Menon")
    assert await _count(outreach_db, CompanyContact) == 4


async def test_typed_name_prefers_the_callers_own_contact_over_a_public_one(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
        await make_contact(session, company, "founder", "Asha Rao")
        mine = await make_contact(
            session,
            company,
            "referral",
            "Asha Rao",
            user_id=user.id,
            source_url=None,
            source_kind="user",
        )
    thread_id = await _open(user, company)

    detail = await add_person(str(user.id), thread_id, contact_id=None, name="Asha Rao")

    assert [p["contact_id"] for p in detail["people"]] == [str(mine.id)]
    assert detail["people"][0]["user_saved"] is True
    assert await _count(outreach_db, CompanyContact) == 2


async def test_typed_name_matching_another_users_private_contact_makes_a_new_one(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Tessel AI")
        theirs = await make_contact(
            session,
            company,
            "referral",
            "Meera Pillai",
            user_id=other.id,
            source_url=None,
            source_kind="user",
            title="Staff Engineer",
        )
    thread_id = await _open(user, company)

    detail = await add_person(
        str(user.id), thread_id, contact_id=None, name="meera pillai", email="meera@example.com"
    )

    [person] = detail["people"]
    async with outreach_db() as session:
        rows = (await session.execute(select(CompanyContact))).scalars().all()
    assert len(rows) == 2
    their_row = next(c for c in rows if c.id == theirs.id)
    # Their private row is exactly as it was, and not the one linked.
    assert (their_row.user_id, their_row.email, their_row.linkedin_url, their_row.title) == (
        other.id,
        None,
        None,
        "Staff Engineer",
    )
    assert person["contact_id"] != str(theirs.id)
    mine = next(c for c in rows if c.id != theirs.id)
    assert str(mine.id) == person["contact_id"]
    assert (mine.user_id, mine.kind, mine.name, mine.email) == (
        user.id,
        "referral",
        "meera pillai",
        "meera@example.com",
    )


async def test_a_contact_id_and_typed_fields_are_not_taken_together(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
        founder = await make_contact(session, company, "founder", "Asha Rao")
    thread_id = await _open(user, company)

    with pytest.raises(Invalid):
        await add_person(str(user.id), thread_id, contact_id=str(founder.id), name="Someone Else")
    # Blank form fields next to a contact id are just empty.
    detail = await add_person(
        str(user.id), thread_id, contact_id=str(founder.id), name="  ", title="", email=None
    )
    assert [p["name"] for p in detail["people"]] == ["Asha Rao"]


async def test_people_cap(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    thread_id = await _typed_thread(user)

    for number in range(MAX_PEOPLE_PER_THREAD):
        await add_person(str(user.id), thread_id, contact_id=None, name=f"Teammate {number}")

    with pytest.raises(Conflict):
        await add_person(str(user.id), thread_id, contact_id=None, name="Teammate Thirteen")
    assert await _count(outreach_db, OutreachPerson) == MAX_PEOPLE_PER_THREAD


# --- channels ----------------------------------------------------------------------


def test_channels_never_guess() -> None:
    sourced = _contact(email="asha@tessel.example", source_url=None)
    channels = channels_for(contact=sourced, person=_person(), company_name="Tessel AI")
    # An address with no cited page is not offered, and one is never built from a pattern.
    assert [c["channel"] for c in channels] == ["linkedin"]

    cited = _contact(email="asha@tessel.example")
    email = _by_channel(
        channels_for(contact=cited, person=_person(), company_name="Tessel AI"), "email"
    )
    assert email == [
        {
            "channel": "email",
            "url": "mailto:asha@tessel.example",
            "label": "asha@tessel.example",
            "direct": True,
            "source_host": "tessel.example",
        }
    ]

    saved = _contact(user_id=uuid.uuid4(), email="asha@tessel.example", source_url=None)
    [saved_email] = _by_channel(
        channels_for(contact=saved, person=_person(), company_name="Tessel AI"), "email"
    )
    assert saved_email["source_host"] is None

    # A user who also cited a page for their own contact still gets "saved by you".
    cited_and_saved = _contact(user_id=uuid.uuid4(), email="asha@tessel.example")
    [both] = _by_channel(
        channels_for(contact=cited_and_saved, person=_person(), company_name="Tessel AI"), "email"
    )
    assert both["source_host"] is None

    typed = _person(email="nadia@example.com")
    [typed_email] = _by_channel(
        channels_for(contact=None, person=typed, company_name="Acme Robotics"), "email"
    )
    assert typed_email["url"] == "mailto:nadia@example.com"
    assert typed_email["source_host"] is None

    for broken in ("asha at tessel", "asha@tessel", "a@b.co?bcc=x@y.zz", "asha@tessel.example>"):
        no_email = channels_for(
            contact=_contact(email=broken), person=_person(), company_name="Tessel AI"
        )
        assert _by_channel(no_email, "email") == []


def test_linkedin_channel_is_a_search_unless_a_profile_is_stored() -> None:
    [search] = channels_for(contact=_contact(), person=_person(), company_name="Tessel AI")
    assert search["channel"] == "linkedin"
    assert search["label"] == "Search LinkedIn"
    assert search["direct"] is False
    assert search["url"].startswith(LINKEDIN_SEARCH)
    assert search["source_host"] is None

    stored = _contact(linkedin_url="https://in.linkedin.com/in/asha-rao-12ab/?trk=public")
    [profile] = channels_for(contact=stored, person=_person(), company_name="Tessel AI")
    assert (profile["label"], profile["direct"]) == ("LinkedIn", True)
    # The link is rebuilt from the profile name, not echoed.
    assert profile["url"] == "https://www.linkedin.com/in/asha-rao-12ab"

    for unsafe in (
        "javascript:alert(1)",
        "https://evil.example/in/asha-rao",
        "https://www.linkedin.com.evil.example/in/asha-rao",
        "https://www.linkedin.com/company/tessel-ai",
        "ftp://www.linkedin.com/in/asha-rao",
    ):
        [fallback] = channels_for(
            contact=_contact(linkedin_url=unsafe), person=_person(), company_name="Tessel AI"
        )
        assert (fallback["label"], fallback["direct"]) == ("Search LinkedIn", False)

    # A careers inbox has no one to search for.
    inbox = _contact(kind="careers_inbox", name=None, email="careers@tessel.example")
    named_for_inbox = _person(name="Careers inbox", kind="careers_inbox")
    assert [
        c["channel"]
        for c in channels_for(contact=inbox, person=named_for_inbox, company_name="Tessel AI")
    ] == ["email"]


def test_x_and_github_channels_are_stored_profiles_only() -> None:
    contact = _contact(x_url="https://twitter.com/asharao", github_url="https://github.com/asharao")
    channels = channels_for(contact=contact, person=_person(), company_name="Tessel AI")

    assert _by_channel(channels, "x") == [
        {
            "channel": "x",
            "url": "https://x.com/asharao",
            "label": "X",
            "direct": True,
            "source_host": None,
        }
    ]
    [github] = _by_channel(channels, "github")
    assert (github["url"], github["label"]) == ("https://github.com/asharao", "GitHub")

    odd = _contact(x_url="https://x.com.evil.example/asharao", github_url="javascript:1")
    odd_channels = channels_for(contact=odd, person=_person(), company_name="Tessel AI")
    assert [c["channel"] for c in odd_channels] == ["linkedin"]


# --- the detail --------------------------------------------------------------------


async def test_detail_ranks_suggested_people_and_excludes_thread_members(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Tessel AI", stage="seed")
        elsewhere = await make_company(session, "Ledgerline")
        await make_contact(session, company, "founder", "Asha Rao")
        leader = await make_contact(session, company, "leader", "Dev Menon")
        await make_contact(session, company, "engineer", "Isha Bose")
        await make_contact(session, company, "recruiter", "Karan Shah")
        await _nameless_contact(
            session,
            company,
            "careers_inbox",
            email="careers@tessel.example",
            source_url="https://tessel.example/contact",
        )
        await _nameless_contact(
            session, company, "careers_page", source_url="https://tessel.example/careers"
        )
        await make_contact(session, elsewhere, "founder", "Vikram Nair")
        await make_contact(
            session,
            company,
            "referral",
            "Meera Pillai",
            user_id=other.id,
            source_url=None,
            source_kind="user",
        )
    thread_id = await _open(user, company)
    await add_person(str(user.id), thread_id, contact_id=str(leader.id))

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    # A seed-stage company is small: the founder first. The careers inbox is a person-less
    # shared address, so it ranks last; the careers page is a link, never a person.
    ranked = [(p["name"], p["score"]) for p in detail["suggested_people"]]
    assert ranked == [
        ("Asha Rao", 50),
        ("Isha Bose", 25),
        ("Karan Shah", 20),
        ("Careers inbox", 18),
    ]
    assert (
        detail["suggested_people"][0]["reason"] == "Founders read their own messages at this size"
    )
    assert {p["name"] for p in detail["people"]} == {"Dev Menon"}
    assert detail["careers_url"] == "https://tessel.example/careers"
    for suggested in detail["suggested_people"]:
        assert set(suggested) == SUGGESTED_KEYS
        assert suggested["kind"] != "careers_page"
        assert suggested["verified"] is False
        assert suggested["user_saved"] is False
    assert detail["suggested_people"][0]["source_host"] == "example.com"


async def test_suggested_people_follow_the_size_of_the_company(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Orbit Global", tier="big_tech")
        await make_contact(session, company, "founder", "Asha Rao")
        await make_contact(session, company, "leader", "Dev Menon")
        await make_contact(session, company, "engineer", "Isha Bose")
        await make_contact(session, company, "recruiter", "Karan Shah")
    thread_id = await _open(user, company)

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    assert [p["name"] for p in detail["suggested_people"]] == [
        "Karan Shah",
        "Isha Bose",
        "Dev Menon",
        "Asha Rao",
    ]


async def test_detail_draft_field_follows_can_draft(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    await _store_person(outreach_db, thread_id, "Planned Person")
    await _store_person(
        outreach_db,
        thread_id,
        "Replied Person",
        "replied",
        first_sent_at=NOW - timedelta(days=8),
        last_outbound_at=NOW - timedelta(days=8),
        replied_at=NOW - timedelta(days=2),
    )
    await _waiting(outreach_db, thread_id, "Waiting Person")
    await _waiting(
        outreach_db,
        thread_id,
        "Cold Person",
        follow_ups_sent=2,
        first_sent_at=NOW - timedelta(days=30),
        last_outbound_at=NOW - timedelta(days=11),
        next_follow_up_at=None,
    )
    await _store_person(outreach_db, thread_id, "Dropped Person", "dropped")

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    seen = {p["name"]: (p["state"], p["draft"]) for p in detail["people"]}
    assert seen == {
        "Planned Person": ("planned", "first"),
        "Replied Person": ("replied", None),
        "Waiting Person": ("waiting", "follow_up"),
        "Cold Person": ("cold", None),
        "Dropped Person": ("dropped", None),
    }


async def test_snooze_and_drop(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    waiting = await _waiting(outreach_db, thread_id, "Waiting Person")
    planned = await _store_person(outreach_db, thread_id, "Planned Person")
    replied = await _store_person(outreach_db, thread_id, "Replied Person", "replied")

    before = datetime.now(UTC)
    detail = await update_person(str(user.id), thread_id, str(waiting.id), snooze_days=3)
    after = datetime.now(UTC)

    snoozed = next(p for p in detail["people"] if p["id"] == str(waiting.id))
    due = datetime.fromisoformat(snoozed["next_follow_up_at"])
    assert before + timedelta(days=3) <= due <= after + timedelta(days=3)
    assert snoozed["state"] == "waiting"

    for who in (planned, replied):
        with pytest.raises(Invalid):
            await update_person(str(user.id), thread_id, str(who.id), snooze_days=3)
    for days in (0, 15, -1):
        with pytest.raises(Invalid):
            await update_person(str(user.id), thread_id, str(waiting.id), snooze_days=days)
    # Exactly one change at a time, and only one is allowed.
    with pytest.raises(Invalid):
        await update_person(str(user.id), thread_id, str(waiting.id))
    with pytest.raises(Invalid):
        await update_person(
            str(user.id), thread_id, str(waiting.id), snooze_days=2, state="dropped"
        )
    with pytest.raises(Invalid):
        await update_person(str(user.id), thread_id, str(waiting.id), state=cast("Any", "replied"))

    detail = await update_person(str(user.id), thread_id, str(waiting.id), state="dropped")

    dropped = next(p for p in detail["people"] if p["id"] == str(waiting.id))
    assert dropped["state"] == "dropped"
    assert dropped["next_follow_up_at"] is None
    row = await _person_row(outreach_db, str(waiting.id))
    assert (row.state, row.next_follow_up_at) == ("dropped", None)
    # Dropping is not counted in the thread, and doing it again changes nothing.
    assert detail["people_count"] == 2
    again = await update_person(str(user.id), thread_id, str(waiting.id), state="dropped")
    assert again["people_count"] == 2


async def test_pacing_note_in_detail(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    first = await _waiting(outreach_db, thread_id, "Asha Rao")
    second = await _waiting(outreach_db, thread_id, "Dev Menon")

    await _store_touch(outreach_db, thread_id, user.id, first.id, NOW - timedelta(days=6))
    quiet = await get_thread(str(user.id), thread_id, now=NOW)
    assert quiet["pacing_note"] is None

    # Writing to the same person again is not writing to another person.
    await _store_touch(outreach_db, thread_id, user.id, first.id, NOW - timedelta(days=3))
    assert (await get_thread(str(user.id), thread_id, now=NOW))["pacing_note"] is None

    await _store_touch(outreach_db, thread_id, user.id, second.id, NOW - timedelta(days=2))
    noisy = await get_thread(str(user.id), thread_id, now=NOW)
    assert noisy["pacing_note"] == pacing_note("Tessel AI", 2)
    assert noisy["pacing_note"] == (
        "You've written to 2 people at Tessel AI this week. Give them a few days before the next."
    )


def test_first_messages_are_counted_in_a_rolling_seven_days() -> None:
    def touch(at: datetime, **kw: Any) -> OutreachTouch:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "person_id": uuid.uuid4(),
            "direction": "out",
            "kind": "first",
            "channel": "linkedin",
            "happened_at": at,
        }
        values.update(kw)
        return OutreachTouch(**values)

    thread = OutreachThread(id=uuid.uuid4(), company_name="Tessel AI")
    thread.touches = [
        touch(NOW - timedelta(days=1)),
        touch(NOW - timedelta(days=7)),
        touch(NOW - timedelta(days=7, seconds=1)),
        touch(NOW - timedelta(days=2), kind="follow_up"),
        touch(NOW - timedelta(days=2), kind="other"),
        touch(NOW - timedelta(days=2), kind="reply", direction="in"),
        touch(NOW - timedelta(days=30)),
        # A naive timestamp is read as UTC, which is what the database returns.
        touch((NOW - timedelta(days=3)).replace(tzinfo=None)),
    ]

    assert first_messages_last_7_days(thread, NOW) == 3

    thread.touches = []
    assert first_messages_last_7_days(thread, NOW) == 0


def test_first_messages_count_people_not_touches() -> None:
    def touch(person_id: uuid.UUID | None, at: datetime, **kw: Any) -> OutreachTouch:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "person_id": person_id,
            "direction": "out",
            "kind": "first",
            "channel": "linkedin",
            "happened_at": at,
        }
        values.update(kw)
        return OutreachTouch(**values)

    asha, dev = uuid.uuid4(), uuid.uuid4()
    thread = OutreachThread(id=uuid.uuid4(), company_name="Tessel AI")

    # Two first messages to one person (a fresh start after going cold) are one person.
    thread.touches = [touch(asha, NOW - timedelta(days=1)), touch(asha, NOW - timedelta(days=3))]
    assert first_messages_last_7_days(thread, NOW) == 1

    # A different person is another. A first touch with no person (a thread-level
    # one, or one whose person was removed) cannot be matched to anyone, so each
    # counts: the note errs toward showing.
    thread.touches = [
        touch(asha, NOW - timedelta(days=1)),
        touch(asha, NOW - timedelta(days=2)),
        touch(dev, NOW - timedelta(days=2)),
        touch(None, NOW - timedelta(days=1), channel="github"),
        touch(None, NOW - timedelta(days=4), channel="github"),
    ]
    assert first_messages_last_7_days(thread, NOW) == 4

    # Only touches inside the window say who was written to.
    thread.touches = [touch(asha, NOW - timedelta(days=1)), touch(dev, NOW - timedelta(days=9))]
    assert first_messages_last_7_days(thread, NOW) == 1


async def test_pacing_survives_removing_the_people_written_to(outreach_db: Factory) -> None:
    """Removing a person detaches their touches (``person_id`` NULL); the week's
    count must not shrink because of it."""
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    asha = await _waiting(outreach_db, thread_id, "Asha Rao")
    dev = await _waiting(outreach_db, thread_id, "Dev Menon")
    await _store_touch(outreach_db, thread_id, user.id, asha.id, NOW - timedelta(days=3))
    await _store_touch(outreach_db, thread_id, user.id, dev.id, NOW - timedelta(days=2))

    await remove_person(str(user.id), thread_id, str(asha.id))
    detail = await remove_person(str(user.id), thread_id, str(dev.id))

    assert [t["person_id"] for t in detail["touches"]] == [None, None]
    assert detail["pacing_note"] == pacing_note("Tessel AI", 2)


async def test_pacing_counts_a_removed_person_and_a_thread_level_touch(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    asha = await _waiting(outreach_db, thread_id, "Asha Rao")
    await _store_touch(outreach_db, thread_id, user.id, asha.id, NOW - timedelta(days=3))
    quiet = await remove_person(str(user.id), thread_id, str(asha.id))
    assert quiet["pacing_note"] is None

    await _store_touch(
        outreach_db, thread_id, user.id, None, NOW - timedelta(days=1), channel="github"
    )
    detail = await get_thread(str(user.id), thread_id, now=NOW)

    assert detail["pacing_note"] == pacing_note("Tessel AI", 2)


async def test_remove_person_keeps_the_timeline(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    gone = await _waiting(outreach_db, thread_id, "Asha Rao")
    stays = await _waiting(outreach_db, thread_id, "Dev Menon")
    await _store_touch(outreach_db, thread_id, user.id, gone.id, NOW - timedelta(days=6))

    detail = await remove_person(str(user.id), thread_id, str(gone.id))

    assert [p["name"] for p in detail["people"]] == ["Dev Menon"]
    assert detail["people"][0]["id"] == str(stays.id)
    [touch] = detail["touches"]
    assert touch["person_id"] is None
    assert touch["person_name"] is None
    assert await _count(outreach_db, OutreachPerson) == 1
    with pytest.raises(NotFound):
        await remove_person(str(user.id), thread_id, str(gone.id))


async def test_other_users_thread_is_404(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        owner = await make_user(session)
        stranger = await make_user(session)
        company = await make_company(session, "Tessel AI")
        founder = await make_contact(session, company, "founder", "Asha Rao")
    thread_id = await _open(owner, company)
    other_thread_id = await _open(stranger, company)
    waiting = await _waiting(outreach_db, thread_id, "Dev Menon")

    attempts = (
        get_thread(str(stranger.id), thread_id, now=NOW),
        add_person(str(stranger.id), thread_id, contact_id=str(founder.id)),
        add_person(str(stranger.id), thread_id, contact_id=None, name="Intruder"),
        update_person(str(stranger.id), thread_id, str(waiting.id), state="dropped"),
        remove_person(str(stranger.id), thread_id, str(waiting.id)),
        # Their own thread, but the owner's person.
        update_person(str(stranger.id), other_thread_id, str(waiting.id), state="dropped"),
        remove_person(str(stranger.id), other_thread_id, str(waiting.id)),
        get_thread("not-a-uuid", thread_id, now=NOW),
        get_thread(str(owner.id), "not-a-uuid", now=NOW),
    )
    for attempt in attempts:
        with pytest.raises(NotFound):
            await attempt

    row = await _person_row(outreach_db, str(waiting.id))
    assert (row.state, row.thread_id) == ("waiting", uuid.UUID(thread_id))
    assert await _count(outreach_db, OutreachPerson) == 1


async def test_detail_shape(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(
            session,
            "Tessel AI",
            stage="series_a",
            sector="fintech",
            area="Indiranagar",
            last_funding_at=NOW - timedelta(days=12),
            linkedin_url="https://www.linkedin.com/company/tessel-ai",
        )
        founder = await make_contact(
            session, company, "founder", "Asha Rao", email="asha@tessel.example"
        )
        await make_contact(session, company, "engineer", "Isha Bose")
        session.add(Profile(user_id=user.id, target_roles=["Backend Engineer"]))
        await session.commit()
    thread_id = await _open(user, company)
    await add_person(str(user.id), thread_id, contact_id=str(founder.id))
    [person] = (await get_thread(str(user.id), thread_id, now=NOW))["people"]
    await _store_touch(
        outreach_db, thread_id, user.id, uuid.UUID(person["id"]), NOW - timedelta(days=1)
    )

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    assert set(detail) == DETAIL_KEYS
    assert detail["id"] == thread_id
    assert detail["company_summary"] == "What Tessel AI makes, in one line."
    assert (detail["note"], detail["next_step"], detail["next_step_at"]) == (None, None, None)
    assert detail["closed_at"] is None
    assert set(detail["company"]) == COMPANY_KEYS
    assert detail["company"] == {
        "stage": "Series A",
        "sector": "Fintech",
        "area": "Indiranagar",
        "last_funding_at": (NOW - timedelta(days=12)).isoformat(),
        "linkedin_url": "https://www.linkedin.com/company/tessel-ai",
    }
    [shown] = detail["people"]
    assert set(shown) == PERSON_KEYS
    assert all(set(channel) == CHANNEL_KEYS for channel in shown["channels"])
    assert [c["channel"] for c in shown["channels"]] == ["linkedin", "email"]
    assert shown["source_host"] == "example.com"
    assert shown["source_url"] == "https://example.com/team/asha-rao"
    [touch] = detail["touches"]
    assert set(touch) == TOUCH_KEYS
    assert touch["person_name"] == "Asha Rao"
    assert [s["name"] for s in detail["suggested_people"]] == ["Isha Bose"]
    assert detail["careers_url"] is None
    assert detail["pacing_note"] is None
    assert detail["issues"] is None
    assert [link["label"] for link in detail["linkedin_links"]] == [
        "Founders",
        "Engineering leaders",
        "Recruiters",
        "Backend Engineer",
    ]
    assert detail["linkedin_links"][0]["url"] == (
        "https://www.linkedin.com/company/tessel-ai/people/?keywords=founder"
    )


async def test_detail_of_a_name_only_thread(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        await make_contact(session, company, "founder", "Asha Rao")
        session.add(Profile(user_id=user.id, target_roles=["Backend Engineer"]))
        await session.commit()
    thread_id = await _typed_thread(user, "Acme Robotics")

    empty = await get_thread(str(user.id), thread_id, now=NOW)

    assert set(empty) == DETAIL_KEYS
    assert empty["company"] is None
    assert empty["company_id"] is None
    assert empty["people"] == []
    assert empty["suggested_people"] == []
    assert empty["linkedin_links"] == []
    assert empty["careers_url"] is None
    assert empty["issues"] is None
    assert empty["company_summary"] is None

    await add_person(
        str(user.id), thread_id, contact_id=None, name="Nadia Verma", email="nadia@example.com"
    )
    [person] = (await get_thread(str(user.id), thread_id, now=NOW))["people"]
    assert [c["channel"] for c in person["channels"]] == ["linkedin", "email"]
    assert person["channels"][0]["url"].startswith(LINKEDIN_SEARCH)


async def test_touches_come_newest_first(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    person = await _waiting(outreach_db, thread_id, "Asha Rao")
    older = await _store_touch(outreach_db, thread_id, user.id, person.id, NOW - timedelta(days=6))
    newest = await _store_touch(
        outreach_db,
        thread_id,
        user.id,
        None,
        NOW - timedelta(hours=3),
        channel="github",
        kind="other",
        note="Commented on the issue",
    )
    middle = await _store_touch(
        outreach_db,
        thread_id,
        user.id,
        person.id,
        NOW - timedelta(days=1),
        channel="email",
        direction="in",
        kind="reply",
        body="Thanks for writing",
    )

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    assert [t["id"] for t in detail["touches"]] == [str(newest.id), str(middle.id), str(older.id)]
    assert [t["person_name"] for t in detail["touches"]] == [None, "Asha Rao", "Asha Rao"]
    assert detail["touches"][0]["note"] == "Commented on the issue"
    assert detail["touches"][1]["body"] == "Thanks for writing"
    assert detail["touches"][2]["happened_at"] == (NOW - timedelta(days=6)).isoformat()


async def test_detail_carries_the_stored_snapshot(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
        session.add(
            OssIssueSnapshot(
                company_id=company.id,
                org="tessel-ai",
                status="ok",
                total_count=1,
                issues=[],
                fetched_at=NOW - timedelta(days=2),
            )
        )
        await session.commit()
    thread_id = await _open(user, company)

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    assert detail["issues"] == {
        "org": "tessel-ai",
        "status": "ok",
        "total_count": 1,
        "issues": [],
        "fetched_at": (NOW - timedelta(days=2)).isoformat(),
    }


async def test_detail_without_a_snapshot_has_no_issues(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)

    detail = await get_thread(str(user.id), thread_id, now=NOW)

    assert detail["issues"] is None
    assert detail["company"] is not None


async def test_detail_reads_the_snapshot_after_its_own_session_is_closed(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
    thread_id = await _open(user, company)
    open_sessions = 0

    @asynccontextmanager
    async def counted() -> AsyncIterator[AsyncSession]:
        nonlocal open_sessions
        open_sessions += 1
        try:
            async with outreach_db() as session:
                yield session
        finally:
            open_sessions -= 1

    open_when_asked: list[int] = []

    async def snapshot_for(company_id: Any) -> None:
        open_when_asked.append(open_sessions)

    monkeypatch.setattr("services.outreach.people.AsyncSessionLocal", counted)
    monkeypatch.setattr("services.outreach.people.snapshot_for", snapshot_for)

    await get_thread(str(user.id), thread_id, now=NOW)

    # The snapshot has its own session; two pooled connections are never held at once.
    assert open_when_asked == [0]


async def test_a_name_only_thread_asks_for_no_snapshot(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    asked: list[Any] = []

    async def snapshot_for(company_id: Any) -> None:
        asked.append(company_id)

    monkeypatch.setattr("services.outreach.people.snapshot_for", snapshot_for)

    detail = await get_thread(str(user.id), await _typed_thread(user), now=NOW)

    assert detail["issues"] is None
    assert asked == []
