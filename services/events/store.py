"""
services/events/store.py
~~~~~~~~~~~~~~~~~~~~~~~~
Where what a source states about an event meets the ``events`` table. This is the
only writer of source events (a user's own manual event is written by
``services/events/user.py``).

:func:`upsert_event` takes one :class:`EventFacts` and, in order:

1. refuses what cannot be stored: no usable identity (``invalid``), a start outside
   2000-2100 (``bad_time``), a start more than 90 days away (``horizon``), an event
   that is over (``ended``);
2. asks :func:`domain.event_relevance.assess` whether it is Bengaluru's (``location``).
   Where an event is does not depend on who hosts it, so this comes before the
   company lookups, which are not spent on an event that is not stored;
3. resolves the companies the event names (:func:`resolve_companies`) and, if there is
   one, assesses again: a hiring event is listed only when its host is a listed company;
4. inserts or updates the row on ``(source, external_id)``;
5. on insert, finds the same event already stored from another source
   (:func:`find_duplicate`) and points ``duplicate_of`` at it;
6. replaces the event's ``event_companies`` rows.

Rules that hold throughout:

* **A source's quirks are fixed in one place**, :func:`refine_facts`, before the
  verdict: a title that says "Online" with no position, and "the exact location will
  be shared once the invite is accepted".
* **Nothing a source stopped saying is erased.** A field the facts leave empty keeps
  its stored value; ``is_cancelled`` is the exception, because it is a fact the
  source restates every time. An event missing from a feed is never cancelled: the
  store only ever sees sightings, so it has nothing to cancel an absence with.
* **An event never creates a company.** It links only to a *listed* tech company
  (``companies.is_tech IS TRUE`` and the map's "says what it does" text), found
  through :func:`services.company_map.store.find_existing`. The organiser's website
  is read as a string and never fetched.
* **The earliest-stored row is the primary.** Only a primary from another source,
  listed and not private, can take a later row. A row somebody saved or noted people
  at is never hidden behind another.

Callers own the transaction: nothing here commits, and a database error is *not*
caught. The refresh steps call :func:`upsert_event` inside a wrapper that never
raises, and that wrapper rolls the session back; swallowing the error here would
leave a half-written event in the transaction. The one exception is a lost insert
race: the insert runs in a savepoint, and when another run stored the same
``(source, external_id)`` first, the savepoint is rolled back and the sighting becomes
an update of the winner.

Every text field is cleaned before anything reads it (NUL removed, cut to its column,
hosts capped at :data:`MAX_HOSTS`, an event link that is not ``https://`` emptied), so
the verdict, the company lookups and the row all see the same text, and the reasons
``assess`` builds from it are bounded too.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.areas import centroid, jitter
from domain.company_identity import normalize_domain
from domain.event_facts import EventFacts, SourceScope
from domain.event_identity import START_WINDOW, CompanyCandidate, company_candidates, same_event
from domain.event_relevance import Verdict, assess
from domain.event_time import as_utc
from services.company_map.read import listed_clause
from services.company_map.store import find_existing
from storage.models import Company, Event, EventCompany, EventContact, EventSave

#: An event further ahead than this is not stored yet; a later run finds it again.
HORIZON_DAYS = 90
#: Two listings are candidates for one event only if they start this close.
DEDUPE_WINDOW = START_WINDOW

#: A start or end outside these years is a parser's mistake (an epoch, a sentinel).
MIN_YEAR = 2000
MAX_YEAR = 2100

# Column widths of ``events``; ``tests/test_event_store.py`` pins them to the model.
_SOURCE_MAX = 16
_EXTERNAL_ID_MAX = 160
_URL_MAX = 1024
_TITLE_MAX = 300
_SNIPPET_MAX = 600
_VENUE_MAX = 200
_ADDRESS_MAX = 300
_AREA_MAX = 48
_ORGANISER_MAX = 200
_ORGANISER_URL_MAX = 512
_HOST_MAX = 200
_SCOPE_KEY_MAX = 120
_TAXONOMY_MAX = 32
_TIMEZONE_MAX = 64
_LOCALITY_MAX = 120
#: A reason is a short phrase; ``assess`` builds some from the facts, so it is cut too.
_REASON_MAX = 200

#: An event is hosted by a handful of companies. Past this many, a feed is padding.
MAX_HOSTS = 10

_NUL = chr(0)
_HTTPS = "https://"
#: ``Event.__table__``'s unique constraint on ``(source, external_id)``; a test pins it.
_KEY_CONSTRAINT = "uq_events_source_external_id"
_SQLITE_KEY = "UNIQUE constraint failed: events.source, events.external_id"
_STATED = "stated"  # a pin the source gave
_PLACED = "placed"  # a pin we put at the centre of the address's area

#: Refining reads at most this much of any one field, however much a parser handed over.
_SCAN_LIMIT = 3_000

#: Pages of the event platforms. An organiser whose only "website" is its profile on
#: one is not a company's domain.
_PLATFORM_DOMAINS = frozenset({"lu.ma", "luma.com", "community.dev"})


@dataclass(frozen=True)
class UpsertResult:
    """What one sighting did.

    ``event`` is the stored row, or ``None`` when ``skipped`` says why not: ``invalid``,
    ``bad_time``, ``horizon``, ``ended`` or ``location``. ``created`` is true only for
    an insert; ``duplicate_of`` is the primary this row sits behind, if any.
    """

    event: Event | None
    created: bool
    duplicate_of: uuid.UUID | None
    skipped: str | None


def _skipped(reason: str) -> UpsertResult:
    return UpsertResult(event=None, created=False, duplicate_of=None, skipped=reason)


# ── refine_facts ─────────────────────────────────────────────────────────────

# "online" as a word, so "Onlinemart" and "online-first" are not a format.
_ONLINE_WORD = re.compile(r"(?<!\w)online(?!\w)", re.IGNORECASE)
# A title that also says it is physical is a hybrid or in-person event: not online.
_IN_PERSON_WORDS = re.compile(
    r"(?<!\w)(?:offline|in[\s-]?person|hybrid|on[\s-]?site)(?!\w)", re.IGNORECASE
)
# "The exact location will be shared once the invite is accepted" (Luma), within one
# sentence: [^.] stops a match from running across a full stop into unrelated text.
_EXACT_LOCATION = re.compile(
    r"\bexact\s+(?:location|address|venue)\b[^.]{0,80}?"
    r"\b(?:shared|sent|revealed|disclosed|provided|announced|released|emailed)\b[^.]{0,60}?"
    r"\b(?:once|after|when|upon)\b[^.]{0,60}?"
    r"\b(?:invite|invitation|registration|rsvp|approval|approved|accepted|confirmed|confirmation)\b",
    re.IGNORECASE,
)


def _plausible(moment: object) -> bool:
    return isinstance(moment, datetime) and MIN_YEAR <= moment.year <= MAX_YEAR


def refine_facts(facts: EventFacts) -> EventFacts:
    """``facts`` with the quirks of the sources put right. Pure, and it never raises.

    * A title that says "online" as a word, for an event with no position (no latitude
      or longitude) that the source did not call online, is online. A Luma iCal event
      has no field for it: three "Swift Developers Meetup - Online" events in the
      recorded feed have no position and no meeting link. A title that also says
      offline, in person, hybrid or on-site is left alone: a false "online" hides a real
      event from the in-person list, a missed one only drops an online event.
    * Text that says the exact location will be shared once or after the invite is
      accepted marks ``address_withheld``. ``venue_name`` is left as it is: it is the
      area hint ("Indiranagar"), not a venue.
    * An end outside 2000-2100 is dropped, so no later time arithmetic can overflow.
      The start is *not* touched here: :func:`upsert_event` refuses a start outside
      those years before it does any arithmetic.

    Facts come from parsers this module does not control, so each field is read
    defensively and at most :data:`_SCAN_LIMIT` characters of it are scanned. The
    same object is returned when nothing needs changing.
    """
    changes: dict[str, Any] = {}
    if facts.ends_at is not None and not _plausible(facts.ends_at):
        changes["ends_at"] = None

    title = str(facts.title or "")[:_SCAN_LIMIT]
    if (
        not facts.is_online
        and facts.lat is None
        and facts.lng is None
        and _ONLINE_WORD.search(title)
        and not _IN_PERSON_WORDS.search(title)
    ):
        changes["is_online"] = True

    if not facts.address_withheld:
        text = " ".join(
            str(part)[:_SCAN_LIMIT]
            for part in (facts.title, facts.snippet, facts.address, facts.venue_name)
            if part
        )
        if _EXACT_LOCATION.search(text):
            changes["address_withheld"] = True

    return replace(facts, **changes) if changes else facts


# ── companies ────────────────────────────────────────────────────────────────


def _organiser_domain(organiser_url: str | None) -> str | None:
    """The registrable domain of the organiser's own website, else ``None``.

    The URL is parsed, never requested (it may be any host a source printed). The event
    platform's own pages are not a website: Eventbrite falls back to the organiser's
    profile on eventbrite.com, and that must not link every such event to Eventbrite.
    """
    domain = normalize_domain(organiser_url)
    if domain is None or domain in _PLATFORM_DOMAINS or domain.split(".")[0] == "eventbrite":
        return None
    return domain


async def _resolve(
    session: AsyncSession,
    organiser_name: str | None,
    organiser_url: str | None,
    hosts: Iterable[str],
    title: str,
) -> list[tuple[Company, CompanyCandidate]]:
    # Whoever calls this (a refresh step, the link step), the names are cleaned and the
    # hosts capped here: each candidate costs a lookup, and a NUL fails the whole query.
    candidates = company_candidates(
        _text(organiser_name, _ORGANISER_MAX), _hosts(hosts), _clean(title, _TITLE_MAX)
    )
    if not candidates:
        return []
    organiser_domain = _organiser_domain(organiser_url)

    found: list[tuple[Company, CompanyCandidate]] = []
    for candidate in candidates:
        domain = organiser_domain if candidate.matched_on == "organiser_name" else None
        company = await find_existing(session, domain=domain, wikidata_id=None, name=candidate.name)
        if company is None:
            continue
        if domain is not None and company.domain == domain:
            candidate = replace(candidate, matched_on="organiser_domain")
        found.append((company, candidate))
    if not found:
        return []

    # find_existing returns any company; only a listed tech one is linked.
    listed = set(
        (
            await session.execute(
                select(Company.id).where(
                    Company.id.in_([company.id for company, _ in found]), listed_clause(None)
                )
            )
        ).scalars()
    )
    resolved: list[tuple[Company, CompanyCandidate]] = []
    seen: set[uuid.UUID] = set()
    for company, candidate in found:  # strongest evidence first, so the first link wins
        if company.id in listed and company.id not in seen:
            seen.add(company.id)
            resolved.append((company, candidate))
    return resolved


async def resolve_companies(
    session: AsyncSession, facts: EventFacts
) -> list[tuple[Company, CompanyCandidate]]:
    """The listed tech companies ``facts`` names, each with how it was matched.

    Candidates come from the organiser, the "Hosted by" names and the title
    (:func:`domain.event_identity.company_candidates`). The organiser is matched on its
    website's domain first, then on its name; everything else on its name. A name that
    two companies share matches neither. Nothing is created.
    """
    return await _resolve(
        session, facts.organiser_name, facts.organiser_url, facts.hosts, facts.title
    )


async def _replace_links(
    session: AsyncSession, event_id: uuid.UUID, resolved: list[tuple[Company, CompanyCandidate]]
) -> int:
    # Core statements, not ORM objects: a link added earlier in this session would still
    # be in the identity map after its row is deleted, and a new one with the same key
    # would conflict with it at flush.
    await session.execute(delete(EventCompany).where(EventCompany.event_id == event_id))
    if resolved:
        await session.execute(
            insert(EventCompany),
            [
                {
                    "event_id": event_id,
                    "company_id": company.id,
                    "role": candidate.role,
                    "matched_on": candidate.matched_on,
                }
                for company, candidate in resolved
            ],
        )
    return len(resolved)


async def link_companies(session: AsyncSession, event: Event) -> int:
    """Re-link ``event`` from its stored row; the number of companies linked.

    The ``link`` step runs this over upcoming events, because the map grows daily and a
    company that was not listed when the event was stored may be now (or the other way
    round). It reads the organiser, hosts and title *as stored*.
    """
    resolved = await _resolve(
        session, event.organiser_name, event.organiser_url, event.hosts or (), event.title
    )
    return await _replace_links(session, event.id, resolved)


# ── dedupe ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Probe:
    """An event that is not stored (yet), as :func:`same_event` reads it."""

    title: str
    starts_at: datetime
    lat: float | None
    lng: float | None
    area: str | None
    is_online: bool


def _stored_before(row: Event, other: Event) -> bool:
    """``row`` was stored before ``other``; ties fall to the id, so the order is total."""
    return (as_utc(row.first_seen_at), row.id) < (as_utc(other.first_seen_at), other.id)


async def find_duplicate(
    session: AsyncSession, facts: EventFacts, area: str | None, *, stored: Event | None = None
) -> Event | None:
    """The primary this event is a second listing of, or ``None``.

    A primary has ``duplicate_of IS NULL`` and comes from another source, starts within
    :data:`DEDUPE_WINDOW`, and is the same event by :func:`domain.event_identity.same_event`.
    The window is an indexed range on ``starts_at``; the comparison is pure. Two kinds of
    row never qualify, because hiding an event behind them would make it vanish: one
    that is not listed, and a user's private manual event.

    ``area`` is the verdict's area for ``facts``. With ``stored`` (the row being checked
    again) only a row stored *before* it qualifies, so the earliest-stored row wins and
    two rows can never each take the other.
    """
    start = as_utc(facts.starts_at)
    probe = _Probe(
        title=facts.title,
        starts_at=start,
        lat=facts.lat,
        lng=facts.lng,
        area=area,
        is_online=facts.is_online,
    )
    candidates = await session.execute(
        select(Event)
        .where(
            Event.duplicate_of.is_(None),
            Event.owner_user_id.is_(None),
            Event.listed.is_(True),
            Event.source != facts.source,
            Event.starts_at >= start - DEDUPE_WINDOW,
            Event.starts_at <= start + DEDUPE_WINDOW,
        )
        .order_by(Event.first_seen_at, Event.id)
    )
    for row in candidates.scalars():
        if stored is not None and not _stored_before(row, stored):
            continue
        if same_event(probe, row):
            return row
    return None


async def _has_user_data(session: AsyncSession, event_id: uuid.UUID) -> bool:
    """Somebody saved this event or noted people at it."""
    saved = await session.execute(
        select(EventSave.id).where(EventSave.event_id == event_id).limit(1)
    )
    if saved.first() is not None:
        return True
    noted = await session.execute(
        select(EventContact.id).where(EventContact.event_id == event_id).limit(1)
    )
    return noted.first() is not None


async def rematch(session: AsyncSession, row: Event, facts: EventFacts) -> None:
    """A stored primary meets an earlier-stored primary of the same event: it joins it.

    This happens when the primary a row sat behind was deleted (``ON DELETE SET NULL``
    leaves its duplicates as strays), or when an earlier row has been listed since. The
    row's own duplicates move with it, so there is never a chain. A row somebody saved
    or noted people at stays a primary: hiding it would make their event disappear.
    """
    primary = await find_duplicate(session, facts, row.area, stored=row)
    if primary is None or await _has_user_data(session, row.id):
        return
    row.duplicate_of = primary.id
    await session.execute(
        update(Event).where(Event.duplicate_of == row.id).values(duplicate_of=primary.id)
    )


# ── writing the row ──────────────────────────────────────────────────────────


def _clean(value: str, width: int) -> str:
    """``value`` without NUL (PostgreSQL refuses it in text and in JSON), trimmed and cut."""
    return str(value).replace(_NUL, "").strip()[:width]


def _text(value: str | None, width: int) -> str | None:
    """``value`` cleaned and cut to a column's width; ``None`` when nothing is left."""
    return (_clean(value, width) or None) if value is not None else None


