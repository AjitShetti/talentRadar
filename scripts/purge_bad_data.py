"""
scripts/purge_bad_data.py
~~~~~~~~~~~~~~~~~~~~~~~~~
One-off & maintenance purge script to eliminate contaminated/seeded data.

Identifies and removes:
1. Job records whose `source_url` fails the shared job URL validator
   (e.g., Wikipedia articles, Reddit threads, search-listing pages).
2. Corresponding ChromaDB vector embeddings.
3. Orphaned or garbage Company records (e.g. "En", "Www", "Reddit", "Wikipedia",
   or companies left with 0 active jobs).

Usage:
    python -m scripts.purge_bad_data [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from config.settings import get_settings
from ingestion.embeddings.chroma_store import ChromaJobStore
from ingestion.validation import is_valid_job_url, validate_job_url
from storage.database import AsyncSessionLocal, engine
from storage.models import Company, Job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("purge_bad_data")

GARBAGE_COMPANY_NAMES = {
    "en",
    "www",
    "reddit",
    "wikipedia",
    "youtube",
    "medium",
    "quora",
    "unknown company",
}


async def purge_contaminated_data(dry_run: bool = False) -> dict[str, int]:
    """
    Scan database for invalid job postings and orphaned companies,
    then remove them from PostgreSQL and ChromaDB.
    """
    logger.info("Starting database audit and purge (dry_run=%s)...", dry_run)

    async with AsyncSessionLocal() as session:
        # 1. Fetch all jobs
        stmt = select(Job)
        result = await session.execute(stmt)
        all_jobs = result.scalars().all()
        logger.info("Found %d total jobs in database to inspect.", len(all_jobs))

        invalid_jobs: list[Job] = []
        embedding_ids_to_remove: list[str] = []

        for job in all_jobs:
            url = job.source_url or ""
            is_valid, reason = validate_job_url(url)
            if not is_valid:
                logger.info(
                    "Contaminated Job found: id=%s title=%r url=%r reason=%s",
                    job.id,
                    job.title,
                    url,
                    reason,
                )
                invalid_jobs.append(job)
                if job.embedding_id:
                    embedding_ids_to_remove.append(job.embedding_id)
                elif job.external_id:
                    embedding_ids_to_remove.append(job.external_id)

        logger.info(
            "Identified %d contaminated jobs to purge (%d associated embeddings).",
            len(invalid_jobs),
            len(embedding_ids_to_remove),
        )

        if not dry_run and invalid_jobs:
            # Delete invalid jobs
            invalid_ids = [j.id for j in invalid_jobs]
            delete_jobs_stmt = delete(Job).where(Job.id.in_(invalid_ids))
            await session.execute(delete_jobs_stmt)
            await session.commit()
            logger.info("Successfully deleted %d invalid jobs from PostgreSQL.", len(invalid_ids))

            # Delete ChromaDB embeddings
            try:
                chroma_store = ChromaJobStore()
                deleted_embeddings = 0
                for emb_id in set(embedding_ids_to_remove):
                    try:
                        chroma_store.delete(emb_id)
                        deleted_embeddings += 1
                    except Exception as emb_err:
                        logger.warning("Could not delete ChromaDB embedding %s: %s", emb_id, emb_err)
                logger.info("Deleted %d embeddings from ChromaDB.", deleted_embeddings)
            except Exception as chroma_err:
                logger.warning("ChromaDB connection unavailable or failed: %s", chroma_err)

        # 2. Check for orphaned / garbage companies
        # A company is orphaned if it has 0 jobs associated
        companies_stmt = select(
            Company.id,
            Company.name,
            Company.domain,
            func.count(Job.id).label("job_count")
        ).outerjoin(Job, Job.company_id == Company.id).group_by(Company.id)

        comp_result = await session.execute(companies_stmt)
        companies_to_delete: list[Any] = []

        for row in comp_result.all():
            comp_id, comp_name, comp_domain, job_count = row
            name_lower = (comp_name or "").strip().lower()
            is_garbage = name_lower in GARBAGE_COMPANY_NAMES or (comp_domain and any(g in comp_domain.lower() for g in GARBAGE_COMPANY_NAMES))

            if job_count == 0 or is_garbage:
                logger.info(
                    "Orphaned/Garbage Company found: id=%s name=%r domain=%r remaining_jobs=%d",
                    comp_id,
                    comp_name,
                    comp_domain,
                    job_count,
                )
                companies_to_delete.append(comp_id)

        logger.info("Identified %d orphaned or garbage companies to purge.", len(companies_to_delete))

        if not dry_run and companies_to_delete:
            delete_comp_stmt = delete(Company).where(Company.id.in_(companies_to_delete))
            await session.execute(delete_comp_stmt)
            await session.commit()
            logger.info("Successfully deleted %d orphaned companies from PostgreSQL.", len(companies_to_delete))

        summary = {
            "total_jobs_scanned": len(all_jobs),
            "contaminated_jobs_purged": len(invalid_jobs),
            "embeddings_removed": len(embedding_ids_to_remove),
            "orphaned_companies_purged": len(companies_to_delete),
        }
        logger.info("Purge summary: %s", summary)
        return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description="Purge contaminated job seed data and orphaned companies.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without deleting records.")
    args = parser.parse_args()

    try:
        await purge_contaminated_data(dry_run=args.dry_run)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
