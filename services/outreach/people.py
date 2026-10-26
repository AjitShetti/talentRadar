"""
services/outreach/people.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The people in a thread, how to reach each of them, and the full thread detail
the drawer renders.

This module adds a person to a thread, snoozes or drops one, removes one, builds
a person's channels, and assembles ``ThreadDetail``: the thread's summary plus
its people, the ranked people not yet in it, its timeline, the careers link, the
LinkedIn searches and the pacing note. Creating the thread is ``threads.py``;
recording what happened is ``touches.py``.

Rules this module keeps:

* **Ownership first.** Every function takes the caller's ``user_id`` and loads the
  thread through ``load_owned_thread`` before anything else, so another user's
  thread, or a person who is in someone else's thread, is a ``NotFound`` (404).
* **A person in a company thread is a contact first.** Adding a curated or saved
  contact snapshots its name, title and kind; adding someone the user types
  saves a private contact (``user_id`` set, ``kind="referral"``) and links to it.
  The contact must belong to the thread's company and be visible to the caller
  (curated, or their own). A name-only thread has no company to hold contacts, so
  the person keeps what was typed (``email``, ``linkedin_url``) and nothing is
  written to the shared ``company_contacts`` table.
* **Channels say where an address came from.** An email channel is ``saved`` (the
  user's own contact), ``found`` (a curated address that cites the page it was
  published on), or ``guessed``: the company's format, learned from a found address
  on its own domain, applied to a named person who has none. A guess is built here
  at read time, labelled, and never stored (spec 2026-10-26, section 2). LinkedIn is a deep link the user opens in their
  own session: a stored profile (rebuilt from its slug, never echoed), else a
  people search. Nothing here fetches LinkedIn, X, GitHub or an address.
* **No model call.** Ranking and the detail are deterministic reads.

The ``company.stage`` and ``company.sector`` of a detail are display labels
("Series A", "Fintech") ready to show, not the stored keys.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.company_taxonomy import SECTORS, STAGES
from domain.email_patterns import Pattern, email_domain_ok, guess, learn
from domain.linkedin_links import company_people_links, person_search_link
from domain.outreach import (
    MAX_PEOPLE_PER_THREAD,
    aware,
    can_draft,
    effective_state,
    pacing_note,
    snooze,
)
from domain.outreach_rank import PersonSignals, rank_people, size_class
from services.base import parse_uuid
from services.company_contacts import _EMAIL_RE
from services.company_map.read import target_roles
from services.outreach.errors import Conflict, Invalid, NotFound
from services.outreach.oss import snapshot_for
from services.outreach.threads import (
    _id,
    _in_added_order,
    _iso,
    _name_key,
    _one_line,
    load_owned_thread,
    person_clock,
    summarise_many,
)
from storage.database import AsyncSessionLocal
from storage.models import Company, CompanyContact, OutreachPerson, OutreachThread, OutreachTouch

logger = logging.getLogger(__name__)

# The widths of the columns a typed person is stored in.
NAME_MAX = 128
TITLE_MAX = 128
EMAIL_MAX = 256
LINKEDIN_URL_MAX = 512

#: The kind of someone the user types in: a person they know.
TYPED_KIND = "referral"

#: A careers page is a link, never a person.
_NOT_A_PERSON = frozenset({"careers_page"})

#: What a contact with no name is called, by kind.
_NAMELESS = {"careers_inbox": "Careers inbox", "recruiter": "Recruiting team"}

_ALREADY = "Already in this thread"
_PACING_WINDOW = timedelta(days=7)

# Profile links are accepted only in these shapes and rebuilt from the captured
# name, so a stored string such as ``javascript:`` or a lookalike host can never
# reach an ``href``. The optional trailing path, query and fragment are dropped.
_TAIL = r"(?:/[^?#]*)?(?:[?#].*)?"
_LINKEDIN_PROFILE = re.compile(
    rf"(?:https?://)?(?:[a-z]{{2,3}}\.)?linkedin\.com/in/(?P<name>[A-Za-z0-9\-_.%]+){_TAIL}",
    re.IGNORECASE,
)
_X_PROFILE = re.compile(
    rf"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/(?P<name>[A-Za-z0-9_]{{1,15}}){_TAIL}",
    re.IGNORECASE,
)
_GITHUB_PROFILE = re.compile(
    rf"(?:https?://)?(?:www\.)?github\.com/(?P<name>[A-Za-z0-9][A-Za-z0-9\-]{{0,38}}){_TAIL}",
    re.IGNORECASE,
)


# --- links -------------------------------------------------------------------------


def _web_url(value: str | None) -> str | None:
    """``value`` when it is an http(s) URL with a host, else ``None``."""
    text = (value or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    return text if parts.scheme in {"http", "https"} and parts.hostname else None


def _source_host(url: str | None) -> str | None:
    """The host a source page is on, for "from <host>"; ``None`` for no usable URL."""
    text = _web_url(url)
    return None if text is None else (urlsplit(text).hostname or "").lower() or None


def _profile(pattern: re.Pattern[str], origin: str, value: str | None) -> str | None:
    match = pattern.fullmatch((value or "").strip())
    return None if match is None else f"{origin}{match.group('name')}"


def _linkedin_profile(value: str | None) -> str | None:
    return _profile(_LINKEDIN_PROFILE, "https://www.linkedin.com/in/", value)


def _valid_email(value: str | None) -> str | None:
    text = (value or "").strip()
    return text if _EMAIL_RE.fullmatch(text) else None


# --- channels ----------------------------------------------------------------------


AddressFormat = tuple[Pattern, str]


def address_format_from(
    contacts: list[CompanyContact], company_domain: str | None
) -> AddressFormat | None:
    """``(format, the found address that taught it)`` for a company, or ``None``.

    Learned only from curated named people whose address was published
    (``email_source_url``) on the company's own domain. A user's saved contact is
    private and teaches nothing; an inbox has no name and teaches nothing.
    """
    examples = [
        (contact.name or "", (contact.email or "").strip().lower())
        for contact in contacts
        if contact.user_id is None
        and contact.name
        and contact.email
        and contact.email_source_url
        and email_domain_ok(contact.email, company_domain)
    ]
    pattern = learn(examples)
    if pattern is None:
        return None
    teacher = next(
        address
        for name, address in examples
        if guess(pattern, name, address.rpartition("@")[2]) == address
    )
    return pattern, teacher


def _build_channels(
    *,
    name: str,
    company_name: str,
    contact: CompanyContact | None,
    typed_email: str | None,
    typed_linkedin: str | None,
    address_format: AddressFormat | None = None,
    company_domain: str | None = None,
) -> list[dict[str, Any]]:
    """The ways to reach someone: LinkedIn, then email, X and GitHub, where stored.

    ``contact`` is the truth when there is one. Without it the person is something
    the user typed in a name-only thread, so what they typed is theirs to use. With
    an ``address_format`` and no address on record, a named contact gets a
    ``guessed`` email channel on ``company_domain``.
    """
    if contact is None:
        email, linkedin, x_url, github_url = typed_email, typed_linkedin, None, None
        user_saved, source_url, has_name = True, None, True
    else:
        email, linkedin = contact.email, contact.linkedin_url
        x_url, github_url = contact.x_url, contact.github_url
        user_saved, source_url, has_name = (
            contact.user_id is not None,
            contact.source_url,
            bool(contact.name),
        )

    channels: list[dict[str, Any]] = []

    profile = _linkedin_profile(linkedin)
    if profile is not None:
        channels.append(_channel("linkedin", profile, "LinkedIn", direct=True))
    elif has_name:
        # A shared inbox has no one to look up; a named person does.
        channels.append(
            _channel(
                "linkedin", person_search_link(name, company_name), "Search LinkedIn", direct=False
            )
        )

    address = _valid_email(email)
    email_source = None if contact is None else contact.email_source_url
    host = _source_host(email_source or source_url)
    if address is not None and (user_saved or host is not None):
        channels.append(
            _channel(
                "email",
                f"mailto:{address}",
                address,
                direct=True,
                source_host=None if user_saved else host,
                basis="saved" if user_saved else "found",
            )
        )
    elif contact is not None and contact.name and address_format and company_domain:
        pattern, learned_from = address_format
        guessed = guess(pattern, contact.name, learned_from.rpartition("@")[2])
        if guessed is not None and email_domain_ok(guessed, company_domain):
            channels.append(
                {
                    **_channel(
                        "email", f"mailto:{guessed}", guessed, direct=False, basis="guessed"
                    ),
                    "learned_from": learned_from,
                }
            )

    x_profile = _profile(_X_PROFILE, "https://x.com/", x_url)
    if x_profile is not None:
        channels.append(_channel("x", x_profile, "X", direct=True))
    github = _profile(_GITHUB_PROFILE, "https://github.com/", github_url)
    if github is not None:
        channels.append(_channel("github", github, "GitHub", direct=True))
    return channels


def _channel(
    channel: str,
    url: str,
    label: str,
    *,
    direct: bool,
    source_host: str | None = None,
    basis: str | None = None,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "url": url,
        "label": label,
        "direct": direct,
        "source_host": source_host,
        "basis": basis,
    }


def channels_for(
    *,
    contact: CompanyContact | None,
    person: OutreachPerson,
    company_name: str,
    address_format: AddressFormat | None = None,
    company_domain: str | None = None,
) -> list[dict[str, Any]]:
    """The channels a person in a thread has, built from stored data only.

    * **LinkedIn**: a stored profile link is ``"LinkedIn"`` and ``direct``; with none,
      a people search for the name at the company is ``"Search LinkedIn"`` and not
      ``direct``. A contact with no name (a shared inbox) gets no search.
    * **Email**: an address the user saved (``basis="saved"``), a curated contact's
      address that cites the page it was published on (``"found"``; ``source_host``
      is that page's host), or, with an ``address_format``, the company's format
      applied to the contact's name (``"guessed"``, with ``learned_from``; never
      ``direct`` and never stored).
    * **X** and **GitHub**: the contact's stored profile links.

    ``contact`` is ``None`` for someone typed into a name-only thread, whose own
    ``email`` and ``linkedin_url`` are then used.
    """
    return _build_channels(
        name=person.name,
        company_name=company_name,
        contact=contact,
        typed_email=person.email,
        typed_linkedin=person.linkedin_url,
        address_format=address_format,
        company_domain=company_domain,
    )


# --- the pacing note ---------------------------------------------------------------


def first_messages_last_7_days(thread: OutreachThread, now: datetime) -> int:
    """How many people the user sent a first message to in this thread in the last
    seven days.

    The pacing note says "N people", so this counts distinct people, not touches: two
    first messages to one person (a fresh start after they went cold) are one.

    A first touch with no person cannot be told apart from another: it is either
    thread-level (a GitHub comment) or belongs to someone since removed, whose link the
    database cleared (``ON DELETE SET NULL``). Each such touch counts as one, so the
    count is the distinct non-null ``person_id``s plus one per person-less first touch.
    That errs toward showing the note, and removing a person never hides it.

    The thread must have its touches loaded. A follow-up, a reply or an unrecorded
    ``other`` is not a first message. The window is rolling and its start is
    included. A user has one thread per company, so this is the count for the company.
    """
    moment = aware(now)
    since = moment - _PACING_WINDOW
    firsts = [
        touch
        for touch in thread.touches
        if touch.kind == "first"
        and touch.direction == "out"
        and since <= aware(touch.happened_at) <= moment
    ]
    people = {touch.person_id for touch in firsts if touch.person_id is not None}
    return len(people) + sum(1 for touch in firsts if touch.person_id is None)


# --- what the user types -----------------------------------------------------------


@dataclass(frozen=True)
class _Typed:
    """The fields a person was typed in with, cleaned. Empty ones are ``None``."""

    name: str | None
    title: str | None
    email: str | None
    linkedin_url: str | None

    @property
    def present(self) -> bool:
        return any(v is not None for v in (self.name, self.title, self.email, self.linkedin_url))


def _typed_fields(
    name: str | None, title: str | None, email: str | None, linkedin_url: str | None
) -> _Typed:
    """Clean and check what the user typed. Too long, or not an address or a
    profile link, is ``Invalid``."""
    clean_email = _one_line(email, EMAIL_MAX, "email")
    if clean_email is not None:
        clean_email = clean_email.lower()
        if _valid_email(clean_email) is None:
            raise Invalid("That does not look like an email address.")
    link = _one_line(linkedin_url, LINKEDIN_URL_MAX, "LinkedIn link")
    if link is not None:
        link = _linkedin_profile(link)
        if link is None:
            raise Invalid("Use a LinkedIn profile link, like linkedin.com/in/their-name.")
    return _Typed(
        name=_one_line(name, NAME_MAX, "name"),
        title=_one_line(title, TITLE_MAX, "title"),
        email=clean_email,
        linkedin_url=link,
    )


def _contact_name(contact: CompanyContact) -> str:
    return contact.name or _NAMELESS.get(contact.kind, "Contact")


# --- loading -----------------------------------------------------------------------


async def _visible_contacts(
    session: AsyncSession, company_id: uuid.UUID, user_id: uuid.UUID
) -> list[CompanyContact]:
    """The company's curated contacts and the caller's own, never anyone else's."""
    rows = await session.execute(
        select(CompanyContact)
        .where(
            CompanyContact.company_id == company_id,
            or_(CompanyContact.user_id.is_(None), CompanyContact.user_id == user_id),
        )
        .order_by(CompanyContact.created_at, CompanyContact.id)
    )
    return list(rows.scalars())


