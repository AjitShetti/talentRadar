"""
tests/test_realtime_scraper.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit and integration tests for Scrapling manager, ATS scrapers, Indian board scrapers,
search caching service, and the real-time scraper engine.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from ingestion.scrapling_manager import ScraplingManager
from ingestion.scrapers.ats_scraper import ATSScraper, matches_location, matches_query
from ingestion.scrapers.indian_boards_scraper import IndianBoardsScraper, normalize_location
from ingestion.scrapers.stealth_boards_scraper import StealthBoardsScraper
from ingestion.engine import RealtimeScraperEngine, compute_job_dedup_hash, job_to_dict
from services.search_cache_service import SearchCacheService


# ── Location & Query Matching Tests ──────────────────────────────────────────

def test_matches_query():
    assert matches_query("Senior Python Backend Engineer", "python") is True
    assert matches_query("React Frontend Developer", "react") is True
    assert matches_query("DevOps Engineer", "python") is False
    assert matches_query("Fullstack Software Engineer", "") is True


def test_matches_location_india():
    # Bengaluru synonym match
    matched, is_remote, country, city = matches_location("Bangalore, India", "Bengaluru", None)
    assert matched is True
    assert city == "Bengaluru"
    assert country == "India"

    # Gurgaon to Delhi NCR match
    matched, is_remote, country, city = matches_location("Gurgaon office", "Delhi NCR", None)
    assert matched is True
    assert city == "Delhi NCR"

    # Worldwide Remote match
    matched, is_remote, country, city = matches_location("Remote - Worldwide", None, True)
    assert matched is True
    assert is_remote is True


def test_normalize_location():
    country, city, is_remote = normalize_location("Bengaluru, Karnataka")
    assert country == "India"
    assert city == "Bengaluru"
    assert is_remote is False

    country, city, is_remote = normalize_location("Worldwide Remote")
    assert country == "Global"
    assert is_remote is True


# ── Search Cache Service Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_cache_service():
    query = "Python Developer"
    location = "Bengaluru"
    is_remote = False

    key = SearchCacheService.make_cache_key(query, location, is_remote)
    assert key.startswith("talentradar:search:")

    test_payload = {
        "jobs": [{"id": "1", "title": "Python Dev", "company_name": "Acme"}],
        "sources_stats": {"ats": {"latency_ms": 300, "status": "success"}},
    }

    await SearchCacheService.set_cached_search(query, location, is_remote, test_payload, ttl_seconds=60)
    cached = await SearchCacheService.get_cached_search(query, location, is_remote)

    assert cached is not None
    assert len(cached["jobs"]) == 1
    assert cached["jobs"][0]["title"] == "Python Dev"


# ── ATS Scraper Tests with Mocking ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_greenhouse_scraper_mock():
    mock_gh_response = {
        "jobs": [
            {
                "id": 12345,
                "title": "Senior Python Developer",
                "location": {"name": "Bengaluru, India"},
                "absolute_url": "https://boards.greenhouse.io/test/jobs/12345",
            },
            {
                "id": 67890,
                "title": "Marketing Manager",
                "location": {"name": "New York, USA"},
                "absolute_url": "https://boards.greenhouse.io/test/jobs/67890",
            },
        ]
    }

    with patch.object(ScraplingManager, "fetch_html_or_json", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (200, mock_gh_response)
        jobs = await ATSScraper.fetch_greenhouse_company("test", "python", "Bengaluru", None)

        assert len(jobs) == 1
        assert jobs[0].title == "Senior Python Developer"
        assert jobs[0].city == "Bengaluru"
        assert jobs[0].source == "greenhouse:test"


# ── Indian Boards Scraper Mock Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_linkedin_guest_scraper_mock():
    mock_html = """
    <ul>
        <li>
            <div class="base-card">
                <h3 class="base-search-card__title">Python Backend Engineer</h3>
                <h4 class="base-search-card__subtitle">TechCorp India</h4>
                <span class="job-search-card__location">Bengaluru, Karnataka, India</span>
                <a class="base-card__full-link" href="https://in.linkedin.com/jobs/view/python-engineer-123456"></a>
            </div>
        </li>
    </ul>
    """

    with patch.object(ScraplingManager, "fetch_html_or_json", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (200, mock_html)
        jobs = await IndianBoardsScraper.search_linkedin_guest("Python", "Bengaluru", None)

        assert len(jobs) == 1
        assert jobs[0].title == "Python Backend Engineer"
        assert jobs[0].city == "Bengaluru"
        assert jobs[0].source == "linkedin"
        assert jobs[0].extra_metadata["company_name"] == "TechCorp India"


# ── Deduplication & Engine Streaming Tests ───────────────────────────────────

def test_compute_job_dedup_hash():
    job1 = Job(
        title="Python Dev",
        company_id="00000000-0000-0000-0000-000000000001",
        source="linkedin",
        city="Bengaluru",
        extra_metadata={"company_name": "Razorpay"},
    )
    job2 = Job(
        title="Python Dev",
        company_id="00000000-0000-0000-0000-000000000002",
        source="naukri",
        city="Bengaluru",
        extra_metadata={"company_name": "Razorpay"},
    )
    # Different sources and IDs, but same role at same company in same city -> identical dedup hash
    assert compute_job_dedup_hash(job1) == compute_job_dedup_hash(job2)


@pytest.mark.asyncio
async def test_realtime_scraper_engine_stream():
    dummy_job = Job(
        title="Software Engineer - Python",
        company_id="00000000-0000-0000-0000-000000000001",
        source="greenhouse:test",
        city="Bengaluru",
        extra_metadata={"company_name": "Figma"},
    )

    with patch.object(ATSScraper, "search_all_ats", new_callable=AsyncMock) as mock_ats, \
         patch.object(IndianBoardsScraper, "search_linkedin_guest", new_callable=AsyncMock) as mock_li, \
         patch.object(IndianBoardsScraper, "search_foundit_india", new_callable=AsyncMock) as mock_foundit, \
         patch.object(IndianBoardsScraper, "search_freshersworld", new_callable=AsyncMock) as mock_fw, \
         patch.object(StealthBoardsScraper, "search_naukri", new_callable=AsyncMock) as mock_naukri, \
         patch.object(StealthBoardsScraper, "search_indeed_india", new_callable=AsyncMock) as mock_indeed:

        mock_ats.return_value = [dummy_job]
        mock_li.return_value = []
        mock_foundit.return_value = []
        mock_fw.return_value = []
        mock_naukri.return_value = []
        mock_indeed.return_value = []

        events = []
        async for chunk in RealtimeScraperEngine.stream_search("Python", "Bengaluru", False, force_refresh=True):
            events.append(chunk)

        event_names = [e["event"] for e in events]
        assert "init" in event_names
        assert "chunk" in event_names
        assert "done" in event_names
