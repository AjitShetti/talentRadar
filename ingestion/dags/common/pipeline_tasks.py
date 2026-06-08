"""
ingestion/dags/common/pipeline_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Common tasks for the fetch and parse DAG.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the repo root importable inside Airflow tasks
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # .../talentRadar/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from airflow.decorators import task

logger = logging.getLogger(__name__)

_SOURCE_NAME = "tavily"

def _company_domain(company_name: str) -> str:
    """
    Create a deterministic pseudo-domain key from a company name.
    """
    import re
    slug = re.sub(r"[^\w]", "-", company_name.lower()).strip("-")
    return f"{slug}.talentradar.internal"

def _stable_id(text: str) -> str:
    """MD5 fingerprint of a URL or title+company string used as external_id."""
    return hashlib.md5(text.encode()).hexdigest()

@task(multiple_outputs=True)
def fetch_raw(
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    max_results_per_query: int | None = None,
    **context: Any,
) -> dict[str, Any]:
    from ingestion.scrapers.tavily_client import TavilyJobScraper

    # Resolve params — dag_run.conf overrides explicit args, which overrides params
    dag_run_conf: dict[str, Any] = context.get("dag_run").conf if context.get("dag_run") and context.get("dag_run").conf else {}
    _params = context.get("params", {})
    
    _roles = dag_run_conf.get("roles") or roles or _params.get("roles") or []
    _locations = dag_run_conf.get("locations") or locations or _params.get("locations") or []
    _max_results = int(dag_run_conf.get("max_results_per_query") or max_results_per_query or _params.get("max_results_per_query") or 5)

    run_id: str = context["run_id"]  # Airflow run ID
    logger.info(
        "fetch_raw | run_id=%s | roles=%s | locations=%s | max=%d",
        run_id, _roles, _locations, _max_results,
    )

    all_paths: list[str] = []
    total_fetched = 0

    with TavilyJobScraper() as scraper:
        for role in _roles:
            for location in _locations:
                try:
                    results = scraper.search_jobs(role, location, count=_max_results)
                    paths = scraper.save_raw(
                        results,
                        run_id=run_id,
                        role=role,
                        location=location,
                    )
                    all_paths.extend(str(p) for p in paths)
                    total_fetched += len(results)
                    logger.info(
                        "Fetched %d results for role=%r location=%r",
                        len(results), role, location,
                    )
                except Exception as exc:
                    logger.error(
                        "fetch_raw failed for role=%r location=%r: %s",
                        role, location, exc,
                    )

    logger.info(
        "fetch_raw complete: total_fetched=%d, files=%d",
        total_fetched, len(all_paths),
    )
    return {
        "run_id": run_id,
        "file_paths": all_paths,
        "total_fetched": total_fetched,
    }

@task(multiple_outputs=True)
def parse_with_llm(upstream: dict[str, Any], **context: Any) -> dict[str, Any]:
    from ingestion.parsers.jd_parser import JDParser
    from ingestion.parsers.schemas import RawJobResult

    file_paths: list[str] = upstream.get("file_paths", [])
    run_id: str = upstream.get("run_id", context["run_id"])

    logger.info("parse_with_llm | %d files to parse", len(file_paths))

    if not file_paths:
        logger.warning("No raw files to parse — skipping LLM step.")
        return {"parsed_jobs": [], "run_id": run_id, "failed_count": 0}

    raw_results: list[RawJobResult] = []
    for fp in file_paths:
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
            result = RawJobResult(
                title=data.get("title", ""),
                url=data.get("url", ""),
                content=data.get("content", ""),
                score=float(data.get("score", 0.0)),
                published_date=data.get("published_date"),
                raw_content=data.get("raw_content"),
            )
            raw_results.append(result)
        except Exception as exc:
            logger.warning("Could not load raw file %s: %s", fp, exc)

    parser = JDParser()
    parsed = parser.batch_parse(raw_results)
    failed_count = len(raw_results) - len(parsed)

    logger.info(
        "parse_with_llm complete: parsed=%d, failed=%d",
        len(parsed), failed_count,
    )

    return {
        "parsed_jobs": [p.model_dump() for p in parsed],
        "run_id": run_id,
        "failed_count": failed_count,
    }

@task(multiple_outputs=True)
def save_to_postgres(upstream: dict[str, Any], **context: Any) -> dict[str, Any]:
    import asyncio
    from ingestion.parsers.schemas import ParsedJobDescription
    from storage.database import AsyncSessionLocal
    from storage.models import IngestionStatus
    from storage.repository import UnitOfWork

    parsed_dicts: list[dict[str, Any]] = upstream.get("parsed_jobs", [])
    run_id: str = upstream.get("run_id", context["run_id"])

    logger.info("save_to_postgres | %d jobs to upsert", len(parsed_dicts))

    async def _upsert_all() -> dict[str, int]:
        inserted = updated = skipped = 0

        async with AsyncSessionLocal() as session:
            uow = UnitOfWork(session)

            ingestion_run = await uow.ingestion_runs.create(
                source=_SOURCE_NAME,
                status=IngestionStatus.RUNNING,
                started_at=datetime.now(tz=timezone.utc),
                run_config={"airflow_run_id": run_id},
            )
            await session.commit()

            for data in parsed_dicts:
                try:
                    pjd = ParsedJobDescription(**data)
                except Exception as exc:
                    logger.warning("Skipping invalid parsed job: %s", exc)
                    skipped += 1
                    continue

                company_slug = _company_domain(pjd.company)
                company, _ = await uow.companies.upsert_by_domain(
                    domain=company_slug,
                    defaults={"name": pjd.company},
                )

                external_id = _stable_id(pjd.source_url or pjd.title + pjd.company)
                job_kwargs = pjd.to_job_kwargs()
                job_kwargs.update(
                    {
                        "company_id": company.id,
                        "ingestion_run_id": ingestion_run.id,
                        "source": _SOURCE_NAME,
                    }
                )

                _, created = await uow.jobs.upsert_by_external_id(
                    external_id=external_id,
                    source=_SOURCE_NAME,
                    defaults=job_kwargs,
                )
                if created:
                    inserted += 1
                else:
                    updated += 1

                await session.commit()

            await uow.ingestion_runs.finish(
                ingestion_run.id,
                status=IngestionStatus.SUCCESS,
                jobs_discovered=len(parsed_dicts),
                jobs_inserted=inserted,
                jobs_updated=updated,
                jobs_skipped=skipped,
            )
            await session.commit()

        return {"jobs_inserted": inserted, "jobs_updated": updated, "jobs_skipped": skipped}

    counters = asyncio.run(_upsert_all())
    logger.info(
        "save_to_postgres complete: inserted=%d, updated=%d, skipped=%d",
        counters["jobs_inserted"], counters["jobs_updated"], counters["jobs_skipped"],
    )
    return counters

@task(multiple_outputs=True)
def embed_to_chromadb(upstream: dict[str, Any], **context: Any) -> dict[str, Any]:
    import asyncio
    import hashlib
    from ingestion.embeddings.chroma_store import ChromaJobStore
    from ingestion.parsers.schemas import ParsedJobDescription
    from storage.database import AsyncSessionLocal
    from storage.repository import JobRepository

    parsed_dicts: list[dict[str, Any]] = upstream.get("parsed_jobs", [])

    logger.info("embed_to_chromadb | %d jobs to embed", len(parsed_dicts))

    if not parsed_dicts:
        return {"embedded": 0, "skipped": 0, "chroma_total": 0}

    store = ChromaJobStore()
    embedded = skipped = 0
    chroma_items: list[dict[str, Any]] = []

    for data in parsed_dicts:
        try:
            pjd = ParsedJobDescription(**data)
        except Exception as exc:
            logger.warning("Skipping invalid job for embedding: %s", exc)
            skipped += 1
            continue

        job_id = hashlib.md5(
            (pjd.source_url or pjd.title + pjd.company).encode()
        ).hexdigest()

        metadata: dict[str, Any] = {
            "title": pjd.title,
            "company": pjd.company,
            "location": pjd.location or "",
            "is_remote": pjd.is_remote,
            "seniority": pjd.seniority or "",
            "employment_type": pjd.employment_type or "",
            "skills_str": ", ".join(pjd.skills),
            "source_url": pjd.source_url or "",
            "salary": pjd.salary or "",
        }

        chroma_items.append({
            "job_id": job_id,
            "text": pjd.raw_text[:4096],
            "metadata": metadata,
        })

    embedded = store.add_batch(chroma_items)

    async def _backfill() -> None:
        async with AsyncSessionLocal() as session:
            repo = JobRepository(session)
            for item in chroma_items:
                ext_id = item["job_id"]
                job = await repo.get_by_external_id(ext_id, _SOURCE_NAME)
                if job:
                    await repo.set_embedding_id(job.id, item["job_id"])
            await session.commit()

    asyncio.run(_backfill())

    logger.info(
        "embed_to_chromadb complete: embedded=%d, skipped=%d, chroma_total=%d",
        embedded, skipped, store.count(),
    )
    return {"embedded": embedded, "skipped": skipped, "chroma_total": store.count()}
