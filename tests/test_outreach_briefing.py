"""
tests/test_outreach_briefing.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The two Outreach cards on the Overview day plan: one for everything due today,
and one suggestion while the user is still under their daily target.

Most tests stand in for the four reads the cards are made of, so the copy and
the thresholds are what is under test. The last two run the real services on
in-memory SQLite (``outreach_db``): the follow-up card from a stored thread, and
the suggestion card agreeing with "Not now" on the id it is dismissed by.
Every person and company is fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.outreach import DueItem
from services.cache_backend import CacheBackend
from services.copilot import _card
from services.outreach import briefing
from services.outreach.briefing import outreach_cards
from services.outreach.people import add_person
from services.outreach.suggest import dismiss_suggestion, suggestion_card_id
from services.outreach.threads import create_thread
from services.outreach.touches import record_touch
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 10:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)

CARD_KEYS = set(_card(card_id="x", kind="k", title="t", detail="d"))


@pytest_asyncio.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    """The in-process cache, empty, and no attempt to reach Redis."""
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


def follow_up(
    person: str = "Asha Rao",
    company: str = "Tessel AI",
    *,
    thread_id: str = "thread-asha",
    number: int = 1,
    wrote: datetime = datetime(2026, 10, 10, 5, 0, tzinfo=UTC),
) -> DueItem:
    """A follow-up owed to ``person``: the last message was sent at ``wrote``."""
    return DueItem(
        thread_id=thread_id,
        person_id=f"person-{person}",
        kind="follow_up",
        due_at=wrote + timedelta(days=5),
        company_name=company,
        person_name=person,
        follow_up_number=number,
        last_outbound_at=wrote,
        next_step=None,
    )


def next_step(
    company: str = "Ledgerline",
    step: str | None = "Call with Vikram",
    *,
    thread_id: str = "thread-ledgerline",
    due_at: datetime = datetime(2026, 10, 14, 18, 30, tzinfo=UTC),
) -> DueItem:
    """A next step the user set on a thread where someone replied. The default is
    due from 00:00 IST on 15 October, which is "today" at ``NOW``."""
    return DueItem(
        thread_id=thread_id,
        person_id=None,
        kind="next_step",
        due_at=due_at,
        company_name=company,
        person_name=None,
        follow_up_number=None,
        last_outbound_at=None,
        next_step=step,
    )


def ranked(
    company_id: str, name: str = "Tessel AI", reasons: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {"company_id": company_id, "name": name, "reasons": list(reasons)}


@pytest.fixture
def feeds(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the four reads the cards are made of. The defaults mean nothing is due,
    nobody has been reached today, the target is 3 and no company is worth a note."""
    state: dict[str, Any] = {"due": [], "reached": 0, "target": 3, "ranked": [], "asked": 0}

    async def fake_due(user_id: str, *, now: datetime | None = None) -> list[DueItem]:
        return list(state["due"])

    async def fake_reached(user_id: str, *, now: datetime | None = None) -> int:
        return int(state["reached"])

    async def fake_target(user_id: str) -> int:
        return int(state["target"])

    async def fake_suggestions(
        user_id: str, *, limit: int = 10, now: datetime | None = None
    ) -> dict[str, Any]:
        state["asked"] += 1
        return {"suggestions": state["ranked"][:limit], "profile_complete": True}

    monkeypatch.setattr(briefing, "due_items", fake_due)
    monkeypatch.setattr(briefing, "reached_today", fake_reached)
    monkeypatch.setattr(briefing, "get_target", fake_target)
    monkeypatch.setattr(briefing, "suggestions", fake_suggestions)
    return state


# --- the follow-up card -----------------------------------------------------------


async def test_followup_card_copy(feeds: dict[str, Any]) -> None:
    feeds["due"] = [follow_up()]

    cards = await outreach_cards("u1", now=NOW)

    card = cards["followup"]
    assert card is not None
    assert set(card) == CARD_KEYS
    assert card["id"] == "outreach_followup"
    assert card["kind"] == "outreach_followup"
    assert card["tone"] == "primary"
    assert card["dismissible"] is False
    assert card["title"] == "Follow up with Asha Rao at Tessel AI"
    assert card["detail"] == (
        "You wrote on 10 Oct and haven't heard back. "
        "This is follow-up 1 of 2 — one short note, then let it rest."
    )
    assert card["actions"][0] == {
        "label": "Open Outreach",
        "href": "/outreach?thread=thread-asha",
        "style": "primary",
    }
    assert card["meta"] == {"count": 1, "thread_id": "thread-asha"}


