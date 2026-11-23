"""
services/liveness.py
~~~~~~~~~~~~~~~~~~~~
Keeping the liveness evidence current, and reading it back as a verdict.

Three jobs, none of which calls a model:

* ``record_sighting`` - fold each scraped posting into ``role_sightings``,
  the per-role history that outlives retention and lets us say "re-listed".
* ``reverify_batch`` - ask the employer's ATS whether postings we hold are
  still served. There is no worker process; ``POST /api/v1/ingest/reverify``
  runs this from a scheduled GitHub Action, the same way the keepalive runs.
* ``verdict_for`` - build the evidence from stored rows and hand it to the
  pure rule set in ``domain/liveness.py``.

Nothing here may raise into its caller. A sighting that cannot be recorded
must not fail the write of the job itself, and a re-check that cannot reach
the database must not take down the scheduler that asked for it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx

from domain.job_boards import ATS, PACE_SECONDS, BoardRef
from domain.liveness import (
    VERIFICATION_VALID_FOR,
    LivenessEvidence,
    LivenessVerdict,
    SightingState,
    assess,
    is_verifiable_source,
    next_sighting,
    title_key,
)
from domain.posting_urls import PostingRef, posting_ref
from ingestion.boards.base import BoardRead, failed
from ingestion.scrapers.ats_scraper import ATSScraper, PostingCheck

logger = logging.getLogger(__name__)

# Sized for one scheduled run on a free instance: ~150 small JSON requests,
# five at a time, finishes well inside the HTTP timeout of the cron's curl.
DEFAULT_BATCH_LIMIT = 150
MAX_BATCH_LIMIT = 500
RECHECK_CONCURRENCY = 5

#: The board reads a batch may make before the rest are answered ``error``. The workflow's
#: curl waits 300 s; a Workable board alone waits its 10 s pace, so the reads are bounded
#: and what is not read in time is unknown, never closed.
BOARD_READ_BUDGET_SECONDS = 200.0

#: How a batch waits between two board reads of one ATS: a module-level reference, so a
#: test can replace it and not really wait.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


# A closure is looked at again for this long. Postings do come back, and an
# earlier version of the re-check closed open roles by asking about the wrong
# id; this is what lets those reopen without anyone editing the database.
REOPEN_WINDOW = timedelta(days=14)
ON_DEMAND_RECHECK_SECONDS = 4.0

@dataclass(frozen=True)
class Candidate:
    """One posting due a re-check."""

    job_id: uuid.UUID
    source: str
    external_id: str | None
    source_url: str | None = None


# ── sightings ────────────────────────────────────────────────────────────────


async def record_sighting(
    session: Any,
    *,
    company_id: uuid.UUID,
    title: str,
    external_id: str | None,
    seen_at: datetime,
) -> None:
    """
    Record that a role was seen. Never raises.

    Runs inside the caller's transaction, in a savepoint, so a failure here
    rolls back only the sighting and leaves the job write intact.
    """
    key = title_key(title)
    if not key:
        return
    try:
        from storage.models import RoleSighting

        async with session.begin_nested():
            row = await session.get(RoleSighting, (company_id, key))
            previous = (
                SightingState(
                    first_seen_at=row.first_seen_at,
                    last_seen_at=row.last_seen_at,
                    times_seen=row.times_seen,
                    times_reposted=row.times_reposted,
                    last_external_id=row.last_external_id,
                )
                if row is not None
                else None
            )
            state = next_sighting(previous, external_id=external_id, seen_at=seen_at)
            if row is None:
                session.add(
                    RoleSighting(
                        company_id=company_id,
                        title_key=key,
                        first_seen_at=state.first_seen_at,
                        last_seen_at=state.last_seen_at,
                        times_seen=state.times_seen,
                        times_reposted=state.times_reposted,
                        last_external_id=state.last_external_id,
                    )
                )
            else:
                row.first_seen_at = state.first_seen_at
                row.last_seen_at = state.last_seen_at
                row.times_seen = state.times_seen
                row.times_reposted = state.times_reposted
                row.last_external_id = state.last_external_id
            await session.flush()
    except Exception as exc:
        logger.debug("Could not record a sighting of %r: %s", title[:60], exc)


async def sightings_for(
    session: Any, pairs: list[tuple[uuid.UUID, str]]
) -> dict[tuple[uuid.UUID, str], Any]:
    """Load sightings for (company_id, title) pairs in one query. Never raises."""
    keys = {(company_id, title_key(title)) for company_id, title in pairs if company_id}
    keys = {k for k in keys if k[1]}
    if not keys:
        return {}
    try:
        from sqlalchemy import select, tuple_

        from storage.models import RoleSighting

        rows = (
            await session.execute(
                select(RoleSighting).where(
                    tuple_(RoleSighting.company_id, RoleSighting.title_key).in_(list(keys))
                )
            )
        ).scalars()
        return {(row.company_id, row.title_key): row for row in rows}
    except Exception as exc:
        logger.debug("Could not load role sightings: %s", exc)
        return {}


# ── re-verification ──────────────────────────────────────────────────────────


async def _load_candidates(limit: int) -> list[Candidate]:
    """
    Postings due a re-check: roles someone is tracking first, then whichever
    has gone longest without one.
    """
    from sqlalchemy import text

    from storage.database import AsyncSessionLocal

    # One bound pattern per ATS on the roster (``greenhouse:%``, ``workable:%``
    # ...), so the list cannot drift from ``domain.job_boards.ATS``.
    source_params = {f"ats_{i}": f"{ats}:%" for i, ats in enumerate(ATS)}
    source_clause = " OR ".join(f"j.source LIKE :{name}" for name in source_params)

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                f"""
                SELECT j.id, j.source, j.external_id, j.source_url
                FROM jobs j
                WHERE (j.closed_at IS NULL OR j.closed_at > :reopen_after)
                  AND ({source_clause})
                ORDER BY
                  EXISTS (SELECT 1 FROM job_applications a WHERE a.job_id = j.id) DESC,
                  j.last_verified_at ASC NULLS FIRST
                LIMIT :limit
                """
            ),
            {"limit": limit, "reopen_after": datetime.now(UTC) - REOPEN_WINDOW, **source_params},
        )
        return [
            Candidate(job_id=row[0], source=row[1], external_id=row[2], source_url=row[3])
            for row in rows.all()
        ]


async def _apply_results(
    open_ids: list[uuid.UUID], closed_ids: list[uuid.UUID], *, now: datetime
) -> None:
    from sqlalchemy import update

    from storage.database import AsyncSessionLocal
    from storage.models import Job

    async with AsyncSessionLocal() as session:
        if open_ids:
            await session.execute(
                update(Job)
                .where(Job.id.in_(open_ids))
                .values(last_verified_at=now, closed_at=None)
            )
        if closed_ids:
            await session.execute(
                # Keep the date it was first found gone.
                update(Job)
                .where(Job.id.in_(closed_ids), Job.closed_at.is_(None))
                .values(closed_at=now)
            )
        await session.commit()


async def _read_boards_once(
    wanted: list[BoardRef], boards: dict[BoardRef, BoardRead], client: httpx.AsyncClient
) -> None:
    """
    Read each board in ``wanted`` once, in order, into ``boards``.

    A board's reads of one ATS are ``PACE_SECONDS`` apart. Past ``BOARD_READ_BUDGET_SECONDS``
    a board is not read: it is recorded as a failed read, so its postings are ``ERROR``.
    """
    started = time.monotonic()
    seen_ats: set[str] = set()
    for board in dict.fromkeys(wanted):
        if board in boards:
            continue
        if time.monotonic() - started > BOARD_READ_BUDGET_SECONDS:
            boards[board] = failed("error")
            continue
        if board.ats in seen_ats:
            await _sleep(PACE_SECONDS[board.ats])
        seen_ats.add(board.ats)
        await ATSScraper.read_board_once(board, boards=boards, client=client)


async def reverify_batch(limit: int = DEFAULT_BATCH_LIMIT) -> dict[str, int]:
    """Re-check up to ``limit`` postings against their ATS. Never raises."""
    summary = {"checked": 0, "open": 0, "closed": 0, "errors": 0}
    limit = max(1, min(limit, MAX_BATCH_LIMIT))

    try:
        candidates = await _load_candidates(limit)
    except Exception as exc:
        logger.warning("Re-verification could not load candidates: %s", exc)
        return summary
    if not candidates:
        return summary

    # ``refs`` is what each row is asked about: the id in its posting URL, not
    # the stored external_id (see ``posting_ref``). A row with no real id is
    # an error, never a closure.
    refs = [posting_ref(c.source, c.source_url, c.external_id) for c in candidates]

    # Each board that a membership check needs is read once, before any posting is
    # checked, so ten postings on one board cost one read rather than ten racing ones.
    boards: dict[BoardRef, BoardRead] = {}
    wanted: list[BoardRef] = []
    for candidate, ref in zip(candidates, refs, strict=True):
        board = ATSScraper.board_for(ref.source, candidate.source_url) if ref is not None else None
        if board is not None:
            wanted.append(board)

    gate = asyncio.Semaphore(RECHECK_CONCURRENCY)

    async with ATSScraper.recheck_client() as client:
        await _read_boards_once(wanted, boards, client)

        async def check(candidate: Candidate, ref: PostingRef | None) -> PostingCheck:
            if ref is None:
                return PostingCheck.ERROR
            async with gate:
                try:
                    return await ATSScraper.check_posting(
                        ref.source,
                        ref.external_id,
                        source_url=candidate.source_url,
                        boards=boards,
                        client=client,
                    )
                except Exception as exc:
                    logger.debug("Re-check of %s raised: %s", ref.external_id, exc)
                    return PostingCheck.ERROR

        results = await asyncio.gather(
            *(check(c, r) for c, r in zip(candidates, refs, strict=True))
        )

    open_ids = [c.job_id for c, r in zip(candidates, results, strict=True) if r is PostingCheck.OPEN]
    closed_ids = [
        c.job_id for c, r in zip(candidates, results, strict=True) if r is PostingCheck.CLOSED
    ]
    summary.update(
        checked=len(candidates),
        open=len(open_ids),
        closed=len(closed_ids),
        errors=len(candidates) - len(open_ids) - len(closed_ids),
    )

    try:
        await _apply_results(open_ids, closed_ids, now=datetime.now(UTC))
    except Exception as exc:
        logger.warning("Re-verification could not record its results: %s", exc)

    logger.info("Re-verification: %s", summary)
    return summary


async def reverify_job(job: Any) -> PostingCheck:
    """
    Re-check one posting now and record the result on the given ORM row.
    The caller owns the session and the commit. Never raises.
    """
    ref = posting_ref(
        job.source, getattr(job, "source_url", None), getattr(job, "external_id", None)
    )
    if ref is None:
        return PostingCheck.ERROR
    try:
        result = await ATSScraper.check_posting(
            ref.source, ref.external_id, source_url=getattr(job, "source_url", None)
        )
    except Exception as exc:
        logger.debug("On-demand re-check raised: %s", exc)
        return PostingCheck.ERROR
    now = datetime.now(UTC)
    if result is PostingCheck.OPEN:
        job.last_verified_at = now
        job.closed_at = None
    elif result is PostingCheck.CLOSED:
        job.closed_at = now
    return result


def recheck_is_due(job: Any, now: datetime) -> bool:
    """True when a verifiable, open role has no confirmation fresh enough to show."""
    if not is_verifiable_source(job.source or "") or job.closed_at is not None:
        return False
    verified = job.last_verified_at
    if verified is None:
        return True
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=UTC)
    return bool(now - verified > VERIFICATION_VALID_FOR)


async def refresh_if_due(session: Any, job: Any) -> None:
    """
    Re-check ``job`` now if its last confirmation is stale, and save the result.

    For the moments someone is about to read the verdict - a dossier, a public
    role page being regenerated. One bounded request; any failure leaves the
    row as it was. Never raises.
    """
    if not recheck_is_due(job, datetime.now(UTC)):
        return
    try:
        await asyncio.wait_for(reverify_job(job), timeout=ON_DEMAND_RECHECK_SECONDS)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.debug("On-demand re-check skipped: %s", exc)


# ── reading it back ──────────────────────────────────────────────────────────


def verdict_for(job: Any, sighting: Any | None, *, now: datetime) -> LivenessVerdict:
    """The liveness verdict for a stored job row and its role's sighting, if any."""
    return assess(
        LivenessEvidence(
            source_posted_at=getattr(job, "source_posted_at", None),
            first_seen_at=getattr(job, "first_seen_at", None),
            last_seen_at=getattr(job, "last_seen_at", None),
            last_verified_at=getattr(job, "last_verified_at", None),
            closed_at=getattr(job, "closed_at", None),
            times_reposted=int(getattr(sighting, "times_reposted", 0) or 0),
            verifiable=is_verifiable_source(getattr(job, "source", "") or ""),
        ),
        now=now,
    )


