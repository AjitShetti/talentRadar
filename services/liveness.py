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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from domain.liveness import (
    LivenessEvidence,
    LivenessVerdict,
    SightingState,
    assess,
    is_verifiable_source,
    next_sighting,
    title_key,
)
from ingestion.scrapers.ats_scraper import ATSScraper, PostingCheck

logger = logging.getLogger(__name__)

# Sized for one scheduled run on a free instance: ~150 small JSON requests,
# five at a time, finishes well inside the HTTP timeout of the cron's curl.
DEFAULT_BATCH_LIMIT = 150
MAX_BATCH_LIMIT = 500
RECHECK_CONCURRENCY = 5


@dataclass(frozen=True)
class Candidate:
    """One posting due a re-check."""

    job_id: uuid.UUID
    source: str
    external_id: str


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

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                """
                SELECT j.id, j.source, j.external_id
                FROM jobs j
                WHERE j.closed_at IS NULL
                  AND j.external_id IS NOT NULL
                  AND (j.source LIKE 'greenhouse:%'
                       OR j.source LIKE 'lever:%'
                       OR j.source LIKE 'ashby:%')
                ORDER BY
                  EXISTS (SELECT 1 FROM job_applications a WHERE a.job_id = j.id) DESC,
                  j.last_verified_at ASC NULLS FIRST
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return [
            Candidate(job_id=row[0], source=row[1], external_id=row[2]) for row in rows.all()
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
                update(Job).where(Job.id.in_(open_ids)).values(last_verified_at=now)
            )
        if closed_ids:
            await session.execute(
                update(Job).where(Job.id.in_(closed_ids)).values(closed_at=now)
            )
        await session.commit()


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

    # Each Ashby board is read once, up front, so six postings from one
    # company cost one request rather than six racing ones.
    ashby_boards: dict[str, set[str] | None] = {}
    for slug in sorted(
        {c.source.partition(":")[2] for c in candidates if c.source.startswith("ashby:")}
    ):
        ashby_boards[slug] = await ATSScraper.fetch_ashby_open_ids(slug)

    gate = asyncio.Semaphore(RECHECK_CONCURRENCY)

    async def check(candidate: Candidate) -> PostingCheck:
        async with gate:
            try:
                return await ATSScraper.check_posting(
                    candidate.source, candidate.external_id, ashby_boards=ashby_boards
                )
            except Exception as exc:
                logger.debug("Re-check of %s raised: %s", candidate.external_id, exc)
                return PostingCheck.ERROR

    results = await asyncio.gather(*(check(c) for c in candidates))

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
    if not is_verifiable_source(job.source or "") or not job.external_id:
        return PostingCheck.ERROR
    try:
        result = await ATSScraper.check_posting(job.source, job.external_id)
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
