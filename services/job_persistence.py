"""
services/job_persistence.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Persist live-scraped postings without paying an LLM call per job.

Live search used to write nothing: ``RealtimeScraperEngine`` filled a cache
and returned, so a posting found by one search was invisible to the next and
the whole index stayed as empty as the day it was deployed. This module is the
missing write path.

What makes it affordable
------------------------
``ingestion/pipeline.py`` parses every posting with Groq before storing it.
That is the right shape for a curated bulk ingest and the wrong shape here: a
single live search returns ~60 postings, which would be ~60 LLM calls per
search and would exhaust a free Groq tier in a handful of queries.

So persistence is split in two:

* **Structural write (this module).** Uses only fields the scraper already
  returned - title, company, URL, location, skills - plus a local ONNX
  embedding. Zero API calls, and the embedding is what makes these rows
  findable by semantic search at all.
* **LLM enrichment (deferred).** Rows land as ``enrichment_status="raw"`` and
  are enriched only when a user opens one, or by the scheduled pass under a
  daily budget. Enrichment fills in the fields that genuinely need a model.

Everything here degrades. No database, no vector store, a failed embed: the
search that triggered the write still returns its results, because this runs
as a background task and its failure must never surface to the user.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from domain.entities import Job
from ingestion.validation import validate_job_url

logger = logging.getLogger(__name__)

# Descriptions are truncated before they reach Postgres. Neon's free tier is
# 0.5 GB total and a scraped description is mostly boilerplate; the first few
# KB carry the signal that matters for search.
MAX_DESCRIPTION_CHARS = 4096

# Cross-search URL dedupe, so a posting seen by one query is not re-examined
# by the next. Seven days, because a posting that vanishes and returns is
# effectively new.
SEEN_URL_TTL_SECONDS = 7 * 24 * 3600
SEEN_URL_PREFIX = "tr:seen:"


@dataclass
class PersistSummary:
    """What one persistence run did. Returned for logging and tests."""

    received: int = 0
    rejected_url: int = 0
    duplicate: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "rejected_url": self.rejected_url,
            "duplicate": self.duplicate,
            "inserted": self.inserted,
            "updated": self.updated,
            "failed": self.failed,
        }


def _stable_external_id(job: Job) -> str:
    """
    A deterministic id for this posting.

    Keyed on the source URL, falling back to title+company. It must not
    involve a random value: an unstable external_id re-inserts the same
    posting on every scrape, which on a 0.5 GB database is a storage leak.
    """
    company = (job.extra_metadata or {}).get("company_name", "")
    basis = job.source_url or f"{job.title}{company}"
    return hashlib.md5(basis.encode("utf-8")).hexdigest()


def _company_domain(company_name: str) -> str:
    """Deterministic pseudo-domain key, matching ingestion/pipeline.py."""
    slug = re.sub(r"[^\w]", "-", company_name.lower()).strip("-")
    return f"{slug}.talentradar.internal"


def _seen_key(url: str) -> str:
    return f"{SEEN_URL_PREFIX}{hashlib.md5(url.encode('utf-8')).hexdigest()}"


def build_job_kwargs(job: Job) -> dict[str, Any]:
    """
    Map a scraped :class:`Job` onto ORM column values.

    Only fields the scraper already produced are used. Nothing here calls a
    model, an API, or the network.
    """
    description = (job.description_clean or "")[:MAX_DESCRIPTION_CHARS]
    return {
        "source_url": job.source_url,
        "title": job.title,
        "status": job.status,
        "employment_type": job.employment_type,
        "seniority": job.seniority,
        "location_raw": job.location_raw,
        "country": job.country,
        "city": job.city,
        "is_remote": job.is_remote,
        "salary_raw": job.salary_raw,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "skills": job.skills or [],
        "tags": job.tags or [],
        "description_clean": description,
        "posted_at": job.posted_at,
        # Marks the row as structurally-complete but not LLM-parsed. The
        # enrichment pass and the job-detail endpoint both key off this.
        "enrichment_status": "raw",
    }


def embedding_text(job: Job) -> str:
    """
    The text a raw job is embedded on.

    A scraped posting usually has no description, so embedding that alone
    would produce a near-empty vector and the row would never surface in a
    semantic search. Title, company, skills and location are what a raw row
    actually knows.
    """
    company = (job.extra_metadata or {}).get("company_name", "")
    parts = [
        job.title or "",
        company,
        ", ".join(job.skills or []),
        job.location_raw or job.city or "",
        (job.description_clean or "")[:512],
    ]
    return " | ".join(p for p in parts if p).strip()


async def persist_live_jobs(jobs: list[Job], *, source_label: str = "live_search") -> PersistSummary:
    """
    Write live-scraped jobs to Postgres and embed them.

    Never raises. This runs as a fire-and-forget background task behind a
    user's search, and a persistence failure must not affect the response
    that user already received.
    """
    summary = PersistSummary(received=len(jobs))
    if not jobs:
        return summary

    from services.cache_backend import CacheBackend

    # 1. Reject anything that is not an individual job posting, before it
    #    reaches the database. foundit.in, instahyre.com and freshersworld.com
    #    were absent from the allowlist, so this step silently discarded every
    #    row from them until they were added.
    candidates: list[Job] = []
    for job in jobs:
        url = job.source_url or ""
        valid, reason = validate_job_url(url)
        if not valid:
            summary.rejected_url += 1
            logger.debug("Rejected %s: %s", url[:96], reason)
            continue
        candidates.append(job)

    # 2. Drop URLs already written by a recent search. This is the cheap
    #    filter; the database's own unique key is the correct one.
    fresh: list[Job] = []
    for job in candidates:
        try:
            if await CacheBackend.exists(_seen_key(job.source_url or "")):
                summary.duplicate += 1
                continue
        except Exception:  # noqa: BLE001 - the cache is optional
            pass
        fresh.append(job)

    if not fresh:
        logger.info("Live persistence: nothing new (%s)", summary.as_dict())
        return summary

    # 3. Write. One session, one commit.
    embedding_items: list[dict[str, Any]] = []
    try:
        from storage.database import AsyncSessionLocal
        from storage.repository import UnitOfWork

        async with AsyncSessionLocal() as session:
            async with UnitOfWork(session) as uow:
                for job in fresh:
                    try:
                        company_name = (job.extra_metadata or {}).get("company_name") or "Company"
                        company, _ = await uow.companies.upsert_by_domain(
                            domain=_company_domain(company_name),
                            defaults={"name": company_name},
                        )

                        external_id = job.external_id or _stable_external_id(job)
                        kwargs = build_job_kwargs(job)
                        kwargs["company_id"] = company.id

                        stored, created = await uow.jobs.upsert_by_external_id(
                            external_id=external_id,
                            source=job.source or source_label,
                            defaults=kwargs,
                        )
                        if created:
                            summary.inserted += 1
                        else:
                            summary.updated += 1

                        embedding_items.append(
                            {
                                "job_id": external_id,
                                "text": embedding_text(job),
                                "metadata": {
                                    "title": job.title or "",
                                    "company": company_name,
                                    "location": job.location_raw or "",
                                    "country": job.country or "",
                                    "city": job.city or "",
                                    "is_remote": bool(job.is_remote),
                                    "seniority": job.seniority.value if job.seniority else "",
                                    "employment_type": (
                                        job.employment_type.value if job.employment_type else ""
                                    ),
                                    "skills_str": ", ".join(job.skills or []),
                                    "source_url": job.source_url or "",
                                    "source": job.source or source_label,
                                },
                                "internal_job_id": stored.id,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001 - one bad row must not lose the batch
                        summary.failed += 1
                        logger.debug("Could not persist %r: %s", (job.title or "")[:60], exc)

                await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live persistence could not write to the database: %s", exc)
        return summary

    # 4. Mark URLs seen only after a successful write, so a failed run retries.
    for job in fresh:
        try:
            await CacheBackend.set(_seen_key(job.source_url or ""), "1", SEEN_URL_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass

    # 5. Embed. The vector store is optional by design: without it the rows
    #    are still in Postgres and still reachable by relational search.
    if embedding_items:
        await _embed_quietly(embedding_items)

    logger.info("Live persistence: %s", summary.as_dict())
    return summary


async def _embed_quietly(items: list[dict[str, Any]]) -> None:
    """
    Embed persisted jobs, treating an absent vector store as ordinary.

    On success each row's ``embedding_id`` is recorded. That write is not
    bookkeeping: without it ``jobs.embedding_id`` points at a document
    nothing stored, which is how a row ends up believing it is searchable
    when it is not.
    """
    try:
        from ingestion.embeddings.vector_store import get_vector_store

        store = get_vector_store()
        if store is None:
            logger.debug("No vector store configured; %d jobs stay relationally searchable", len(items))
            return

        embedded = await store.aadd_batch(items)
        if not embedded:
            return

        from storage.database import AsyncSessionLocal
        from storage.repository import UnitOfWork

        async with AsyncSessionLocal() as session:
            async with UnitOfWork(session) as uow:
                for item in items:
                    await uow.jobs.set_embedding_id(item["internal_job_id"], item["job_id"])
                await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not embed %d live jobs: %s", len(items), exc)