async def _contact_to_add(
    session: AsyncSession, thread: OutreachThread, contact_id: str
) -> CompanyContact:
    """The contact to add: at this thread's company, and curated or the caller's own."""
    cid = parse_uuid(contact_id)
    contact = None
    if cid is not None and thread.company_id is not None:
        contact = (
            await session.execute(
                select(CompanyContact).where(
                    CompanyContact.id == cid,
                    CompanyContact.company_id == thread.company_id,
                    or_(CompanyContact.user_id.is_(None), CompanyContact.user_id == thread.user_id),
                )
            )
        ).scalar_one_or_none()
    if contact is None:
        raise NotFound("Person not found")
    return contact


async def _typed_contact(
    session: AsyncSession, thread: OutreachThread, company_id: uuid.UUID, typed: _Typed
) -> CompanyContact:
    """The contact behind someone the user typed into a company thread.

    A contact the caller can already see at this company with the same name (case
    and spacing ignored; a careers page is never a person) is reused, so removing
    a person and typing them again, or typing the name of a curated founder, never
    leaves two rows for one person. The caller's own contact is preferred over a
    curated one. A curated row is shared and is never written to; the caller's own
    gains the email or LinkedIn link it did not have, and keeps the ones it has.
    Otherwise a new private ``referral`` contact is created and flushed, so the
    person's foreign key can point at it.

    This builds the row itself rather than calling ``company_contacts.save_contact``,
    which opens its own session and commits, so the add stays one atomic write.
    """
    key = _name_key(typed.name or "")
    matches = [
        c
        for c in await _visible_contacts(session, company_id, thread.user_id)
        if c.name and c.kind not in _NOT_A_PERSON and _name_key(c.name) == key
    ]
    if matches:
        contact = min(matches, key=lambda c: c.user_id is None)
        if any(p.contact_id == contact.id for p in thread.people):
            raise Conflict(_ALREADY)
        if contact.user_id is not None:
            contact.email = contact.email or typed.email
            contact.linkedin_url = contact.linkedin_url or typed.linkedin_url
        return contact
    contact = CompanyContact(
        id=uuid.uuid4(),
        company_id=company_id,
        user_id=thread.user_id,
        kind=TYPED_KIND,
        name=typed.name,
        title=typed.title,
        email=typed.email,
        linkedin_url=typed.linkedin_url,
        source_kind="user",
        verified=False,
    )
    session.add(contact)
    await session.flush()  # the person's foreign key needs the row to exist
    return contact


