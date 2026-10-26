"""
services/outreach/pack.py
~~~~~~~~~~~~~~~~~~~~~~~~~
The daily outreach pack: up to five companies a day, the people to write to at each,
and the follow-ups that are due, built on the day's first read and kept.

Design: docs/superpowers/specs/2026-10-26-daily-outreach-pack-design.md.

Rules this module keeps:

* **A read never calls a model or the web.** ``get_pack`` builds the pack from what
  ``suggest`` already ranks from stored facts; messages are written only by the write
  POSTs.
* **One pack per user per IST day** (unique ``(user_id, day)``). Two first reads at
  once both try to insert; the loser re-reads the winner's pack.
* **No address is stored on an item.** A found address lives on the contact; a
  guessed one is worked out here at read time (``people._build_channels``) and goes
  out in the payload only.
* **Only what the user saw holds a company back.** A company whose items were shown
  on /outreach (``seen_at``) in the last ``PACK_REPEAT_DAYS`` and left untouched is
  not picked again; a pack that was built and never opened holds back nothing.
* **Another user's item is a 404**, exactly as an id that does not exist.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import weakref
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.outreach import DueItem, aware, ist_today
from domain.outreach_pack import (
    PACK_APPLIED_DAYS,
    PACK_REPEAT_DAYS,
    PackCompany,
    PersonOption,
    choose_pack,
)
from services.base import parse_uuid
from services.company_map.read import _title_matches, target_roles
from services.outreach import drafts, people, suggest, threads, touches
from services.outreach.errors import NotFound
from services.outreach.people import (
    _build_channels,
    _contact_name,
    address_format_from,
    channels_for,
)
from storage.database import AsyncSessionLocal
from storage.models import (
    ApplicationEvent,
    ApplicationStatus,
    Company,
    CompanyContact,
    Job,
    JobApplication,
    JobStatus,
    OutreachPack,
    OutreachPackItem,
    OutreachPerson,
    OutreachThread,
    OutreachTouch,
)

logger = logging.getLogger(__name__)

#: Matching open roles listed per company.
MAX_ROLES = 3
#: Most item ids one "seen" call may carry.
MAX_SEEN_IDS = 20

_NAME_MAX = 128
_COMPANY_NAME_MAX = 256


def _owner(user_id: str) -> uuid.UUID:
    uid = parse_uuid(user_id)
    if uid is None:
        raise NotFound("User not found")
    return uid


def _bounded(value: str | None, limit: int) -> str | None:
    return None if value is None else value[:limit]


# --- what holds a company back --------------------------------------------------------


async def _held_back(session: AsyncSession, uid: uuid.UUID, moment: datetime) -> set[str]:
    """Companies seen in a pack in the last ``PACK_REPEAT_DAYS`` and left untouched, and
    companies applied to in the last ``PACK_APPLIED_DAYS``."""
    seen_since = moment - timedelta(days=PACK_REPEAT_DAYS)
    applied_since = moment - timedelta(days=PACK_APPLIED_DAYS)
    rows = await session.execute(
        select(
            OutreachPackItem.company_id,
            OutreachPackItem.seen_at,
            OutreachPackItem.status,
            OutreachPackItem.applied_at,
        )
        .join(OutreachPack, OutreachPack.id == OutreachPackItem.pack_id)
        .where(
            OutreachPack.user_id == uid,
            OutreachPackItem.kind == "first",
            OutreachPackItem.company_id.is_not(None),
            OutreachPack.day >= ist_today(applied_since),
        )
    )
    seen: set[str] = set()
    touched: set[str] = set()
    applied: set[str] = set()
    for company_id, seen_at, status, applied_at in rows:
        cid = str(company_id)
        if seen_at is not None and aware(seen_at) >= seen_since:
            seen.add(cid)
        if status != "new" or applied_at is not None:
            touched.add(cid)
        if applied_at is not None and aware(applied_at) >= applied_since:
            applied.add(cid)

    applications = await session.execute(
        select(Job.company_id)
        .join(JobApplication, JobApplication.job_id == Job.id)
        .where(
            JobApplication.user_id == uid,
            JobApplication.created_at >= applied_since,
            Job.company_id.is_not(None),
        )
    )
    applied.update(str(company_id) for company_id in applications.scalars())
    return (seen - touched) | applied


# --- building the day's pack ----------------------------------------------------------


def _options(candidates: suggest.PackCandidates, company_id: str) -> list[PersonOption]:
    """Each ranked person at the company, and whether an address can be shown for them:
    found or saved, or guessed from the company's format."""
    domain = candidates.domains.get(company_id)
    address_format = address_format_from(candidates.contacts.get(company_id, []), domain)
    name = next((s.name for s in candidates.ranked if s.company_id == company_id), "")
    options: list[PersonOption] = []
    for person, contact in candidates.people.get(company_id, []):
        channels = _build_channels(
            name=_contact_name(contact),
            company_name=name,
            contact=contact,
            typed_email=None,
            typed_linkedin=None,
            address_format=address_format,
            company_domain=domain,
        )
        options.append(
            PersonOption(person=person, has_email=any(c["channel"] == "email" for c in channels))
        )
    return options


