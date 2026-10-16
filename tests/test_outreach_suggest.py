"""
tests/test_outreach_suggest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Company suggestions: who is listed and worth a message, why, and who inside
each one to write to; the per-user cache and what keeps it honest; "Not now".

Runs on in-memory SQLite (``outreach_db``) and the in-process cache. Companies,
people and skills here are fictional.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import services.agent_memory as agent_memory
from services.cache_backend import CacheBackend
from services.outreach import suggest
from services.outreach.errors import NotFound
from services.outreach.suggest import (
    CACHED_TOP,
    MAX_CANDIDATES,
    SUGGESTION_CACHE_TTL,
    SUGGESTION_SNOOZE_DAYS,
    cache_key,
    dismiss_suggestion,
    invalidate,
    suggestion_card_id,
    suggestions,
)
from services.outreach.threads import create_thread, delete_thread
from storage.models import (
    AgentMemory,
    CompanyNews,
    CompanyProfile,
    OssIssueSnapshot,
    OutreachThread,
    Profile,
    User,
    UserSkill,
)
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_job,
    make_user,
    outreach_db,
)

Factory = async_sessionmaker[AsyncSession]

# 11:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)

BACKEND = "Backend Engineer"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_cache() -> AsyncIterator[None]:
    """The in-process cache, empty, and no attempt to reach Redis."""
    await CacheBackend.reset()
    CacheBackend._initialised = True
    CacheBackend._client = None
    yield
    await CacheBackend.reset()


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


# --- seed helpers ------------------------------------------------------------------


async def make_profile(
    session: AsyncSession, user: User, *, skills: tuple[str, ...] = (), **kw: Any
) -> Profile:
    profile = Profile(user_id=user.id, target_roles=[BACKEND], **kw)
    session.add(profile)
    session.add_all(UserSkill(user_id=user.id, skill_name=name) for name in skills)
    await session.commit()
    return profile


async def make_funding(
    session: AsyncSession, company_id: uuid.UUID, round_: str | None, when: datetime
) -> CompanyNews:
    news = CompanyNews(
        url=f"https://example.com/news/{uuid.uuid4().hex}",
        source="inc42",
        title="A company raised money",
        published_at=when,
        category="funding",
        company_id=company_id,
        round=round_,
    )
    session.add(news)
    await session.commit()
    return news


async def make_funded(session: AsyncSession, name: str, days: int, **kw: Any) -> Any:
    """A listed company that raised ``days`` ago: enough on its own to be suggested."""
    return await make_company(session, name, last_funding_at=days_ago(days), **kw)


async def make_thread(
    session: AsyncSession, user: User, company_id: uuid.UUID, name: str, **kw: Any
) -> OutreachThread:
    thread = OutreachThread(
        id=uuid.uuid4(), user_id=user.id, company_id=company_id, company_name=name, source="drawer"
    )
    for key, value in kw.items():
        setattr(thread, key, value)
    session.add(thread)
    await session.commit()
    return thread


def names(result: dict[str, Any]) -> list[str]:
    return [s["name"] for s in result["suggestions"]]


async def count_statements(factory: Factory, work: Any) -> int:
    """How many SQL statements ``work()`` sends, over every session it opens."""
    engine = factory.kw["bind"].sync_engine
    sent: list[str] = []

    def record(*args: Any) -> None:
        sent.append(args[2])

    event.listen(engine, "before_cursor_execute", record)
    try:
        await work()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(sent)


# --- ranking ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranks_listed_companies_with_reasons(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_profile(s, user)
        funded = await make_funded(s, "Fernhill Labs", 12)
        await make_funding(s, funded.id, "Seed", days_ago(12))
        hiring = await make_company(s, "Quillstone")
        await make_job(s, hiring, "Senior Backend Engineer", city="Bengaluru")
        busy = await make_company(s, "Marrowgate")
        for n in range(6):
            await make_job(s, busy, f"Sales Manager {n}", city="Bengaluru")

    result = await suggestions(str(user.id), now=NOW)

    assert names(result) == ["Quillstone", "Fernhill Labs"]
    first, second = result["suggestions"]
    assert first["reasons"][0] == "Hiring Backend Engineer · 1 open"
    assert first["score"] == 35
    assert second["reasons"] == ["Raised Seed · 12 days ago"]
    assert second["score"] == 30
    assert set(first) == {
        "company_id",
        "name",
        "summary",
        "area",
        "stage",
        "sector",
        "score",
        "reasons",
        "top_person",
    }
    assert first["company_id"] == str(hiring.id)
    assert first["summary"] == "What Quillstone makes, in one line."


@pytest.mark.asyncio
async def test_hiring_counts_only_bengaluru_roles_and_names_the_matching_role(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        s.add(Profile(user_id=user.id, target_roles=["Data Scientist", BACKEND]))
        await s.commit()
        here = await make_company(s, "Quillstone")
        for title in ("Backend Engineer", "Staff Backend Engineer", "Backend Engineer II"):
            await make_job(s, here, title, city="Bangalore")
        await make_job(s, here, "Data Scientist", city="Bengaluru")
        # Open and matching, but in another city: it is not a Bengaluru role.
        away = await make_company(s, "Pune Works")
        await make_job(s, away, BACKEND, city="Pune")
        await make_job(s, away, BACKEND, city="Pune")

    result = await suggestions(str(user.id), now=NOW)

    assert names(result) == ["Quillstone"]
    only = result["suggestions"][0]
    # Three matching roles is the 40-point band, and the role named is the one hiring most.
    assert only["score"] == 40
    assert only["reasons"] == ["Hiring Backend Engineer · 4 open"]


@pytest.mark.asyncio
async def test_sector_open_source_and_skills_make_a_candidate(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_profile(
            s, user, skills=("Python", "Kafka", "Go"), career_goals="I want to work in fintech."
        )
        oss_only = await make_company(s, "Tallyforge", sector="fintech")
        s.add(
            OssIssueSnapshot(
                company_id=oss_only.id, org="tallyforge", status="ok", total_count=4, fetched_at=NOW
            )
        )
        # Skills from the stack and from an open posting, plus a 120-day-old raise.
        stack = await make_funded(s, "Brightloom", 120)
        s.add(CompanyProfile(company_id=stack.id, tech_stack=["Python", "Kafka"]))
        await make_job(s, stack, "Sales Manager", city="Pune", skills=["Go", "Excel"])
        await s.commit()

    result = await suggestions(str(user.id), now=NOW)

    by_name = {row["name"]: row for row in result["suggestions"]}
    assert set(by_name) == {"Tallyforge", "Brightloom"}
    assert by_name["Tallyforge"]["score"] == 22
    assert by_name["Tallyforge"]["reasons"] == ["4 good first issues", "Fintech"]
    assert by_name["Tallyforge"]["sector"] == "Fintech"
    assert by_name["Brightloom"]["score"] == 15 + 8
    assert by_name["Brightloom"]["reasons"] == [
        "Uses Python, Kafka",
        "Raised funding · 120 days ago",
    ]


@pytest.mark.asyncio
async def test_stage_is_shown_as_a_label(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_funded(s, "Fernhill Labs", 3, stage="series_a", area="Indiranagar")

    only = (await suggestions(str(user.id), now=NOW))["suggestions"][0]

    assert only["stage"] == "Series A"
    assert only["area"] == "Indiranagar"


@pytest.mark.asyncio
async def test_limit_slices_the_ranking(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        for n, days in enumerate((5, 10, 20, 40)):
            await make_funded(s, f"Co {n}", days)

    assert names(await suggestions(str(user.id), limit=2, now=NOW)) == ["Co 0", "Co 1"]
    assert len((await suggestions(str(user.id), limit=10, now=NOW))["suggestions"]) == 4
    assert (await suggestions(str(user.id), limit=0, now=NOW))["suggestions"] == []


@pytest.mark.asyncio
async def test_a_malformed_user_id_gets_an_empty_answer(outreach_db: Factory) -> None:
    assert await suggestions("not-a-uuid", now=NOW) == {
        "suggestions": [],
        "profile_complete": False,
    }


# --- who is left out ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_excludes_threads_dismissals_and_unlisted(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        other = await make_user(s)
        await make_profile(s, user)
        kept = await make_funded(s, "Kept Co", 10)
        threaded = await make_funded(s, "Threaded Co", 10)
        dismissed = await make_funded(s, "Dismissed Co", 10)
        await make_funded(s, "Not Tech Co", 10, is_tech=False)
        await make_funded(s, "Unlisted Co", 10, summary=None)
        await make_funded(s, "Elsewhere Co", 10, office_cities=["Pune"])
        # Closed, and another user's thread on the kept company: only the first counts.
        await make_thread(s, user, threaded.id, "Threaded Co", closed_at=NOW, outcome="not_now")
        await make_thread(s, other, kept.id, "Kept Co")

    await dismiss_suggestion(str(user.id), str(dismissed.id))
    result = await suggestions(str(user.id), now=NOW)

    assert names(result) == ["Kept Co"]
    # The other user is not told about it, and sees what the first user was refused.
    assert "Kept Co" not in names(await suggestions(str(other.id), now=NOW))
    assert "Dismissed Co" in names(await suggestions(str(other.id), now=NOW))


@pytest.mark.asyncio
async def test_a_company_below_the_threshold_is_not_suggested(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        # An old raise (8) and an open role that is not the user's (8): no strong signal.
        weak = await make_funded(s, "Weak Co", 170)
        await make_job(s, weak, "Sales Manager", city="Bengaluru")

    assert (await suggestions(str(user.id), now=NOW))["suggestions"] == []


# --- the profile -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_complete_flag(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        nobody = await make_user(s)
        roles_only = await make_user(s)
        complete = await make_user(s)
        await make_funded(s, "Fernhill Labs", 12)
        await make_profile(s, roles_only)
        await make_profile(s, complete, skills=("Python",))

    # No profile at all: the funding-based suggestion still comes back.
    bare = await suggestions(str(nobody.id), now=NOW)
    assert bare["profile_complete"] is False
    assert names(bare) == ["Fernhill Labs"]

    # A target role without skills is not enough to rank by skills.
    assert (await suggestions(str(roles_only.id), now=NOW))["profile_complete"] is False
    assert (await suggestions(str(complete.id), now=NOW))["profile_complete"] is True


# --- people ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_person_uses_rank_people(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        small = await make_funded(s, "Fernhill Labs", 12, stage="seed")
        await make_contact(s, small, "founder", "Devika Menon", title="Co-founder")
        # The user's own recruiter at a small company ranks under its founder.
        await make_contact(s, small, "recruiter", "Rohan Pillai", user_id=user.id, source_url=None)

    only = (await suggestions(str(user.id), now=NOW))["suggestions"][0]

    assert only["top_person"] == {"name": "Devika Menon", "title": "Co-founder", "kind": "founder"}
    # A named person is worth 5 points on top of the raise.
    assert only["score"] == 30 + 5


@pytest.mark.asyncio
async def test_a_large_company_prefers_a_recruiter_the_user_saved(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        big = await make_funded(s, "Tessel AI", 12, tier="big_tech")
        await make_contact(s, big, "founder", "Devika Menon")
        await make_contact(s, big, "recruiter", "Rohan Pillai", user_id=user.id, source_url=None)

    only = (await suggestions(str(user.id), now=NOW))["suggestions"][0]

    assert only["top_person"]["name"] == "Rohan Pillai"
    assert only["top_person"]["kind"] == "recruiter"


@pytest.mark.asyncio
async def test_only_people_this_user_may_see_are_considered(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        other = await make_user(s)
        company = await make_funded(s, "Fernhill Labs", 12, stage="seed")
        # Not people the user may be told about: another user's private contact, a curated
        # recruiter (only founders, leaders and engineers are curated people), and a
        # careers page (a link, not a person).
        await make_contact(s, company, "referral", "Karan Joshi", user_id=other.id)
        await make_contact(s, company, "recruiter", "Sanjana Rao")
        await make_contact(s, company, "careers_page", "Careers page")
        await make_contact(s, company, "careers_page", "My careers page", user_id=user.id)

    only = (await suggestions(str(user.id), now=NOW))["suggestions"][0]

    assert only["top_person"] is None
    assert only["score"] == 30  # and no 5 points for a named person

    async with outreach_db() as s:
        await make_contact(s, company, "referral", "Neha Kapoor", user_id=user.id, source_url=None)
    await invalidate(str(user.id))
    mine = (await suggestions(str(user.id), now=NOW))["suggestions"][0]
    assert mine["top_person"]["name"] == "Neha Kapoor"
    assert mine["score"] == 35


@pytest.mark.asyncio
async def test_a_sourced_email_adds_to_a_persons_rank(outreach_db: Factory) -> None:
    """A public address counts only when the contact cites the page it came from."""
    async with outreach_db() as s:
        user = await make_user(s)
        company = await make_funded(s, "Fernhill Labs", 12, stage="seed")
        # Equal on every other count: the one with a sourced address ranks first.
        await make_contact(s, company, "leader", "Arun Bhat", email="arun@fernhill.example")
        await make_contact(
            s, company, "leader", "Aarti Bhat", email="aarti@fernhill.example", source_url=None
        )

    only = (await suggestions(str(user.id), now=NOW))["suggestions"][0]

    assert only["top_person"]["name"] == "Arun Bhat"


# --- the cache -------------------------------------------------------------------


def test_keys_and_ids_are_fixed() -> None:
    cid = "0b1c2d3e-0000-4000-8000-000000000001"
    uid = "6f1f6c3a-0000-4000-8000-000000000002"
    assert suggestion_card_id(cid) == f"outreach_suggestion:{cid}"
    assert cache_key(uid) == f"tr:outreach:suggest:{uid}"
    assert (SUGGESTION_CACHE_TTL, MAX_CANDIDATES, SUGGESTION_SNOOZE_DAYS, CACHED_TOP) == (
        3600,
        400,
        30,
        25,
    )


@pytest.mark.asyncio
async def test_second_call_is_served_from_cache_until_invalidated(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_funded(s, "Fernhill Labs", 12)

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]
    async with outreach_db() as s:
        await make_funded(s, "Newcomer Co", 2)

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]  # cached
    await invalidate(str(user.id))
    assert names(await suggestions(str(user.id), now=NOW)) == ["Newcomer Co", "Fernhill Labs"]


@pytest.mark.asyncio
async def test_cache_is_invalidated_by_create_thread(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cache two rows of three, so the third can only appear if the list is rebuilt.
    monkeypatch.setattr(suggest, "CACHED_TOP", 2)
    async with outreach_db() as s:
        user = await make_user(s)
        top = await make_funded(s, "Co 0", 2)
        await make_funded(s, "Co 1", 10)
        await make_funded(s, "Co 2", 40)

    assert names(await suggestions(str(user.id), now=NOW)) == ["Co 0", "Co 1"]
    assert await CacheBackend.get(cache_key(str(user.id))) is not None

    _, created = await create_thread(
        str(user.id), company_id=str(top.id), company_name=None, job_id=None, source="suggestion"
    )
    assert created is True
    assert await CacheBackend.get(cache_key(str(user.id))) is None

    assert names(await suggestions(str(user.id), now=NOW)) == ["Co 1", "Co 2"]


@pytest.mark.asyncio
async def test_cache_is_invalidated_by_delete_thread(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        company = await make_funded(s, "Fernhill Labs", 12)
        thread = await make_thread(s, user, company.id, "Fernhill Labs")

    assert (await suggestions(str(user.id), now=NOW))["suggestions"] == []
    await delete_thread(str(user.id), str(thread.id))

    assert await CacheBackend.get(cache_key(str(user.id))) is None
    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]


@pytest.mark.asyncio
async def test_a_failing_cache_never_fails_the_thread_write(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        first = await make_funded(s, "Co 0", 2)
        second = await make_funded(s, "Co 1", 10)

    # The backend itself raising, and then invalidate raising outright.
    monkeypatch.setattr(CacheBackend, "delete", AsyncMock(side_effect=RuntimeError("redis down")))
    _, created = await create_thread(
        str(user.id), company_id=str(first.id), company_name=None, job_id=None, source="drawer"
    )
    assert created is True

    monkeypatch.setattr(suggest, "invalidate", AsyncMock(side_effect=RuntimeError("boom")))
    _, created = await create_thread(
        str(user.id), company_id=str(second.id), company_name=None, job_id=None, source="drawer"
    )
    assert created is True
    async with outreach_db() as s:
        assert (await s.execute(select(func.count()).select_from(OutreachThread))).scalar_one() == 2


@pytest.mark.asyncio
async def test_a_failing_cache_falls_through_to_computing(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_funded(s, "Fernhill Labs", 12)
    monkeypatch.setattr(CacheBackend, "get_json", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(CacheBackend, "set_json", AsyncMock(side_effect=RuntimeError("down")))

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]
    await invalidate(str(user.id))  # and clearing it does not raise either


@pytest.mark.asyncio
async def test_a_corrupt_cache_entry_is_a_miss(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_funded(s, "Fernhill Labs", 12)
    for junk in ({"suggestions": "nope"}, ["x"], {"suggestions": [{"name": "no id"}]}, "text"):
        await CacheBackend.set_json(cache_key(str(user.id)), junk, 60)
        assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]


@pytest.mark.asyncio
async def test_dismissed_after_caching_is_filtered(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_funded(s, "Fernhill Labs", 12)
        gone = await make_funded(s, "Quiet Co", 30)

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs", "Quiet Co"]
    assert await CacheBackend.get(cache_key(str(user.id))) is not None

    await agent_memory.dismiss_card(
        user_id=str(user.id),
        card_id=suggestion_card_id(str(gone.id)),
        snooze_days=SUGGESTION_SNOOZE_DAYS,
    )

    # No invalidation: the cached list is filtered on the way out.
    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]


@pytest.mark.asyncio
async def test_a_thread_started_after_caching_is_filtered(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        company = await make_funded(s, "Fernhill Labs", 12)
        await make_funded(s, "Quiet Co", 30)

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs", "Quiet Co"]
    async with outreach_db() as s:
        await make_thread(s, user, company.id, "Fernhill Labs")

    assert names(await suggestions(str(user.id), now=NOW)) == ["Quiet Co"]


@pytest.mark.asyncio
async def test_the_cache_holds_plain_dicts_for_this_user_only(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        other = await make_user(s)
        company = await make_funded(s, "Fernhill Labs", 12, stage="seed")
        await make_contact(s, company, "referral", "Karan Joshi", user_id=other.id)
        await make_contact(s, company, "founder", "Devika Menon")
        await make_thread(s, other, company.id, "Fernhill Labs")

    await suggestions(str(user.id), now=NOW)
    await suggestions(str(other.id), now=NOW)

    raw = await CacheBackend.get(cache_key(str(user.id)))
    assert raw is not None
    stored = json.loads(raw)
    assert set(stored) == {"suggestions", "profile_complete"}
    assert all(isinstance(row, dict) for row in stored["suggestions"])
    assert "Karan Joshi" not in raw
    assert "Devika Menon" in raw
    # The other user's entry is a different key, and has the thread's company filtered out.
    assert cache_key(str(user.id)) != cache_key(str(other.id))
    assert names(await suggestions(str(other.id), now=NOW)) == []


# --- dismissing ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_snoozes_the_card_for_thirty_days(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        company = await make_funded(s, "Fernhill Labs", 12)
        other = await make_user(s)

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]
    await dismiss_suggestion(str(user.id), str(company.id))
    await dismiss_suggestion(str(user.id), str(company.id))  # a second click adds nothing

    assert suggestion_card_id(str(company.id)) in await agent_memory.active_dismissals(
        user_id=str(user.id)
    )
    assert await CacheBackend.get(cache_key(str(user.id))) is None
    async with outreach_db() as s:
        rows = (await s.execute(select(AgentMemory))).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == user.id
        expires = rows[0].expires_at
        assert expires is not None
        expires = expires.replace(tzinfo=UTC) if expires.tzinfo is None else expires
        remaining = expires - datetime.now(UTC)
        assert timedelta(days=29) < remaining <= timedelta(days=30)
    assert (await suggestions(str(user.id), now=NOW))["suggestions"] == []
    # Dismissing is the user's own: nobody else's list changes.
    assert names(await suggestions(str(other.id), now=NOW)) == ["Fernhill Labs"]


@pytest.mark.asyncio
async def test_dismiss_refuses_an_unknown_company(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
    for bad in ("not-a-uuid", str(uuid.uuid4())):
        with pytest.raises(NotFound):
            await dismiss_suggestion(str(user.id), bad)
    with pytest.raises(NotFound):
        await dismiss_suggestion("not-a-uuid", str(uuid.uuid4()))
    async with outreach_db() as s:
        assert (await s.execute(select(func.count()).select_from(AgentMemory))).scalar_one() == 0


@pytest.mark.asyncio
async def test_a_snooze_that_has_ended_hides_nothing(outreach_db: Factory) -> None:
    """A snoozed dismissal is read back whether the database returns aware or naive times."""
    async with outreach_db() as s:
        user = await make_user(s)
        lapsed = await make_funded(s, "Lapsed Co", 12)
        live = await make_funded(s, "Live Co", 13)
        for company, expires in ((lapsed, -1), (live, 5)):
            s.add(
                AgentMemory(
                    user_id=user.id,
                    memory_type=agent_memory.DISMISSAL_TYPE,
                    content=suggestion_card_id(str(company.id)),
                    expires_at=datetime.now(UTC) + timedelta(days=expires),
                )
            )
        await s.commit()

    assert names(await suggestions(str(user.id), now=NOW)) == ["Lapsed Co"]


@pytest.mark.asyncio
async def test_other_dismissals_do_not_hide_suggestions(outreach_db: Factory) -> None:
    async with outreach_db() as s:
        user = await make_user(s)
        await make_funded(s, "Fernhill Labs", 12)
    await agent_memory.dismiss_card(user_id=str(user.id), card_id="briefing-next-step")
    await agent_memory.dismiss_card(user_id=str(user.id), card_id="outreach_suggestion:garbage")

    assert names(await suggestions(str(user.id), now=NOW)) == ["Fernhill Labs"]


# --- cost -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidates_are_bounded_and_funded_first(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(suggest, "MAX_CANDIDATES", 3)
    async with outreach_db() as s:
        user = await make_user(s)
        await make_profile(s, user)
        for n, days in enumerate((20, 5, 10)):
            await make_funded(s, f"Funded {n}", days)
        # Hiring for the target role, but raised nothing: it sorts after every raise.
        unfunded = await make_company(s, "Unfunded Co")
        await make_job(s, unfunded, BACKEND, city="Bengaluru")

    assert names(await suggestions(str(user.id), now=NOW)) == ["Funded 1", "Funded 2", "Funded 0"]


@pytest.mark.asyncio
async def test_an_uncached_call_batches_its_loads(outreach_db: Factory) -> None:
    """The statement count does not grow with the number of companies."""
    async with outreach_db() as s:
        user = await make_user(s)
        await make_profile(s, user, skills=("Python",), career_goals="fintech")
        companies = []
        for n in range(3):
            company = await make_funded(s, f"Few {n}", 5 + n, stage="seed")
            companies.append(company)
            await make_contact(s, company, "founder", f"Founder {n}")
            await make_job(s, company, BACKEND, city="Bengaluru", skills=["Python"])
            await make_funding(s, company.id, "Seed", days_ago(5 + n))

    few = await count_statements(outreach_db, lambda: suggestions(str(user.id), now=NOW))

    async with outreach_db() as s:
        for n in range(40):
            company = await make_funded(s, f"Many {n}", 5 + n % 50, stage="seed")
            await make_contact(s, company, "founder", f"Leader {n}")
            await make_job(s, company, BACKEND, city="Bengaluru", skills=["Python"])
            await make_funding(s, company.id, "Seed", days_ago(5 + n % 50))
    await invalidate(str(user.id))

    many = await count_statements(outreach_db, lambda: suggestions(str(user.id), now=NOW))

    # The user's roles, goals and skills (3); the threads and dismissals (2); the candidates;
    # and one each for role counts, two stacks, issues, rounds and people (6).
    assert many == few == 12
