"""
ingestion/scrapers/ats_scraper.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Direct high-speed scraper for modern ATS platforms:
- Greenhouse (boards-api.greenhouse.io)
- Ashby (api.ashbyhq.com)
- Lever (api.lever.co)

These APIs return structured JSON in 200-500ms with zero anti-bot challenges.
Provides real openings from top tech startups & global companies hiring in India / Remote.

Reading a board is ``ingestion/boards``' job, the same reader the scheduled sweep
uses, so a search and the sweep parse a posting identically. This module only
decides which of a board's roles match a search.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from enum import Enum
from typing import Any

import httpx

from domain.entities import Job
from domain.job_boards import AtsName, BoardRef
from ingestion.boards import READERS
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


class PostingCheck(str, Enum):
    """Outcome of asking an ATS whether it still serves one posting."""

    OPEN = "open"
    CLOSED = "closed"
    # Could not tell. Never treated as closed: a rate limit or an outage is
    # not evidence that an employer stopped hiring.
    ERROR = "error"


#: A live search waits at most this long for one board.
_BOARD_TIMEOUT_SECONDS = 2.0


def _board_client() -> httpx.AsyncClient:
    """The client live search reads boards with. Tests replace it with a replaying one."""
    return httpx.AsyncClient(timeout=_BOARD_TIMEOUT_SECONDS, follow_redirects=False)


RECHECK_TIMEOUT_SECONDS = 5.0
_RETRY_PAUSE_SECONDS = 0.2
_GONE_STATUSES = frozenset({404, 410})


class ATSScraper:
    """
    High-speed aggregator querying Greenhouse, Ashby, and Lever job boards in parallel.
    """

    # ── re-checking a single posting ────────────────────────────────────────

    @classmethod
    async def _fetch_for_recheck(cls, url: str) -> tuple[int, Any]:
        """One GET with a single retry on a timeout or a 5xx. Never raises."""
        status: int = 0
        data: Any = ""
        for attempt in range(2):
            try:
                status, data = await ScraplingManager.fetch_html_or_json(
                    url, timeout=RECHECK_TIMEOUT_SECONDS
                )
            except Exception as exc:
                logger.debug("Re-check of %s failed: %s", url, exc)
                status, data = 0, ""
            if status and status < 500:
                break
            if attempt == 0:
                await asyncio.sleep(_RETRY_PAUSE_SECONDS)
        return status, data

    @classmethod
    async def fetch_ashby_open_ids(cls, company_slug: str) -> set[str] | None:
        """
        Ids currently on an Ashby board, or ``None`` when the board could not
        be read. Ashby has no per-posting endpoint, so membership of the board
        is the only check available.
        """
        status, data = await cls._fetch_for_recheck(
            f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
        )
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            return None
        return {str(item.get("id")) for item in data["jobs"] if isinstance(item, dict)}

    @classmethod
    async def check_posting(
        cls,
        source: str,
        external_id: str,
        *,
        ashby_boards: dict[str, set[str] | None] | None = None,
    ) -> PostingCheck:
        """
        Ask the employer's ATS whether it still serves this posting.

        ``ashby_boards`` lets a batch read each Ashby board once instead of
        once per posting.
        """
        ats, _, slug = (source or "").partition(":")
        ats, slug = ats.strip().lower(), slug.strip()
        if not slug or not external_id:
            return PostingCheck.ERROR

        if ats == "ashby":
            if ashby_boards is not None and slug in ashby_boards:
                open_ids = ashby_boards[slug]
            else:
                open_ids = await cls.fetch_ashby_open_ids(slug)
                if ashby_boards is not None:
                    ashby_boards[slug] = open_ids
            if open_ids is None:
                return PostingCheck.ERROR
            return PostingCheck.OPEN if external_id in open_ids else PostingCheck.CLOSED

        if ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{external_id}"
        elif ats == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}/{external_id}"
        else:
            return PostingCheck.ERROR

        status, data = await cls._fetch_for_recheck(url)
        if status in _GONE_STATUSES:
            return PostingCheck.CLOSED
        # A 200 that is not the posting itself is a challenge page or an
        # error envelope, and proves nothing either way.
        if status == 200 and isinstance(data, dict) and data.get("id") is not None:
            return PostingCheck.OPEN
        return PostingCheck.ERROR

    # ── searching boards ─────────────────────────────────────────────────────

    @classmethod
    async def _read_board(
        cls, ats: AtsName, company_slug: str, client: httpx.AsyncClient | None
    ) -> list[Job]:
        """Every role on one board, from the same reader the scheduled sweep uses."""
        ref = BoardRef(ats, company_slug)
        if client is not None:
            return (await READERS[ats](client, ref)).roles
        async with _board_client() as own:
            return (await READERS[ats](own, ref)).roles

    @staticmethod
    def _select(
        roles: list[Job],
        query: str,
        location: str | None,
        is_remote: bool | None,
        *,
        trust_remote_flag: bool,
    ) -> list[Job]:
        """
        The roles matching a search's query and location.

        ``trust_remote_flag`` keeps a role the ATS itself flags remote even when its
        location text does not match (Ashby's ``isRemote``, Lever's ``workplaceType``);
        Greenhouse states no such flag and goes by the text alone.
        """
        kept: list[Job] = []
        for role in roles:
            if not matches_query(role.title, query):
                continue
            matched, job_remote, country, city = matches_location(
                role.location_raw or "", location, is_remote
            )
            flagged = trust_remote_flag and role.is_remote
            if not matched and not flagged:
                continue
            kept.append(
                role.model_copy(
                    update={
                        "country": country or "India",
                        "city": city or None,
                        "is_remote": job_remote or flagged,
                    }
                )
            )
        return kept

    @classmethod
    async def fetch_greenhouse_company(
        cls,
        company_slug: str,
        query: str,
        location: str | None,
        is_remote: bool | None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[Job]:
        """Fetch and filter jobs from a Greenhouse public board."""
        roles = await cls._read_board("greenhouse", company_slug, client)
        return cls._select(roles, query, location, is_remote, trust_remote_flag=False)

    @classmethod
    async def fetch_ashby_company(
        cls,
        company_slug: str,
        query: str,
        location: str | None,
        is_remote: bool | None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[Job]:
        """Fetch and filter jobs from an Ashby public board."""
        roles = await cls._read_board("ashby", company_slug, client)
        return cls._select(roles, query, location, is_remote, trust_remote_flag=True)

    @classmethod
    async def fetch_lever_company(
        cls,
        company_slug: str,
        query: str,
        location: str | None,
        is_remote: bool | None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[Job]:
        """Fetch and filter jobs from a Lever public board."""
        roles = await cls._read_board("lever", company_slug, client)
        return cls._select(roles, query, location, is_remote, trust_remote_flag=True)

    @classmethod
    async def search_all_ats(cls, query: str, location: str | None = None, is_remote: bool | None = None) -> list[Job]:
        """
        Queries all configured Greenhouse, Ashby, and Lever companies concurrently with individual timeouts.
        """
        async def _safe_fetch(coro: Awaitable[list[Job]]) -> list[Job]:
            try:
                return await asyncio.wait_for(coro, timeout=2.0)
            except Exception:
                return []

        # One client for the whole fan-out: three hosts, one connection pool each.
        async with _board_client() as client:
            tasks = []
            for c in GREENHOUSE_COMPANIES:
                tasks.append(_safe_fetch(cls.fetch_greenhouse_company(c, query, location, is_remote, client=client)))
            for c in ASHBY_COMPANIES:
                tasks.append(_safe_fetch(cls.fetch_ashby_company(c, query, location, is_remote, client=client)))
            for c in LEVER_COMPANIES:
                tasks.append(_safe_fetch(cls.fetch_lever_company(c, query, location, is_remote, client=client)))

            results = await asyncio.gather(*tasks, return_exceptions=True)
        all_jobs: list[Job] = []
        for res in results:
            if isinstance(res, list):
                all_jobs.extend(res)
        return all_jobs