async def test_followup_card_names_the_second_nudge(feeds: dict[str, Any]) -> None:
    feeds["due"] = [follow_up(number=2)]

    card = (await outreach_cards("u1", now=NOW))["followup"]

    assert card is not None
    assert "This is follow-up 2 of 2" in card["detail"]


async def test_followup_date_is_the_ist_day(feeds: dict[str, Any]) -> None:
    """Written at 01:30 IST on 11 October is 20:00 UTC on the 10th: it is the 11th."""
    feeds["due"] = [follow_up(wrote=datetime(2026, 10, 10, 20, 0, tzinfo=UTC))]

    card = (await outreach_cards("u1", now=NOW))["followup"]

    assert card is not None
    assert card["detail"].startswith("You wrote on 11 Oct and")


async def test_followup_card_counts_others(feeds: dict[str, Any]) -> None:
    feeds["due"] = [
        follow_up(),
        follow_up("Vikram Rao", "Ledgerline", thread_id="thread-vikram"),
    ]
    two = (await outreach_cards("u1", now=NOW))["followup"]

    feeds["due"].append(follow_up("Meera Pillai", "Northwind Labs", thread_id="thread-meera"))
    three = (await outreach_cards("u1", now=NOW))["followup"]

    assert two is not None and three is not None
    assert two["title"] == "Follow up with Asha Rao at Tessel AI and 1 other"
    assert three["title"] == "Follow up with Asha Rao at Tessel AI and 2 others"
    assert (two["meta"]["count"], three["meta"]["count"]) == (2, 3)
    # The card opens the thread it names, not the last one in the list.
    assert three["meta"]["thread_id"] == "thread-asha"
    assert three["actions"][0]["href"] == "/outreach?thread=thread-asha"


async def test_next_step_leads(feeds: dict[str, Any]) -> None:
    feeds["due"] = [next_step(), follow_up()]

    card = (await outreach_cards("u1", now=NOW))["followup"]

    assert card is not None
    assert card["id"] == "outreach_followup"
    assert card["title"] == "Next step with Ledgerline: Call with Vikram"
    assert card["detail"] == "You set this for today. 1 more due on Outreach."
    assert card["actions"][0]["href"] == "/outreach?thread=thread-ledgerline"
    assert card["meta"] == {"count": 2, "thread_id": "thread-ledgerline"}


async def test_next_step_leads_even_when_listed_after_a_follow_up(
    feeds: dict[str, Any],
) -> None:
    feeds["due"] = [follow_up(), next_step()]

    card = (await outreach_cards("u1", now=NOW))["followup"]

    assert card is not None
    assert card["title"].startswith("Next step with Ledgerline")


async def test_next_step_alone_and_overdue(feeds: dict[str, Any]) -> None:
    """With nothing else due the detail stops after the date; a step dated before today
    says which day, since "today" would be wrong."""
    feeds["due"] = [next_step()]
    today = (await outreach_cards("u1", now=NOW))["followup"]
    feeds["due"] = [next_step(due_at=datetime(2026, 10, 11, 18, 30, tzinfo=UTC), step=None)]
    overdue = (await outreach_cards("u1", now=NOW))["followup"]

    assert today is not None and overdue is not None
    assert today["detail"] == "You set this for today."
    assert overdue["title"] == "Next step with Ledgerline"
    assert overdue["detail"] == "You set this for 12 Oct."


async def test_no_followup_card_when_nothing_is_due(feeds: dict[str, Any]) -> None:
    assert (await outreach_cards("u1", now=NOW))["followup"] is None


# --- the suggestion card ----------------------------------------------------------


async def test_suggestion_card_only_under_target(feeds: dict[str, Any]) -> None:
    cid = str(uuid.uuid4())
    # The reasons arrive in the order the card should print them; the ranking that
    # puts hiring ahead of funding is the suggestion service's business, not this test's.
    feeds["ranked"] = [
        ranked(cid, reasons=("Raised Seed · 12 days ago", "Hiring Backend Engineer · 1 open"))
    ]
    feeds["reached"] = 1

    card = (await outreach_cards("u1", now=NOW))["suggestion"]

    assert card is not None
    assert set(card) == CARD_KEYS
    assert card["id"] == f"outreach_suggestion:{cid}"
    assert card["kind"] == "outreach_suggestion"
    assert card["tone"] == "action"
    assert card["dismissible"] is True
    assert card["title"] == "Reach out to Tessel AI"
    assert card["detail"] == (
        "Raised Seed · 12 days ago · Hiring Backend Engineer · 1 open. Today: 1 of 3 reached."
    )
    assert card["actions"][0]["label"] == "See who to contact"
    assert card["actions"][0]["href"] == f"/outreach?company={cid}"
    assert card["meta"] == {
        "company_id": cid,
        "reached_today": 1,
        "daily_target": 3,
        "reasons": ["Raised Seed · 12 days ago", "Hiring Backend Engineer · 1 open"],
    }

    feeds["reached"] = 3
    assert (await outreach_cards("u1", now=NOW))["suggestion"] is None
    feeds["reached"] = 5
    assert (await outreach_cards("u1", now=NOW))["suggestion"] is None
    feeds["reached"], feeds["target"] = 0, 0
    assert (await outreach_cards("u1", now=NOW))["suggestion"] is None


