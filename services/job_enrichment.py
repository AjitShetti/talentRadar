"""
services/job_enrichment.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Turn structurally-stored jobs into fully-parsed ones, under a daily budget.

``services/job_persistence.py`` writes live-scraped postings with no LLM call
at all - that is what makes a 60-result search free. The cost has to land
somewhere, so it lands here, spent deliberately rather than per-scrape:

* **On demand.** Opening a job enriches that one job. This is where the spend
  is most justified: someone is actually reading it.
* **Scheduled.** The overnight sweep enriches a batch of the newest raw rows.

Both go through :func:`_consume_budget`, a counter in the shared cache keyed by
date. When the day's budget is gone, enrichment stops and rows stay ``raw`` -
which costs detail on a job card, not the job itself. A free Groq tier is a
rate limit, not an overdraft, so the alternative to a budget is the whole
feature failing at an unpredictable time of day.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

#: LLM parses per day across the whole deployment. Sized for a free Groq tier
#: with room left for the features that need a model to answer at all -
#: intent classification, the copilot, mock interviews.
DAILY_ENRICHMENT_BUDGET = 200

#: How many rows one scheduled pass will attempt.
SCHEDULED_BATCH_SIZE = 25

_BUDGET_KEY_PREFIX = "tr:budget:llm:"
_BUDGET_TTL_SECONDS = 48 * 3600


def _budget_key(day: date | None = None) -> str:
    return f"{_BUDGET_KEY_PREFIX}{(day or date.today()).isoformat()}"


async def remaining_budget() -> int:
    """How many enrichment calls are left today."""
    from services.cache_backend import CacheBackend

    used = int(await CacheBackend.get(_budget_key()) or 0)
    return max(0, DAILY_ENRICHMENT_BUDGET - used)


async def _consume_budget() -> bool:
    """
    Claim one unit of today's budget.

    Increments first and compares after, so two concurrent requests cannot
    both see the last unit as free. A cache failure returns True - the budget
    is a cost guard, and losing it should not disable enrichment outright.
    """
    from services.cache_backend import CacheBackend

    try:
        used = await CacheBackend.incr(_budget_key(), _BUDGET_TTL_SECONDS)
    except Exception as exc:
        logger.debug("Could not read the enrichment budget: %s", exc)
        return True

    if used > DAILY_ENRICHMENT_BUDGET:
        logger.info("Daily LLM enrichment budget (%d) exhausted", DAILY_ENRICHMENT_BUDGET)
        return False
    return True


async def enrich_job(job_id: str) -> bool:
    """
    LLM-parse one stored job in place.

    Returns True when the row was enriched. A row that cannot be parsed is
    marked ``failed`` rather than left ``raw``, so the scheduled pass does not
    retry it every night forever.
    """
    if not await _consume_budget():
        return False

    try:
        import asyncio
        import uuid as uuid_mod

        from domain.enums import SeniorityLevel
        from ingestion.parsers.jd_parser import JDParser
        from storage.database import AsyncSessionLocal
        from storage.repository import UnitOfWork

        async with AsyncSessionLocal() as session:
            async with UnitOfWork(session) as uow:
                job = await uow.jobs.get(uuid_mod.UUID(str(job_id)))
                if job is None or job.enrichment_status == "enriched":
                    return False

                # There is often little to parse - a scraped row may be title,
                # company and location only. Feeding the parser what we have
                # still recovers skills and seniority from the title.
                company = job.company.name if getattr(job, "company", None) else ""
                source_text = job.description_clean or f"{job.title} at {company} in {job.location_raw or 'India'}"

                # JDParser.parse_jd is synchronous and calls Groq over the
                # network. Run on the event loop it would stall every other
                # request on this single-worker instance for the duration.
                parsed = await asyncio.to_thread(
                    lambda: JDParser().parse_jd(source_text, source_url=job.source_url or "")
                )

                if parsed is None:
                    await uow.jobs.update(job.id, enrichment_status="failed")
                    await session.commit()
                    return False

                updates: dict[str, Any] = {"enrichment_status": "enriched"}
                # Only fill gaps. The scraped values came from the board
                # itself and are more trustworthy than an inference drawn from
                # a thin description.
                if parsed.skills and not job.skills:
                    updates["skills"] = parsed.skills
                if parsed.salary and not job.salary_raw:
                    updates["salary_raw"] = parsed.salary
                if parsed.salary_min is not None and job.salary_min is None:
                    updates["salary_min"] = parsed.salary_min
                if parsed.salary_max is not None and job.salary_max is None:
                    updates["salary_max"] = parsed.salary_max
                if parsed.seniority and not job.seniority:
                    try:
                        updates["seniority"] = SeniorityLevel(parsed.seniority)
                    except ValueError:
                        # The model returned a band that is not in the enum.
                        # Dropping it is right: a bad seniority silently
                        # excludes the job from every filtered search.
                        logger.debug("Ignoring unknown seniority %r", parsed.seniority)

                await uow.jobs.update(job.id, **updates)
                await session.commit()
                return True

    except Exception as exc:
        logger.warning("Could not enrich job %s: %s", job_id, exc)
        return False


async def enrich_pending(limit: int = SCHEDULED_BATCH_SIZE) -> dict[str, int]:
    """
    Enrich a batch of the newest structural rows.

    Newest first on purpose: a posting from this week is the one someone might
    still apply to, and an old raw row is more likely to be pruned by
    ``services/job_retention.py`` than read.
    """
    summary = {"attempted": 0, "enriched": 0, "budget_remaining": 0}

    available = await remaining_budget()
    if available <= 0:
        logger.info("Skipping scheduled enrichment: daily budget exhausted")
        return summary

    try:
        from sqlalchemy import text

        from storage.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT id FROM jobs
                    WHERE enrichment_status = 'raw'
                    ORDER BY created_at DESC
                    LIMIT :cap
                    """
                ),
                {"cap": min(limit, available)},
            )
            job_ids = [str(row[0]) for row in rows.fetchall()]
    except Exception as exc:
        logger.warning("Could not list jobs pending enrichment: %s", exc)
        return summary

    for job_id in job_ids:
        summary["attempted"] += 1
        if await enrich_job(job_id):
            summary["enriched"] += 1

    summary["budget_remaining"] = await remaining_budget()
    if summary["attempted"]:
        logger.info("Scheduled enrichment: %s", summary)
    return summary
