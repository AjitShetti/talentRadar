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
from typing import Any

from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from ingestion.scrapers._parsing import parse_html
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


def _epoch_ms_to_datetime(value: object) -> datetime:
    """
    Convert a millisecond epoch timestamp to an aware datetime.

    Boards report posting dates as epoch milliseconds, as free text
    ("a day ago"), or not at all. Anything unparseable falls back to now,
    because a posting with no date is still a posting - and a null here
    would make it sort as infinitely stale.
    """
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now(timezone.utc)


def _seniority_from_years(years: object) -> SeniorityLevel:
    """Map a minimum-years-of-experience figure onto a seniority band."""
    if not isinstance(years, (int, float)):
        return SeniorityLevel.MID
    if years <= 1:
        return SeniorityLevel.JUNIOR
    if years <= 5:
        return SeniorityLevel.MID
    if years <= 9:
        return SeniorityLevel.SENIOR
    return SeniorityLevel.LEAD


def _clean_freshersworld_title(raw: str) -> str:
    """
    Reduce a Freshersworld card title to the role itself.

    The markup renders titles as
    ``"EHS Coordinator Job Opening in <company> at <state>Less"`` - the
    company, the state, and the collapsed "Less"/"More" toggle text are all
    part of the same text node.
    """
    title = re.sub(r"\s*(Less|More)$", "", raw.strip())
    title = re.split(r"\s+Job Opening\s+in\s+", title, maxsplit=1)[0]
    return title.strip(" -|,")


