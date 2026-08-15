"""
ingestion/seed_db.py
~~~~~~~~~~~~~~~~~~~~
Automated database seeder for TalentRadar.
Scans test/fixture JSON files and populates PostgreSQL and ChromaDB.
Protected with strict URL validation to ensure non-job content is never seeded.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import glob
import hashlib
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

from config.settings import get_settings
from ingestion.embeddings.chroma_store import ChromaJobStore
from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult
from ingestion.scrapers.tavily_client import detect_source_from_url
from ingestion.tasks import _company_domain, _stable_id
from ingestion.validation import is_valid_job_url, validate_job_url
from storage.database import AsyncSessionLocal, Base, engine
from storage.models import IngestionStatus, Job
from storage.repository import UnitOfWork
from sqlalchemy import func, select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingestion.seed_db")


def extract_job_from_raw(raw: RawJobResult) -> ParsedJobDescription:
    """
    Robust heuristic extractor that converts a RawJobResult into a valid
    ParsedJobDescription without relying on external LLM calls. Ensures fast,
    deterministic, and offline-safe seeding on startup.

    Rejects non-job URLs (Wikipedia, Reddit, search listing pages, etc.).
    """
    is_valid, reason = validate_job_url(raw.url)
    if not is_valid:
        raise ValueError(f"Cannot extract job from invalid URL ({reason}): {raw.url}")

    text = (raw.best_content + " " + raw.title).lower()

    # 1. Title
    title = raw.title.strip()
    if "|" in title:
        parts = [p.strip() for p in title.split("|") if p.strip()]
        if len(parts) >= 2:
            title = parts[0]
    elif " - " in title:
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        if len(parts) >= 2:
            title = parts[0]
    if not title:
        title = "Job Posting"

    # 2. Company
    company = "Unknown Company"
    orig_title = raw.title.strip()
    if "|" in orig_title:
        parts = [p.strip() for p in orig_title.split("|") if p.strip()]
        if len(parts) >= 2:
            company = parts[1]
    elif " - " in orig_title:
        parts = [p.strip() for p in orig_title.split(" - ") if p.strip()]
        if len(parts) >= 2:
            company = parts[1]
    elif " at " in orig_title.lower():
        idx = orig_title.lower().rfind(" at ")
        if idx != -1:
            company = orig_title[idx + 4:].strip()
            title = orig_title[:idx].strip()

    company = re.sub(r"[\(\[\{].*?[\)\]\}]", "", company).strip()
    if not company or len(company) > 60 or company.lower() in ("unknown company", "en", "www", "reddit", "wikipedia"):
        try:
            domain = urlparse(raw.url).netloc
            if domain:
                domain_clean = domain.replace("www.", "").split(".")[0].lower()
                if domain_clean and domain_clean not in [
                    "linkedin", "indeed", "naukri", "glassdoor", "monster",
                    "wikipedia", "reddit", "youtube", "medium", "quora", "en", "www"
                ] and len(domain_clean) >= 3:
                    company = domain_clean.capitalize()
                else:
                    company = "Tech Company"
        except Exception:
            company = "Tech Company"

    # 3. Skills
    known_skills = [
        "python", "java", "javascript", "typescript", "react", "angular", "vue",
        "node", "nodejs", "express", "nextjs", "nest", "django", "fastapi", "flask",
        "spring", "hibernate", "c++", "c#", ".net", "go", "golang", "rust", "ruby",
        "rails", "php", "laravel", "swift", "kotlin", "flutter", "react native",
        "aws", "gcp", "azure", "docker", "kubernetes", "k8s", "terraform", "sql",
        "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
        "cassandra", "graphql", "rest", "grpc", "microservices", "ci/cd", "jenkins",
        "github actions", "gitlab", "jira", "agile", "scrum", "machine learning",
        "deep learning", "ai", "nlp", "llm", "pytorch", "tensorflow", "scikit-learn",
        "pandas", "numpy", "spark", "hadoop", "kafka", "airflow", "snowflake",
        "databricks", "dbt", "tableau", "powerbi", "excel", "git", "linux", "bash",
        "html", "css", "tailwind", "redux", "solidity", "web3"
    ]
    skills = []
    for skill in known_skills:
        pattern = r"\b" + re.escape(skill) + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            if skill in ["aws", "gcp", "ai", "nlp", "llm", "sql", "ui", "ux", "ci/cd", "html", "css", "api", "rest", "grpc"]:
                skills.append(skill.upper())
            elif skill in ["javascript", "typescript", "kubernetes", "postgresql", "mongodb"]:
                skills.append(skill.capitalize())
            elif skill == "k8s":
                skills.append("Kubernetes")
            elif skill == "postgres":
                skills.append("PostgreSQL")
            elif skill == "react native":
                skills.append("React Native")
            else:
                skills.append(skill.title())
    skills = sorted(list(set(skills)))
    if not skills:
        skills = ["Software Engineering", "Problem Solving"]

    # 4. Location & Remote
    known_locations = [
        "Remote", "Hybrid", "Bangalore", "Bengaluru", "Mumbai", "Delhi", "NCR",
        "New Delhi", "Gurgaon", "Noida", "Hyderabad", "Pune", "Chennai", "Kolkata",
        "Ahmedabad", "San Francisco", "New York", "London", "Seattle", "Austin",
        "Boston", "Chicago", "Toronto", "Vancouver", "Singapore", "Sydney", "Berlin",
        "Paris", "Amsterdam", "Dublin", "California", "Texas", "Washington", "USA", "India", "UK"
    ]
    location = "Remote"
    for loc in known_locations:
        if re.search(r"\b" + re.escape(loc) + r"\b", text, re.IGNORECASE):
            if loc == "Bengaluru":
                location = "Bangalore"
            elif loc in ("NCR", "New Delhi"):
                location = "Delhi"
            else:
                location = loc
            break

    is_remote = bool(re.search(r"\b(remote|wfh|work from home|anywhere|distributed)\b", text, re.IGNORECASE))
    if location == "Remote":
        is_remote = True

    # 5. Seniority
    seniority = None
    if re.search(r"\b(intern|internship)\b", text):
        seniority = "intern"
    elif re.search(r"\b(junior|jr|entry level)\b", text):
        seniority = "junior"
    elif re.search(r"\b(principal|architect)\b", text):
        seniority = "principal"
    elif re.search(r"\b(staff)\b", text):
        seniority = "staff"
    elif re.search(r"\b(director|head of)\b", text):
        seniority = "director"
    elif re.search(r"\b(vp|vice president)\b", text):
        seniority = "vp"
    elif re.search(r"\b(lead)\b", text):
        seniority = "lead"
    elif re.search(r"\b(senior|sr)\b", text):
        seniority = "senior"
    elif re.search(r"\b(mid|intermediate)\b", text):
        seniority = "mid"

    # 6. Employment type
    emp_type = "full_time"
    if re.search(r"\b(part[\s-]time)\b", text):
        emp_type = "part_time"
    elif re.search(r"\b(contract|contractor)\b", text):
        emp_type = "contract"
    elif re.search(r"\b(intern|internship)\b", text):
        emp_type = "internship"
    elif re.search(r"\b(freelance)\b", text):
        emp_type = "freelance"

    # 7. Salary
    salary = None
    sal_match = re.search(
        r"(\$|₹|INR|Rs\.?|CAD|EUR|GBP)\s*\d+(?:,\d+)*(?:\.\d+)?(?:\s*-\s*(?:\$|₹|INR|Rs\.?|CAD|EUR|GBP)?\s*\d+(?:,\d+)*(?:\.\d+)?)?\s*(?:LPA|lpa|k|K|M|m|/year|/month|per annum|per year|per month|pa|Lakhs)?",
        raw.best_content,
    )
    if sal_match:
        salary = sal_match.group(0).strip()

    return ParsedJobDescription(
        title=title,
        company=company,
        skills=skills,
        location=location,
        is_remote=is_remote,
        salary=salary,
        seniority=seniority,
        employment_type=emp_type,
        source_url=raw.url,
        raw_text=raw.best_content,
    )


async def seed_database(force: bool = False, fixture_dir: str | None = None) -> None:
    """
    Seed PostgreSQL and ChromaDB with validated job postings from fixture directory.
    Gated behind SEED_FROM_FIXTURES=true or force=True.
    """
    seed_enabled = os.getenv("SEED_FROM_FIXTURES", "false").lower() in ("true", "1", "yes")
    if not seed_enabled and not force:
        logger.info("SEED_FROM_FIXTURES is disabled. Skipping fixture-based seeding.")
        return

    logger.info("Initializing database schemas if needed...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Look in explicit fixture directory or tests/fixtures
    search_dirs = [fixture_dir] if fixture_dir else ["tests/fixtures", "data/fixtures"]
    raw_files: list[str] = []
    for d in search_dirs:
        if os.path.exists(d):
            raw_files.extend(glob.glob(f"{d}/**/*.json", recursive=True))

    if not raw_files:
        logger.info("No fixture JSON files found in %s. Seeder finished.", search_dirs)
        return

    logger.info("Scanning %d fixture JSON files...", len(raw_files))
    seen_urls = set()
    raw_results: list[RawJobResult] = []
    for filepath in raw_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                url = data.get("url", "").strip()
                if not url:
                    continue
                if not is_valid_job_url(url):
                    logger.warning("Dropping invalid job URL in fixture %s: %s", filepath, url)
                    continue
                if url not in seen_urls:
                    seen_urls.add(url)
                    raw_results.append(RawJobResult(**data))
        except Exception as exc:
            logger.warning("Failed reading raw file %s: %s", filepath, exc)

    if not raw_results:
        logger.info("No valid job postings found in fixtures. Seeder complete.")
        return

    logger.info("Extracted %d valid job postings from fixtures. Starting ingestion...", len(raw_results))

    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)
        ingestion_run = await uow.ingestion_runs.create(
            source="db_seeder",
            status=IngestionStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
            run_config={"files_count": len(raw_files), "unique_jobs": len(raw_results)},
        )
        await session.commit()

        inserted = 0
        updated = 0
        chroma_items: list[dict[str, Any]] = []

        for raw in raw_results:
            try:
                pjd = extract_job_from_raw(raw)
            except ValueError as exc:
                logger.warning("Skipping raw job due to extraction failure: %s", exc)
                continue

            job_source = detect_source_from_url(pjd.source_url or "") if pjd.source_url else "fixture_seeder"
            company_slug = _company_domain(pjd.company)
            company, _ = await uow.companies.upsert_by_domain(
                domain=company_slug,
                defaults={"name": pjd.company},
            )
            external_id = _stable_id(pjd.source_url or pjd.title + pjd.company)
            job_kwargs = pjd.to_job_kwargs()
            job_kwargs.update({
                "company_id": company.id,
                "ingestion_run_id": ingestion_run.id,
            })
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
                "internal_job_id": job.id,
                "text": (job_kwargs.get("description_clean") or pjd.raw_text)[:4096],
                "metadata": {
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
                },
            })

        await session.commit()

        logger.info("Upserted %d jobs (inserted: %d, updated: %d). Updating ChromaDB embeddings...", len(chroma_items), inserted, updated)
        try:
            store = ChromaJobStore()
            if chroma_items:
                store.add_batch(chroma_items)
                for item in chroma_items:
                    await uow.jobs.set_embedding_id(item["internal_job_id"], item["job_id"])
                await session.commit()
                logger.info("Successfully updated ChromaDB vector embeddings.")
        except Exception as exc:
            logger.warning("ChromaDB embedding warning during seeding: %s", exc)

        await uow.ingestion_runs.finish(
            ingestion_run.id,
            status=IngestionStatus.SUCCESS,
            jobs_discovered=len(raw_results),
            jobs_inserted=inserted,
            jobs_updated=updated,
            jobs_skipped=len(raw_results) - (inserted + updated),
        )
        await session.commit()

    logger.info("Database seeding complete!")


async def main() -> None:
    try:
        await seed_database()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