def _link(value: str | None, width: int) -> str | None:
    """``value``, or ``None`` when it does not fit: a cut link is a wrong link."""
    trimmed = _text(value, width + 1)
    return trimmed if trimmed is not None and len(trimmed) <= width else None


def _https_url(value: str | None, width: int) -> str | None:
    """``value`` if it is an ``https://`` link that fits, else ``None``."""
    link = _link(value, width)
    if link is None or len(link) <= len(_HTTPS) or link[: len(_HTTPS)].lower() != _HTTPS:
        return None
    return link


def _hosts(names: Iterable[str]) -> tuple[str, ...]:
    """At most :data:`MAX_HOSTS` distinct names, cleaned, in the order they came."""
    kept: dict[str, str] = {}
    for name in names:
        cleaned = _text(name, _HOST_MAX)
        if cleaned is not None:
            kept.setdefault(cleaned.casefold(), cleaned)
        if len(kept) == MAX_HOSTS:
            break
    return tuple(kept.values())


def _sanitise(facts: EventFacts) -> EventFacts:
    """``facts`` with every text field cleaned, bounded and fit for its column.

    Text loses NUL and is cut to its column; the event link must be ``https://`` and
    fit, else it is empty (the event is stored without a link, not dropped); the
    organiser's website is dropped if it does not fit; hosts are capped. This happens
    before :func:`assess`, which writes some reasons ("hosted by ...") from these very
    fields, so what it says is what is stored.
    """
    return replace(
        facts,
        title=_clean(facts.title, _TITLE_MAX),
        url=_https_url(facts.url, _URL_MAX) or "",
        snippet=_text(facts.snippet, _SNIPPET_MAX),
        venue_name=_text(facts.venue_name, _VENUE_MAX),
        address=_text(facts.address, _ADDRESS_MAX),
        locality=_text(facts.locality, _LOCALITY_MAX),
        organiser_name=_text(facts.organiser_name, _ORGANISER_MAX),
        organiser_url=_link(facts.organiser_url, _ORGANISER_URL_MAX),
        hosts=_hosts(facts.hosts),
    )


