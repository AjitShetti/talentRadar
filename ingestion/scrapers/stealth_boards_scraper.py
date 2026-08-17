"""
ingestion/scrapers/stealth_boards_scraper.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Stealth scrapers for heavily bot-protected and high-volume Indian job boards:
- Naukri.com (India's #1 job portal, using Camoufox anti-detect browser hydration)
- Indeed India (in.indeed.com, using Scrapling TLS/HTTP2 browser impersonation)
- Instahyre (India's premier tech and startup hiring network)

Utilizes ScraplingManager with resource blocking to keep response times fast
and consistently bypass Cloudflare, Akamai, and bot challenges.
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
    "jaipur": "Jaipur",
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
    Stealth scraper using Camoufox & Scrapling for anti-bot protected portals.
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
        Scrapes Naukri.com using Camoufox anti-detect browser with client-side hydration.
        """
        clean_role = re.sub(r"[^a-zA-Z0-9]+", "-", query.strip().lower()).strip("-")
        clean_loc = re.sub(r"[^a-zA-Z0-9]+", "-", (location or "india").strip().lower()).strip("-")
        
        # Build Naukri search URL
        url = f"https://www.naukri.com/{clean_role}-jobs-in-{clean_loc}"
        if is_remote:
            url += "?wfhType=0"

        # Fetch using Camoufox stealth browser with 4s wait for Next.js hydration
        status, html_content = await ScraplingManager.fetch_stealth(
            url,
            timeout=12.0,
            wait_for_selector="a.title",
            wait_seconds=4.0,
        )

        jobs: list[Job] = []
        seen_urls: set[str] = set()

        if status == 200 and html_content.strip():
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Find all job title links
            title_links = soup.find_all("a", class_=re.compile(r"title", re.I))
            if not title_links:
                title_links = [a for a in soup.find_all("a", href=True) if "job-listings" in a["href"]]

            for a_tag in title_links:
                title = a_tag.get_text(strip=True)
                source_url = a_tag.get("href", "")
                if not title or not source_url or source_url in seen_urls:
                    continue
                seen_urls.add(source_url)

                # Climb up container
                container = a_tag
                for _ in range(4):
                    if container.parent:
                        container = container.parent

                # Extract company
                comp_tag = container.find("a", class_=re.compile(r"comp-name|compName", re.I))
                company_name = comp_tag.get_text(strip=True) if comp_tag else "Company"

                # Extract location
                loc_tag = container.find("span", class_=re.compile(r"locWdth|loc-wrap|location", re.I))
                loc_text = loc_tag.get_text(strip=True) if loc_tag else (location or "India")

                # Extract salary
                sal_tag = container.find("span", class_=re.compile(r"sal-wrap|salary|ni-job-tuple-icon-srp-rupee", re.I))
                salary_text = sal_tag.get_text(strip=True) if sal_tag else None

                # Extract experience
                exp_tag = container.find("span", class_=re.compile(r"expwdth|exp-wrap|experience", re.I))
                exp_text = exp_tag.get_text(strip=True) if exp_tag else None

                # Extract tags / skills
                tag_items = container.find_all("li", class_=re.compile(r"tags-gt|tag", re.I))
                extracted_tags = [t.get_text(strip=True) for t in tag_items if t.get_text(strip=True)]
                if not extracted_tags and query:
                    extracted_tags = [s for s in query.split() if len(s) > 2]

                country, city, remote_flag = normalize_location(loc_text)

                job = Job(
                    id=uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    external_id=str(uuid.uuid4()),
                    source="naukri",
                    source_url=source_url,
                    title=title,
                    description_raw=None,
                    description_clean=f"Experience: {exp_text}" if exp_text else None,
                    status=JobStatus.ACTIVE,
                    employment_type=EmploymentType.FULL_TIME,
                    seniority=SeniorityLevel.MID,
                    location_raw=loc_text,
                    country=country,
                    city=city,
                    is_remote=remote_flag or bool(is_remote),
                    salary_raw=salary_text,
                    skills=extracted_tags,
                    tags=["naukri", "india"] + extracted_tags[:3],
                    posted_at=datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc),
                    extra_metadata={"company_name": company_name, "source": "naukri", "experience": exp_text},
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
        Scrapes Indeed India (in.indeed.com) using Scrapling TLS browser impersonation.
        """
        loc = location or "India"
        encoded_query = urllib.parse.quote_plus(query)
        encoded_loc = urllib.parse.quote_plus(loc)

        url = f"https://in.indeed.com/jobs?q={encoded_query}&l={encoded_loc}"
        if is_remote:
            url += "&sc=0kf%3Aattr(DS3S6)%3B"  # Indeed Remote filter

        headers = {
            "Referer": "https://www.google.com/",
            "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        status, html_content = await ScraplingManager.fetch_html_or_json(
            url,
            headers=headers,
            timeout=8.0,
            impersonate="chrome124",
        )
        if status != 200 or not isinstance(html_content, str) or not html_content.strip():
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        job_cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|cardOutline|jobsearch-JobGrid-item", re.I))
        if not job_cards:
            job_cards = soup.find_all("td", class_=re.compile(r"resultContent", re.I))

        jobs: list[Job] = []
        seen_titles: set[str] = set()

        for card in job_cards:
            title_tag = (
                card.find("h2", class_=re.compile(r"jobTitle", re.I))
                or card.find("a", class_=re.compile(r"jcs-JobTitle", re.I))
                or card.find("h2")
            )
            comp_tag = card.find(attrs={"data-testid": "company-name"}) or card.find("span", class_=re.compile(r"companyName", re.I))
            loc_tag = card.find(attrs={"data-testid": "text-location"}) or card.find("div", class_=re.compile(r"companyLocation", re.I))
            snippet_tag = card.find(class_=re.compile(r"job-snippet|underShelfFooter", re.I))
            link_tag = card.find("a", class_=re.compile(r"jcs-JobTitle", re.I)) or card.find("a", href=True)

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            company_name = comp_tag.get_text(strip=True) if comp_tag else "Company"
            loc_text = loc_tag.get_text(strip=True) if loc_tag else loc
            desc_text = snippet_tag.get_text(strip=True) if snippet_tag else None
            
            source_url = None
            if link_tag and link_tag.has_attr("href"):
                href = link_tag["href"]
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

    @classmethod
    async def search_instahyre(
        cls,
        query: str,
        location: str | None = None,
        is_remote: bool | None = None,
        limit: int = 15,
    ) -> list[Job]:
        """
        Scrapes Instahyre's tech search API for high-growth startups and tech companies in India.
        """
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://www.instahyre.com/api/v1/job_search?skills={encoded_query}"

        status, data = await ScraplingManager.fetch_html_or_json(
            url,
            timeout=5.0,
            impersonate="chrome124",
        )
        if status != 200 or not isinstance(data, dict):
            return []

        objects = data.get("objects", [])
        jobs: list[Job] = []

        for obj in objects:
            title = obj.get("title", "")
            if not title:
                continue

            employer = obj.get("employer", {}) or {}
            company_name = employer.get("company_name", "Company")
            locations = obj.get("locations", [])
            loc_text = ", ".join(locations) if isinstance(locations, list) and locations else (location or "India")
            
            job_id = obj.get("id")
            landing_url = f"https://www.instahyre.com/job-{job_id}" if job_id else "https://www.instahyre.com"

            country, city, remote_flag = normalize_location(loc_text)

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(job_id or uuid.uuid4()),
                source="instahyre",
                source_url=landing_url,
                title=title,
                description_raw=None,
                description_clean=obj.get("description"),
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=SeniorityLevel.MID,
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                skills=[s for s in query.split() if len(s) > 2] if query else [],
                tags=["instahyre", "india", "startup"],
                posted_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                extra_metadata={"company_name": company_name, "source": "instahyre"},
            )
            jobs.append(job)
            if len(jobs) >= limit:
                break

        return jobs