def _first_items(
    chosen: Sequence[PackCompany], candidates: suggest.PackCandidates
) -> list[OutreachPackItem]:
    names = {s.company_id: s.name for s in candidates.ranked}
    items: list[OutreachPackItem] = []
    for company in chosen:
        for recipient in company.recipients:
            items.append(
                OutreachPackItem(
                    kind="first",
                    company_id=uuid.UUID(company.company_id),
                    company_name=names[company.company_id][:_COMPANY_NAME_MAX],
                    position=len(items),
                    contact_id=uuid.UUID(recipient.contact_id),
                    person_name=_bounded(recipient.name, _NAME_MAX),
                    person_title=_bounded(recipient.title, _NAME_MAX),
                    person_kind=recipient.kind,
                    channel=recipient.channel,
                    format=recipient.format,
                    reasons=list(company.reasons),
                )
            )
    return items


async def _load_pack(
    session: AsyncSession, uid: uuid.UUID, moment: datetime
) -> OutreachPack | None:
    return (
        await session.execute(
            select(OutreachPack)
            .where(OutreachPack.user_id == uid, OutreachPack.day == ist_today(moment))
            .options(selectinload(OutreachPack.items))
        )
    ).scalar_one_or_none()


async def _build(uid: uuid.UUID, moment: datetime) -> None:
    """Choose today's companies and store the pack. A pack another request stored first
    wins: the insert is refused and nothing else is written."""
    async with AsyncSessionLocal() as session:
        held_back = await _held_back(session, uid, moment)
    candidates = await suggest.ranked_for_pack(uid, moment)
    chosen = choose_pack(
        candidates.ranked,
        {s.company_id: _options(candidates, s.company_id) for s in candidates.ranked},
        {cid: str(inbox.id) for cid, inbox in candidates.inboxes.items()},
        held_back,
    )
    async with AsyncSessionLocal() as session:
        session.add(
            OutreachPack(
                user_id=uid,
                day=ist_today(moment),
                created_at=moment,
                items=_first_items(chosen, candidates),
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return
    logger.info("outreach pack built: %d companies", len(chosen))


# --- follow-ups -------------------------------------------------------------------------


async def _first_channels(session: AsyncSession, person_ids: list[uuid.UUID]) -> dict[str, str]:
    """``{person: the channel of the first message to them}``."""
    rows = await session.execute(
        select(OutreachTouch.person_id, OutreachTouch.channel)
        .where(OutreachTouch.person_id.in_(person_ids), OutreachTouch.direction == "out")
        .order_by(OutreachTouch.happened_at, OutreachTouch.created_at)
    )
    first: dict[str, str] = {}
    for person_id, channel in rows:
        first.setdefault(str(person_id), channel)
    return first


async def _add_follow_ups(uid: uuid.UUID, pack_id: uuid.UUID, due: list[DueItem]) -> None:
    """One ``follow_up`` item per due person not already in the pack. Written under a
    lock on the pack row, so two reads at once add each person once."""
    async with AsyncSessionLocal() as session:
        locked = (
            await session.execute(
                select(OutreachPack.id).where(OutreachPack.id == pack_id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            return
        existing = await session.execute(
            select(OutreachPackItem.outreach_person_id, OutreachPackItem.position).where(
                OutreachPackItem.pack_id == pack_id
            )
        )
        have: set[str] = set()
        position = 0
        for person_id, item_position in existing:
            if person_id is not None:
                have.add(str(person_id))
            position = max(position, item_position + 1)
        wanted = [d for d in due if d.person_id is not None and d.person_id not in have]
        if not wanted:
            return
        ids = [uuid.UUID(d.person_id) for d in wanted if d.person_id is not None]
        persons = await session.execute(
            select(OutreachPerson, OutreachThread.company_id)
            .join(OutreachThread, OutreachThread.id == OutreachPerson.thread_id)
            .where(OutreachPerson.id.in_(ids), OutreachThread.user_id == uid)
        )
        by_id = {str(person.id): (person, company_id) for person, company_id in persons}
        channels = await _first_channels(session, ids)
        for due_item in wanted:
            found = by_id.get(due_item.person_id or "")
            if found is None:
                continue
            person, company_id = found
            by_email = channels.get(str(person.id)) == "email"
            session.add(
                OutreachPackItem(
                    pack_id=pack_id,
                    kind="follow_up",
                    company_id=company_id,
                    company_name=due_item.company_name[:_COMPANY_NAME_MAX],
                    position=position,
                    contact_id=person.contact_id,
                    outreach_person_id=person.id,
                    person_name=_bounded(person.name, _NAME_MAX),
                    person_title=_bounded(person.title, _NAME_MAX),
                    person_kind=person.kind,
                    channel="email" if by_email else "linkedin",
                    format="email" if by_email else "linkedin_message",
                    thread_id=person.thread_id,
                    reasons=[],
                )
            )
            position += 1
        await session.commit()


# --- the payload ----------------------------------------------------------------------


def _address(channels: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    email = next((c for c in channels if c["channel"] == "email"), None)
    if email is None:
        return None
    return {
        "value": email["label"],
        "basis": email["basis"],
        "source_host": email["source_host"],
        "learned_from": email.get("learned_from"),
    }


def _item_payload(
    item: OutreachPackItem,
    contact: CompanyContact | None,
    person: OutreachPerson | None,
    company: Company | None,
    contacts: list[CompanyContact],
) -> dict[str, Any]:
    """One item as the page shows it. The address and the LinkedIn link are worked out
    now, from the contact (or the thread's person), never read from the item row."""
    domain = None if company is None else company.domain
    address_format = address_format_from(contacts, domain)
    channels: list[dict[str, Any]] = []
    if person is not None:
        channels = channels_for(
            contact=contact,
            person=person,
            company_name=item.company_name,
            address_format=address_format,
            company_domain=domain,
        )
    elif contact is not None or item.person_name:
        channels = _build_channels(
            name=_contact_name(contact) if contact is not None else item.person_name or "",
            company_name=item.company_name,
            contact=contact,
            typed_email=None,
            typed_linkedin=None,
            address_format=address_format,
            company_domain=domain,
        )
    linkedin = next((c for c in channels if c["channel"] == "linkedin"), None)
    return {
        "id": str(item.id),
        "kind": item.kind,
        "person": {"name": item.person_name, "title": item.person_title, "kind": item.person_kind},
        "channel": item.channel,
        "address": _address(channels),
        "linkedin_url": None if linkedin is None else linkedin["url"],
        "format": item.format,
        "subject": item.subject,
        "message": item.message,
        "generated": item.generated,
        "reason": item.reason,
        "status": item.status,
        "thread_id": None if item.thread_id is None else str(item.thread_id),
    }


async def _roles(
    session: AsyncSession, uid: uuid.UUID, company_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    """``{company: up to MAX_ROLES open Bengaluru roles matching a target role}``."""
    matchers = [
        clause
        for role in (await target_roles(session, uid))[: suggest.MAX_TARGET_ROLES]
        if (clause := _title_matches(role)) is not None
    ]
    if not matchers or not company_ids:
        return {}
    rows = await session.execute(
        select(Job.company_id, Job.id, Job.title)
        .where(
            Job.company_id.in_(company_ids),
            Job.status == JobStatus.ACTIVE,
            suggest._posting_in_city(),
            or_(*matchers),
        )
        .order_by(Job.title, Job.id)
    )
    roles: dict[uuid.UUID, list[dict[str, str]]] = {}
    for company_id, job_id, title in rows:
        listed = roles.setdefault(company_id, [])
        if len(listed) < MAX_ROLES:
            listed.append({"job_id": str(job_id), "title": title})
    return roles


async def _render(
    uid: uuid.UUID, items: Sequence[OutreachPackItem]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(companies, follow_ups)`` as the page shows them, for these items."""
    company_ids = sorted({item.company_id for item in items if item.company_id is not None})
    person_ids = [item.outreach_person_id for item in items if item.outreach_person_id]

    async with AsyncSessionLocal() as session:
        companies = {
            c.id: c
            for c in (
                await session.execute(select(Company).where(Company.id.in_(company_ids)))
            ).scalars()
        }
        contacts: dict[uuid.UUID, list[CompanyContact]] = {}
        if company_ids:
            rows = await session.execute(
                select(CompanyContact)
                .where(
                    CompanyContact.company_id.in_(company_ids),
                    or_(CompanyContact.user_id.is_(None), CompanyContact.user_id == uid),
                )
                .order_by(CompanyContact.created_at, CompanyContact.id)
            )
            for contact in rows.scalars():
                contacts.setdefault(contact.company_id, []).append(contact)
        persons = {
            p.id: p
            for p in (
                await session.execute(
                    select(OutreachPerson).where(OutreachPerson.id.in_(person_ids))
                )
            ).scalars()
        }
        roles = await _roles(session, uid, company_ids)

    contact_by_id = {c.id: c for found in contacts.values() for c in found}

    def payload(item: OutreachPackItem) -> dict[str, Any]:
        company = None if item.company_id is None else companies.get(item.company_id)
        return _item_payload(
            item,
            None if item.contact_id is None else contact_by_id.get(item.contact_id),
            None if item.outreach_person_id is None else persons.get(item.outreach_person_id),
            company,
            [] if item.company_id is None else contacts.get(item.company_id, []),
        )

    grouped: dict[str, dict[str, Any]] = {}
    follow_ups: list[dict[str, Any]] = []
    for item in items:
        if item.kind != "first":
            follow_ups.append(payload(item))
            continue
        key = str(item.company_id) if item.company_id is not None else f"item:{item.id}"
        entry = grouped.get(key)
        if entry is None:
            company = None if item.company_id is None else companies.get(item.company_id)
            entry = grouped[key] = {
                "company_id": None if item.company_id is None else str(item.company_id),
                "name": item.company_name,
                "summary": None if company is None else company.summary,
                "reasons": list(item.reasons or []),
                "roles": [] if item.company_id is None else roles.get(item.company_id, []),
                "applied": False,
                "items": [],
            }
        entry["applied"] = entry["applied"] or item.applied_at is not None
        entry["items"].append(payload(item))
    return list(grouped.values()), follow_ups


async def _payload(uid: uuid.UUID, stored: OutreachPack, due: list[DueItem]) -> dict[str, Any]:
    still_due = {d.person_id for d in due}
    items = [
        item
        for item in stored.items
        if item.kind == "first" or item.status != "new" or str(item.outreach_person_id) in still_due
    ]
    companies, follow_ups = await _render(uid, items)
    every = [i for c in companies for i in c["items"]] + follow_ups
    return {
        "day": stored.day.isoformat(),
        "companies": companies,
        "follow_ups": follow_ups,
        "counts": {
            "companies": len(companies),
            "messages_ready": sum(
                1 for i in every if i["message"] is not None and i["status"] == "new"
            ),
        },
    }


# --- the reads and writes -------------------------------------------------------------


async def get_pack(user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Today's pack (IST), built on the day's first call. No model call, no network.

    ``{day, companies[{company_id, name, summary, reasons, roles, applied, items}],
    follow_ups[], counts{companies, messages_ready}}``. A follow-up that became due
    since the pack was built is added on this read; one that is no longer due (written
    from the thread meanwhile) is left out unless it was acted on here.
    """
    uid = _owner(user_id)
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        stored = await _load_pack(session, uid, moment)
    if stored is None:
        await _build(uid, moment)

    due = [d for d in await touches.due_items(user_id, now=moment) if d.kind == "follow_up"]
    async with AsyncSessionLocal() as session:
        stored = await _load_pack(session, uid, moment)
    if stored is None:  # pragma: no cover - the build or a concurrent one stored it
        raise NotFound("Pack not found")
    have = {str(i.outreach_person_id) for i in stored.items if i.outreach_person_id}
    if any(d.person_id not in have for d in due):
        await _add_follow_ups(uid, stored.id, due)
        async with AsyncSessionLocal() as session:
            stored = await _load_pack(session, uid, moment)
        assert stored is not None
    return await _payload(uid, stored, due)


async def mark_seen(user_id: str, item_ids: list[str], *, now: datetime | None = None) -> None:
    """Record that /outreach displayed these items. Only the first sighting is kept.

    Every id must be one of the caller's items, or nothing is written and the call is a
    ``NotFound``: another user's item is indistinguishable from an id that does not
    exist.
    """
    uid = _owner(user_id)
    moment = datetime.now(UTC) if now is None else aware(now)
    parsed = {parse_uuid(item_id) for item_id in item_ids}
    if None in parsed:
        raise NotFound("Pack item not found")
    ids = {item_id for item_id in parsed if item_id is not None}
    if not ids:
        return
    async with AsyncSessionLocal() as session:
        owned = set(
            (
                await session.execute(
                    select(OutreachPackItem.id)
                    .join(OutreachPack, OutreachPack.id == OutreachPackItem.pack_id)
                    .where(OutreachPackItem.id.in_(ids), OutreachPack.user_id == uid)
                )
            ).scalars()
        )
        if owned != ids:
            raise NotFound("Pack item not found")
        await session.execute(
            update(OutreachPackItem)
            .where(OutreachPackItem.id.in_(ids), OutreachPackItem.seen_at.is_(None))
            .values(seen_at=moment)
        )
        await session.commit()


# --- writing the messages -------------------------------------------------------------

#: One lock per (user, company or item) in this process, so two writes at once spend
#: once. Across processes the ``FOR UPDATE`` on the items does the same on Postgres.
_write_locks: weakref.WeakValueDictionary[tuple[uuid.UUID, uuid.UUID], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


def _lock_for(key: tuple[uuid.UUID, uuid.UUID]) -> asyncio.Lock:
    lock = _write_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _write_locks[key] = lock
    return lock


async def _company_items(
    session: AsyncSession, uid: uuid.UUID, cid: uuid.UUID, moment: datetime, *, lock: bool
) -> list[OutreachPackItem]:
    """Today's ``first`` items for this company in the caller's pack, in order."""
    stmt = (
        select(OutreachPackItem)
        .join(OutreachPack, OutreachPack.id == OutreachPackItem.pack_id)
        .where(
            OutreachPack.user_id == uid,
            OutreachPack.day == ist_today(moment),
            OutreachPackItem.kind == "first",
            OutreachPackItem.company_id == cid,
        )
        .order_by(OutreachPackItem.position)
    )
    if lock:
        stmt = stmt.with_for_update(of=OutreachPackItem)
    return list((await session.execute(stmt)).scalars())


async def _posting(session: AsyncSession, uid: uuid.UUID, cid: uuid.UUID) -> dict[str, Any] | None:
    """The first open role listed for the company, as ``build_role_context`` takes it."""
    listed = (await _roles(session, uid, [cid])).get(cid)
    if not listed:
        return None
    job = await session.get(Job, uuid.UUID(listed[0]["job_id"]))
    if job is None:
        return None
    return {
        "title": job.title,
        "company": None,
        "skills": list(job.skills or []),
        "description": job.description_clean or "",
    }


async def write_company(
    user_id: str, company_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Write every unwritten message for one company in today's pack: ``{items}``.

    One model call for all the company's recipients (``drafts.write_pack_messages``,
    which claims the budgets and falls back to templates), stored on the items. Items
    that already have a message are returned as they are and spend nothing. A company
    not in the caller's pack today is a ``NotFound``.
    """
    uid = _owner(user_id)
    cid = parse_uuid(company_id)
    if cid is None:
        raise NotFound("Company not in today's pack")
    moment = datetime.now(UTC) if now is None else aware(now)

    async with _lock_for((uid, cid)), AsyncSessionLocal() as session:
        items = await _company_items(session, uid, cid, moment, lock=True)
        if not items:
            raise NotFound("Company not in today's pack")
        pending = [item for item in items if item.message is None and item.status == "new"]
        if pending:
            company = await session.get(Company, cid)
            profile = await drafts._profile_row(session, uid)
            last_round = (await suggest._funding_rounds(session, [cid])).get(cid)
            posting = await _posting(session, uid, cid)
            contexts = [
                drafts.context_for_pack(
                    item, company, profile, last_round=last_round, posting=posting
                )
                for item in pending
            ]
            written = await drafts.write_pack_messages(user_id, contexts, moment)
            for item, message in zip(pending, written, strict=True):
                item.subject = message.subject
                item.message = message.message
                item.generated = message.generated
                item.reason = message.reason
            await session.commit()
            logger.info("outreach pack company %s: %d messages written", cid, len(pending))

    async with AsyncSessionLocal() as session:
        items = await _company_items(session, uid, cid, moment, lock=False)
    companies, _ = await _render(uid, items)
    return {"items": companies[0]["items"] if companies else []}


async def _owned_item(session: AsyncSession, uid: uuid.UUID, iid: uuid.UUID) -> OutreachPackItem:
    item = (
        await session.execute(
            select(OutreachPackItem)
            .join(OutreachPack, OutreachPack.id == OutreachPackItem.pack_id)
            .where(OutreachPackItem.id == iid, OutreachPack.user_id == uid)
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFound("Pack item not found")
    return item


async def write_follow_up(
    user_id: str, item_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Write a due follow-up in the caller's pack: ``{item}``.

    Through the thread draft (``drafts.draft``, which knows the clock, the earlier
    message and its own budgets), stored on the item so a second call spends nothing.
    Another user's item, or a first-message item, is a ``NotFound``.
    """
    uid = _owner(user_id)
    iid = parse_uuid(item_id)
    if iid is None:
        raise NotFound("Pack item not found")
    moment = datetime.now(UTC) if now is None else aware(now)

    async with _lock_for((uid, iid)):
        async with AsyncSessionLocal() as session:
            item = await _owned_item(session, uid, iid)
            if item.kind != "follow_up":
                raise NotFound("Pack item not found")
            thread_id, person_id = item.thread_id, item.outreach_person_id
            written = item.message is not None
        if not written:
            if thread_id is None or person_id is None:
                raise NotFound("Thread not found")
            result = await drafts.draft(
                user_id, str(thread_id), person_id=str(person_id), fmt=item.format, now=moment
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(OutreachPackItem)
                    .where(OutreachPackItem.id == iid, OutreachPackItem.message.is_(None))
                    .values(
                        subject=result["subject"],
                        message=result["message"],
                        generated=result["generated"],
                        reason=result["reason"],
                    )
                )
                await session.commit()

    async with AsyncSessionLocal() as session:
        item = await _owned_item(session, uid, iid)
    _, follow_ups = await _render(uid, [item])
    return {"item": follow_ups[0]}


# --- acting on the pack ---------------------------------------------------------------


async def _render_item(uid: uuid.UUID, iid: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        item = await _owned_item(session, uid, iid)
    companies, follow_ups = await _render(uid, [item])
    return companies[0]["items"][0] if companies else follow_ups[0]


async def _person_for_contact(thread_id: str, contact_id: uuid.UUID) -> str | None:
    async with AsyncSessionLocal() as session:
        found = (
            await session.execute(
                select(OutreachPerson.id).where(
                    OutreachPerson.thread_id == uuid.UUID(thread_id),
                    OutreachPerson.contact_id == contact_id,
                )
            )
        ).scalar_one_or_none()
    return None if found is None else str(found)


async def mark_sent(
    user_id: str,
    item_id: str,
    *,
    message: str,
    subject: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record that the user sent this item's message themselves: ``{item}``.

    A first message opens (or finds) the company's thread with ``source="suggestion"``,
    adds the person from the contact (no address is passed: a found one already lives
    on the contact, a guessed one must never be stored) and records an outbound touch
    with the text the user sent. A follow-up records the touch on its thread. The item
    becomes ``sent`` last, so a refusal from a cap (``Conflict``) leaves it ``new``.
    A second call on a sent item changes nothing.
    """
    uid = _owner(user_id)
    iid = parse_uuid(item_id)
    if iid is None:
        raise NotFound("Pack item not found")
    moment = datetime.now(UTC) if now is None else aware(now)

    async with _lock_for((uid, iid)):
        async with AsyncSessionLocal() as session:
            item = await _owned_item(session, uid, iid)
        if item.status != "sent":
            if item.kind == "first":
                if item.company_id is None or item.contact_id is None:
                    raise NotFound("Company not found")
                summary, _ = await threads.create_thread(
                    user_id,
                    company_id=str(item.company_id),
                    company_name=None,
                    job_id=None,
                    source="suggestion",
                )
                thread_id = str(summary["id"])
                person_id = await _person_for_contact(thread_id, item.contact_id)
                if person_id is None:
                    await people.add_person(user_id, thread_id, contact_id=str(item.contact_id))
                    person_id = await _person_for_contact(thread_id, item.contact_id)
                if person_id is None:  # pragma: no cover - add_person raised instead
                    raise NotFound("Person not found")
            else:
                if item.thread_id is None or item.outreach_person_id is None:
                    raise NotFound("Thread not found")
                thread_id, person_id = str(item.thread_id), str(item.outreach_person_id)

            await touches.record_touch(
                user_id,
                thread_id,
                person_id=person_id,
                channel=item.channel,
                direction="out",
                body=message,
                now=moment,
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(OutreachPackItem)
                    .where(OutreachPackItem.id == iid)
                    .values(
                        status="sent",
                        thread_id=uuid.UUID(thread_id),
                        acted_at=moment,
                        message=message,
                        subject=subject if item.format == "email" else None,
                    )
                )
                await session.commit()
            logger.info("outreach pack item %s sent", iid)
    return {"item": await _render_item(uid, iid)}


async def mark_skipped(user_id: str, item_id: str, *, now: datetime | None = None) -> None:
    """Skip one item. When every first-message item of its company in that pack is
    skipped, the company's suggestion is dismissed for 30 days, so it stays out of the
    next packs too. A sent item stays sent."""
    uid = _owner(user_id)
    iid = parse_uuid(item_id)
    if iid is None:
        raise NotFound("Pack item not found")
    moment = datetime.now(UTC) if now is None else aware(now)

    async with AsyncSessionLocal() as session:
        item = await _owned_item(session, uid, iid)
        if item.status == "new":
            item.status = "skipped"
            item.acted_at = moment
        company_id = item.company_id if item.kind == "first" else None
        siblings: list[str] = []
        if company_id is not None:
            siblings = list(
                (
                    await session.execute(
                        select(OutreachPackItem.status).where(
                            OutreachPackItem.pack_id == item.pack_id,
                            OutreachPackItem.kind == "first",
                            OutreachPackItem.company_id == company_id,
                            OutreachPackItem.id != iid,
                        )
                    )
                ).scalars()
            )
        all_skipped = item.status == "skipped" and all(s == "skipped" for s in siblings)
        await session.commit()
    if company_id is not None and all_skipped:
        await suggest.dismiss_suggestion(user_id, str(company_id))
    logger.info("outreach pack item %s skipped", iid)


async def mark_applied(
    user_id: str, company_id: str, job_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Record that the user applied to one of the company's roles from today's pack.

    The role must be the company's. The tracker row is created as ``applied`` (or a
    saved one is moved to ``applied``; one already further along is left alone), the
    company's items get ``applied_at`` (which keeps it out of packs for 30 days), and
    the company's thread, if there is one without an application, is linked to it.
    Saying it twice is one application.
    """
    uid = _owner(user_id)
    cid, jid = parse_uuid(company_id), parse_uuid(job_id)
    if cid is None or jid is None:
        raise NotFound("Role not found")
    moment = datetime.now(UTC) if now is None else aware(now)

    async with _lock_for((uid, cid)), AsyncSessionLocal() as session:
        items = await _company_items(session, uid, cid, moment, lock=False)
        if not items:
            raise NotFound("Company not in today's pack")
        job = await session.get(Job, jid)
        if job is None or job.company_id != cid:
            raise NotFound("Role not found")

        application = (
            await session.execute(
                select(JobApplication).where(
                    JobApplication.user_id == uid, JobApplication.job_id == jid
                )
            )
        ).scalar_one_or_none()
        if application is None:
            application = JobApplication(
                user_id=uid,
                job_id=jid,
                status=ApplicationStatus.APPLIED,
                applied_at=moment,
                applied_at_explicit=moment,
            )
            session.add(application)
            await session.flush()
            session.add(
                ApplicationEvent(
                    application_id=application.id,
                    from_status=None,
                    to_status=ApplicationStatus.APPLIED.value,
                )
            )
        elif application.status == ApplicationStatus.SAVED:
            application.status = ApplicationStatus.APPLIED
            application.applied_at = application.applied_at or moment
            application.applied_at_explicit = moment
            session.add(
                ApplicationEvent(
                    application_id=application.id,
                    from_status=ApplicationStatus.SAVED.value,
                    to_status=ApplicationStatus.APPLIED.value,
                )
            )

        for item in items:
            if item.applied_at is None:
                item.applied_at = moment
        thread = (
            await session.execute(
                select(OutreachThread).where(
                    OutreachThread.user_id == uid, OutreachThread.company_id == cid
                )
            )
        ).scalar_one_or_none()
        if thread is not None and thread.application_id is None:
            thread.application_id = application.id
        application_id = str(application.id)
        await session.commit()
    logger.info("outreach pack company %s applied", cid)
    return {"application_id": application_id, "applied": True}


# --- the briefing's read -----------------------------------------------------------------


async def pack_counts(user_id: str, *, now: datetime | None = None) -> tuple[int, int] | None:
    """``(companies still to review, messages ready)`` in today's pack, or ``None`` when
    the user has no pack today. Reads only: a briefing never builds a pack."""
    uid = parse_uuid(user_id)
    if uid is None:
        return None
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        stored = await _load_pack(session, uid, moment)
    if stored is None:
        return None
    waiting = [item for item in stored.items if item.status == "new"]
    companies = {item.company_id for item in waiting if item.kind == "first" and item.company_id}
    ready = sum(1 for item in waiting if item.message is not None)
    return len(companies), ready