def _stated_position(facts: EventFacts) -> tuple[float, float] | None:
    return None if facts.lat is None or facts.lng is None else (facts.lat, facts.lng)


def _placed_position(facts: EventFacts, area: str | None) -> tuple[float, float] | None:
    """A postal address placed at the centroid of its area plus a jitter fixed by the event."""
    centre = centroid(area) if area else None
    if centre is None:
        return None
    return jitter(f"{facts.source}:{facts.external_id}", *centre)


def _precision(source: str, pin: str, withheld: bool) -> str:
    """A pin's precision: only a venue's own Eventbrite coordinates are exact."""
    return "exact" if source == "eventbrite" and pin == _STATED and not withheld else "area"


def _locate(row: Event, facts: EventFacts, area: str | None, meta: dict[str, Any]) -> None:
    """Where to draw the event and how precisely (spec 6.4).

    * ``exact``: Eventbrite's venue coordinates, the only ones a source states for a
      venue it names, unless the listing says the exact location comes later.
    * ``area``: any other stated position (Luma's pin may be only near the venue), or a
      postal address placed at its area's centroid (:func:`_placed_position`).
    * ``none``: online, or nothing placeable.

    A pin is replaced only by a position the source *states*; a sighting with just an
    address never moves a pin the row already has. The precision always follows the pin
    the row ends up with: ``meta["pin"]`` remembers whether it was stated or placed by
    us, and ``meta["address_withheld"]`` (which only ever turns on) whether it is only
    near, so an event that comes back in person after an online sighting gets its pin's
    own precision back rather than staying ``none``. A pin whose origin was never
    recorded is read as placed, so it can never become ``exact``.
    """
    if facts.is_online:
        row.geo_precision = "none"  # the pin, if any, is kept for when it is in person again
        return
    stated = _stated_position(facts)
    if stated is not None:
        row.lat, row.lng = stated
        meta["pin"] = _STATED
    elif row.lat is None or row.lng is None:
        placed = _placed_position(facts, area)
        if placed is None:
            row.geo_precision = "none"
            return
        row.lat, row.lng = placed
        meta["pin"] = _PLACED
    row.geo_precision = _precision(
        row.source, str(meta.get("pin", _PLACED)), bool(meta.get("address_withheld"))
    )


