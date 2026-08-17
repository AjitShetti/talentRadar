"""
ingestion/scrapers/stealth_boards_scraper.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Stealth scrapers for heavily bot-protected job boards:
- Naukri.com (India's leading job portal)
- Indeed India (in.indeed.com)
- Wellfound / AngelList (Startup Remote & India tech roles)

Utilizes ScraplingManager.fetch_stealth() (Camoufox engine + anti-detection patches)
with resource blocking to keep response times fast and avoid Cloudflare/Akamai blocks.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from ingestion.scrapling_manager import ScraplingManager

logger = logging.getLogger(__name__)

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
    "india": "India",
}


def normalize_location(loc_str: str | None) -> tuple[str, str, bool]:
    """Returns (country, city, is_remote)"""
    if not loc_str:
        return "India", "India", False
    
    loc_lower = loc_str.lower()
    is_remote = any(r in loc_lower for r in ["remote", "anywhere", "wfh", "work from home"])
    
    city = ""
    for k, v in INDIAN_CITY_SYNONYMS.items():
        if k in loc_lower and k != "india":
            city = v
            break
            
    country = "India" if ("india" in loc_lower or city) else "Global"
    return country, city or ("India" if country == "India" else "Remote"), is_remote


class StealthBoardsScraper:
    """
    Stealth scraper using Camoufox / Scrapling for anti-bot protected portals.
    """

    @classmethod
    async def search_naukri(
        cls,
        query: str,
        location: str | None = None,
        is_remote: bool | None = None,
        limit: int = 15,
    ) -> list[Job]:
        """
        Scrapes Naukri.com using stealth browser headers / Camoufox engine.
        """
        clean_role = re.sub(r"[^a-zA-Z0-9]+", "-", query.strip().lower()).strip("-")
        clean_loc = re.sub(r"[^a-zA-Z0-9]+", "-", (location or "india").strip().lower()).strip("-")
        
        # Build Naukri search URL
        url = f"https://www.naukri.com/{clean_role}-jobs-in-{clean_loc}"
        if is_remote:
            url += "?wfhType=0"  # Work from home filter

        status, html_content = await ScraplingManager.fetch_stealth(url, timeout=7.0)
        if status != 200 or not html_content.strip():
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        
        # Naukri uses srp-jobtuple-wrapper or cust-job-tuple
        job_tuples = soup.find_all("div", class_=re.compile(r"srp-jobtuple-wrapper|cust-job-tuple|jobTuple", re.I))
        if not job_tuples:
            job_tuples = soup.find_all("article", class_=re.compile(r"jobTuple", re.I))

        jobs: list[Job] = []

        for tuple_item in job_tuples:
            title_tag = tuple_item.find("a", class_=re.compile(r"title", re.I))
            comp_tag = tuple_item.find("a", class_=re.compile(r"comp-name|compName", re.I))
            loc_tag = tuple_item.find("span", class_=re.compile(r"locWdth|loc-wrap|location", re.I))
            desc_tag = tuple_item.find("span", class_=re.compile(r"job-desc|job-description", re.I))
            sal_tag = tuple_item.find("span", class_=re.compile(r"sal-wrap|salary", re.I))

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            company_name = comp_tag.get_text(strip=True) if comp_tag else "Company"
            loc_text = loc_tag.get_text(strip=True) if loc_tag else (location or "India")
            salary_text = sal_tag.get_text(strip=True) if sal_tag else None
            desc_text = desc_tag.get_text(strip=True) if desc_tag else None
            source_url = title_tag["href"] if title_tag.has_attr("href") else None

            country, city, remote_flag = normalize_location(loc_text)

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(uuid.uuid4()),
                source="naukri",
                source_url=source_url,
                title=title,
                description_raw=None,
                description_clean=desc_text,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                salary_raw=salary_text,
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["naukri", "india"],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_name, "source": "naukri"},
            )
            jobs.append(job)
            if len(jobs) >= limit:
                break

        return jobs

    @classmethod
    async def search_indeed_india(
        cls,
        query: str,
        location: str | None = None,
        is_remote: bool | None = None,
        limit: int = 15,
    ) -> list[Job]:
        """
        Scrapes Indeed India (in.indeed.com) using stealth fetching.
        """
        loc = location or "India"
        encoded_query = urllib.parse.quote_plus(query)
        encoded_loc = urllib.parse.quote_plus(loc)

        url = f"https://in.indeed.com/jobs?q={encoded_query}&l={encoded_loc}"
        if is_remote:
            url += "&sc=0kf%3Aattr(DS3S6)%3B"  # Indeed Remote filter

        status, html_content = await ScraplingManager.fetch_stealth(url, timeout=7.0)
        if status != 200 or not html_content.strip():
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        job_cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|jobsearch-JobGrid-item", re.I))
        jobs: list[Job] = []

        for card in job_cards:
            title_tag = card.find("a", class_=re.compile(r"jcs-JobTitle", re.I)) or card.find("h2")
            comp_tag = card.find(class_=re.compile(r"companyName|company_location", re.I))
            loc_tag = card.find(class_=re.compile(r"companyLocation", re.I))
            snippet_tag = card.find(class_=re.compile(r"job-snippet", re.I))

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            company_name = comp_tag.get_text(strip=True) if comp_tag else "Company"
            loc_text = loc_tag.get_text(strip=True) if loc_tag else loc
            desc_text = snippet_tag.get_text(strip=True) if snippet_tag else None
            
            source_url = None
            if title_tag.name == "a" and title_tag.has_attr("href"):
                href = title_tag["href"]
                source_url = href if href.startswith("http") else f"https://in.indeed.com{href}"

            country, city, remote_flag = normalize_location(loc_text)

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(uuid.uuid4()),
                source="indeed_india",
                source_url=source_url,
                title=title,
                description_raw=None,
                description_clean=desc_text,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["indeed", "india"],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_name, "source": "indeed"},
            )
            jobs.append(job)
            if len(jobs) >= limit:
                break

        return jobs
