"""
ingestion/dags/fetch_and_parse_dag.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Airflow DAG: fetch raw JDs via Tavily → parse with Groq LLM → save to Postgres → embed to ChromaDB.

Pipeline
--------
  fetch_raw  ──►  parse_with_llm  ──►  save_to_postgres  ──►  embed_to_chromadb

Retry policy (per task)
-----------------------
  retries          = 3
  retry_delay      = 5 minutes
  (exponential backoff disabled to keep delays predictable)

DAG params (configurable via Airflow UI → Trigger DAG w/ config)
----------------------------------------------------------------
  roles                 – list[str]   job roles to search for
  locations             – list[str]   geographic filters
  max_results_per_query – int         Tavily results per (role, location) pair

Customisation
-------------
  - Swap ``_DEFAULT_MODEL`` in ``jd_parser.py`` to use a larger Groq model.
  - Change ``schedule`` below to run more/less frequently.
  - Add ``email_on_failure=True`` and ``email=["ops@your-org.com"]`` to
    ``default_args`` once SMTP is configured.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ─── Make the repo root importable inside Airflow tasks ──────────────────────
# Airflow workers execute DAG files directly; the PYTHONPATH may not include
# the project root when running inside Docker.
_REPO_ROOT = Path(__file__).parent.parent.parent  # …/talentRadar/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DAG-level configuration
# ─────────────────────────────────────────────────────────────────────────────

_DAG_ID = "talentradar_fetch_and_parse"
_SOURCE_NAME = "tavily"
_RAW_DATA_DIR = Path(os.getenv("RAW_DATA_DIR", "/data/raw"))

# Default search parameters — overridable from the Airflow UI at trigger time
_DEFAULT_ROLES = [
    "Software Engineer",
    "Data Scientist",
    "MLOps Engineer",
    "Backend Engineer",
    "Machine Learning Engineer",
]
_DEFAULT_LOCATIONS = ["Remote", "San Francisco", "New York", "India"]
_DEFAULT_MAX_RESULTS = 5   # keep low for the initial run; raise in production

# ─────────────────────────────────────────────────────────────────────────────
# Retry / scheduling defaults
# ─────────────────────────────────────────────────────────────────────────────

default_args: dict[str, Any] = {
    "owner": "talentradar",
    "depends_on_past": False,
    "start_date": days_ago(1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": False,
}

# ─────────────────────────────────────────────────────────────────────────────
# Task implementations
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_raw(
    *,
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    max_results_per_query: int = _DEFAULT_MAX_RESULTS,
    **context: Any,
) -> dict[str, Any]:
    """
    Task 1 — ``fetch_raw``
    ~~~~~~~~~~~~~~~~~~~~~~
    Iterate over every (role, location) pair, call Tavily, and persist raw
    JSON files under ``/data/raw/{run_id}/``.

    XCom return value
    -----------------
    ``{"run_id": str, "file_paths": [str, ...], "total_fetched": int}``
    """
    from ingestion.scrapers.tavily_client import TavilyJobScraper

    # Resolve params — dag_run.conf overrides defaults
    dag_run_conf: dict[str, Any] = context.get("dag_run").conf or {}  # type: ignore[union-attr]
    _roles = dag_run_conf.get("roles", roles or _DEFAULT_ROLES)
    _locations = dag_run_conf.get("locations", locations or _DEFAULT_LOCATIONS)
    _max_results = int(dag_run_conf.get("max_results_per_query", max_results_per_query))

    run_id: str = context["run_id"]  # Airflow run ID (unique per trigger)
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
                    # Log but don't abort — other role/location pairs can succeed
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


def _parse_with_llm(*, ti: Any, **context: Any) -> dict[str, Any]:  # noqa: ANN401
    """
    Task 2 — ``parse_with_llm``
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Read raw JSON files from XCom (produced by ``fetch_raw``), call the LLM
    parser for each, and push validated ``ParsedJobDescription`` dicts.

    XCom return value
    -----------------
    ``{"parsed_jobs": [dict, ...], "run_id": str, "failed_count": int}``
    """
    from ingestion.parsers.jd_parser import JDParser
    from ingestion.parsers.schemas import RawJobResult

    upstream: dict[str, Any] = ti.xcom_pull(task_ids="fetch_raw")
    file_paths: list[str] = upstream.get("file_paths", [])
    run_id: str = upstream.get("run_id", context["run_id"])

    logger.info("parse_with_llm | %d files to parse", len(file_paths))

    if not file_paths:
        logger.warning("No raw files to parse — skipping LLM step.")
        return {"parsed_jobs": [], "run_id": run_id, "failed_count": 0}

    # Re-hydrate RawJobResult objects from saved JSON files
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


def _save_to_postgres(*, ti: Any, **context: Any) -> dict[str, Any]:  # noqa: ANN401
    """
    Task 3 — ``save_to_postgres``
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Upsert each parsed job into PostgreSQL using the existing repository layer.

    Strategy
    --------
    * Company lookup: ``upsert_by_domain`` using a slug derived from company name.
    * Job deduplication: ``upsert_by_external_id`` keyed on MD5(url) + "tavily".
    * Ingestion run row tracks counters and lifecycle state.

    XCom return value
    -----------------
    ``{"jobs_inserted": int, "jobs_updated": int, "jobs_skipped": int}``
    """
    import asyncio
    from ingestion.parsers.schemas import ParsedJobDescription
    from storage.database import AsyncSessionLocal
    from storage.models import IngestionStatus
    from storage.repository import UnitOfWork

    upstream: dict[str, Any] = ti.xcom_pull(task_ids="parse_with_llm")
    parsed_dicts: list[dict[str, Any]] = upstream.get("parsed_jobs", [])
    run_id: str = upstream.get("run_id", context["run_id"])

    logger.info("save_to_postgres | %d jobs to upsert", len(parsed_dicts))

    async def _upsert_all() -> dict[str, int]:
        inserted = updated = skipped = 0

        async with AsyncSessionLocal() as session:
            uow = UnitOfWork(session)

            # ── Create an ingestion run audit row ─────────────────────────
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

                # ── Upsert company ─────────────────────────────────────── #
                company_slug = _company_domain(pjd.company)
                company, _ = await uow.companies.upsert_by_domain(
                    domain=company_slug,
                    defaults={"name": pjd.company},
                )

                # ── Upsert job ─────────────────────────────────────────── #
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

            # ── Finalise ingestion run ─────────────────────────────────── #
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


def _embed_to_chromadb(*, ti: Any, **context: Any) -> dict[str, Any]:  # noqa: ANN401
    """
    Task 4 — ``embed_to_chromadb``
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Embed each parsed job description and upsert into ChromaDB for
    semantic search. Also back-fills ``jobs.embedding_id`` in Postgres.

    XCom return value
    -----------------
    ``{"embedded": int, "skipped": int}``
    """
    import asyncio
    import hashlib
    from ingestion.embeddings.chroma_store import ChromaJobStore
    from ingestion.parsers.schemas import ParsedJobDescription
    from storage.database import AsyncSessionLocal
    from storage.repository import JobRepository

    upstream: dict[str, Any] = ti.xcom_pull(task_ids="parse_with_llm")
    parsed_dicts: list[dict[str, Any]] = upstream.get("parsed_jobs", [])

    logger.info("embed_to_chromadb | %d jobs to embed", len(parsed_dicts))

    if not parsed_dicts:
        return {"embedded": 0, "skipped": 0}

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

        # Build flat metadata (chromadb requires scalar values only)
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
            "text": pjd.raw_text[:4096],  # chromadb document size limit
            "metadata": metadata,
        })

    embedded = store.add_batch(chroma_items)

    # Back-fill embedding_id in Postgres so jobs can be looked up by vector ID
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


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _company_domain(company_name: str) -> str:
    """
    Create a deterministic pseudo-domain key from a company name.
    This is used as the deduplication key in ``companies.domain``.
    """
    import re
    slug = re.sub(r"[^\w]", "-", company_name.lower()).strip("-")
    return f"{slug}.talentradar.internal"


def _stable_id(text: str) -> str:
    """MD5 fingerprint of a URL or title+company string used as external_id."""
    return hashlib.md5(text.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# DAG definition
# ─────────────────────────────────────────────────────────────────────────────

with DAG(
    dag_id=_DAG_ID,
    description=(
        "TalentRadar: fetch raw job descriptions via Tavily, "
        "parse with Groq LLM, and persist to PostgreSQL."
    ),
    default_args=default_args,
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["talentradar", "ingestion", "tavily", "llm"],
    params={
        "roles": _DEFAULT_ROLES,
        "locations": _DEFAULT_LOCATIONS,
        "max_results_per_query": _DEFAULT_MAX_RESULTS,
    },
    doc_md=__doc__,
) as dag:

    t_fetch = PythonOperator(
        task_id="fetch_raw",
        python_callable=_fetch_raw,
        doc_md="Scrape raw job descriptions from Tavily → /data/raw/{run_id}/.",
    )

    t_parse = PythonOperator(
        task_id="parse_with_llm",
        python_callable=_parse_with_llm,
        doc_md="LLM extraction via Groq → Pydantic-validated ParsedJobDescription.",
    )

    t_save = PythonOperator(
        task_id="save_to_postgres",
        python_callable=_save_to_postgres,
        doc_md="Upsert jobs + companies into PostgreSQL; create ingestion_run audit row.",
    )

    t_embed = PythonOperator(
        task_id="embed_to_chromadb",
        python_callable=_embed_to_chromadb,
        doc_md="Embed job descriptions into ChromaDB; back-fill embedding_id in Postgres.",
    )

    # ── Task dependency chain ──────────────────────────────────────────── #
    t_fetch >> t_parse >> t_save >> t_embed