def _find_person(thread: OutreachThread, person_id: str) -> OutreachPerson:
    pid = parse_uuid(person_id)
    person = None if pid is None else next((p for p in thread.people if p.id == pid), None)
    if person is None:
        raise NotFound("Person not found")
    return person


# --- add, update, remove -----------------------------------------------------------


async def add_person(
    user_id: str,
    thread_id: str,
    *,
    contact_id: str | None,
    name: str | None = None,
    title: str | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
) -> dict[str, Any]:
    """Add someone to the caller's thread. Returns the ``ThreadDetail``.

    Either ``contact_id`` (a curated contact, or one the caller saved, at the
    thread's company) or the fields of someone the caller knows, never both:

    * a contact is snapshotted (``name``, ``title``, ``kind``); adding it twice is a
      ``Conflict``, as is typing a name already in the thread;
    * a typed person in a company thread is linked to the contact the caller can
      already see at that company under the same name, else saved as a new private
      ``referral`` contact (see ``_typed_contact``, which inlines
      ``save_contact``'s construction to keep the add atomic); in a name-only
      thread the person keeps the typed ``email`` and LinkedIn link and no
      contact is written.

    All free text is cleaned and bounded. A contact id on a name-only thread, a
    ``careers_page`` contact, a bad address or a link that is not a LinkedIn
    profile is ``Invalid``; a thread at ``MAX_PEOPLE_PER_THREAD`` is a ``Conflict``.
    """
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        thread = await load_owned_thread(session, user_id, thread_id)
        typed = _typed_fields(name, title, email, linkedin_url)
        thread_uuid = thread.id

        contact: CompanyContact | None = None
        if contact_id is not None:
            if typed.present:
                raise Invalid("Pick a saved person or type one in, not both.")
            if thread.company_id is None:
                raise Invalid("There are no people on record for a company we do not list.")
            contact = await _contact_to_add(session, thread, contact_id)
            if contact.kind in _NOT_A_PERSON:
                raise Invalid("A careers page is a link, not a person.")
            if any(p.contact_id == contact.id for p in thread.people):
                raise Conflict(_ALREADY)
            fields: dict[str, Any] = {
                "name": _contact_name(contact),
                "title": contact.title,
                "kind": contact.kind,
            }
        else:
            if typed.name is None:
                raise Invalid("Give their name.")
            if any(_name_key(p.name) == _name_key(typed.name) for p in thread.people):
                raise Conflict(_ALREADY)
            fields = {"name": typed.name, "title": typed.title, "kind": TYPED_KIND}

        if len(thread.people) >= MAX_PEOPLE_PER_THREAD:
            raise Conflict(f"A thread holds at most {MAX_PEOPLE_PER_THREAD} people")

        if contact_id is None and thread.company_id is not None:
            contact = await _typed_contact(session, thread, thread.company_id, typed)
            fields = {
                "name": _contact_name(contact),
                "title": contact.title or typed.title,
                "kind": contact.kind,
            }
        elif contact_id is None:
            fields.update(email=typed.email, linkedin_url=typed.linkedin_url)

        person = OutreachPerson(
            id=uuid.uuid4(),
            thread_id=thread_uuid,
            contact_id=None if contact is None else contact.id,
            state="planned",
            follow_ups_sent=0,
            created_at=now,
            **fields,
        )
        session.add(person)
        person_uuid = person.id
        try:
            await session.commit()
        except IntegrityError as exc:
            # Two requests added the same contact and the unique index let one in.
            await session.rollback()
            raise Conflict(_ALREADY) from exc
        logger.info("outreach person %s added to thread %s", person_uuid, thread_uuid)
    return await get_thread(user_id, thread_id, now=now)