def _metadata(
    current: dict[str, Any] | None, facts: EventFacts, scope: SourceScope
) -> dict[str, Any]:
    """The inputs of the verdict that have no column, so a row can be judged again.

    The relevance step re-runs :func:`assess` over stored rows. Without these it would
    lose Eventbrite's technology category, the timezone that makes an online event
    Indian, and what the source vouched for.
    """
    meta = dict(current or {})
    meta["scope"] = {
        "key": _clean(scope.key, _SCOPE_KEY_MAX),
        "india": scope.india,
        "trusted_tech": scope.trusted_tech,
    }
    if facts.taxonomy:
        meta["taxonomy"] = {
            _clean(key, _TAXONOMY_MAX): _clean(value, _TAXONOMY_MAX)
            for key, value in facts.taxonomy.items()
        }
    timezone = _text(facts.timezone, _TIMEZONE_MAX)
    if timezone:
        meta["timezone"] = timezone
    locality = _text(facts.locality, _LOCALITY_MAX)
    if locality:
        meta["locality"] = locality
    # Only ever on: a later sighting that no longer carries the sentence does not mean
    # the address was published.
    meta["address_withheld"] = bool(facts.address_withheld) or bool(meta.get("address_withheld"))
    return meta


def _write(
    row: Event, facts: EventFacts, verdict: Verdict, scope: SourceScope, now: datetime
) -> None:
    """Put one sighting on ``row`` (new or stored). A field the facts leave empty keeps its value.

    ``facts`` have been through :func:`_sanitise`.
    """
    row.title = facts.title or row.title
    row.url = facts.url or row.url
    row.starts_at = as_utc(facts.starts_at)
    if facts.ends_at is not None:
        row.ends_at = as_utc(facts.ends_at)

    for column, value in (
        ("snippet", facts.snippet),
        ("venue_name", facts.venue_name),
        ("address", facts.address),
        ("organiser_name", facts.organiser_name),
        ("organiser_url", facts.organiser_url),
        ("is_free", facts.is_free),
    ):
        if value is not None:
            setattr(row, column, value)
    if facts.hosts:
        row.hosts = list(facts.hosts)

    # Flags and the verdict are restated by every sighting.
    row.is_online = facts.is_online
    row.is_cancelled = facts.is_cancelled
    row.format = verdict.format
    row.topics = list(verdict.topics)
    row.listed = verdict.listed
    row.reasons = [r for r in (_clean(reason, _REASON_MAX) for reason in verdict.reasons) if r]
    if verdict.area:
        row.area = verdict.area[:_AREA_MAX]

    meta = _metadata(row.extra_metadata, facts, scope)
    _locate(row, facts, verdict.area, meta)
    row.extra_metadata = meta
    row.last_seen_at = now


