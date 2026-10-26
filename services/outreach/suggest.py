"""
services/outreach/suggest.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Which companies are worth a message today, and who to write to inside each.

This module gathers the facts about up to ``MAX_CANDIDATES`` listed Bengaluru
companies in a handful of batched queries, hands them to the pure ranking in
``domain/outreach_rank.py``, and caches the best ``CACHED_TOP`` per user. It is
the read behind ``GET /outreach/suggestions``, the Overview's suggestion card and
the "Not now" button.

Rules this module keeps:

* **No model call and no outside call.** Every signal is already stored: funding
  and sector on the company, open roles on ``jobs``, the stack on
  ``company_profiles`` and the postings, issues in ``oss_issue_snapshots``, people
  in ``company_contacts``, the round in ``company_news``. A page load never reaches
  GitHub or the web.
* **One query per kind of fact, not per company.** Whatever the number of
  candidates, an uncached call sends the same few statements (see ``_compute``).
* **The cache is a convenience, never a dependency.** It holds only plain dicts for
  the one user whose key it is: no ORM object and nothing that came from another
  user's private rows. A cache that is missing, broken or throwing is a miss, and
  the answer is computed instead.
* **The cache is never the last word.** The user's threads and dismissals change
  without anything clearing it, so both are read on every call and removed from
  what the cache returns. ``invalidate`` is what lets the list refill: a company
  the user just started (or stopped) a thread with changes which twenty-five make
  the cut. A profile save clears it too (``services/profiles.py``), since the
  roles, goals and skills decide the ranking.
* **Companies that are hiring are weighed first.** The candidate cap is applied
  after ordering the companies with an active Bengaluru role ahead of the rest, so
  a company hiring for you that never raised is not what falls outside the 400.
* **Only people the user may be told about.** A suggested person is a curated
  founder, leader or engineer, or one of the user's own saved contacts. Another
  user's private contact is never loaded, and a ``careers_page`` row is a link, not
  a person.
* **Dismissals are the briefing's.** "Not now" is a briefing dismissal with the card
  id ``outreach_suggestion:<company_id>``, snoozed for 30 days, so the Overview
  card and the /outreach row can never disagree.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import services.agent_memory as agent_memory
from domain.company_taxonomy import SECTORS, STAGES
from domain.outreach import aware
from domain.outreach_rank import (
    FUNDING_BANDS,
    CompanySignals,
    PersonSignals,
    RankedPerson,
    Suggestion,
    UserSignals,
    rank_companies,
    rank_people,
    sectors_for_profile,
    size_class,
)
from services.base import parse_uuid
from services.cache_backend import CacheBackend
from services.company_map.read import (
    AREA_CITY,
    _city_terms,
    _newest_first,
    _title_matches,
    listed_clause,
    target_roles,
)
from services.outreach.errors import NotFound
from services.outreach.people import _build_channels, _contact_name, _label, _source_host
from storage.database import AsyncSessionLocal
from storage.models import (
    Company,
    CompanyContact,
    CompanyNews,
    CompanyProfile,
    Job,
    JobStatus,
    OssIssueSnapshot,
    OutreachThread,
    Profile,
    UserSkill,
)

logger = logging.getLogger(__name__)

#: How long one user's ranked list is kept.
SUGGESTION_CACHE_TTL = 3600
#: The most companies one call weighs.
MAX_CANDIDATES = 400
#: How long "Not now" hides a company.
SUGGESTION_SNOOZE_DAYS = 30
#: How many ranked rows are cached per user.
CACHED_TOP = 25

#: A company that raised within this many days is a candidate: the widest funding band.
FUNDING_SIGNAL_DAYS = FUNDING_BANDS[-1][0]
#: Target roles read per user. Each adds a column to the one role-count query.
MAX_TARGET_ROLES = 5
#: The longest role name echoed back in a reason line.
ROLE_NAME_MAX = 80
#: The longest skill name read from a stack or a posting.
SKILL_NAME_MAX = 64

#: The kinds of curated person a suggestion can name. The user's own contacts are
#: any kind but ``careers_page``.
_CURATED_PERSON_KINDS = ("founder", "leader", "engineer")

_CARD_PREFIX = "outreach_suggestion:"


def suggestion_card_id(company_id: str) -> str:
    """The briefing card id of a company's suggestion: what "Not now" dismisses."""
    return f"{_CARD_PREFIX}{company_id}"