async def update_person(
    user_id: str,
    thread_id: str,
    person_id: str,
    *,
    snooze_days: int | None = None,
    state: Literal["dropped"] | None = None,
) -> dict[str, Any]:
    """Snooze a person's follow-up or drop them from the plan. Returns the ``ThreadDetail``.

    Exactly one of the two. A snooze moves ``next_follow_up_at`` to ``snooze_days``
    from now (1 to ``SNOOZE_MAX_DAYS``) and is refused for anyone who is not
    waiting on a follow-up still to send. Dropping sets the state and clears the
    follow-up date; dropping someone already dropped changes nothing.
    """
    if (snooze_days is None) == (state is None):
        raise Invalid("Snooze the follow-up or drop the person, one at a time.")
    if state is not None and state != "dropped":
        raise Invalid(f"Unknown state: {state!r}")
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        thread = await load_owned_thread(session, user_id, thread_id)
        person = _find_person(thread, person_id)
        if state is not None:
            person.state = "dropped"
            person.next_follow_up_at = None
        elif snooze_days is not None:
            try:
                moved = snooze(person_clock(person), days=snooze_days, now=now)
            except ValueError as exc:
                raise Invalid(str(exc)) from exc
            person.next_follow_up_at = moved.next_follow_up_at
        person_uuid = person.id
        await session.commit()
        logger.info("outreach person %s updated", person_uuid)
    return await get_thread(user_id, thread_id, now=now)


