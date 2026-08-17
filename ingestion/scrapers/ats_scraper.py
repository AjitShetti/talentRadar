"""
ingestion/scrapers/ats_scraper.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Direct high-speed scraper for modern ATS platforms:
- Greenhouse (boards-api.greenhouse.io)
- Ashby (api.ashbyhq.com)
- Lever (api.lever.co)

These APIs return structured JSON in 200-500ms with zero anti-bot challenges.
Provides real openings from top tech startups & global companies hiring in India / Remote.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from ingestion.scrapling_manager import ScraplingManager

logger = logging.getLogger(__name__)

# Curated list of companies on Greenhouse, Ashby, Lever with active remote/India hiring
GREENHOUSE_COMPANIES = [
    "stripe", "figma", "gitlab", "postman", "inmobi",
    "cloudflare", "cockroachlabs", "elastic",
    "mongodb", "affirm", "airtable", "reddit"
]

ASHBY_COMPANIES = [
    "openai", "replit", "linear", "vercel", "supabase",
    "cursor", "modal", "resend", "dust", "perplexity"
]

LEVER_COMPANIES = [
    "spotify"
]

INDIAN_CITY_SYNONYMS = {
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "hyderabad": "Hyderabad",
    "pune": "Pune",
    "delhi": "Delhi NCR",
    "gurgaon": "Delhi NCR",
    "gurugram": "Delhi NCR",
    "noida": "Delhi NCR",
    "mumbai": "Mumbai",
    "chennai": "Chennai",
    "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad",
    "remote": "Remote",
    "india": "India",
}


def matches_query(text: str, query: str) -> bool:
    """Check if query words appear in the target text."""
    if not query:
        return True
    text_lower = text.lower()
    keywords = query.lower().split()
    return any(kw in text_lower for kw in keywords)


def get_canonical_city(text: str) -> str:
    """Extract canonical Indian city name from text."""
    t = (text or "").lower()
    for k, v in INDIAN_CITY_SYNONYMS.items():
        if k in t and k not in ["remote", "india"]:
            return v
    return ""


def matches_location(job_location: str, target_location: str | None, is_remote_only: bool | None) -> tuple[bool, bool, str, str]:
    """
    Check if a job location matches India hubs or global remote.
    Returns: (is_match, is_remote, country, city)
    """
    loc_lower = (job_location or "").lower()

    # Remote detection
    is_job_remote = any(r in loc_lower for r in ["remote", "anywhere", "worldwide", "global", "wfh"])

    if is_remote_only and not is_job_remote:
        return False, False, "", ""

    # Check for India or Indian cities
    city = get_canonical_city(loc_lower)
    is_india = "india" in loc_lower or bool(city)

    country = "India" if is_india else ("Global" if is_job_remote else "Unknown")

    if not target_location:
        # User has no specific location constraint: accept India or Worldwide Remote
        if is_india or is_job_remote:
            return True, is_job_remote, country, city
        return False, is_job_remote, country, city

    target_lower = target_location.lower()
    target_city = get_canonical_city(target_lower)

    # 1. Direct substring match
    if target_lower in loc_lower:
        return True, is_job_remote, country, city

    # 2. Canonical city match (e.g. Bangalore matches Bengaluru, Gurgaon matches Delhi NCR)
    if city and target_city and city.lower() == target_city.lower():
        return True, is_job_remote, country, city

    # 3. If user searched "India" and job is anywhere in India or India/Worldwide remote
    if target_lower == "india" and (is_india or is_job_remote):
        return True, is_job_remote, country, city

    # 4. If user searched "Remote" and job is remote
    if target_lower in ["remote", "worldwide", "anywhere"] and is_job_remote:
        return True, is_job_remote, country, city

    return False, is_job_remote, country, city


class ATSScraper:
    """
    High-speed aggregator querying Greenhouse, Ashby, and Lever job boards in parallel.
    """

    @classmethod
    async def fetch_greenhouse_company(cls, company_slug: str, query: str, location: str | None, is_remote: bool | None) -> list[Job]:
        """Fetch and filter jobs from a Greenhouse public board."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs"
        status, data = await ScraplingManager.fetch_html_or_json(url, timeout=2.0)
        if status != 200 or not isinstance(data, dict):
            return []

        jobs_data = data.get("jobs", [])
        matched_jobs: list[Job] = []

        for item in jobs_data:
            title = item.get("title", "")
            loc_name = item.get("location", {}).get("name", "")
            
            if not matches_query(title, query):
                continue

            matched, job_remote, country, city = matches_location(loc_name, location, is_remote)
            if not matched:
                continue

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(item.get("id", "")),
                source=f"greenhouse:{company_slug}",
                source_url=item.get("absolute_url", f"https://boards.greenhouse.io/{company_slug}/jobs/{item.get('id')}"),
                title=title,
                description_raw=None,
                description_clean=None,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_name or "Remote / India",
                country=country or "India",
                city=city or None,
                is_remote=job_remote,
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["ats", "greenhouse", company_slug],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_slug.capitalize(), "ats": "greenhouse"}
            )
            matched_jobs.append(job)

        return matched_jobs

    @classmethod
    async def fetch_ashby_company(cls, company_slug: str, query: str, location: str | None, is_remote: bool | None) -> list[Job]:
        """Fetch and filter jobs from an Ashby public board."""
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
        status, data = await ScraplingManager.fetch_html_or_json(url, timeout=2.0)
        if status != 200 or not isinstance(data, dict):
            return []

        jobs_data = data.get("jobs", [])
        matched_jobs: list[Job] = []

        for item in jobs_data:
            title = item.get("title", "")
            loc_name = item.get("location", "")
            if not loc_name and item.get("isRemote"):
                loc_name = "Remote"

            if not matches_query(title, query):
                continue

            matched, job_remote, country, city = matches_location(loc_name, location, is_remote)
            if not matched and not item.get("isRemote"):
                continue

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(item.get("id", "")),
                source=f"ashby:{company_slug}",
                source_url=item.get("jobUrl", f"https://jobs.ashbyhq.com/{company_slug}/{item.get('id')}"),
                title=title,
                description_raw=None,
                description_clean=None,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_name or "Remote",
                country=country or "India",
                city=city or None,
                is_remote=job_remote or bool(item.get("isRemote")),
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["ats", "ashby", company_slug],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_slug.capitalize(), "ats": "ashby"}
            )
            matched_jobs.append(job)

        return matched_jobs

    @classmethod
    async def fetch_lever_company(cls, company_slug: str, query: str, location: str | None, is_remote: bool | None) -> list[Job]:
        """Fetch and filter jobs from a Lever public board."""
        url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
        status, data = await ScraplingManager.fetch_html_or_json(url, timeout=2.0)
        if status != 200 or not isinstance(data, list):
            return []

        matched_jobs: list[Job] = []

        for item in data:
            title = item.get("text", "")
            cats = item.get("categories", {})
            loc_name = cats.get("location", "") or cats.get("allLocations", [""])[0]
            workplace_type = item.get("workplaceType", "").lower()
            is_lever_remote = workplace_type == "remote"

            if not matches_query(title, query):
                continue

            matched, job_remote, country, city = matches_location(loc_name, location, is_remote)
            if not matched and not is_lever_remote:
                continue

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(item.get("id", "")),
                source=f"lever:{company_slug}",
                source_url=item.get("hostedUrl", f"https://jobs.lever.co/{company_slug}/{item.get('id')}"),
                title=title,
                description_raw=None,
                description_clean=item.get("descriptionPlain", None),
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_name or "Remote",
                country=country or "India",
                city=city or None,
                is_remote=job_remote or is_lever_remote,
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["ats", "lever", company_slug],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_slug.capitalize(), "ats": "lever"}
            )
            matched_jobs.append(job)

        return matched_jobs

    @classmethod
    async def search_all_ats(cls, query: str, location: str | None = None, is_remote: bool | None = None) -> list[Job]:
        """
        Queries all configured Greenhouse, Ashby, and Lever companies concurrently with individual timeouts.
        """
        async def _safe_fetch(coro):
            try:
                return await asyncio.wait_for(coro, timeout=2.0)
            except Exception:
                return []

        tasks = []
        for c in GREENHOUSE_COMPANIES:
            tasks.append(_safe_fetch(cls.fetch_greenhouse_company(c, query, location, is_remote)))
        for c in ASHBY_COMPANIES:
            tasks.append(_safe_fetch(cls.fetch_ashby_company(c, query, location, is_remote)))
        for c in LEVER_COMPANIES:
            tasks.append(_safe_fetch(cls.fetch_lever_company(c, query, location, is_remote)))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_jobs: list[Job] = []
        for res in results:
            if isinstance(res, list):
                all_jobs.extend(res)
        return all_jobs
