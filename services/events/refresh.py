"""
services/events/refresh.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled work behind Events, one bounded step per call.

``POST /api/v1/ingest/events/refresh?step=…`` (run by ``.github/workflows/events.yml``)
and ``scripts/refresh_events.py`` both call :func:`run_step`. A step:

* ``luma`` — the curated Luma iCal feeds: the Bengaluru Discover feed and each organiser
  calendar. A feed read in the last :data:`LUMA_MIN_INTERVAL` is not read again (cache key
  ``tr:events:luma:<id>:read``), one request a second, at most ``limit`` feeds.
* ``gdg`` — each chapter page, then the event pages of the upcoming events they list,
  soonest first. :data:`~services.events.gdg.CRAWL_DELAY_S` between requests, at most
  ``limit`` requests, an event page at most once in :data:`GDG_DETAIL_INTERVAL` unless its
  start moved. Everything is fetched through :func:`services.events.gdg.fetch_page`, which
  refuses any path robots.txt disallows.
* ``eventbrite`` — the curated organisers' and venues' live events through the official API,
  paged by continuation. Skipped without a token (and then makes no request). One request a
  second, at most ``limit`` calls (never over 60), an event that is not publicly listed is
  skipped, and a repeated or invalid continuation ends the paging.
* ``link`` — links upcoming events to the companies on the map again: the map grows daily.
* ``relevance`` — judges upcoming stored events again from what the store kept, the oldest
  judged first; puts an event right whose listing a duplicate has outlived.
* ``prune`` — the retention rule (``services.job_retention.prune_events``).
* ``purge`` — admin only, ``source=eventbrite``: deletes stored Eventbrite rows nobody saved
  or noted, for when API access ends. Not in the workflow.

Rules that hold throughout:

* **A step never raises.** An unknown step answers ``{"error": …}``; a failure answers
  ``{"step": …, "error": …}`` with the counts so far. Work is committed in units (a feed, an
  event page, an API page, a batch of rows), each in a session of its own: a database error
  rolls that unit back, is counted in ``errors`` and the step goes on; anything else is
  reported as ``error`` after the step has gone on too, so one bad feed cannot lose the rest.
* **Each source sits behind its own breaker** (``events:luma``, ``events:gdg``,
  ``events:eventbrite`` in :class:`~services.source_health.SourceHealthService`). Every read
  is recorded with ``job_count=len(events)``; three failures open it and the step answers
  ``"<source>: circuit open"``. Eventbrite refusing one organiser or venue to this token (400,
  403, 404) is a fact about that id, not about the source, and does not count as a failure.
* **Parsing runs off the event loop** (``asyncio.to_thread``).
* **The cache is optional.** If it cannot say when a feed was read, the feed is read: at worst
  once per run.
* **The token is never logged or returned**; an error names the status or the exception type,
  never its text.
* **Private events are not touched.** ``owner_user_id`` marks a user's own event: no step
  re-judges, re-links or re-points one, and one is never another event's primary.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import ColumnElement, and_, delete, exists, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from config.settings import get_settings
from domain.event_facts import EventFacts, SourceScope
from domain.event_relevance import Verdict, assess
from domain.event_time import DEFAULT_DURATION, as_utc
from services import job_retention
from services.cache_backend import CacheBackend
from services.events import eventbrite, gdg, luma, store
from services.events.sources import GdgChapter, load_sources, scope_for
from services.events.store import UpsertResult, link_companies, resolve_companies, upsert_event
from services.source_health import SourceHealthService
from storage.database import AsyncSessionLocal
from storage.models import Event, EventContact, EventSave

logger = logging.getLogger(__name__)

STEPS = ("luma", "gdg", "eventbrite", "link", "relevance", "prune", "purge")

#: Hard ceilings whatever the caller asks for: each source's polite rate, and what one request
#: can finish. ``prune`` and ``purge`` take no limit; they are bounded by their own caps.
MAX_LIMIT = {
    "luma": 26,
    "gdg": 30,
    "eventbrite": 60,
    "link": 500,
    "relevance": 2000,
    "prune": 1,
    "purge": 1,
}

#: Luma's iCal feeds declare ``REFRESH-INTERVAL: PT12H``; a feed is read at most this often.
LUMA_MIN_INTERVAL = timedelta(hours=11)
#: An event page is read at most this often, unless the chapter page says its start moved.
GDG_DETAIL_INTERVAL = timedelta(days=3)
#: One request a second to Luma and to the Eventbrite API. GDG asks for two (``Crawl-delay``).
LUMA_PAUSE_S = 1.0
EVENTBRITE_PAUSE_S = 1.0

#: Rows a ``link`` or ``relevance`` unit handles before it commits.
BATCH = 50
#: Most rows one ``purge`` call deletes.
PURGE_CAP = 5000

LUMA_KEY = "events:luma"
GDG_KEY = "events:gdg"
EVENTBRITE_KEY = "events:eventbrite"

#: What the API says when this token may not read one organiser or venue, or does not take the
#: query: about that id, not about the source's health.
_REFUSED = frozenset({400, 403, 404})
_HALTING = frozenset({401, 429})  # a refused token or a rate limit: asking again does not help

_COUNT_KEYS = ("seen", "created", "updated", "duplicates", "skipped", "listed")
_REASON_MAX = 200
_AREA_MAX = 48
_MAX_REPORTED_ERRORS = 5
_ERRORS_MAX = 600
_FAULT_MAX = 300
_PURGE_SOURCE = "eventbrite"


def _now() -> datetime:
    """The clock of a run; tests replace it."""
    return datetime.now(UTC)


# ── the report ───────────────────────────────────────────────────────────────


@dataclass
class _Report:
    """What a step did so far, and what went wrong on the way."""

    step: str
    counts: dict[str, int]
    problems: list[str] = field(default_factory=list)
    fault: str | None = None

    @classmethod
    def for_source(cls, step: str, unit: str) -> _Report:
        return cls(step, {unit: 0, **dict.fromkeys(_COUNT_KEYS, 0)})

    @classmethod
    def of(cls, step: str, *keys: str) -> _Report:
        return cls(step, dict.fromkeys(keys, 0))

    def add(self, counts: Mapping[str, int]) -> None:
        for key, value in counts.items():
            self.counts[key] = self.counts.get(key, 0) + value

    def problem(self, text: str) -> None:
        """A known, ordinary trouble: a source down, a feed refused, a database error."""
        self.problems.append(text)

    def broke(self, exc: BaseException) -> None:
        """An unexpected failure: the first is reported as ``error``, the rest as problems."""
        text = f"{type(exc).__name__}: {exc}"[:_FAULT_MAX]
        if self.fault is None:
            self.fault = text
        else:
            self.problems.append(text)

    def as_dict(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {"step": self.step, **self.counts}
        if self.problems:
            shown = "; ".join(self.problems[:_MAX_REPORTED_ERRORS])
            if len(self.problems) > _MAX_REPORTED_ERRORS:
                shown += f"; (+{len(self.problems) - _MAX_REPORTED_ERRORS} more)"
            result["errors"] = shown[:_ERRORS_MAX]
        if self.fault is not None:
            result["error"] = self.fault
        return result


Work = Callable[[AsyncSession], Awaitable[dict[str, int]]]


async def _unit(report: _Report, label: str, work: Work) -> bool:
    """Run ``work`` in a session of its own and commit it; ``True`` when it committed.

    A database error rolls the unit back and is counted in ``errors``. Any other exception
    also rolls it back and is reported as the step's ``error``. Either way the caller goes on
    to its next unit. What ``work`` returns is added to the report only once it has committed.
    """
    async with AsyncSessionLocal() as session:
        try:
            counts = await work(session)
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.warning("events %s: database error %s", label, type(exc).__name__)
            report.problem(f"{label}: {type(exc).__name__}")
            return False
        except Exception as exc:
            await session.rollback()
            logger.exception("events %s failed", label)
            report.broke(exc)
            return False
    report.add(counts)
    return True


def _upserts(
    facts: Sequence[EventFacts],
    scope: SourceScope,
    now: datetime,
    *,
    detail_at: datetime | None = None,
) -> Work:
    """A unit of work that stores ``facts`` and counts what became of each."""

    async def work(session: AsyncSession) -> dict[str, int]:
        counts = dict.fromkeys(_COUNT_KEYS, 0)
        for fact in facts:
            result = await upsert_event(session, fact, scope, now=now)
            if detail_at is not None and result.event is not None:
                result.event.detail_fetched_at = detail_at
            _tally(result, counts)
        return counts

    return work


def _tally(result: UpsertResult, counts: dict[str, int]) -> None:
    if result.skipped is not None:
        counts["skipped"] += 1
        return
    counts["created" if result.created else "updated"] += 1
    if result.duplicate_of is not None:
        counts["duplicates"] += 1
    elif result.event is not None and result.event.listed:
        counts["listed"] += 1


def _chunks(items: Sequence[uuid.UUID], size: int) -> Iterator[list[uuid.UUID]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# ── source health ────────────────────────────────────────────────────────────


def _describe(exc: BaseException) -> str:
    """What to say of a failure: the status or the exception's type, never its text."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _path_of(url: str) -> str:
    """The path of ``url``: all an error says of a page (the host is always the same)."""
    return httpx.URL(url).path[:80]


