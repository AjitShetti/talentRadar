"""
services/sweep.py
~~~~~~~~~~~~~~~~~
The overnight sweep: fetch fresh postings, then rank them against each user.

``services/job_matching.py`` already ranks a user's target roles against the
``jobs`` table and caches the top 3 for the day, and ``api/main.py`` already
schedules it. What was missing is the half that makes the dashboard's "fetched
while you were away" true: nothing ever *ingested* on a schedule, so the daily
scan re-ranked a table that only changed when an admin hit ``/ingest/trigger``
by hand. The same three postings surfaced every morning.

This module closes that loop by running the two existing entry points in order:

    dispatch_ingestion()                — discover → parse → persist → embed
    run_daily_matching_for_all_users()  — rank the fresh rows per user

    run_overnight_sweep()  — the scheduler's entry point (does both)
    get_last_sweep()       — what the last sweep did, for the dashboard manifest

Nothing here may raise: it runs unattended, inside the API process, and a failed
sweep must leave yesterday's cached matches in place rather than take the app
down with it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from config.settings import get_settings
from services.job_matching import run_daily_matching_for_all_users
from storage.database import AsyncSessionLocal
from storage.models import IngestionRun, Profile

logger = logging.getLogger(__name__)


async def collect_target_roles() -> list[str]:
    """
    The union of every onboarded user's target roles, de-duplicated
    case-insensitively and capped.

    Ingestion cost scales with the number of roles searched, so the cap is what
    keeps one user with a long role list from setting the whole batch's budget.
    """
    session = AsyncSessionLocal()
    try:
        rows = (
            (
                await session.execute(
                    select(Profile.target_roles).where(Profile.onboarding_completed.is_(True))
                )
            )
            .scalars()
            .all()
        )
    finally:
        await session.close()

    seen: dict[str, str] = {}
    for roles in rows:
        for role in roles or []:
            cleaned = (role or "").strip()
            if cleaned and cleaned.lower() not in seen:
                seen[cleaned.lower()] = cleaned

    return list(seen.values())[: get_settings().sweep_max_roles]


async def run_overnight_sweep() -> dict[str, Any]:
    """
    Ingest fresh postings for everyone's target roles, then re-rank per user.

    Returns a summary of what happened. Never raises — the matching pass still
    runs when ingestion fails, so a scraper outage degrades the sweep to
    "re-rank what we already have" instead of skipping the day.
    """
    settings = get_settings()
    started = datetime.now()
    summary: dict[str, Any] = {"started_at": started.isoformat(), "ingested": None}

    if settings.sweep_ingest_enabled:
        roles = await collect_target_roles()
        if roles:
            try:
                # The same parse → persist → embed path /ingest/trigger uses.
                # Deliberately not RealtimeScraperEngine.search_all, which caches
                # to Redis for the live search UI and never writes to Postgres.
                from ingestion.dispatcher import dispatch_ingestion

                result = await dispatch_ingestion(
                    roles=roles,
                    max_results_per_query=settings.sweep_results_per_role,
                )
                summary["ingested"] = {
                    "roles": roles,
                    "sources": result.get("sources", []),
                    "fetched": result.get("total_fetched", 0),
                    "inserted": result.get("inserted", 0),
                    "updated": result.get("updated", 0),
                }
                logger.info(
                    "Overnight sweep ingested %s postings across %s roles (%s new)",
                    result.get("total_fetched", 0),
                    len(roles),
                    result.get("inserted", 0),
                )
            except Exception:
                logger.warning("Overnight sweep ingestion failed", exc_info=True)
        else:
            logger.info("Overnight sweep: no target roles across any profile, skipping ingestion")
    else:
        logger.info("Overnight sweep: ingestion disabled (SWEEP_INGEST_ENABLED=false)")

    try:
        await run_daily_matching_for_all_users()
        summary["matched"] = True
    except Exception:
        logger.warning("Overnight sweep matching failed", exc_info=True)
        summary["matched"] = False

    summary["finished_at"] = datetime.now().isoformat()
    return summary


async def get_last_sweep() -> dict[str, Any] | None:
    """
    What the most recent completed ingestion run did, for the dashboard's
    "fetched while you were away" manifest.

    Reads the existing ``ingestion_runs`` audit log rather than adding a table —
    it already records the source list, the discovery count and the finish time.
    Returns ``None`` when nothing has ever run, which the UI reads as "no sweep
    yet" rather than "a sweep that found nothing".
    """
    session = AsyncSessionLocal()
    try:
        run = (
            (
                await session.execute(
                    select(IngestionRun)
                    .where(IngestionRun.finished_at.is_not(None))
                    .order_by(IngestionRun.finished_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    except Exception:
        logger.warning("Could not read the last sweep", exc_info=True)
        return None
    finally:
        await session.close()

    if run is None:
        return None

    # ``dispatch_ingestion`` records the run under a comma-joined source list,
    # so the board count is the length of that list, not a row count.
    sources = [s for s in (run.source or "").split(",") if s]
    return {
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "sources": sources,
        "boards": len(sources),
        "postings_read": run.jobs_discovered or 0,
        "postings_added": run.jobs_inserted or 0,
    }