# ── upsert ───────────────────────────────────────────────────────────────────


def _usable(value: object, width: int) -> bool:
    """A string with something in it, of at most ``width`` characters and no NUL."""
    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= width and _NUL not in value
    )


async def _find_stored(session: AsyncSession, source: str, external_id: str) -> Event | None:
    found = await session.execute(
        select(Event).where(Event.source == source, Event.external_id == external_id).limit(1)
    )
    return found.scalars().first()


def _is_key_conflict(error: IntegrityError) -> bool:
    """The failure is the unique ``(source, external_id)`` constraint, and nothing else.

    asyncpg names the constraint on the exception the driver's was raised from
    (``constraint_name``) and in its message; psycopg2 in its message; SQLite never
    names a constraint, only the columns ("UNIQUE constraint failed: events.source,
    events.external_id"). A foreign key, the primary key or any other violation is not
    this one.
    """
    constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
    message = str(error.orig)
    return constraint == _KEY_CONSTRAINT or _KEY_CONSTRAINT in message or _SQLITE_KEY in message


async def _insert(session: AsyncSession, row: Event) -> Event:
    """Insert ``row``, or return the row another run stored first.

    The insert runs in a savepoint: two runs can both miss the lookup and both insert,
    and the unique ``(source, external_id)`` then fails one of them. That one is not an
    error. Its savepoint is rolled back (the transaction and everything the caller has
    done in it stand), and the winner is returned for it to update.

    Only that constraint is a lost race (:func:`_is_key_conflict`). Any other integrity
    error, such as the ``duplicate_of`` foreign key when the primary vanished mid-run,
    propagates even if a row with this key turns up afterwards: taking that row as the
    winner would drop the failed insert's duplicate link without a word. A key conflict
    whose winner has gone again propagates too.
    """
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        if not _is_key_conflict(error):
            raise
        winner = await _find_stored(session, row.source, row.external_id)
        if winner is None:
            raise
        return winner
    return row