async def _record(
    key: str, jobs: int, started: float, failure: BaseException | None = None
) -> None:
    await SourceHealthService.record_attempt(
        key,
        job_count=jobs,
        latency_ms=(time.monotonic() - started) * 1000,
        status="success" if failure is None else f"error: {_describe(failure)}",
    )


# ── luma ─────────────────────────────────────────────────────────────────────


def _luma_read_key(feed_id: str) -> str:
    return f"tr:events:luma:{feed_id}:read"


async def _luma_read_recently(feed_id: str) -> bool:
    """Was the feed read inside :data:`LUMA_MIN_INTERVAL`? A cache that cannot say means no."""
    try:
        return await CacheBackend.get(_luma_read_key(feed_id)) is not None
    except Exception as exc:
        logger.debug("Could not read the Luma read stamp: %s", type(exc).__name__)
        return False


async def _luma_remember(feed_id: str, now: datetime) -> None:
    try:
        await CacheBackend.set(
            _luma_read_key(feed_id), now.isoformat(), int(LUMA_MIN_INTERVAL.total_seconds())
        )
    except Exception as exc:
        logger.debug("Could not write the Luma read stamp: %s", type(exc).__name__)


async def _step_luma(report: _Report, limit: int, now: datetime) -> None:
    fetched = 0
    async with httpx.AsyncClient() as client:
        for feed in load_sources().luma:
            if fetched >= limit:
                break
            if await _luma_read_recently(feed.id):
                continue  # does not use up the cap: the next feed is read instead
            if not await SourceHealthService.is_available(LUMA_KEY):
                report.problem("luma: circuit open")
                break
            if fetched:
                await asyncio.sleep(LUMA_PAUSE_S)
            fetched += 1
            report.counts["feeds"] += 1
            started = time.monotonic()
            try:
                body = await luma.fetch_feed(client, luma.feed_url(feed.kind, feed.id))
            except (httpx.HTTPError, ValueError) as exc:
                await _record(LUMA_KEY, 0, started, exc)
                logger.warning("Luma feed %s unavailable: %s", feed.name, _describe(exc))
                report.problem(f"luma {feed.name}: {_describe(exc)}")
                continue
            facts = await asyncio.to_thread(luma.parse_feed, body)
            await _record(LUMA_KEY, len(facts), started)
            report.counts["seen"] += len(facts)
            if await _unit(report, f"luma {feed.name}", _upserts(facts, scope_for(feed), now)):
                await _luma_remember(feed.id, now)