def cache_key(user_id: str) -> str:
    """Where one user's ranked list lives. A user id is spelled the one way, so
    whoever clears the entry reaches the one that was written."""
    parsed = parse_uuid(user_id)
    return f"tr:outreach:suggest:{user_id if parsed is None else parsed}"


# --- the cache -----------------------------------------------------------------------


def _is_entry(value: Any) -> bool:
    """Is ``value`` something this module wrote? Anything else is a miss."""
    if not isinstance(value, dict) or not isinstance(value.get("profile_complete"), bool):
        return False
    rows = value.get("suggestions")
    return isinstance(rows, list) and all(
        isinstance(row, dict) and isinstance(row.get("company_id"), str) for row in rows
    )


async def _read_cache(user_id: str) -> dict[str, Any] | None:
    try:
        value = await CacheBackend.get_json(cache_key(user_id))
    except Exception:
        logger.warning("Could not read cached outreach suggestions", exc_info=True)
        return None
    return value if _is_entry(value) else None


async def _write_cache(user_id: str, entry: dict[str, Any]) -> None:
    try:
        await CacheBackend.set_json(cache_key(user_id), entry, SUGGESTION_CACHE_TTL)
    except Exception:
        logger.warning("Could not cache outreach suggestions", exc_info=True)


async def invalidate(user_id: str) -> None:
    """Drop one user's cached list, so the next call ranks again. Never raises: the
    cache degrades, and a cache that cannot be cleared must not fail a write."""
    try:
        await CacheBackend.delete(cache_key(user_id))
    except Exception:
        logger.warning("Could not clear cached outreach suggestions", exc_info=True)


# --- what the user has already dealt with --------------------------------------------


async def _threaded(uid: uuid.UUID) -> set[str]:
    """Ids of the companies the user has a thread with, open or closed."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(OutreachThread.company_id).where(
                OutreachThread.user_id == uid, OutreachThread.company_id.is_not(None)
            )
        )
        return {str(company_id) for company_id in rows.scalars()}


async def _dismissed(uid: uuid.UUID) -> set[str]:
    """Ids of the companies whose suggestion the user dismissed and whose snooze has not ended."""
    ids: set[str] = set()
    for card in await agent_memory.active_dismissals(user_id=str(uid)):
        if card.startswith(_CARD_PREFIX) and (cid := parse_uuid(card[len(_CARD_PREFIX) :])):
            ids.add(str(cid))
    return ids


# --- gathering the facts -------------------------------------------------------------


def _labels(value: Any) -> list[str]:
    """The non-empty, bounded strings of a stored list; anything else is nothing."""
    if not isinstance(value, list | tuple):
        return []
    labels = (item.strip()[:SKILL_NAME_MAX] for item in value if isinstance(item, str))
    return [label for label in labels if label]


async def _user_signals(
    session: AsyncSession, uid: uuid.UUID
) -> tuple[list[str], UserSignals, bool]:
    """``(target roles, what the profile says, is the profile complete)``.

    Complete means a target role *and* at least one skill: the roles decide who is
    hiring for you and the skills decide what you share with a stack.
    """
    roles = await target_roles(session, uid)
    goals = (
        await session.execute(select(Profile.career_goals).where(Profile.user_id == uid))
    ).scalar_one_or_none()
    skills = (
        await session.execute(
            select(UserSkill.skill_name)
            .where(UserSkill.user_id == uid)
            .order_by(UserSkill.skill_name)
        )
    ).scalars()
    skill_names = tuple(_labels(list(skills)))
    user = UserSignals(skills=skill_names, sectors=sectors_for_profile(roles, goals))
    return roles, user, bool(roles) and bool(skill_names)


def _posting_in_city() -> ColumnElement[bool]:
    """A posting located in Bengaluru, by its city or its raw location text, in any of
    the city's spellings: the match ``hiring_for_you`` makes."""
    return or_(
        *(
            clause
            for term in _city_terms(AREA_CITY)
            for clause in (Job.city.ilike(f"%{term}%"), Job.location_raw.ilike(f"%{term}%"))
        )
    )