async def test_suggestion_is_not_ranked_when_the_card_cannot_show(feeds: dict[str, Any]) -> None:
    """Target met, or target off: the ranking is not even asked for."""
    feeds["ranked"] = [ranked(str(uuid.uuid4()), reasons=("Hiring Backend Engineer · 1 open",))]
    feeds["reached"] = 3
    await outreach_cards("u1", now=NOW)
    feeds["reached"], feeds["target"] = 0, 0
    await outreach_cards("u1", now=NOW)

    assert feeds["asked"] == 0


async def test_suggestion_card_without_reasons_still_reads(feeds: dict[str, Any]) -> None:
    cid = str(uuid.uuid4())
    feeds["ranked"] = [ranked(cid)]

    card = (await outreach_cards("u1", now=NOW))["suggestion"]

    assert card is not None
    assert card["detail"] == "Today: 0 of 3 reached."
    assert card["meta"]["reasons"] == []


async def test_no_suggestion_card_when_nothing_is_worth_a_note(feeds: dict[str, Any]) -> None:
    assert (await outreach_cards("u1", now=NOW))["suggestion"] is None


async def test_both_cards_together(feeds: dict[str, Any]) -> None:
    feeds["due"] = [follow_up()]
    feeds["ranked"] = [ranked(str(uuid.uuid4()), reasons=("1 open role",))]

    cards = await outreach_cards("u1", now=NOW)

    assert set(cards) == {"followup", "suggestion"}
    assert cards["followup"] is not None and cards["suggestion"] is not None


# --- never raises -----------------------------------------------------------------


@pytest.mark.parametrize("broken", ["due_items", "reached_today", "get_target", "suggestions"])
async def test_outreach_cards_never_raise(
    feeds: dict[str, Any], monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Asha Rao's note, which must not be logged")

    feeds["due"] = [follow_up()]
    feeds["ranked"] = [ranked(str(uuid.uuid4()), reasons=("1 open role",))]
    monkeypatch.setattr(briefing, broken, explode)

    assert await outreach_cards("u1", now=NOW) == {"followup": None, "suggestion": None}


async def test_a_failure_is_logged_without_its_message(
    feeds: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Asha Rao's note, which must not be logged")

    monkeypatch.setattr(briefing, "due_items", explode)
    with caplog.at_level("WARNING", logger=briefing.logger.name):
        await outreach_cards("u1", now=NOW)

    assert "RuntimeError" in caplog.text
    assert "Asha" not in caplog.text


# --- against the real services ------------------------------------------------------


async def test_followup_card_from_a_stored_thread(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Tessel AI")
        contact = await make_contact(session, company, "founder", "Asha Rao")
    uid = str(user.id)
    summary, _ = await create_thread(
        uid, company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    thread_id = str(summary["id"])
    detail = await add_person(uid, thread_id, contact_id=str(contact.id))
    person_id = detail["people"][0]["id"]
    await record_touch(
        uid,
        thread_id,
        person_id=person_id,
        channel="linkedin",
        direction="out",
        now=NOW - timedelta(days=7),
    )

    cards = await outreach_cards(uid, now=NOW)

    followup = cards["followup"]
    assert followup is not None
    assert followup["title"] == "Follow up with Asha Rao at Tessel AI"
    assert followup["detail"].startswith("You wrote on 8 Oct and haven't heard back.")
    assert followup["meta"] == {"count": 1, "thread_id": thread_id}
    # The company already has a thread, so it is not suggested again.
    assert cards["suggestion"] is None


async def test_suggestion_card_id_is_what_not_now_dismisses(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(
            session, "Fernhill Labs", last_funding_at=NOW - timedelta(days=12)
        )
    uid, cid = str(user.id), str(company.id)

    card = (await outreach_cards(uid, now=NOW))["suggestion"]

    assert card is not None
    assert card["id"] == suggestion_card_id(cid)
    assert card["title"] == "Reach out to Fernhill Labs"
    assert card["meta"]["company_id"] == cid
    assert card["meta"]["reached_today"] == 0
    assert card["meta"]["daily_target"] == 3

    await dismiss_suggestion(uid, cid)

    assert (await outreach_cards(uid, now=NOW))["suggestion"] is None