# ── gdg ──────────────────────────────────────────────────────────────────────


def _gdg_read_key(listing: gdg.GdgListing) -> str:
    return f"tr:events:gdg:{listing.slug}:{int(as_utc(listing.starts_at).timestamp())}:read"


async def _gdg_read_recently(listing: gdg.GdgListing, now: datetime) -> bool:
    """Was this event page read inside :data:`GDG_DETAIL_INTERVAL`, for this start?

    The cache remembers every page read, stored or not, keyed by the start time, so a moved
    start is a new key. The stored row remembers too (``detail_fetched_at``), which survives
    a restart that empties the cache.
    """
    try:
        if await CacheBackend.get(_gdg_read_key(listing)) is not None:
            return True
    except Exception as exc:
        logger.debug("Could not read the GDG read stamp: %s", type(exc).__name__)
    try:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(Event.starts_at, Event.detail_fetched_at).where(
                        Event.source == "gdg", Event.external_id == listing.slug
                    )
                )
            ).first()
    except SQLAlchemyError as exc:
        logger.warning("Could not look up GDG event %s: %s", listing.slug, type(exc).__name__)
        return False
    if row is None or row.detail_fetched_at is None:
        return False
    return (
        as_utc(row.starts_at) == as_utc(listing.starts_at)
        and as_utc(now) - as_utc(row.detail_fetched_at) < GDG_DETAIL_INTERVAL
    )


