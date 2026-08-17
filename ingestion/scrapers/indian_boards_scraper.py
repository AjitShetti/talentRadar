"""
ingestion/scrapers/indian_boards_scraper.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Scrapers for prominent Indian job portals and guest search APIs:
- LinkedIn Guest Job Search API (fast HTML, location=India or Indian cities, remote filters)
- Foundit India (formerly Monster India)
- Freshersworld (campus & entry level tech hiring in India)

Uses ScraplingManager for high concurrency and stealth header spoofing.
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
    """
    Returns (country, city, is_remote)
    """
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


class IndianBoardsScraper:
    """
    Scraper for LinkedIn (India/Remote), Foundit, and Freshersworld.
    """

    @classmethod
    async def search_linkedin_guest(
        cls,
        query: str,
        location: str | None = None,
        is_remote: bool | None = None,
        limit: int = 15,
    ) -> list[Job]:
        """
        Scrapes LinkedIn's guest job search API without requiring authentication.
        """
        loc = location or "India"
        encoded_query = urllib.parse.quote_plus(query)
        encoded_loc = urllib.parse.quote_plus(loc)

        # f_WT=2 is LinkedIn filter for Remote
        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
            f"keywords={encoded_query}&location={encoded_loc}&start=0"
        )
        if is_remote:
            url += "&f_WT=2"

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.linkedin.com/jobs",
        }

        status, html_content = await ScraplingManager.fetch_html_or_json(url, headers=headers, timeout=5.0)
        if status != 200 or not isinstance(html_content, str) or not html_content.strip():
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        job_cards = soup.find_all("li")
        jobs: list[Job] = []

        for card in job_cards:
            title_tag = card.find("h3", class_=re.compile(r"base-search-card__title", re.I)) or card.find("h3")
            company_tag = card.find("h4", class_=re.compile(r"base-search-card__subtitle", re.I)) or card.find("h4")
            loc_tag = card.find("span", class_=re.compile(r"job-search-card__location", re.I))
            link_tag = card.find("a", class_=re.compile(r"base-card__full-link", re.I)) or card.find("a", href=True)
            time_tag = card.find("time")

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            company_name = company_tag.get_text(strip=True) if company_tag else "Company"
            loc_text = loc_tag.get_text(strip=True) if loc_tag else loc
            source_url = link_tag["href"].split("?")[0] if link_tag and link_tag.has_attr("href") else None

            # Extract job ID
            ext_id = None
            if source_url and "view/" in source_url:
                parts = source_url.split("view/")
                if len(parts) > 1:
                    ext_id = parts[1].strip("/").split("/")[0]

            country, city, remote_flag = normalize_location(loc_text)

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=ext_id or str(uuid.uuid4()),
                source="linkedin",
                source_url=source_url,
                title=title,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["linkedin", "india"],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_name, "source": "linkedin"},
            )
            jobs.append(job)
            if len(jobs) >= limit:
                break

        return jobs

    @classmethod
    async def search_foundit_india(
        cls,
        query: str,
        location: str | None = None,
        is_remote: bool | None = None,
        limit: int = 15,
    ) -> list[Job]:
        """
        Scrapes Foundit India (formerly Monster India) job search results.
        """
        loc = location or "India"
        encoded_query = urllib.parse.quote_plus(query)
        encoded_loc = urllib.parse.quote_plus(loc)

        url = f"https://www.foundit.in/srp/results?query={encoded_query}&locations={encoded_loc}"
        if is_remote:
            url += "&workFromHome=true"

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.foundit.in/",
        }

        status, html_content = await ScraplingManager.fetch_html_or_json(url, headers=headers, timeout=5.0)
        if status != 200 or not isinstance(html_content, str) or not html_content.strip():
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        job_cards = soup.find_all("div", class_=re.compile(r"cardContainer|srpResultCard", re.I))
        jobs: list[Job] = []

        for card in job_cards:
            title_tag = card.find(class_=re.compile(r"jobTitle|cardTitle", re.I)) or card.find("h3") or card.find("a")
            company_tag = card.find(class_=re.compile(r"companyName|company", re.I))
            loc_tag = card.find(class_=re.compile(r"location|locationText", re.I))
            link_tag = card.find("a", href=True)

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            company_name = company_tag.get_text(strip=True) if company_tag else "Company"
            loc_text = loc_tag.get_text(strip=True) if loc_tag else loc
            source_url = link_tag["href"] if link_tag else None
            if source_url and not source_url.startswith("http"):
                source_url = f"https://www.foundit.in{source_url}"

            country, city, remote_flag = normalize_location(loc_text)

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(uuid.uuid4()),
                source="foundit",
                source_url=source_url,
                title=title,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["foundit", "india"],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_name, "source": "foundit"},
            )
            jobs.append(job)
            if len(jobs) >= limit:
                break

        return jobs

    @classmethod
    async def search_freshersworld(
        cls,
        query: str,
        location: str | None = None,
        is_remote: bool | None = None,
        limit: int = 15,
    ) -> list[Job]:
        """
        Scrapes Freshersworld job listings in India.
        """
        clean_query = re.sub(r"[^a-zA-Z0-9]+", "-", query.strip().lower())
        url = f"https://www.freshersworld.com/jobs/jobsearch/{clean_query}-jobs"
        if location:
            clean_loc = re.sub(r"[^a-zA-Z0-9]+", "-", location.strip().lower())
            url += f"-in-{clean_loc}"

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.freshersworld.com/",
        }

        status, html_content = await ScraplingManager.fetch_html_or_json(url, headers=headers, timeout=5.0)
        if status != 200 or not isinstance(html_content, str) or not html_content.strip():
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        job_cards = soup.find_all("div", class_=re.compile(r"job-container|latest-jobs", re.I))
        jobs: list[Job] = []

        for card in job_cards:
            title_tag = card.find(class_=re.compile(r"bold_font|job-title", re.I)) or card.find("h3") or card.find("a")
            company_tag = card.find(class_=re.compile(r"company-name", re.I))
            loc_tag = card.find(class_=re.compile(r"job-location", re.I))
            link_tag = card.find("a", href=True)

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            company_name = company_tag.get_text(strip=True) if company_tag else "Employer"
            loc_text = loc_tag.get_text(strip=True) if loc_tag else "India"
            source_url = link_tag["href"] if link_tag else None

            country, city, remote_flag = normalize_location(loc_text)

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(uuid.uuid4()),
                source="freshersworld",
                source_url=source_url,
                title=title,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.ENTRY,
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["freshersworld", "entry-level", "india"],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_name, "source": "freshersworld"},
            )
            jobs.append(job)
            if len(jobs) >= limit:
                break

        return jobs
