"""
tests/test_outreach_threads.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Threads: create (idempotent per company), update, delete, the lookups the
company drawer and the "Add a company" box use, and the summary every list row
is built from.

The service runs against in-memory SQLite (``outreach_db``). ``summarise`` is a
pure function of loaded rows, so its edge cases are tested on rows that were
never stored.
"""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition. A comment on the import line cannot silence it, so it is off here.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import services.agent_memory as agent_memory
from domain.outreach import MAX_OPEN_THREADS
from services.outreach import threads
from services.outreach.errors import Conflict, Invalid, NotFound, OutreachError
from services.outreach.threads import (
    create_thread,
    delete_thread,
    load_owned_thread,
    search_companies,
    summarise,
    summarise_many,
    thread_for_company,
    update_thread,
)
from storage.models import (
    ApplicationStatus,
    Company,
    JobApplication,
    OutreachPerson,
    OutreachThread,
    OutreachTouch,
)
from tests.outreach_support import (  # noqa: F401
    make_company,
    make_contact,
    make_job,
    make_user,
    outreach_db,
    outreach_modules,
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


async def _typed(user_id: uuid.UUID, name: str = "Acme Robotics") -> dict[str, Any]:
    summary, _ = await create_thread(
        str(user_id), company_id=None, company_name=name, job_id=None, source="typed"
    )
    return summary


async def _row(factory: Factory, thread_id: str) -> OutreachThread:
    async with factory() as session:
        return (
            await session.execute(
                select(OutreachThread).where(OutreachThread.id == uuid.UUID(thread_id))
            )
        ).scalar_one()


async def _count(factory: Factory, model: type[Any]) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _summary_of(factory: Factory, user_id: uuid.UUID, thread_id: str) -> dict[str, Any]:
    async with factory() as session:
        thread = await load_owned_thread(session, str(user_id), thread_id)
        return (await summarise_many(session, [thread], datetime.now(UTC)))[0]


# --- the fixture itself ----------------------------------------------------------


async def test_outreach_db_turns_foreign_keys_on(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


async def test_outreach_db_patches_every_session_the_services_open(outreach_db: Factory) -> None:
    names = {module.__name__ for module in outreach_modules()}
    assert {"services.outreach.errors", "services.outreach.threads"} <= names
    for module in outreach_modules():
        if hasattr(module, "AsyncSessionLocal"):
            assert module.AsyncSessionLocal is outreach_db, module.__name__
    assert vars(threads)["AsyncSessionLocal"] is outreach_db
    assert vars(agent_memory)["AsyncSessionLocal"] is outreach_db


# --- create ----------------------------------------------------------------------


async def test_create_is_idempotent_per_company(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
    uid, cid = str(user.id), str(company.id)

    first, created = await create_thread(
        uid, company_id=cid, company_name=None, job_id=None, source="drawer"
    )
    assert created is True

    again, created_again = await create_thread(
        uid, company_id=cid, company_name=None, job_id=None, source="list"
    )
    assert created_again is False
    assert again["id"] == first["id"]
    assert again["source"] == "drawer"  # the first way in is the one kept
    assert await _count(outreach_db, OutreachThread) == 1

    # A closed thread comes back when the company is added again.
    await update_thread(uid, first["id"], {"close_outcome": "not_now"})
    closed = await _row(outreach_db, first["id"])
    assert closed.closed_at is not None and closed.outcome == "not_now"

    reopened, created_third = await create_thread(
        uid, company_id=cid, company_name=None, job_id=None, source="list"
    )
    assert created_third is False
    assert reopened["id"] == first["id"]
    assert reopened["stage"] != "closed"
    assert reopened["outcome"] is None
    row = await _row(outreach_db, first["id"])
    assert row.closed_at is None and row.outcome is None
    assert await _count(outreach_db, OutreachThread) == 1


async def test_two_users_can_each_open_the_same_company(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        first_user = await make_user(session)
        second_user = await make_user(session)
        company = await make_company(session, "Acme Robotics")

    first, _ = await create_thread(
        str(first_user.id),
        company_id=str(company.id),
        company_name=None,
        job_id=None,
        source="list",
    )
    second, created = await create_thread(
        str(second_user.id),
        company_id=str(company.id),
        company_name=None,
        job_id=None,
        source="list",
    )
    assert created is True
    assert second["id"] != first["id"]


async def test_a_racing_create_returns_the_thread_that_won(
    outreach_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
    uid, cid = str(user.id), str(company.id)
    winner, _ = await create_thread(
        uid, company_id=cid, company_name=None, job_id=None, source="drawer"
    )

    # The second request looked before the first had committed, found nothing,
    # and then met the unique index. It must answer with the winner, not a 500.
    real = threads._thread_for
    lookups: list[int] = []

    async def miss_the_first_time(*args: Any, **kwargs: Any) -> Any:
        lookups.append(1)
        return None if len(lookups) == 1 else await real(*args, **kwargs)

    monkeypatch.setattr(threads, "_thread_for", miss_the_first_time)

    loser, created = await create_thread(
        uid, company_id=cid, company_name=None, job_id=None, source="list"
    )
    assert created is False
    assert loser["id"] == winner["id"]
    assert len(lookups) == 2
    assert await _count(outreach_db, OutreachThread) == 1


async def test_create_by_typed_name_without_a_match(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)

    summary, created = await create_thread(
        str(user.id), company_id=None, company_name="Acme Robotics", job_id=None, source="typed"
    )

    assert created is True
    assert summary["company_id"] is None
    assert summary["company_name"] == "Acme Robotics"
    assert summary["company_summary"] is None
    assert summary["stage"] == "planned"
    assert summary["people_count"] == 0
    assert summary["lead"] is None
    assert summary["source"] == "typed"
    assert summary["created_at"] is not None  # the database's default, read back
    assert await _count(outreach_db, Company) == 0  # typing never writes to the shared table


async def test_typed_name_is_flattened_to_one_line(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)

    summary = await _typed(user.id, "  Acme \n\t Robotics  ")

    assert summary["company_name"] == "Acme Robotics"


async def test_create_snapshots_company_and_job(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        job = await make_job(session, company, "Platform Engineer")
        tracked = JobApplication(
            id=uuid.uuid4(), user_id=user.id, job_id=job.id, status=ApplicationStatus.APPLIED
        )
        session.add(tracked)
        await session.commit()

    summary, created = await create_thread(
        str(user.id),
        company_id=str(company.id),
        company_name=None,
        job_id=str(job.id),
        source="list",
    )

    assert created is True
    assert summary["company_id"] == str(company.id)
    assert summary["company_name"] == "Acme Robotics"
    assert summary["company_summary"] == company.summary
    assert summary["job"] == {"id": str(job.id), "title": "Platform Engineer"}
    assert summary["application"] == {"id": str(tracked.id), "status": "applied"}

    # The names are copied onto the thread, so a rename or a pruned posting
    # leaves the conversation readable.
    async with outreach_db() as session:
        stored_company = await session.get(Company, company.id)
        assert stored_company is not None
        stored_company.name = "Acme Robotics Ltd"
        await session.commit()
    row = await _row(outreach_db, summary["id"])
    assert row.company_name == "Acme Robotics"
    assert row.job_title == "Platform Engineer"
    assert row.application_id == tracked.id


async def test_another_users_application_is_never_linked(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        job = await make_job(session, company)
        session.add(
            JobApplication(
                id=uuid.uuid4(), user_id=other.id, job_id=job.id, status=ApplicationStatus.SAVED
            )
        )
        await session.commit()

    summary, _ = await create_thread(
        str(user.id),
        company_id=str(company.id),
        company_name=None,
        job_id=str(job.id),
        source="list",
    )

    assert summary["job"] is not None
    assert summary["application"] is None


async def test_create_validates_its_input(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
    uid = str(user.id)
    missing = str(uuid.uuid4())

    async def attempt(**changes: Any) -> None:
        arguments: dict[str, Any] = {
            "company_id": None,
            "company_name": "Acme Robotics",
            "job_id": None,
            "source": "typed",
        }
        arguments.update(changes)
        await create_thread(uid, **arguments)

    with pytest.raises(Invalid):
        await attempt(source="newsletter")
    with pytest.raises(Invalid):
        await attempt(company_name=None)
    with pytest.raises(Invalid):
        await attempt(company_name="   ")
    with pytest.raises(Invalid):
        await attempt(company_name="x" * 257)
    with pytest.raises(NotFound):
        await attempt(company_id=missing)
    with pytest.raises(NotFound):
        await attempt(company_id="not-a-uuid")
    with pytest.raises(NotFound):
        await attempt(company_id=str(company.id), job_id=missing)
    assert await _count(outreach_db, OutreachThread) == 0


def _open_threads(user_id: uuid.UUID, count: int, *, closed: bool = False) -> list[OutreachThread]:
    return [
        OutreachThread(
            id=uuid.uuid4(),
            user_id=user_id,
            company_name=f"Fictional Works {index}",
            source="typed",
            closed_at=NOW if closed else None,
            outcome="not_now" if closed else None,
        )
        for index in range(count)
    ]


async def test_open_thread_cap(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        session.add_all(_open_threads(user.id, MAX_OPEN_THREADS - 1))
        session.add_all(_open_threads(user.id, 5, closed=True))  # closed ones don't count
        await session.commit()

    await _typed(user.id, "The hundredth")  # still room

    with pytest.raises(Conflict) as refused:
        await _typed(user.id, "The hundred and first")
    assert refused.value.status_code == 409
    assert refused.value.detail == "Close a thread before adding another"
    assert await _count(outreach_db, OutreachThread) == MAX_OPEN_THREADS + 5

    # Closing one makes room for exactly one more.
    async with outreach_db() as session:
        still_open = select(OutreachThread.id).where(
            OutreachThread.user_id == user.id, OutreachThread.closed_at.is_(None)
        )
        open_one_id = str((await session.execute(still_open)).scalars().first())
    await update_thread(str(user.id), open_one_id, {"close_outcome": "not_now"})
    await _typed(user.id, "Now there is room")


async def test_another_users_threads_do_not_fill_my_cap(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        session.add_all(_open_threads(other.id, MAX_OPEN_THREADS))
        await session.commit()

    summary = await _typed(user.id)

    assert summary["stage"] == "planned"


async def test_reopening_counts_against_the_cap(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
    uid, cid = str(user.id), str(company.id)
    first, _ = await create_thread(
        uid, company_id=cid, company_name=None, job_id=None, source="drawer"
    )
    await update_thread(uid, first["id"], {"close_outcome": "not_now"})
    async with outreach_db() as session:
        session.add_all(_open_threads(user.id, MAX_OPEN_THREADS))
        await session.commit()

    with pytest.raises(Conflict):
        await create_thread(uid, company_id=cid, company_name=None, job_id=None, source="list")
    with pytest.raises(Conflict):
        await update_thread(uid, first["id"], {"reopen": True})
    assert (await _row(outreach_db, first["id"])).closed_at is not None


# --- update ----------------------------------------------------------------------


async def test_update_validates_and_closes(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    uid = str(user.id)
    thread_id = (await _typed(user.id))["id"]

    with pytest.raises(Invalid) as too_long:
        await update_thread(uid, thread_id, {"note": "x" * 501})
    assert too_long.value.status_code == 422
    with pytest.raises(Invalid):
        await update_thread(uid, thread_id, {"note": "A fine note", "close_outcome": "gossip"})
    assert (await _row(outreach_db, thread_id)).note is None  # nothing was half-applied

    await update_thread(uid, thread_id, {"close_outcome": "not_now"})
    closed = await _row(outreach_db, thread_id)
    assert closed.closed_at is not None
    assert closed.outcome == "not_now"
    assert (await _summary_of(outreach_db, user.id, thread_id))["stage"] == "closed"

    await update_thread(uid, thread_id, {"reopen": True})
    reopened = await _row(outreach_db, thread_id)
    assert reopened.closed_at is None
    assert reopened.outcome is None
    assert (await _summary_of(outreach_db, user.id, thread_id))["stage"] == "planned"


async def test_update_note_and_next_step(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    uid = str(user.id)
    thread_id = (await _typed(user.id))["id"]

    await update_thread(
        uid,
        thread_id,
        {
            "note": "  Builds the arms I would like to work on.  ",
            "next_step": "Call on Thursday",
            "next_step_at": date(2026, 10, 22),
        },
    )
    row = await _row(outreach_db, thread_id)
    assert row.note == "Builds the arms I would like to work on."
    assert row.next_step == "Call on Thursday"
    assert row.next_step_at == date(2026, 10, 22)

    # A key left out is left alone; a key set to None clears it; a blank string clears it too.
    await update_thread(uid, thread_id, {"note": None, "next_step": "   "})
    row = await _row(outreach_db, thread_id)
    assert row.note is None
    assert row.next_step is None
    assert row.next_step_at == date(2026, 10, 22)

    with pytest.raises(Invalid):
        await update_thread(uid, thread_id, {"next_step": "x" * 141})
    await update_thread(uid, thread_id, {"next_step": "x" * 140})


async def test_update_refuses_to_close_and_reopen_at_once(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    thread_id = (await _typed(user.id))["id"]

    with pytest.raises(Invalid):
        await update_thread(str(user.id), thread_id, {"close_outcome": "other", "reopen": True})


async def test_update_attaches_and_clears_a_role(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        job = await make_job(session, company, "Embedded Engineer")
        tracked = JobApplication(
            id=uuid.uuid4(), user_id=user.id, job_id=job.id, status=ApplicationStatus.SCREENING
        )
        session.add(tracked)
        await session.commit()
    uid = str(user.id)
    thread_id = (await _typed(user.id))["id"]

    await update_thread(uid, thread_id, {"job_id": str(job.id)})
    row = await _row(outreach_db, thread_id)
    assert row.job_id == job.id
    assert row.job_title == "Embedded Engineer"
    assert row.application_id == tracked.id  # the role was already tracked
    summary = await _summary_of(outreach_db, user.id, thread_id)
    assert summary["application"] == {"id": str(tracked.id), "status": "screening"}

    await update_thread(uid, thread_id, {"job_id": None, "application_id": None})
    row = await _row(outreach_db, thread_id)
    assert (row.job_id, row.job_title, row.application_id) == (None, None, None)
    assert (await _summary_of(outreach_db, user.id, thread_id))["job"] is None

    with pytest.raises(NotFound):
        await update_thread(uid, thread_id, {"job_id": str(uuid.uuid4())})


async def test_update_application_must_be_the_users_own(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        job = await make_job(session, company)
        mine = JobApplication(id=uuid.uuid4(), user_id=user.id, job_id=job.id)
        theirs = JobApplication(id=uuid.uuid4(), user_id=other.id, job_id=job.id)
        session.add_all([mine, theirs])
        await session.commit()
    uid = str(user.id)
    thread_id = (await _typed(user.id))["id"]

    with pytest.raises(NotFound):
        await update_thread(uid, thread_id, {"application_id": str(theirs.id)})
    assert (await _row(outreach_db, thread_id)).application_id is None

    await update_thread(uid, thread_id, {"application_id": str(mine.id)})
    assert (await _row(outreach_db, thread_id)).application_id == mine.id


# --- ownership -------------------------------------------------------------------


async def test_other_users_thread_is_not_found(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        owner = await make_user(session)
        stranger = await make_user(session)
    thread_id = (await _typed(owner.id))["id"]
    intruder = str(stranger.id)

    with pytest.raises(NotFound) as updating:
        await update_thread(intruder, thread_id, {"note": "mine now"})
    with pytest.raises(NotFound) as deleting:
        await delete_thread(intruder, thread_id)
    async with outreach_db() as session:
        with pytest.raises(NotFound) as loading:
            await load_owned_thread(session, intruder, thread_id)
        with pytest.raises(NotFound):
            await load_owned_thread(session, intruder, thread_id, with_children=False)
    for refusal in (updating, deleting, loading):
        assert refusal.value.status_code == 404  # never a 403
        assert refusal.value.detail == "Thread not found"

    # The same refusal as for an id that never existed, and nothing was touched.
    async with outreach_db() as session:
        with pytest.raises(NotFound) as missing:
            await load_owned_thread(session, str(owner.id), str(uuid.uuid4()))
        with pytest.raises(NotFound):
            await load_owned_thread(session, str(owner.id), "not-a-uuid")
    assert missing.value.detail == "Thread not found"
    row = await _row(outreach_db, thread_id)
    assert row.note is None
    assert row.user_id == owner.id


def test_outreach_errors_carry_their_status() -> None:
    assert (NotFound("Thread not found").status_code, NotFound("x").detail) == (404, "x")
    assert Conflict("x").status_code == 409
    assert Invalid("x").status_code == 422
    assert isinstance(NotFound("x"), OutreachError)
    assert str(Invalid("Too long")) == "Too long"


# --- delete ----------------------------------------------------------------------


async def test_delete_thread_removes_people_and_touches(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
    doomed = (await _typed(user.id, "Acme Robotics"))["id"]
    kept = (await _typed(user.id, "Quill Systems"))["id"]
    async with outreach_db() as session:
        for thread_id in (doomed, kept):
            person = OutreachPerson(
                id=uuid.uuid4(), thread_id=uuid.UUID(thread_id), name="Ila Quill", kind="founder"
            )
            session.add(person)
            await session.flush()
            session.add_all(
                OutreachTouch(
                    id=uuid.uuid4(),
                    thread_id=uuid.UUID(thread_id),
                    person_id=person.id,
                    user_id=user.id,
                    channel="linkedin",
                    direction="out",
                    kind="first",
                    happened_at=NOW,
                )
                for _ in range(3)
            )
        await session.commit()
    assert await _count(outreach_db, OutreachPerson) == 2
    assert await _count(outreach_db, OutreachTouch) == 6

    await delete_thread(str(user.id), doomed)

    assert await _count(outreach_db, OutreachThread) == 1
    assert await _count(outreach_db, OutreachPerson) == 1
    assert await _count(outreach_db, OutreachTouch) == 3
    async with outreach_db() as session:
        survivors = (await session.execute(select(OutreachTouch.thread_id))).scalars().all()
    assert set(survivors) == {uuid.UUID(kept)}
    with pytest.raises(NotFound):
        await delete_thread(str(user.id), doomed)  # gone is gone


# --- lookups ---------------------------------------------------------------------


async def test_thread_for_company(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        owner = await make_user(session)
        stranger = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        unopened = await make_company(session, "Quill Systems")
    created, _ = await create_thread(
        str(owner.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )

    found = await thread_for_company(str(owner.id), str(company.id))

    assert found is not None
    assert set(found) == SUMMARY_KEYS
    assert found["id"] == created["id"]
    assert await thread_for_company(str(stranger.id), str(company.id)) is None
    assert await thread_for_company(str(owner.id), str(unopened.id)) is None
    assert await thread_for_company(str(owner.id), "not-a-uuid") is None
    assert await thread_for_company("not-a-uuid", str(company.id)) is None


async def test_search_companies_matches_all_stored_companies(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        await make_company(session, "Acme Robotics", is_tech=None, area="Indiranagar")
        await make_company(session, "Quill Systems", is_tech=False)
        for suffix in ("Labs", "Foundry", "Dairy", "Freight", "Textiles", "Optics"):
            await make_company(session, f"Acme {suffix}")

    robots = await search_companies("rObOt")  # not listed, mixed case
    assert [row["name"] for row in robots] == ["Acme Robotics"]
    assert set(robots[0]) == {"id", "name", "summary", "area"}
    assert robots[0]["area"] == "Indiranagar"
    assert robots[0]["summary"] == "What Acme Robotics makes, in one line."

    assert [row["name"] for row in await search_companies("quill")] == ["Quill Systems"]
    many = await search_companies("acme")
    assert len(many) == 5
    assert len(await search_companies("acme", limit=2)) == 2
    assert len(await search_companies("acme", limit=50)) == 5  # never more than five
    assert await search_companies("nothing like it") == []


async def test_search_companies_ranks_the_start_of_a_name_first(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        await make_company(session, "Northern Acme Works")
        await make_company(session, "Acme Robotics")
        await make_company(session, "Acme")

    names = [row["name"] for row in await search_companies("acme")]

    assert names == ["Acme", "Acme Robotics", "Northern Acme Works"]


async def test_search_companies_treats_wildcards_as_text(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        await make_company(session, "Acme Robotics")
        await make_company(session, "100% Pure Software")
        await make_company(session, "Quill Systems")
        await make_company(session, "Quill_Systems")

    assert [row["name"] for row in await search_companies("%")] == ["100% Pure Software"]
    assert [row["name"] for row in await search_companies("l_s")] == ["Quill_Systems"]
    assert await search_companies("") == []
    assert await search_companies("   ") == []
    assert await search_companies("acme", limit=0) == []


# --- the summary -----------------------------------------------------------------


def _thread(**kw: Any) -> OutreachThread:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "company_name": "Acme Robotics",
        "source": "drawer",
        "created_at": NOW - timedelta(days=30),
    }
    values.update(kw)
    thread = OutreachThread(**values)
    thread.people = []
    thread.touches = []
    return thread


def _person(thread: OutreachThread, name: str, state: str = "planned", **kw: Any) -> OutreachPerson:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "thread_id": thread.id,
        "name": name,
        "kind": "founder",
        "state": state,
        "follow_ups_sent": 0,
        "created_at": NOW - timedelta(days=20),
    }
    values.update(kw)
    person = OutreachPerson(**values)
    thread.people.append(person)
    return person


def _waiting(
    thread: OutreachThread, name: str, *, follow_up_at: datetime | None, **kw: Any
) -> OutreachPerson:
    sent = NOW - timedelta(days=6)
    values: dict[str, Any] = {
        "first_sent_at": sent,
        "last_outbound_at": sent,
        "next_follow_up_at": follow_up_at,
    }
    values.update(kw)
    return _person(thread, name, "waiting", **values)


def _touch(thread: OutreachThread, at: datetime) -> OutreachTouch:
    touch = OutreachTouch(
        id=uuid.uuid4(),
        thread_id=thread.id,
        user_id=thread.user_id,
        channel="linkedin",
        direction="out",
        kind="first",
        happened_at=at,
    )
    thread.touches.append(touch)
    return touch


def test_summary_shape() -> None:
    thread = _thread()
    _person(thread, "Ila Quill")

    summary = summarise(thread, NOW)

    assert set(summary) == SUMMARY_KEYS
    assert summary["id"] == str(thread.id)
    assert summary["company_id"] is None
    assert summary["company_name"] == "Acme Robotics"
    assert summary["company_summary"] is None
    assert summary["stage"] == "planned"
    assert summary["outcome"] is None
    assert summary["people_count"] == 1
    assert summary["lead"] == {"name": "Ila Quill", "state": "planned"}  # nothing else about them
    assert summary["due"] is False
    assert summary["next_due_at"] is None
    assert summary["last_touch_at"] is None
    assert summary["job"] is None
    assert summary["application"] is None
    assert summary["source"] == "drawer"
    assert summary["created_at"] == (NOW - timedelta(days=30)).isoformat()


def test_an_empty_thread_has_no_lead() -> None:
    summary = summarise(_thread(), NOW)

    assert summary["stage"] == "planned"
    assert summary["people_count"] == 0
    assert summary["lead"] is None


@pytest.mark.parametrize(
    ("build", "stage", "lead_name", "lead_state"),
    [
        pytest.param(
            lambda t: (
                _person(t, "Planned Pia"),
                _waiting(t, "Waiting Wen", follow_up_at=NOW + timedelta(days=1)),
                _waiting(t, "Due Dev", follow_up_at=NOW - timedelta(days=1)),
                _person(t, "Replied Rae", "replied", replied_at=NOW - timedelta(days=2)),
            ),
            "replied",
            "Replied Rae",
            "replied",
            id="replied-beats-everything",
        ),
        pytest.param(
            lambda t: (
                _person(t, "Planned Pia"),
                _waiting(t, "Waiting Wen", follow_up_at=NOW + timedelta(days=1)),
                _waiting(t, "Due Dev", follow_up_at=NOW - timedelta(days=1)),
            ),
            "waiting",
            "Due Dev",
            "waiting",
            id="a-due-person-leads-the-waiting",
        ),
        pytest.param(
            lambda t: (
                _person(t, "Planned Pia"),
                _waiting(t, "Waiting Wen", follow_up_at=NOW + timedelta(days=1)),
            ),
            "waiting",
            "Waiting Wen",
            "waiting",
            id="waiting-beats-planned",
        ),
        pytest.param(
            lambda t: (_person(t, "Planned Pia"), _person(t, "Dropped Dax", "dropped")),
            "planned",
            "Planned Pia",
            "planned",
            id="a-dropped-person-never-leads",
        ),
    ],
)
def test_stage_and_lead(
    build: Callable[[OutreachThread], object], stage: str, lead_name: str, lead_state: str
) -> None:
    thread = _thread()
    build(thread)

    summary = summarise(thread, NOW)

    assert summary["stage"] == stage
    assert summary["lead"] == {"name": lead_name, "state": lead_state}


def test_lead_ties_go_to_the_earliest_added() -> None:
    thread = _thread()
    # Listed newest first: the lead must not depend on the order the rows load in.
    _person(thread, "Added Third", created_at=NOW - timedelta(days=1))
    _person(thread, "Added First", created_at=NOW - timedelta(days=9))
    _person(thread, "Added Second", created_at=NOW - timedelta(days=5))

    assert summarise(thread, NOW)["lead"] == {"name": "Added First", "state": "planned"}


def test_dropped_people_are_counted_but_leave_a_thread_unled() -> None:
    thread = _thread()
    _person(thread, "Dropped Dax", "dropped")

    summary = summarise(thread, NOW)

    assert summary["people_count"] == 1
    assert summary["lead"] is None
    assert summary["stage"] == "planned"


def test_a_cold_person_cools_the_thread_and_still_leads_it() -> None:
    thread = _thread()
    sent = NOW - timedelta(days=11)
    _waiting(
        thread,
        "Cold Cai",
        follow_up_at=None,
        follow_ups_sent=2,
        first_sent_at=NOW - timedelta(days=30),
        last_outbound_at=sent,
    )

    summary = summarise(thread, NOW)

    assert summary["stage"] == "cold"
    assert summary["lead"] == {"name": "Cold Cai", "state": "cold"}
    assert summary["due"] is False
    assert summary["next_due_at"] is None


def test_a_waiting_person_is_due_from_midnight_ist() -> None:
    owed = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)  # 15:30 IST on 15 October
    thread = _thread()
    _waiting(thread, "Due Dev", follow_up_at=owed)

    night_before = datetime(2026, 10, 14, 18, 29, tzinfo=UTC)  # 23:59 IST on 14 October
    midnight = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)  # 00:00 IST on 15 October

    before = summarise(thread, night_before)
    assert before["due"] is False
    assert before["next_due_at"] == owed.isoformat()
    after = summarise(thread, midnight)
    assert after["due"] is True
    assert after["next_due_at"] == owed.isoformat()
    assert after["lead"] == {"name": "Due Dev", "state": "waiting"}


def test_next_due_is_the_earliest_pending_follow_up() -> None:
    thread = _thread()
    _waiting(thread, "Later Lee", follow_up_at=NOW + timedelta(days=4))
    _waiting(thread, "Sooner Sam", follow_up_at=NOW + timedelta(days=2))

    summary = summarise(thread, NOW)

    assert summary["due"] is False
    assert summary["next_due_at"] == (NOW + timedelta(days=2)).isoformat()


def test_a_next_step_on_a_replied_thread_is_due_from_its_day() -> None:
    thread = _thread(next_step="Call on Thursday", next_step_at=date(2026, 10, 15))
    _person(thread, "Replied Rae", "replied", replied_at=NOW - timedelta(days=1))
    day_starts = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)  # 00:00 IST on 15 October

    summary = summarise(thread, NOW)  # 11:30 IST on the 15th
    assert summary["stage"] == "replied"
    assert summary["due"] is True
    assert summary["next_due_at"] == day_starts.isoformat()

    tomorrow = _thread(next_step="Call on Thursday", next_step_at=date(2026, 10, 16))
    _person(tomorrow, "Replied Rae", "replied", replied_at=NOW - timedelta(days=1))
    later = summarise(tomorrow, NOW)
    assert later["due"] is False
    assert later["next_due_at"] == (day_starts + timedelta(days=1)).isoformat()


def test_a_next_step_only_counts_once_someone_has_replied() -> None:
    thread = _thread(next_step="Email Ila", next_step_at=date(2026, 10, 1))
    _waiting(thread, "Waiting Wen", follow_up_at=NOW + timedelta(days=1))

    summary = summarise(thread, NOW)

    assert summary["due"] is False
    assert summary["next_due_at"] == (NOW + timedelta(days=1)).isoformat()


def test_a_closed_thread_is_never_due() -> None:
    thread = _thread(
        closed_at=NOW - timedelta(days=1),
        outcome="not_now",
        next_step="Call on Thursday",
        next_step_at=date(2026, 10, 1),
    )
    _waiting(thread, "Due Dev", follow_up_at=NOW - timedelta(days=3))
    _person(thread, "Replied Rae", "replied", replied_at=NOW - timedelta(days=2))

    summary = summarise(thread, NOW)

    assert summary["stage"] == "closed"
    assert summary["outcome"] == "not_now"
    assert summary["due"] is False
    assert summary["next_due_at"] is None
    assert summary["lead"] == {"name": "Replied Rae", "state": "replied"}


def test_last_touch_is_the_newest_touch() -> None:
    thread = _thread()
    _touch(thread, NOW - timedelta(days=6))
    _touch(thread, NOW - timedelta(days=1))
    _touch(thread, NOW - timedelta(days=3))

    assert summarise(thread, NOW)["last_touch_at"] == (NOW - timedelta(days=1)).isoformat()


def test_stored_times_without_a_zone_are_read_as_utc() -> None:
    thread = _thread(created_at=datetime(2026, 9, 1, 8, 0))  # what SQLite hands back
    _touch(thread, datetime(2026, 10, 14, 7, 30))
    _waiting(thread, "Due Dev", follow_up_at=datetime(2026, 10, 16, 7, 30))

    summary = summarise(thread, NOW)

    assert summary["created_at"] == "2026-09-01T08:00:00+00:00"
    assert summary["last_touch_at"] == "2026-10-14T07:30:00+00:00"
    assert summary["next_due_at"] == "2026-10-16T07:30:00+00:00"


async def test_summary_of_a_stored_thread_has_its_company_job_and_people(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        contact = await make_contact(session, company, "founder", "Ila Quill")
    summary, _ = await create_thread(
        str(user.id), company_id=str(company.id), company_name=None, job_id=None, source="drawer"
    )
    async with outreach_db() as session:
        session.add(
            OutreachPerson(
                id=uuid.uuid4(),
                thread_id=uuid.UUID(summary["id"]),
                contact_id=contact.id,
                name="Ila Quill",
                kind="founder",
            )
        )
        await session.commit()

    found = await thread_for_company(str(user.id), str(company.id))

    assert found is not None
    assert set(found) == SUMMARY_KEYS
    assert found["people_count"] == 1
    assert found["lead"] == {"name": "Ila Quill", "state": "planned"}
    assert found["company_summary"] == company.summary


async def test_summarise_many_fetches_company_and_application_context(
    outreach_db: Factory,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        other = await make_user(session)
        company = await make_company(session, "Acme Robotics")
        job = await make_job(session, company)
        mine = JobApplication(
            id=uuid.uuid4(), user_id=user.id, job_id=job.id, status=ApplicationStatus.INTERVIEW
        )
        theirs = JobApplication(
            id=uuid.uuid4(), user_id=other.id, job_id=job.id, status=ApplicationStatus.OFFER
        )
        session.add_all([mine, theirs])
        await session.flush()
        with_company = OutreachThread(
            id=uuid.uuid4(),
            user_id=user.id,
            company_id=company.id,
            company_name="Acme Robotics",
            application_id=mine.id,
            source="list",
        )
        typed = OutreachThread(
            id=uuid.uuid4(), user_id=user.id, company_name="Quill Systems", source="typed"
        )
        # Not reachable through the service, but a summary must still not leak it.
        borrowed = OutreachThread(
            id=uuid.uuid4(),
            user_id=user.id,
            company_name="Borrowed Works",
            application_id=theirs.id,
            source="typed",
        )
        session.add_all([with_company, typed, borrowed])
        await session.commit()

    async with outreach_db() as session:
        loaded = [
            await load_owned_thread(session, str(user.id), str(thread.id))
            for thread in (with_company, typed, borrowed)
        ]
        rows = await summarise_many(session, loaded, NOW)
        assert await summarise_many(session, [], NOW) == []

    assert [row["company_name"] for row in rows] == [
        "Acme Robotics",
        "Quill Systems",
        "Borrowed Works",
    ]
    assert rows[0]["company_summary"] == company.summary
    assert rows[0]["application"] == {"id": str(mine.id), "status": "interview"}
    assert rows[1]["company_summary"] is None
    assert rows[1]["application"] is None
    assert rows[2]["application"] == {"id": str(theirs.id), "status": None}