async def _gdg_remember(listing: gdg.GdgListing, now: datetime) -> None:
    try:
        await CacheBackend.set(
            _gdg_read_key(listing), now.isoformat(), int(GDG_DETAIL_INTERVAL.total_seconds())
        )
    except Exception as exc:
        logger.debug("Could not write the GDG read stamp: %s", type(exc).__name__)


async def _step_gdg(report: _Report, limit: int, now: datetime) -> None:
    requests = 0
    halted = False
    async with httpx.AsyncClient() as client:

        async def read(url: str) -> tuple[bytes, float] | None:
            """One page, behind the breaker and the crawl delay; ``None`` if it was not read."""
            nonlocal requests, halted
            if not await SourceHealthService.is_available(GDG_KEY):
                report.problem("gdg: circuit open")
                halted = True
                return None
            if requests:
                await asyncio.sleep(gdg.CRAWL_DELAY_S)
            requests += 1
            report.counts["pages"] += 1
            started = time.monotonic()
            try:
                return await gdg.fetch_page(client, url), started
            except (httpx.HTTPError, ValueError) as exc:
                await _record(GDG_KEY, 0, started, exc)
                logger.warning("GDG page unavailable: %s", _describe(exc))
                report.problem(f"gdg {_path_of(url)}: {_describe(exc)}")
                return None

        # Every chapter page first, so a short budget never starves a chapter.
        upcoming: list[tuple[GdgChapter, gdg.GdgListing]] = []
        for chapter in load_sources().gdg:
            if halted or requests >= limit:
                break
            page = await read(gdg.chapter_url(chapter.slug))
            if page is None:
                continue
            body, started = page
            listings = await asyncio.to_thread(gdg.parse_chapter, body)
            await _record(GDG_KEY, len(listings), started)
            upcoming.extend((chapter, listing) for listing in listings)

        # Then the event pages, soonest first, each event once however many chapters list it.
        done: set[str] = set()
        for chapter, listing in sorted(upcoming, key=lambda item: as_utc(item[1].starts_at)):
            if listing.slug in done:
                continue
            done.add(listing.slug)
            report.counts["seen"] += 1
            if halted or requests >= limit or await _gdg_read_recently(listing, now):
                report.counts["skipped"] += 1
                continue
            page = await read(gdg.event_page_url(listing.slug))
            if page is None:
                report.counts["skipped"] += 1
                continue
            body, started = page
            facts = await asyncio.to_thread(
                gdg.parse_event_page, body, slug=listing.slug, chapter=chapter.name
            )
            await _record(GDG_KEY, 0 if facts is None else 1, started)
            if facts is None:  # a page that states no event is not asked for again for days
                report.counts["skipped"] += 1
                await _gdg_remember(listing, now)
                continue
            work = _upserts([facts], scope_for(chapter), now, detail_at=now)
            if await _unit(report, f"gdg {listing.slug[:60]}", work):
                await _gdg_remember(listing, now)


# ── eventbrite ───────────────────────────────────────────────────────────────