async def _load_liveness_rows(ids: list[uuid.UUID]) -> list[tuple[Any, int]]:
    """(job row, times_reposted) for each stored job id, in one query."""
    from sqlalchemy import select

    from storage.database import AsyncSessionLocal
    from storage.models import Job

    async with AsyncSessionLocal() as session:
        jobs = list(
            (await session.execute(select(Job).where(Job.id.in_(ids)))).scalars()
        )
        sightings = await sightings_for(session, [(j.company_id, j.title) for j in jobs])
        return [
            (
                job,
                int(
                    getattr(
                        sightings.get((job.company_id, title_key(job.title))),
                        "times_reposted",
                        0,
                    )
                    or 0
                ),
            )
            for job in jobs
        ]


async def load_verdicts(job_ids: list[str], *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """
    Verdicts for a page of search results, keyed by job id. Never raises.

    Ids that are not UUIDs belong to results scraped a moment ago and not yet
    stored; there is no evidence for them, so they are simply absent from the
    result rather than given a guess.
    """
    ids: list[uuid.UUID] = []
    for raw in job_ids:
        try:
            ids.append(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
    if not ids:
        return {}
    moment = now or datetime.now(UTC)
    try:
        rows = await _load_liveness_rows(ids)
    except Exception as exc:
        logger.debug("Could not load liveness for search results: %s", exc)
        return {}
    return {
        str(job.id): verdict_to_dict(
            verdict_for(job, SimpleNamespace(times_reposted=reposts), now=moment)
        )
        for job, reposts in rows
    }


def verdict_to_dict(verdict: LivenessVerdict) -> dict[str, Any]:
    return {
        "state": verdict.state.value,
        "headline": verdict.headline,
        "evidence": list(verdict.evidence),
        "open_days": verdict.open_days,
    }
