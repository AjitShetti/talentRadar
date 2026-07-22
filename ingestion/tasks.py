import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery import shared_task

from ingestion.celery_app import celery_app
from ingestion.parsers.jd_parser import JDParser
from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult
from ingestion.scrapers.tavily_client import TavilyJobScraper
from ingestion.embeddings.chroma_store import ChromaJobStore
from storage.database import AsyncSessionLocal
from storage.models import IngestionStatus
from storage.repository import UnitOfWork

logger = logging.getLogger(__name__)

_SOURCE_NAME = "tavily_crawler"

DEFAULT_INDIAN_LOCATIONS = [
    "Remote",
    "Bangalore",
    "Mumbai",
    "Delhi",
    "Hyderabad",
    "Pune",
    "India",
]

DEFAULT_INDIAN_DOMAINS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "in.indeed.com",
]

def _company_domain(company_name: str) -> str:
    """Create a deterministic pseudo-domain key from a company name."""
    import re
    slug = re.sub(r"[^\w]", "-", company_name.lower()).strip("-")
    return f"{slug}.talentradar.internal"

def _stable_id(text: str) -> str:
    """MD5 fingerprint of a URL or title+company string used as external_id."""
    return hashlib.md5(text.encode()).hexdigest()


async def _run_pipeline(
    roles: list[str],
    locations: list[str],
    max_results_per_query: int,
    run_id: str,
    include_domains: list[str] | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Execute the full ingestion pipeline asynchronously."""
    # 1. Fetch Raw
    all_paths: list[str] = []
    total_fetched = 0

    with TavilyJobScraper() as scraper:
        for role in roles:
            for location in locations:
                try:
                    results = scraper.search_jobs(
                        role,
                        location,
                        count=max_results_per_query,
                        include_domains=include_domains,
                    )
                    paths = scraper.save_raw(
                        results,
                        run_id=run_id,
                        role=role,
                        location=location,
                    )
                    all_paths.extend(str(p) for p in paths)
                    total_fetched += len(results)
                    logger.info("Fetched %d results for role=%r location=%r", len(results), role, location)
                except Exception as exc:
                    logger.error("fetch_raw failed for role=%r location=%r: %s", role, location, exc)

    if not all_paths:
        return {"status": "success", "message": "No jobs found"}

    # 2. Parse with LLM
    raw_results: list[RawJobResult] = []
    for fp in all_paths:
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
    parsed_dicts = [p.model_dump() for p in parsed]

    # 3. Save to Postgres
    inserted = updated = skipped = 0
    chroma_items: list[dict[str, Any]] = []
    pipeline_source = source_name or _SOURCE_NAME

    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)

        ingestion_run = await uow.ingestion_runs.create(
            source=pipeline_source,
            status=IngestionStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
            run_config={"celery_run_id": run_id, "include_domains": include_domains},
        )
        await session.commit()

        try:
            for data in parsed_dicts:
                try:
                    pjd = ParsedJobDescription(**data)
                except Exception as exc:
                    logger.warning("Skipping invalid parsed job: %s", exc)
                    skipped += 1
                    continue

                from ingestion.scrapers.tavily_client import detect_source_from_url
                job_source = detect_source_from_url(pjd.source_url or "") if pjd.source_url else pipeline_source

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

                # Prepare ChromaDB embedding data
                metadata = {
                    "title": pjd.title,
                    "company": pjd.company,
                    "location": pjd.location or "",
                    "is_remote": pjd.is_remote,
                    "seniority": pjd.seniority or "",
                    "employment_type": pjd.employment_type or "",
                    "skills_str": ", ".join(pjd.skills),
                    "source_url": pjd.source_url or "",
                    "salary": pjd.salary or "",
                    "source": job_source,
                }

                chroma_items.append({
                    "job_id": external_id,
                    # Prefer cleaned description for embedding quality;
                    # fall back to raw_text only if description_clean is absent.
                    "text": (job_kwargs.get("description_clean") or pjd.raw_text)[:4096],
                    "metadata": metadata,
                    "internal_job_id": job.id,
                })

            await session.commit()

            # 4. Embed to ChromaDB
            store = ChromaJobStore()
            embedded = 0
            if chroma_items:
                embedded = store.add_batch(chroma_items)

                # Link embeddings in PostgreSQL
                for item in chroma_items:
                    await uow.jobs.set_embedding_id(item["internal_job_id"], item["job_id"])
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

        except Exception as exc:
            # Always finalize the run record — never leave it stuck as RUNNING
            import traceback
            err_trace = {"traceback": traceback.format_exc()}
            logger.exception("Pipeline failed mid-run run_id=%s: %s", run_id, exc)
            try:
                await uow.ingestion_runs.fail(
                    ingestion_run.id,
                    error_message=str(exc),
                    error_trace=err_trace,
                )
                await session.commit()
            except Exception as finalize_exc:
                logger.error("Could not finalize failed run record: %s", finalize_exc)
            raise

    return {
        "fetched": total_fetched,
        "parsed": len(parsed),
        "inserted": inserted,
        "updated": updated,
        "embedded": embedded,
    }


@shared_task(name="ingestion.tasks.run_crawler", bind=True, max_retries=0)
def run_crawler(
    self,
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    include_domains: list[str] | None = None,
    max_results_per_query: int = 5,
    source_name: str | None = None,
) -> dict[str, Any]:
    """
    Celery task to scrape job postings from ATS pages, parse them with LLM,
    and store them in PostgreSQL and ChromaDB.

    Uses ``bind=True`` so that on failure we can access the task ID for
    diagnostic logging. ``max_retries=0`` prevents silent retry loops that
    would create duplicate ingestion_runs records.
    """
    roles = roles or ["Software Engineer", "Data Scientist"]
    locations = locations or DEFAULT_INDIAN_LOCATIONS
    include_domains = include_domains or DEFAULT_INDIAN_DOMAINS
    run_id = str(uuid.uuid4())

    logger.info(
        "Starting run_crawler task task_id=%s run_id=%s roles=%s locations=%s domains=%s max=%s",
        self.request.id, run_id, roles, locations, include_domains, max_results_per_query,
    )

    try:
        result = asyncio.run(_run_pipeline(
            roles=roles,
            locations=locations,
            max_results_per_query=max_results_per_query,
            run_id=run_id,
            include_domains=include_domains,
            source_name=source_name,
        ))
        logger.info("run_crawler completed successfully: %s", result)
        return result
    except Exception as exc:
        logger.exception(
            "run_crawler failed task_id=%s run_id=%s error=%s",
            self.request.id, run_id, exc,
        )
        # Re-raise so Celery marks the task FAILURE in Redis.
        # The ingestion_run DB record is finalized inside _run_pipeline's
        # error handler; if the crash happened before that, it will remain
        # RUNNING until the cleanup job resets it.
        raise