async def _step_eventbrite(report: _Report, limit: int, now: datetime, token: str) -> None:
    sources = load_sources()
    targets = [("organizers", ident) for ident in sources.eventbrite_organizers] + [
        ("venues", ident) for ident in sources.eventbrite_venues
    ]
    scope = scope_for("eventbrite")
    calls = 0
    venues_ordered = True  # cleared when a venue refuses order_by, so the rest do not ask again
    halted = False
    async with httpx.AsyncClient() as client:
        for kind, ident in targets:
            if halted:
                break
            label = f"eventbrite {kind[:-1]} {ident}"
            continuation: str | None = None
            used: set[str] = set()
            while calls < limit and not halted:
                if not await SourceHealthService.is_available(EVENTBRITE_KEY):
                    report.problem("eventbrite: circuit open")
                    halted = True
                    break
                if calls:
                    await asyncio.sleep(EVENTBRITE_PAUSE_S)
                calls += 1
                report.counts["calls"] += 1
                started = time.monotonic()
                try:
                    if kind == "organizers":
                        payload = await eventbrite.organizer_events(
                            client, token, ident, continuation
                        )
                    else:
                        payload = await eventbrite.venue_events(
                            client, token, ident, continuation, ordered=venues_ordered
                        )
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if kind == "venues" and venues_ordered and status == 400:
                        venues_ordered = False  # ask again, once, without order_by
                        continue
                    report.problem(f"{label}: {_describe(exc)}")
                    if status not in _REFUSED:
                        await _record(EVENTBRITE_KEY, 0, started, exc)
                        halted = status in _HALTING
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    await _record(EVENTBRITE_KEY, 0, started, exc)
                    logger.warning("%s unavailable: %s", label, _describe(exc))
                    report.problem(f"{label}: {_describe(exc)}")
                    break

                raw = payload.get("events")
                dropped = 0
                if isinstance(
                    raw, list
                ):  # an event that is not publicly listed is not ours to list
                    kept = [
                        e for e in raw if not (isinstance(e, dict) and e.get("listed") is False)
                    ]
                    dropped = len(raw) - len(kept)
                    payload = {**payload, "events": kept}
                facts, cursor = await asyncio.to_thread(eventbrite.parse_event_list, payload)
                await _record(EVENTBRITE_KEY, len(facts), started)
                report.counts["seen"] += len(facts) + dropped
                report.counts["skipped"] += dropped
                if facts:
                    await _unit(report, label, _upserts(facts, scope, now))
                if cursor is None or cursor in used:
                    break
                used.add(cursor)
                continuation = cursor


# ── reading stored rows ──────────────────────────────────────────────────────


def _public() -> ColumnElement[bool]:
    """Not a user's own event: refresh steps never read or write one."""
    return Event.owner_user_id.is_(None)


def _upcoming(now: datetime) -> ColumnElement[bool]:
    """Not over yet: the stated end, else two hours after the start."""
    return or_(
        Event.ends_at >= now,
        and_(Event.ends_at.is_(None), Event.starts_at >= now - DEFAULT_DURATION),
    )


async def _upcoming_ids(limit: int, now: datetime, *, judged_first: bool) -> list[uuid.UUID]:
    """Ids of up to ``limit`` upcoming public events: soonest first, or longest unjudged first.

    ``updated_at`` is when a row was last written, by a sighting or by the relevance step
    (which writes it whether or not the verdict changed), so it orders "oldest assessed".
    """
    order = (Event.updated_at, Event.id) if judged_first else (Event.starts_at, Event.id)
    async with AsyncSessionLocal() as session:
        found = await session.execute(
            select(Event.id).where(_public(), _upcoming(now)).order_by(*order).limit(limit)
        )
        return list(found.scalars())


# ── link ─────────────────────────────────────────────────────────────────────


def _link_batch(ids: list[uuid.UUID]) -> Work:
    async def work(session: AsyncSession) -> dict[str, int]:
        rows = (await session.execute(select(Event).where(Event.id.in_(ids)))).scalars().all()
        linked = 0
        for row in rows:
            linked += await link_companies(session, row)
        return {"linked": linked}

    return work


async def _step_link(report: _Report, limit: int, now: datetime) -> None:
    ids = await _upcoming_ids(limit, now, judged_first=False)
    for number, batch in enumerate(_chunks(ids, BATCH), start=1):
        report.counts["seen"] += len(batch)
        await _unit(report, f"link batch {number}", _link_batch(batch))


# ── relevance ────────────────────────────────────────────────────────────────


