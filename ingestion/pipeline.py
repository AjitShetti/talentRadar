"""
ingestion/pipeline.py
~~~~~~~~~~~~~~~~~~~~~
Shared ingestion pipeline: raw results → LLM parse → Postgres + ChromaDB.

Refactored out of ``ingestion/tasks.py`` so that any source (Tavily, ATS
crawler, Greenhouse/Lever/Ashby/Cutshort) can reuse the same persist logic.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.database import AsyncSessionLocal
from storage.models import IngestionStatus
from storage.repository import UnitOfWork

from ingestion.embeddings.chroma_store import ChromaJobStore
from ingestion.parsers.jd_parser import JDParser
from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult

logger = logging.getLogger(__name__)


def _stable_id(text: str) -> str:
    """MD5 fingerprint of a URL or title+company string used as external_id."""
    return hashlib.md5(text.encode()).hexdigest()


def _company_domain(company_name: str) -> str:
    """Create a deterministic pseudo-domain key from a company name."""
    import re
    slug = re.sub(r"[^\w]", "-", company_name.lower()).strip("-")
    return f"{slug}.talentradar.internal"


async def persist_parsed(
    parsed: list[ParsedJobDescription],
    *,
    source: str,
    run_id: str,
) -> dict[str, int]:
    """
    Upsert parsed job descriptions into Postgres + ChromaDB.

    Returns per-step counts: {"inserted", "updated", "skipped", "embedded"}.
    """
    from ingestion.scrapers.tavily_client import detect_source_from_url

    inserted = updated = skipped = embedded = 0
    chroma_items: list[dict[str, Any]] = []

    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)

        ingestion_run = await uow.ingestion_runs.create(
            source=source,
            status=IngestionStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
            run_config={"pipeline_run_id": run_id},
        )
        await session.commit()

        try:
            for data in parsed:
                job_source = (
                    detect_source_from_url(data.source_url or "")
                    if data.source_url else source
                )

                company_slug = _company_domain(data.company)
                company, _ = await uow.companies.upsert_by_domain(
                    domain=company_slug,
                    defaults={"name": data.company},
                )

                external_id = _stable_id(data.source_url or data.title + data.company)
                job_kwargs = data.to_job_kwargs()
                job_kwargs.update(
                    {
                        "company_id": company.id,
                        "ingestion_run_id": ingestion_run.id,
                    }
                )

                job, created = await uow.jobs.upsert_by_external_id(
                    external_id=external_id,
                    source=job_source,
                    defaults=job_kwargs,
                )
                if created:
                    inserted += 1
                else:
                    updated += 1

                chroma_items.append({
                    "job_id": external_id,
                    "text": (job_kwargs.get("description_clean") or data.raw_text)[:4096],
                    "metadata": {
                        "title": data.title,
                        "company": data.company,
                        "location": data.location or "",
                        "is_remote": data.is_remote,
                        "seniority": data.seniority or "",
                        "employment_type": data.employment_type or "",
                        "skills_str": ", ".join(data.skills),
                        "source_url": data.source_url or "",
                        "salary": data.salary or "",
                        "source": job_source,
                    },
                    "internal_job_id": job.id,
                })

            await session.commit()

            if chroma_items:
                store = ChromaJobStore()
                embedded = store.add_batch(chroma_items)
                for item in chroma_items:
                    await uow.jobs.set_embedding_id(item["internal_job_id"], item["job_id"])
                await session.commit()

            await uow.ingestion_runs.finish(
                ingestion_run.id,
                status=IngestionStatus.SUCCESS,
                jobs_discovered=len(parsed),
                jobs_inserted=inserted,
                jobs_updated=updated,
                jobs_skipped=skipped,
            )
            await session.commit()

        except Exception as exc:
            import traceback
            logger.exception("Pipeline failed run_id=%s: %s", run_id, exc)
            try:
                await uow.ingestion_runs.fail(
                    ingestion_run.id,
                    error_message=str(exc),
                    error_trace={"traceback": traceback.format_exc()},
                )
                await session.commit()
            except Exception as finalize_exc:
                logger.error("Could not finalize failed run: %s", finalize_exc)
            raise

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "embedded": embedded}


async def run_pipeline(
    raw_results: list[RawJobResult],
    *,
    source: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute the shared parse → persist pipeline for a batch of raw results.

    Returns summary counts; raises on hard failures after finalising the
    ingestion_run record.
    """
    run_id = run_id or str(uuid.uuid4())
    if not raw_results:
        return {"fetched": 0, "parsed": 0, "inserted": 0, "updated": 0, "embedded": 0}

    parser = JDParser()
    parsed = parser.batch_parse(raw_results)
    counts = await persist_parsed(parsed, source=source, run_id=run_id)
    return {
        "fetched": len(raw_results),
        "parsed": len(parsed),
        **counts,
    }