def _title_matches_query(title: str, query: str) -> bool:
    """
    True when a posting title plausibly answers *query*.

    Some boards ignore the role terms in their own search and return a
    generic listing page. Requiring one significant query term in the title
    keeps those out of the results.
    """
    title_low = title.lower()
    terms = [t for t in re.split(r"[^a-z0-9+#.]+", query.lower()) if len(t) > 2]
    if not terms:
        return True
    return any(term in title_low for term in terms)


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

        soup = await parse_html(html_content)
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
        Fetch Foundit India (formerly Monster India) listings from its search API.

        This reads the JSON endpoint the site's own result page calls, rather
        than the rendered HTML. The HTML is a client-side shell: a live fetch
        of ``/srp/results`` returns 252 KB containing a login widget, no
        ``__NEXT_DATA__``, and not one posting - which is why the previous
        class-name scrape parsed zero jobs from a 200 response, the quietest
        possible failure.

        The JSON additionally carries structured experience, salary and skill
        fields that were never recoverable from the markup.
        """
        loc = location or "India"
        params: dict[str, Any] = {
            "start": 0,
            "sort": 1,
            "limit": limit,
            "query": query,
            "locations": loc,
        }
        if is_remote:
            params["workFromHome"] = "true"

        headers = {
            "Accept": "application/json",
            "Referer": "https://www.foundit.in/",
        }

        status, payload = await ScraplingManager.fetch_html_or_json(
            "https://www.foundit.in/middleware/jobsearch",
            headers=headers,
            params=params,
            timeout=8.0,
        )
        if status != 200 or not isinstance(payload, dict):
            return []

        entries = (payload.get("jobSearchResponse") or {}).get("data") or []
        jobs: list[Job] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = (entry.get("title") or "").strip()
            job_id = entry.get("jobId") or entry.get("id")
            if not title or not job_id:
                continue

            company_name = (entry.get("companyName") or "Company").strip()
            loc_text = (entry.get("locations") or loc).strip()

            # Foundit's own applyUrl is an appcast.io click-tracker, which is
            # neither a job board nor stable. The canonical posting lives at
            # /job/<id> - it answers 403 to a scraper and 200 in a browser,
            # which is the shape of a real page behind a bot check rather
            # than a missing one.
            source_url = f"https://www.foundit.in/job/{job_id}"

            country, city, remote_flag = normalize_location(loc_text)

            skills_raw = entry.get("skills") or ""
            skills = [s.strip() for s in skills_raw.split(",") if s.strip()][:12] if isinstance(skills_raw, str) else []

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=str(job_id),
                source="foundit",
                source_url=source_url,
                title=title,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=_seniority_from_years(
                    (entry.get("minimumExperience") or {}).get("years")
                    if isinstance(entry.get("minimumExperience"), dict)
                    else None
                ),
                location_raw=loc_text,
                country=country,
                city=city,
                is_remote=remote_flag or bool(is_remote),
                skills=skills or ([s for s in query.split() if len(s) > 2] if query else []),
                tags=["foundit", "india"],
                posted_at=_epoch_ms_to_datetime(entry.get("createdAt")),
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

        # 7s, and deliberately just under this source's 8s registry budget.
        # Freshersworld ships a ~1.5 MB search page and its response time was
        # measured swinging between 3.4s and 11.2s. On a timeout the fetcher
        # falls through to the httpx tier, which re-downloads all 1.5 MB — so
        # an inner timeout well below the outer one turns one slow response
        # into two, and overruns the budget that was meant to contain it.
        status, html_content = await ScraplingManager.fetch_html_or_json(url, headers=headers, timeout=7.0)
        if status != 200 or not isinstance(html_content, str) or not html_content.strip():
            return []

        soup = await parse_html(html_content)
        job_cards = soup.find_all("div", class_=re.compile(r"job-container|latest-jobs", re.I))
        jobs: list[Job] = []

        for card in job_cards:
            # The title lives in span.wrap-title. The previous selector led
            # with ``bold_font``, which on this page is the class on each
            # *location* link - so every job came back titled "Bangalore".
            title_tag = card.find("span", class_=re.compile(r"wrap-title", re.I)) or card.find(
                "div", class_=re.compile(r"job-new-title", re.I)
            )
            company_tag = card.find("h3", class_=re.compile(r"latest-jobs-title", re.I))
            loc_tag = card.find("span", class_=re.compile(r"job-location", re.I))
            exp_tag = card.find("span", class_=re.compile(r"experience", re.I))
            link_tag = card.find(
                "a",
                href=re.compile(r"freshersworld\.com/jobs/(?!jobsearch)[a-z0-9-]+-\d+$", re.I),
            )

            if not title_tag or not link_tag:
                continue

            title = _clean_freshersworld_title(title_tag.get_text(strip=True))
            if not title:
                continue

            # Freshersworld's slug search ignores the role terms: a query for
            # "Python Developer" returns EHS Coordinator and Desktop Support
            # postings. Surfacing those as search results is worse than
            # surfacing nothing, so anything that does not match the query is
            # dropped here rather than shown to a user.
            if query and not _title_matches_query(title, query):
                continue

            company_name = company_tag.get_text(strip=True) if company_tag else "Employer"
            loc_text = loc_tag.get_text(strip=True).replace("...", "").strip() if loc_tag else "India"
            source_url = str(link_tag["href"])

            country, city, remote_flag = normalize_location(loc_text)
            # "0 to 3 Years" -> the lower bound drives the seniority band.
            exp_text = exp_tag.get_text(strip=True) if exp_tag else ""
            exp_match = re.search(r"(\d+)", exp_text)
            seniority = _seniority_from_years(int(exp_match.group(1)) if exp_match else None)

            # The trailing numeric id in the posting URL is Freshersworld's own
            # job id and is stable. A fresh uuid4 made the same posting look new
            # on every scrape, defeating deduplication.
            id_match = re.search(r"-(\d+)$", source_url)
            external_id = id_match.group(1) if id_match else str(uuid.uuid4())

            job = Job(
                id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                external_id=external_id,
                source="freshersworld",
                source_url=source_url,
                title=title,
                status=JobStatus.ACTIVE,
                employment_type=EmploymentType.FULL_TIME,
                seniority=seniority,
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