def _facts_from_row(row: Event) -> EventFacts:
    """The facts the store judged ``row`` from: the row, and what it kept in ``metadata``.

    A pin the store *placed* at the centre of the address's area (``metadata["pin"]``) was
    never a stated position, and the verdict was made from the address; so it is not handed
    back as coordinates. A pin whose origin was never recorded is read as placed.
    """
    meta: dict[str, Any] = dict(row.extra_metadata or {})
    stated = meta.get("pin") == "stated"
    taxonomy = meta.get("taxonomy")
    locality = meta.get("locality")
    timezone = meta.get("timezone")
    return EventFacts(
        source=row.source,
        external_id=row.external_id,
        url=row.url or "",
        title=row.title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        snippet=row.snippet,
        venue_name=row.venue_name,
        address=row.address,
        locality=locality if isinstance(locality, str) else None,
        lat=row.lat if stated else None,
        lng=row.lng if stated else None,
        address_withheld=meta.get("address_withheld") is True,
        is_online=row.is_online,
        is_free=row.is_free,
        organiser_name=row.organiser_name,
        organiser_url=row.organiser_url,
        hosts=tuple(row.hosts or ()),
        is_cancelled=row.is_cancelled,
        timezone=timezone if isinstance(timezone, str) else None,
        taxonomy=(
            {str(k): str(v) for k, v in taxonomy.items()} if isinstance(taxonomy, dict) else {}
        ),
    )


def _scope_from_row(row: Event) -> SourceScope:
    """What the source vouched for when the row was stored, else what its name vouches for."""
    kept = (row.extra_metadata or {}).get("scope")
    if isinstance(kept, dict):
        return SourceScope(
            key=str(kept.get("key", "")),
            india=kept.get("india") is True,
            trusted_tech=kept.get("trusted_tech") is True,
        )
    return scope_for(row.source)


async def _verdict(session: AsyncSession, facts: EventFacts, scope: SourceScope) -> Verdict:
    """The store's two-step verdict: a hiring event is listed only when its host is."""
    verdict = assess(facts, scope)
    if verdict.store and await resolve_companies(session, facts):
        verdict = assess(facts, scope, host_is_listed_company=True)
    return verdict


def _apply(row: Event, verdict: Verdict) -> bool:
    """Write the verdict on ``row`` as the store does; ``True`` if anything changed."""
    reasons = [reason[:_REASON_MAX] for reason in verdict.reasons if reason]
    topics = list(verdict.topics)
    changed = (
        row.listed != verdict.listed
        or row.format != verdict.format
        or list(row.topics or []) != topics
        or list(row.reasons or []) != reasons
    )
    row.listed = verdict.listed
    row.format = verdict.format
    row.topics = topics
    row.reasons = reasons
    if verdict.area and verdict.area[:_AREA_MAX] != row.area:
        row.area = verdict.area[:_AREA_MAX]
        changed = True
    return changed


def _reassess_batch(ids: list[uuid.UUID], now: datetime) -> Work:
    async def work(session: AsyncSession) -> dict[str, int]:
        counts = dict.fromkeys(("updated", "listed", "unlisted", "merged"), 0)
        rows = (await session.execute(select(Event).where(Event.id.in_(ids)))).scalars().all()
        for row in rows:
            facts = _facts_from_row(row)
            was_listed = row.listed
            counts["updated"] += _apply(row, await _verdict(session, facts, _scope_from_row(row)))
            row.updated_at = now
            counts["listed"] += not was_listed and row.listed
            counts["unlisted"] += was_listed and not row.listed
            # Autoflush is off: what is queried next must see what was just decided.
            await session.flush()
            if row.listed and row.duplicate_of is None:
                # A stray (its primary was deleted, or it was not listed when it arrived) may
                # be a second listing of an event stored before it. Never a private one's.
                await store._rematch(session, row, facts)
                counts["merged"] += row.duplicate_of is not None
                await session.flush()
        return counts

    return work


async def _stranded_primaries(limit: int, now: datetime) -> list[uuid.UUID]:
    """Unlisted upcoming events that still hide a listed duplicate of theirs."""
    duplicate = aliased(Event)
    async with AsyncSessionLocal() as session:
        found = await session.execute(
            select(Event.id)
            .where(
                _public(),
                _upcoming(now),
                Event.listed.is_(False),
                exists().where(
                    duplicate.duplicate_of == Event.id,
                    duplicate.owner_user_id.is_(None),
                    duplicate.listed.is_(True),
                ),
            )
            .order_by(Event.starts_at, Event.id)
            .limit(limit)
        )
        return list(found.scalars())


