"""
services/job_retention.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Keep the jobs table inside a 0.5 GB database.

Live sourcing changes the arithmetic. Before it, rows arrived only from
deliberate ingestion runs and the table grew slowly enough to ignore. Now
every search that misses the index can add ~60 rows, so unbounded retention
ends in a full disk - and on Neon's free tier a full disk does not degrade
gracefully, it fails writes. Retention is therefore a correctness
requirement, not housekeeping.

Two rules, in order:

1. **Age.** Structural rows (``enrichment_status='raw'``) older than
   ``RAW_RETENTION_DAYS`` go, unless somebody applied to them. A stale scraped
   posting is worse than no posting: the link is usually dead.
2. **Pressure.** Above ``STORAGE_HIGH_WATER_BYTES``, oldest-raw-first rows go
   regardless of age, until back under budget.

Never deleted: any job with an application attached, and anything already
LLM-enriched, which cost quota to produce.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Structural rows older than this are dropped.
RAW_RETENTION_DAYS = 45

#: Prune harder above this. Neon's free tier is 0.5 GB; this leaves headroom
#: for indexes, the embeddings table and WAL rather than waiting for the wall.
STORAGE_HIGH_WATER_BYTES = 400 * 1024 * 1024

#: Ceiling on one pass, so a prune cannot hold a transaction open long enough
#: to matter to a request on a shared-CPU instance.
MAX_DELETIONS_PER_RUN = 5000


async def database_size_bytes(session: Any) -> int:
    """Current database size, or 0 if the backend cannot report it (e.g. SQLite)."""
    try:
        result = await session.execute(text("SELECT pg_database_size(current_database())"))
        return int(result.scalar() or 0)
    except Exception as exc:
        logger.debug("Could not read database size: %s", exc)
        return 0


async def prune_stale_jobs() -> dict[str, Any]:
    """
    Delete expired structural rows, and more if the database is under pressure.

    Returns a summary dict. Never raises: this runs from the scheduler, where
    an exception would take down the whole overnight pass.
    """
    summary: dict[str, Any] = {
        "deleted_by_age": 0,
        "deleted_by_pressure": 0,
        "size_before": 0,
        "size_after": 0,
        "ran": False,
    }

    try:
        from storage.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            summary["size_before"] = await database_size_bytes(session)

            # Rule 1: age. The NOT EXISTS clause is the important part - a job
            # someone applied to is part of their history and outlives any
            # retention window.
            aged = await session.execute(
                text(
                    """
                    DELETE FROM jobs
                    WHERE id IN (
                        SELECT j.id FROM jobs j
                        WHERE j.enrichment_status = 'raw'
                          AND j.created_at < NOW() - (:days || ' days')::interval
                          AND NOT EXISTS (
                              SELECT 1 FROM job_applications a WHERE a.job_id = j.id
                          )
                        LIMIT :cap
                    )
                    """
                ),
                {"days": RAW_RETENTION_DAYS, "cap": MAX_DELETIONS_PER_RUN},
            )
            summary["deleted_by_age"] = aged.rowcount or 0
            await session.commit()

            # Rule 2: pressure.
            size = await database_size_bytes(session)
            if size > STORAGE_HIGH_WATER_BYTES:
                remaining = MAX_DELETIONS_PER_RUN - summary["deleted_by_age"]
                if remaining > 0:
                    logger.warning(
                        "Database at %.0f MB, above the %.0f MB high-water mark; "
                        "pruning oldest structural rows",
                        size / 1024 / 1024,
                        STORAGE_HIGH_WATER_BYTES / 1024 / 1024,
                    )
                    pressured = await session.execute(
                        text(
                            """
                            DELETE FROM jobs
                            WHERE id IN (
                                SELECT j.id FROM jobs j
                                WHERE j.enrichment_status = 'raw'
                                  AND NOT EXISTS (
                                      SELECT 1 FROM job_applications a WHERE a.job_id = j.id
                                  )
                                ORDER BY j.created_at ASC
                                LIMIT :cap
                            )
                            """
                        ),
                        {"cap": remaining},
                    )
                    summary["deleted_by_pressure"] = pressured.rowcount or 0
                    await session.commit()

            summary["size_after"] = await database_size_bytes(session)
            summary["ran"] = True

    except Exception as exc:
        logger.warning("Job retention pass failed: %s", exc)
        return summary

    total = summary["deleted_by_age"] + summary["deleted_by_pressure"]
    if total:
        logger.info("Retention: removed %d stale job(s) — %s", total, summary)
    return summary