def _role_name(role: str) -> str:
    """A target role as a reason line shows it: printable characters only, whitespace
    flattened to single spaces, cut to ``ROLE_NAME_MAX``."""
    printable = "".join(ch for ch in role if ch.isprintable() or ch.isspace())
    return " ".join(printable.split())[:ROLE_NAME_MAX]


def _candidates_statement(uid: uuid.UUID, now: datetime, sectors: frozenset[str]) -> Select[Any]:
    """The query behind :func:`_candidates`, apart from running it.

    Listed Bengaluru companies with at least one signal: a raise in the last
    ``FUNDING_SIGNAL_DAYS``, any active posting, an open good-first-issue count, or the
    sector of one of the user's goals. Companies the user already has a thread with are
    left out (``NOT EXISTS`` on their own threads, so the statement does not grow with
    them). Those with an active Bengaluru posting come first, then the most recently
    funded, so the cap never cuts a company that is hiring for the sake of one that
    merely raised.
    """
    has_posting = exists().where(Job.company_id == Company.id, Job.status == JobStatus.ACTIVE)
    hiring_here = exists().where(
        Job.company_id == Company.id, Job.status == JobStatus.ACTIVE, _posting_in_city()
    )
    has_issues = exists().where(
        OssIssueSnapshot.company_id == Company.id, OssIssueSnapshot.total_count > 0
    )
    has_thread = exists().where(
        OutreachThread.user_id == uid, OutreachThread.company_id == Company.id
    )
    signals = [
        Company.last_funding_at >= now - timedelta(days=FUNDING_SIGNAL_DAYS),
        has_posting,
        has_issues,
    ]
    if sectors:
        signals.append(Company.sector.in_(sorted(sectors)))
    return (
        select(
            Company.id,
            Company.name,
            Company.summary,
            Company.area,
            Company.stage,
            Company.sector,
            Company.tier,
            Company.employee_count,
            Company.employee_count_range,
            Company.last_funding_at,
            Company.domain,
        )
        .where(listed_clause(AREA_CITY), or_(*signals), ~has_thread)
        .order_by(
            case((hiring_here, 0), else_=1),
            Company.last_funding_at.desc().nulls_last(),
            Company.name,
            Company.id,
        )
        .limit(MAX_CANDIDATES)
    )


async def _candidates(
    session: AsyncSession, uid: uuid.UUID, now: datetime, sectors: frozenset[str]
) -> list[Row[Any]]:
    return list((await session.execute(_candidates_statement(uid, now, sectors))).all())


async def _role_counts(
    session: AsyncSession, ids: list[uuid.UUID], roles: list[str]
) -> dict[uuid.UUID, tuple[int, int, str | None]]:
    """``{company: (open Bengaluru roles, those matching a target role, the role most of
    them match)}``, in one grouped query over the active postings.

    Titles are matched the way search and ``hiring_for_you`` match them.
    """
    matchers = [
        (_role_name(role), clause)
        for role in roles[:MAX_TARGET_ROLES]
        if (clause := _title_matches(role)) is not None
    ]
    columns: list[Any] = [Job.company_id, func.count(Job.id)]
    if matchers:
        columns.append(func.sum(case((or_(*(clause for _, clause in matchers)), 1), else_=0)))
        columns.extend(func.sum(case((clause, 1), else_=0)) for _, clause in matchers)
    stmt = (
        select(*columns)
        .where(Job.company_id.in_(ids), Job.status == JobStatus.ACTIVE, _posting_in_city())
        .group_by(Job.company_id)
    )
    counts: dict[uuid.UUID, tuple[int, int, str | None]] = {}
    for row in (await session.execute(stmt)).all():
        matching = int(row[2] or 0) if matchers else 0
        best: str | None = None
        if matching:
            per_role = [int(count or 0) for count in row[3:]]
            best = matchers[per_role.index(max(per_role))][0]
        counts[row[0]] = (int(row[1]), matching, best)
    return counts