async def upsert_event(
    session: AsyncSession, facts: EventFacts, scope: SourceScope, *, now: datetime
) -> UpsertResult:
    """Store one sighting of an event; see the module docstring for the order of steps.

    Validation is cheap and answers with ``skipped``, never an exception: an event
    without a usable identity (``invalid``), with a start outside 2000-2100 (``bad_time``,
    checked before any time arithmetic), more than :data:`HORIZON_DAYS` away
    (``horizon``) or already over (``ended``: its end, or its start when it states no
    end, is before ``now``), or that is not in Bengaluru or online from India
    (``location``). A skipped sighting leaves a stored row as it was.

    Text longer than its column is cut and loses NUL; a link that does not fit is
    dropped; an event link that is not ``https://`` is stored as no link, not as a
    skipped event. Nothing is committed, and a database error propagates (the caller's
    step wrapper rolls back), except a lost insert race, which becomes an update.
    """
    if not (
        _usable(facts.source, _SOURCE_MAX)
        and _usable(facts.external_id, _EXTERNAL_ID_MAX)
        and isinstance(facts.title, str)
        and _text(facts.title, _TITLE_MAX) is not None
    ):
        return _skipped("invalid")
    if not _plausible(facts.starts_at):
        return _skipped("bad_time")

    facts = refine_facts(_sanitise(facts))
    current = as_utc(now)
    starts = as_utc(facts.starts_at)
    if starts > current + timedelta(days=HORIZON_DAYS):
        return _skipped("horizon")
    over = as_utc(facts.ends_at) if facts.ends_at is not None else starts
    if over <= starts:  # an end that is not after the start is a data error
        over = starts
    if over < current:
        return _skipped("ended")

    stored = await _find_stored(session, facts.source, facts.external_id)

    # Whether the event is in Bengaluru does not depend on its host, so the lookups below
    # are not spent on an event that is not stored.
    verdict = assess(facts, scope)
    if not verdict.store:
        return _skipped("location")

    # Companies are resolved from everything known about the event, so a sparser sighting
    # neither loses a link nor flips a hiring event's verdict.
    resolved = await _resolve(
        session,
        facts.organiser_name or (stored.organiser_name if stored else None),
        facts.organiser_url or (stored.organiser_url if stored else None),
        facts.hosts or (stored.hosts if stored else None) or (),
        facts.title,
    )
    if resolved:  # a hiring event is listed only when its host is a listed company
        verdict = assess(facts, scope, host_is_listed_company=True)

    row = stored
    created = False
    if row is None:
        fresh = Event(
            id=uuid.uuid4(),
            source=facts.source,
            external_id=facts.external_id,
            title=facts.title,
            starts_at=starts,
            first_seen_at=current,
        )
        _write(fresh, facts, verdict, scope, current)
        duplicate = await find_duplicate(session, facts, verdict.area)
        fresh.duplicate_of = duplicate.id if duplicate is not None else None
        row = await _insert(session, fresh)
        created = row is fresh
    if not created:  # a stored row, or the winner of an insert race
        _write(row, facts, verdict, scope, current)
        if row.duplicate_of is None:
            await rematch(session, row, facts)
        await session.flush()

    await _replace_links(session, row.id, resolved)
    return UpsertResult(event=row, created=created, duplicate_of=row.duplicate_of, skipped=None)
