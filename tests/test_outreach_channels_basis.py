"""Email channels say where an address came from: found (published, with the page),
saved (the user's own contact) or guessed (the company's format, never stored).
Fictional companies and people only."""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.outreach import people, threads
from services.outreach.people import _build_channels, address_format_from
from storage.models import Company, CompanyContact, OutreachPerson
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_user,
    outreach_db,
)

DOMAIN = "fictional.test"


def _contact(**kw: object) -> CompanyContact:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "company_id": uuid.uuid4(),
        "kind": "founder",
        "name": "Ravi Sample",
        "source_url": "https://fictional.test/team",
    }
    values.update(kw)
    return CompanyContact(**values)


def _email(contact: CompanyContact, fmt: tuple[str, str] | None = None) -> dict[str, object] | None:
    channels = _build_channels(
        name=contact.name or "",
        company_name="Fictional Labs",
        contact=contact,
        typed_email=None,
        typed_linkedin=None,
        address_format=fmt,  # type: ignore[arg-type]
        company_domain=DOMAIN,
    )
    return next((c for c in channels if c["channel"] == "email"), None)


FOUND = _contact(
    name="Asha Example",
    email="asha.example@fictional.test",
    email_source_url="https://fictional.test/about",
)


def test_address_format_learned_from_found_addresses() -> None:
    assert address_format_from([FOUND], DOMAIN) == ("first.last", "asha.example@fictional.test")


def test_address_format_ignores_user_saved_and_foreign_addresses() -> None:
    saved = _contact(name="Asha Example", email="asha.example@fictional.test", user_id=uuid.uuid4())
    foreign = _contact(
        name="Asha Example", email="asha.example@gmail.com", email_source_url="https://x.test/"
    )
    assert address_format_from([saved, foreign], DOMAIN) is None


def test_found_email_has_basis_found_and_host_from_email_source() -> None:
    channel = _email(FOUND)
    assert channel is not None
    assert channel["basis"] == "found"
    assert channel["url"] == "mailto:asha.example@fictional.test"
    assert channel["source_host"] == "fictional.test"
    assert channel["direct"] is True


def test_guessed_email_when_format_learned() -> None:
    channel = _email(_contact(), ("first.last", "asha.example@fictional.test"))
    assert channel == {
        "channel": "email",
        "url": "mailto:ravi.sample@fictional.test",
        "label": "ravi.sample@fictional.test",
        "direct": False,
        "source_host": None,
        "basis": "guessed",
        "learned_from": "asha.example@fictional.test",
    }


def test_no_guess_without_format() -> None:
    assert _email(_contact()) is None


def test_no_guess_for_one_word_name() -> None:
    assert _email(_contact(name="Ravi"), ("first.last", "asha.example@fictional.test")) is None


def test_no_guess_for_a_nameless_inbox() -> None:
    inbox = _contact(kind="careers_inbox", name=None)
    assert _email(inbox, ("first.last", "asha.example@fictional.test")) is None


def test_user_saved_email_basis_saved() -> None:
    saved = _contact(email="ravi@fictional.test", user_id=uuid.uuid4(), source_url=None)
    channel = _email(saved)
    assert channel is not None and channel["basis"] == "saved"


def test_non_email_channels_have_no_basis() -> None:
    channels = _build_channels(
        name="Ravi Sample",
        company_name="Fictional Labs",
        contact=_contact(),
        typed_email=None,
        typed_linkedin=None,
    )
    assert [c["basis"] for c in channels] == [None]  # the LinkedIn search only


async def _thread_with_ravi(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str, Company]:
    async with factory() as session:
        user = await make_user(session)
        company = await make_company(session, "Fictional Labs", domain=DOMAIN)
        await make_contact(
            session,
            company,
            "founder",
            "Asha Example",
            email="asha.example@fictional.test",
            email_source_url="https://fictional.test/about",
        )
        ravi = await make_contact(session, company, "leader", "Ravi Sample")
    summary, _ = await threads.create_thread(
        str(user.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    await people.add_person(str(user.id), summary["id"], contact_id=str(ravi.id))
    return str(user.id), summary["id"], company


async def test_thread_detail_shows_guess_and_stores_nothing(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    user_id, thread_id, _ = await _thread_with_ravi(outreach_db)
    detail = await people.get_thread(user_id, thread_id)
    (ravi,) = detail["people"]
    email = next(c for c in ravi["channels"] if c["channel"] == "email")
    assert email["basis"] == "guessed"
    assert email["label"] == "ravi.sample@fictional.test"

    async with outreach_db() as session:
        stored = [
            *(await session.execute(select(CompanyContact.email))).scalars(),
            *(await session.execute(select(OutreachPerson.email))).scalars(),
        ]
    assert "ravi.sample@fictional.test" not in stored


async def test_guess_not_counted_as_public_email_in_ranking(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    # Two leaders not in the thread. Zara's address was found; Aarav's would only be
    # guessed. A found address earns the reach bonus, so Zara ranks first; if a guess
    # earned it too they would tie and Aarav would win on name.
    user_id, thread_id, company = await _thread_with_ravi(outreach_db)
    async with outreach_db() as session:
        await make_contact(session, company, "leader", "Aarav Test")
        await make_contact(
            session,
            company,
            "leader",
            "Zara Found",
            email="zara.found@fictional.test",
            email_source_url="https://fictional.test/about",
        )
    detail = await people.get_thread(user_id, thread_id)
    leaders = [p["name"] for p in detail["suggested_people"] if p["kind"] == "leader"]
    assert leaders == ["Zara Found", "Aarav Test"]


async def test_email_draft_allowed_for_a_guessed_address(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.outreach import drafts

    async def no_model(*args: object, **kwargs: object) -> str:
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(drafts, "_chat", no_model)
    user_id, thread_id, _ = await _thread_with_ravi(outreach_db)
    detail = await people.get_thread(user_id, thread_id)
    (ravi,) = detail["people"]
    result = await drafts.draft(user_id, thread_id, person_id=ravi["id"], fmt="email")
    assert result["format"] == "email"
    assert result["subject"]