async def _stacks(
    session: AsyncSession, ids: list[uuid.UUID], *, with_postings: bool
) -> dict[uuid.UUID, tuple[str, ...]]:
    """``{company: its stack}``: ``company_profiles.tech_stack`` in its stored order, then
    the skills of its active postings, sorted. Postings are read only when the user has
    skills to share, because nothing else uses them."""
    stacks: dict[uuid.UUID, list[str]] = {}
    seen: dict[uuid.UUID, set[str]] = {}

    def add(company_id: uuid.UUID, labels: list[str]) -> None:
        kept = stacks.setdefault(company_id, [])
        known = seen.setdefault(company_id, set())
        for label in labels:
            if label.casefold() not in known:
                known.add(label.casefold())
                kept.append(label)

    rows = await session.execute(
        select(CompanyProfile.company_id, CompanyProfile.tech_stack).where(
            CompanyProfile.company_id.in_(ids)
        )
    )
    for company_id, tech_stack in rows:
        add(company_id, _labels(tech_stack))

    if with_postings:
        posted: dict[uuid.UUID, set[str]] = {}
        rows = await session.execute(
            select(Job.company_id, Job.skills).where(
                Job.company_id.in_(ids), Job.status == JobStatus.ACTIVE, Job.skills.is_not(None)
            )
        )
        for company_id, skills in rows:
            posted.setdefault(company_id, set()).update(_labels(skills))
        for company_id, labels in posted.items():
            add(company_id, sorted(labels, key=lambda label: (label.casefold(), label)))
    return {company_id: tuple(labels) for company_id, labels in stacks.items()}


