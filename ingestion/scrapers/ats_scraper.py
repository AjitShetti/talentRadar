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
from domain.job_boards import AtsName, BoardRef, board_for_posting
from domain.posting_urls import parse_posting_url, posting_url_key
from ingestion.boards import READERS
from ingestion.boards.base import BoardRead, failed
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

#: The ATSes whose postings are asked about one at a time. Every other ATS is checked by
#: membership: its board is read once through ``READERS`` and the posting's URL is looked
#: for among the board's roles.
PER_POSTING_ATS: frozenset[str] = frozenset({"greenhouse", "lever"})


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

    @staticmethod
    def recheck_client() -> httpx.AsyncClient:
        """The client a re-check reads boards with: the re-check's own timeout, and no redirect is followed."""
        return httpx.AsyncClient(timeout=RECHECK_TIMEOUT_SECONDS, follow_redirects=False)

    @staticmethod
    def board_for(source: str, source_url: str | None) -> BoardRef | None:
        """
        The board a stored posting is checked on, or ``None`` when it is asked about one at a
        time (Greenhouse, Lever), its source names no known board, or its URL names another board.
        """
        ats, _, slug = (source or "").partition(":")
        ats, slug = ats.strip().lower(), slug.strip()
        if not slug or ats in PER_POSTING_ATS or ats not in READERS:
            return None
        return board_for_posting(ats, slug, source_url)

    @classmethod
    async def read_board_once(
        cls,
        board: BoardRef,
        *,
        boards: dict[BoardRef, BoardRead],
        client: httpx.AsyncClient | None = None,
    ) -> BoardRead:
        """
        ``board``'s read: from ``boards`` when this batch has read it already, else read now
        through ``READERS`` and remembered there. A reader that raises is a failed read, and is
        remembered as one, never raised.
        """
        cached = boards.get(board)
        if cached is not None:
            return cached
        try:
            if client is not None:
                read = await READERS[board.ats](client, board)
            else:
                async with cls.recheck_client() as own:
                    read = await READERS[board.ats](own, board)
        except Exception as exc:
            logger.debug("Board %s could not be read: %s", board.source, exc)
            read = failed("error")
        boards[board] = read
        return read

    @classmethod
    async def _check_on_board(
        cls,
        source: str,
        source_url: str | None,
        *,
        boards: dict[BoardRef, BoardRead] | None,
        client: httpx.AsyncClient | None,
    ) -> PostingCheck:
        """
        Membership: is this posting among the roles of its board?

        A role is the same posting when it has the same posting id, each read from its URL by
        ``parse_posting_url`` (so a query string, a trailing slash or the case of an id does not
        matter). A URL that does not parse is compared by its normalised form instead. Only a
        board read whole (``ok`` and ``complete``) can show that a posting is absent: a failed or
        rate-limited read, a 404 for the board (which is not proof that one posting closed) and
        an incomplete read all answer ``ERROR``.
        """
        board = cls.board_for(source, source_url)
        held = parse_posting_url(source_url or "")
        wanted = posting_url_key(source_url)
        if board is None or (held is None and wanted is None):
            return PostingCheck.ERROR
        read = await cls.read_board_once(
            board, boards=boards if boards is not None else {}, client=client
        )
        if read.outcome != "ok" or not read.complete:
            return PostingCheck.ERROR
        for role in read.roles:
            if held is not None:
                listed = parse_posting_url(role.source_url or "")
                if listed is not None and listed.external_id == held.external_id:
                    return PostingCheck.OPEN
            if wanted is not None and posting_url_key(role.source_url) == wanted:
                return PostingCheck.OPEN
        return PostingCheck.CLOSED

    @classmethod
    async def check_posting(
        cls,
        source: str,
        external_id: str,
        *,
        source_url: str | None = None,
        boards: dict[BoardRef, BoardRead] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> PostingCheck:
        """
        Ask the employer's ATS whether it still serves this posting.

        Greenhouse and Lever are asked about the posting by its id. Every other ATS is
        checked by membership of its board (``_check_on_board``), which needs the posting's
        own ``source_url``. ``boards`` is a batch's cache of board reads, so each board is read
        once, and ``client`` is the batch's one HTTP client.
        """
        ats, _, slug = (source or "").partition(":")
        ats, slug = ats.strip().lower(), slug.strip()
        if not slug or not external_id:
            return PostingCheck.ERROR

        if ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{external_id}"
        elif ats == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}/{external_id}"
        else:
            return await cls._check_on_board(
                source, source_url, boards=boards, client=client
            )

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