async def remove_person(
    user_id: str, thread_id: str, person_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Take a person out of the caller's thread. Returns the ``ThreadDetail``.

    Their touches stay on the timeline: the database detaches them
    (``ON DELETE SET NULL``), so they read as thread-level entries afterwards.
    ``now`` is what the detail's pacing note counts "this week" from; for tests.
    """
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        thread = await load_owned_thread(session, user_id, thread_id)
        person = _find_person(thread, person_id)
        person_uuid = person.id
        await session.delete(person)
        await session.commit()
        logger.info("outreach person %s removed", person_uuid)
    return await get_thread(user_id, thread_id, now=moment)


# --- the detail --------------------------------------------------------------------


def _label(table: dict[str, str], key: str | None) -> str | None:
    return None if key is None else table.get(key, key)


def _company_block(company: Company) -> dict[str, Any]:
    return {
        "stage": _label(STAGES, company.stage),
        "sector": _label(SECTORS, company.sector),
        "area": company.area,
        "last_funding_at": _iso(company.last_funding_at),
        "linkedin_url": _web_url(company.linkedin_url),
        "github_org": (company.github_org or "").strip() or None,
    }


def _careers_url(contacts: list[CompanyContact]) -> str | None:
    """The careers page a curated contact cites, else one the user saved."""
    pages = sorted(
        (c for c in contacts if c.kind == "careers_page"), key=lambda c: c.user_id is not None
    )
    return next((url for url in (_web_url(c.source_url) for c in pages) if url), None)


def _person_payload(
    person: OutreachPerson,
    contact: CompanyContact | None,
    *,
    company_name: str,
    typed_here: bool,
    now: datetime,
    address_format: AddressFormat | None = None,
    company_domain: str | None = None,
) -> dict[str, Any]:
    """One ``Person``. ``typed_here`` is true in a name-only thread, where everyone
    was typed in by the user."""
    clock = person_clock(person)
    source_url = None if contact is None else _web_url(contact.source_url)
    return {
        "id": str(person.id),
        "contact_id": _id(person.contact_id),
        "name": person.name,
        "title": person.title,
        "kind": person.kind,
        "state": effective_state(clock, now),
        "verified": contact is not None and contact.verified,
        "user_saved": typed_here if contact is None else contact.user_id is not None,
        "source_url": source_url,
        "source_host": _source_host(source_url),
        "channels": channels_for(
            contact=contact,
            person=person,
            company_name=company_name,
            address_format=address_format,
            company_domain=company_domain,
        ),
        "first_sent_at": _iso(person.first_sent_at),
        "last_outbound_at": _iso(person.last_outbound_at),
        "follow_ups_sent": person.follow_ups_sent,
        "next_follow_up_at": _iso(person.next_follow_up_at),
        "replied_at": _iso(person.replied_at),
        "draft": can_draft(clock, now),
    }


def _suggested_people(
    contacts: list[CompanyContact], thread: OutreachThread, company: Company
) -> list[dict[str, Any]]:
    """The company's contacts that are not in the thread, best first."""
    members = {p.contact_id for p in thread.people if p.contact_id is not None}
    signals: list[PersonSignals] = []
    for contact in contacts:
        if contact.id in members:
            continue
        channels = _build_channels(
            name=_contact_name(contact),
            company_name=company.name,
            contact=contact,
            typed_email=None,
            typed_linkedin=None,
        )
        signals.append(
            PersonSignals(
                contact_id=str(contact.id),
                name=_contact_name(contact),
                title=contact.title,
                kind=contact.kind,
                verified=contact.verified,
                user_saved=contact.user_id is not None,
                has_direct_link=any(c["direct"] and c["channel"] != "email" for c in channels),
                has_public_email=any(
                    c["channel"] == "email" and c["basis"] in ("found", "saved") for c in channels
                ),
                source_host=_source_host(contact.source_url),
            )
        )
    size = size_class(
        company.tier, company.employee_count, company.employee_count_range, company.stage
    )
    return [
        {
            "contact_id": ranked.contact_id,
            "name": ranked.name,
            "title": ranked.title,
            "kind": ranked.kind,
            "verified": ranked.verified,
            "user_saved": ranked.user_saved,
            "source_host": ranked.source_host,
            "reason": ranked.reason,
            "score": ranked.score,
        }
        for ranked in rank_people(signals, size)
    ]


def _touch_payload(touch: OutreachTouch, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": str(touch.id),
        "person_id": _id(touch.person_id),
        "person_name": None if touch.person_id is None else names.get(touch.person_id),
        "channel": touch.channel,
        "direction": touch.direction,
        "kind": touch.kind,
        "note": touch.note,
        "body": touch.body,
        "happened_at": _iso(touch.happened_at),
    }


async def get_thread(
    user_id: str, thread_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """The caller's thread in full, as a ``ThreadDetail``.

    The thread's ``ThreadSummary`` plus its note, next step and close time; the
    company block (stage and sector as display labels); its people with their
    channels and the one message the app may help write next; the company's other
    people, ranked for its size; the timeline, newest first; the open-source
    snapshot; the careers page; the LinkedIn searches and the pacing note.

    A name-only thread has no company, so no company block, suggestions, careers
    page, LinkedIn searches or issues. Only a read: no model, no outside call.
    """
    moment = datetime.now(UTC) if now is None else aware(now)
    async with AsyncSessionLocal() as session:
        thread = await load_owned_thread(session, user_id, thread_id)
        summary = (await summarise_many(session, [thread], moment))[0]

        company = (
            None if thread.company_id is None else await session.get(Company, thread.company_id)
        )
        contacts: list[CompanyContact] = []
        roles: list[str] = []
        company_id = None if company is None else company.id
        if company is not None:
            contacts = await _visible_contacts(session, company.id, thread.user_id)
            roles = await target_roles(session, thread.user_id)

    # The snapshot opens its own session; ask once this one is closed, so two pooled
    # connections are never held at the same time.
    issues: dict[str, Any] | None = None if company_id is None else await snapshot_for(company_id)

    by_id = {contact.id: contact for contact in contacts}
    company_domain = None if company is None else company.domain
    address_format = address_format_from(contacts, company_domain)
    people = _in_added_order(thread.people)
    names = {person.id: person.name for person in people}
    touches = sorted(
        thread.touches,
        key=lambda t: (aware(t.happened_at), aware(t.created_at), str(t.id)),
        reverse=True,
    )

    return {
        **summary,
        "note": thread.note,
        "next_step": thread.next_step,
        "next_step_at": None if thread.next_step_at is None else thread.next_step_at.isoformat(),
        "closed_at": _iso(thread.closed_at),
        "company": None if company is None else _company_block(company),
        "people": [
            _person_payload(
                person,
                None if person.contact_id is None else by_id.get(person.contact_id),
                company_name=thread.company_name,
                typed_here=thread.company_id is None,
                now=moment,
                address_format=address_format,
                company_domain=company_domain,
            )
            for person in people
        ],
        "suggested_people": (
            [] if company is None else _suggested_people(contacts, thread, company)
        ),
        "touches": [_touch_payload(touch, names) for touch in touches],
        "issues": issues,
        "careers_url": _careers_url(contacts),
        "linkedin_links": (
            []
            if company is None
            else company_people_links(
                company.name, company.linkedin_url, roles[0] if roles else None
            )
        ),
        "pacing_note": pacing_note(thread.company_name, first_messages_last_7_days(thread, moment)),
    }