def _promote(primary_id: uuid.UUID) -> Work:
    """Hand an unlisted primary's event to its earliest listed duplicate.

    The earliest-stored listed duplicate becomes the primary and the rest re-point to it,
    except one from the same source: two events of one source are two events, however alike
    they read, and neither hides behind the other. A private event is neither promoted nor
    moved.
    """

    async def work(session: AsyncSession) -> dict[str, int]:
        rows = (
            (
                await session.execute(
                    select(Event)
                    .where(Event.duplicate_of == primary_id, _public())
                    .order_by(Event.first_seen_at, Event.id)
                )
            )
            .scalars()
            .all()
        )
        heir = next((row for row in rows if row.listed), None)
        if heir is None:
            return {"promoted": 0}
        heir.duplicate_of = None
        for row in rows:
            if row is not heir:
                row.duplicate_of = heir.id if row.source != heir.source else None
        return {"promoted": 1}

    return work


async def _step_relevance(report: _Report, limit: int, now: datetime) -> None:
    ids = await _upcoming_ids(limit, now, judged_first=True)
    for number, batch in enumerate(_chunks(ids, BATCH), start=1):
        report.counts["seen"] += len(batch)
        await _unit(report, f"relevance batch {number}", _reassess_batch(batch, now))
    # After the verdicts, so it sees every primary the loop (or a sighting) took off the list.
    for primary_id in await _stranded_primaries(limit, now):
        await _unit(report, "promote duplicates", _promote(primary_id))


# ── prune and purge ──────────────────────────────────────────────────────────


async def _prune() -> dict[str, int | str]:
    prune = getattr(job_retention, "prune_events", None)
    if prune is None:
        return {"step": "prune", "skipped": "no retention rule for events"}
    async with AsyncSessionLocal() as session:
        deleted = int(await prune(session))
        await session.commit()
    return {"step": "prune", "deleted": deleted}


async def _purge() -> dict[str, int | str]:
    """Delete up to :data:`PURGE_CAP` stored Eventbrite rows that nobody saved or noted."""
    async with AsyncSessionLocal() as session:
        wanted = exists().where(EventSave.event_id == Event.id) | exists().where(
            EventContact.event_id == Event.id
        )
        doomed = (
            select(Event.id)
            .where(Event.source == _PURGE_SOURCE, ~wanted)
            .order_by(Event.starts_at, Event.id)
            .limit(PURGE_CAP)
        )
        result = cast(
            "CursorResult[Any]", await session.execute(delete(Event).where(Event.id.in_(doomed)))
        )
        await session.commit()
    return {"step": "purge", "deleted": int(result.rowcount or 0)}


# ── the entry point ──────────────────────────────────────────────────────────


async def run_step(step: str, limit: int, *, source: str | None = None) -> dict[str, int | str]:
    """Run one refresh step; never raises.

    ``limit`` is clamped to :data:`MAX_LIMIT`. ``source`` matters only to ``purge``, which
    needs ``source="eventbrite"``.
    """
    if step not in STEPS:
        return {"error": f"unknown step {step!r}; expected one of {', '.join(STEPS)}"}
    report = _Report.of(step)
    try:
        bounded = max(1, min(int(limit), MAX_LIMIT[step]))
        now = _now()
        if step == "luma":
            report = _Report.for_source(step, "feeds")
            await _step_luma(report, bounded, now)
        elif step == "gdg":
            report = _Report.for_source(step, "pages")
            await _step_gdg(report, bounded, now)
        elif step == "eventbrite":
            token = get_settings().eventbrite_token
            if not token:
                return {"step": step, "skipped": "no token"}
            report = _Report.for_source(step, "calls")
            await _step_eventbrite(report, bounded, now, token)
        elif step == "link":
            report = _Report.of(step, "seen", "linked")
            await _step_link(report, bounded, now)
        elif step == "relevance":
            report = _Report.of(step, "seen", "updated", "listed", "unlisted", "merged", "promoted")
            await _step_relevance(report, bounded, now)
        elif step == "prune":
            return await _prune()
        elif source != _PURGE_SOURCE:  # step == "purge"
            return {"step": step, "error": f"purge needs source={_PURGE_SOURCE}"}
        else:
            return await _purge()
    except Exception as exc:  # a scheduled step must never take the caller down
        logger.exception("events step %s failed", step)
        report.broke(exc)
    return report.as_dict()