async def _issue_counts(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """``{company: open good-first-issues}`` for the companies with any snapshot count."""
    rows = await session.execute(
        select(OssIssueSnapshot.company_id, OssIssueSnapshot.total_count).where(
            OssIssueSnapshot.company_id.in_(ids), OssIssueSnapshot.total_count > 0
        )
    )
    return {company_id: int(total) for company_id, total in rows}


async def _funding_rounds(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """``{company: the round of its newest funding story}``, as the story displays it
    ("Series A"). ``None`` when that story names no round; an older story's round is not
    borrowed, since it would describe a different raise."""
    rows = await session.execute(
        select(CompanyNews.company_id, CompanyNews.round)
        .where(CompanyNews.company_id.in_(ids), CompanyNews.category == "funding")
        .order_by(*_newest_first())
    )
    rounds: dict[uuid.UUID, str | None] = {}
    for company_id, round_ in rows:
        rounds.setdefault(company_id, round_)
    return rounds


async def _people(
    session: AsyncSession, ids: list[uuid.UUID], uid: uuid.UUID
) -> dict[uuid.UUID, list[CompanyContact]]:
    """``{company: the named people the user may be told about}``.

    Curated founders, leaders and engineers, and the user's own saved contacts. Not
    another user's, not a careers page, not a contact with no name.
    """
    visible = or_(
        CompanyContact.kind.in_(_CURATED_PERSON_KINDS) & CompanyContact.user_id.is_(None),
        (CompanyContact.user_id == uid) & (CompanyContact.kind != "careers_page"),
    )
    rows = await session.execute(
        select(CompanyContact)
        .where(
            CompanyContact.company_id.in_(ids),
            CompanyContact.name.is_not(None),
            CompanyContact.name != "",
            visible,
        )
        .order_by(CompanyContact.created_at, CompanyContact.id)
    )
    people: dict[uuid.UUID, list[CompanyContact]] = {}
    for contact in rows.scalars():
        people.setdefault(contact.company_id, []).append(contact)
    return people


async def _inboxes(session: AsyncSession, ids: list[uuid.UUID]) -> dict[str, CompanyContact]:
    """``{company: its published hiring inbox}``: the oldest curated ``careers_inbox``
    contact with an address. A user's own saved inbox is a person they chose, not this."""
    rows = await session.execute(
        select(CompanyContact)
        .where(
            CompanyContact.company_id.in_(ids),
            CompanyContact.kind == "careers_inbox",
            CompanyContact.user_id.is_(None),
            CompanyContact.email.is_not(None),
        )
        .order_by(CompanyContact.created_at, CompanyContact.id)
    )
    inboxes: dict[str, CompanyContact] = {}
    for contact in rows.scalars():
        inboxes.setdefault(str(contact.company_id), contact)
    return inboxes


# --- weighing them -------------------------------------------------------------------


def _ranked_people(
    company: Row[Any], contacts: list[CompanyContact]
) -> list[tuple[RankedPerson, CompanyContact]]:
    """The people at this company, best first, by the ranking the thread drawer uses
    for its suggested people. Only a found or saved address counts towards reach."""
    by_id = {str(contact.id): contact for contact in contacts}
    signals: list[PersonSignals] = []
    for contact in contacts:
        name = _contact_name(contact)
        channels = _build_channels(
            name=name,
            company_name=company.name,
            contact=contact,
            typed_email=None,
            typed_linkedin=None,
        )
        signals.append(
            PersonSignals(
                contact_id=str(contact.id),
                name=name,
                title=contact.title,
                kind=contact.kind,
                verified=contact.verified,
                user_saved=contact.user_id is not None,
                has_direct_link=any(c["direct"] and c["channel"] != "email" for c in channels),
                has_public_email=any(c["channel"] == "email" for c in channels),
                source_host=_source_host(contact.source_url),
            )
        )
    ranked = rank_people(
        signals,
        size_class(
            company.tier, company.employee_count, company.employee_count_range, company.stage
        ),
    )
    return [(person, by_id[person.contact_id]) for person in ranked]


def _top_person(company: Row[Any], contacts: list[CompanyContact]) -> dict[str, Any] | None:
    """The person to write to first at this company."""
    ranked = _ranked_people(company, contacts)
    if not ranked:
        return None
    top = ranked[0][0]
    return {"name": top.name, "title": top.title, "kind": top.kind}


@dataclass(frozen=True)
class _Gathered:
    complete: bool
    user: UserSignals
    rows: list[Row[Any]]
    signals: list[CompanySignals]
    people: dict[uuid.UUID, list[CompanyContact]]


async def _gather(
    session: AsyncSession, uid: uuid.UUID, now: datetime, dismissed: set[str]
) -> _Gathered:
    """The candidates and every fact the ranking weighs, in batched statements.

    Companies with a thread are left out by the candidate query itself; ``dismissed``
    (read once by the caller) is removed here. Statements sent, for any number of
    companies: the user's roles, goals and skills (3), the candidates (1), and one each
    for role counts, stacks (2 with skills to match), issue counts, funding rounds (only
    if any company raised) and people.
    """
    roles, user, complete = await _user_signals(session, uid)
    rows = [
        row
        for row in await _candidates(session, uid, now, user.sectors)
        if str(row.id) not in dismissed
    ]
    if not rows:
        return _Gathered(complete=complete, user=user, rows=[], signals=[], people={})
    ids = [row.id for row in rows]
    role_counts = await _role_counts(session, ids, roles)
    stacks = await _stacks(session, ids, with_postings=bool(user.skills))
    issues = await _issue_counts(session, ids)
    funded = [row.id for row in rows if row.last_funding_at is not None]
    rounds = await _funding_rounds(session, funded) if funded else {}
    people = await _people(session, ids, uid)

    signals = [
        CompanySignals(
            company_id=str(row.id),
            name=row.name,
            summary=row.summary,
            area=row.area,
            stage=row.stage,
            sector=row.sector,
            last_funding_at=row.last_funding_at,
            last_round=rounds.get(row.id),
            open_roles=role_counts.get(row.id, (0, 0, None))[0],
            matching_roles=role_counts.get(row.id, (0, 0, None))[1],
            target_role=role_counts.get(row.id, (0, 0, None))[2],
            stack=stacks.get(row.id, ()),
            good_first_issues=issues.get(row.id, 0),
            has_named_person=row.id in people,
        )
        for row in rows
    ]
    return _Gathered(complete=complete, user=user, rows=rows, signals=signals, people=people)


async def _compute(uid: uuid.UUID, now: datetime, dismissed: set[str]) -> dict[str, Any]:
    """Rank the candidates and return what the cache keeps."""
    async with AsyncSessionLocal() as session:
        gathered = await _gather(session, uid, now, dismissed)
    rows, people = gathered.rows, gathered.people
    if not rows:
        return {"profile_complete": gathered.complete, "suggestions": []}
    by_id = {str(row.id): row for row in rows}
    ranked = [
        {
            "company_id": s.company_id,
            "name": s.name,
            "summary": s.summary,
            "area": s.area,
            "stage": _label(STAGES, s.stage),
            "sector": _label(SECTORS, s.sector),
            "score": s.score,
            "reasons": list(s.reasons),
            "top_person": _top_person(by_id[s.company_id], people.get(by_id[s.company_id].id, [])),
        }
        for s in rank_companies(gathered.signals, gathered.user, now, CACHED_TOP)
    ]
    logger.info("outreach suggestions: %d candidates, %d ranked", len(rows), len(ranked))
    return {"profile_complete": gathered.complete, "suggestions": ranked}


@dataclass(frozen=True)
class PackCandidates:
    """What the daily pack chooses from (``services/outreach/pack.py``)."""

    ranked: list[Suggestion]
    #: ``{company id: [(person, their contact)], best first}``.
    people: dict[str, list[tuple[RankedPerson, CompanyContact]]]
    #: ``{company id: every person the user may be told about}``, for the address format.
    contacts: dict[str, list[CompanyContact]]
    inboxes: dict[str, CompanyContact]
    domains: dict[str, str | None]


async def ranked_for_pack(uid: uuid.UUID, now: datetime) -> PackCandidates:
    """The ranked companies (threads and dismissals already out), their people and
    hiring inboxes, uncached: the pack is built once a day and kept."""
    dismissed = await _dismissed(uid)
    async with AsyncSessionLocal() as session:
        gathered = await _gather(session, uid, now, dismissed)
        ranked = rank_companies(gathered.signals, gathered.user, now, CACHED_TOP)
        inboxes = (
            await _inboxes(session, [uuid.UUID(s.company_id) for s in ranked]) if ranked else {}
        )
    rows = {str(row.id): row for row in gathered.rows}
    contacts = {str(cid): list(found) for cid, found in gathered.people.items()}
    return PackCandidates(
        ranked=ranked,
        people={
            s.company_id: _ranked_people(rows[s.company_id], contacts.get(s.company_id, []))
            for s in ranked
        },
        contacts=contacts,
        inboxes=inboxes,
        domains={s.company_id: rows[s.company_id].domain for s in ranked},
    )


# --- the reads and writes ------------------------------------------------------------


async def suggestions(
    user_id: str, *, limit: int = 10, now: datetime | None = None
) -> dict[str, Any]:
    """The companies worth a message, best first: ``{suggestions[], profile_complete}``.

    Ranked from stored facts only (spec 5.1) and kept per user for
    ``SUGGESTION_CACHE_TTL``. On every call, cached or not, a company the user has a
    thread with or has dismissed is left out, and the list is cut to ``limit`` (at
    most ``CACHED_TOP``). ``now`` is for tests.
    """
    uid = parse_uuid(user_id)
    if uid is None:
        return {"suggestions": [], "profile_complete": False}
    moment = datetime.now(UTC) if now is None else aware(now)

    dismissed = await _dismissed(uid)
    excluded = dismissed | await _threaded(uid)
    entry = await _read_cache(user_id)
    if entry is None:
        entry = await _compute(uid, moment, dismissed)
        await _write_cache(user_id, entry)

    kept = [row for row in entry["suggestions"] if row["company_id"] not in excluded]
    return {"suggestions": kept[: max(0, limit)], "profile_complete": entry["profile_complete"]}


async def dismiss_suggestion(user_id: str, company_id: str) -> None:
    """Hide this company's suggestion ("Not now") for ``SUGGESTION_SNOOZE_DAYS`` days.

    A briefing dismissal under the card id ``outreach_suggestion:<company_id>``, so the
    Overview card hides too. Saying it twice is one dismissal. A company that is not
    stored is a ``NotFound`` and leaves no row behind. Clears the cache, so the list
    refills from the next company down.
    """
    uid, cid = parse_uuid(user_id), parse_uuid(company_id)
    if uid is None:
        raise NotFound("User not found")
    if cid is None:
        raise NotFound("Company not found")
    async with AsyncSessionLocal() as session:
        stored = (
            await session.execute(select(Company.id).where(Company.id == cid))
        ).scalar_one_or_none()
    if stored is None:
        raise NotFound("Company not found")

    card_id = suggestion_card_id(str(cid))
    if card_id not in await agent_memory.active_dismissals(user_id=str(uid)):
        await agent_memory.dismiss_card(
            user_id=str(uid), card_id=card_id, snooze_days=SUGGESTION_SNOOZE_DAYS
        )
    await invalidate(user_id)
    logger.info("outreach suggestion for company %s dismissed", cid)
