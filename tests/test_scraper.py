"""
tests/test_scraper.py
~~~~~~~~~~~~~~~~~~~~~~
Unit and integration tests for TavilyJobScraper Indian job sources (LinkedIn, Naukri, Indeed),
source domain detection, task defaults, and INR currency normalisation.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from ingestion.scrapers.tavily_client import (
    TavilyJobScraper,
    detect_source_from_url,
    INDIAN_JOB_DOMAINS,
)
from ingestion.parsers.schemas import RawJobResult, ParsedJobDescription
from ingestion.parsers.jd_parser import JDParser
from ingestion.scrapers.indian_boards_scraper import INDIAN_CITY_SYNONYMS


MOCK_TAVILY_INDIAN_RESPONSE = {
    "results": [
        {
            "title": "Software Development Engineer II - LinkedIn Jobs",
            "url": "https://www.linkedin.com/jobs/view/software-engineer-at-swiggy-bangalore-391234",
            "content": "Swiggy is hiring a Software Engineer in Bangalore, India. Skills: Python, Django, PostgreSQL, Redis. Salary: ₹1,800,000 - ₹2,800,000 / year.",
            "score": 0.95,
            "published_date": "2026-07-01",
            "raw_content": "Full page text: Swiggy Senior Software Engineer position in Bangalore, India. Requirements: 4+ years Python experience...",
        },
        {
            "title": "Senior Data Scientist - Naukri.com",
            "url": "https://www.naukri.com/job-listings-senior-data-scientist-tech-corp-mumbai-654321",
            "content": "TechCorp Mumbai is looking for a Senior Data Scientist in Mumbai, India. Skills: Python, PyTorch, SQL, Machine Learning. Compensation: Rs 25,000,00 per annum.",
            "score": 0.92,
            "published_date": "2026-07-05",
            "raw_content": "Full text: TechCorp hiring Senior Data Scientist in Mumbai, India...",
        },
        {
            "title": "Full Stack Developer - Indeed India",
            "url": "https://in.indeed.com/viewjob?jk=789012abcdef",
            "content": "FinTech India is hiring a Full Stack Developer in Hyderabad, India. React, Node.js, AWS. Salary: Rupees 1,500,000 - 2,200,000 per year.",
            "score": 0.89,
            "published_date": "2026-07-10",
            "raw_content": "Full text: FinTech India hiring Full Stack Developer in Hyderabad, India...",
        },
    ]
}


class TestIndianSourceDetection:
    def test_detect_source_linkedin(self):
        url = "https://www.linkedin.com/jobs/view/software-engineer-bangalore-123"
        assert detect_source_from_url(url) == "linkedin"

    def test_detect_source_naukri(self):
        url = "https://www.naukri.com/job-listings-data-scientist-mumbai-456"
        assert detect_source_from_url(url) == "naukri"

    def test_detect_source_indeed_in(self):
        url = "https://in.indeed.com/viewjob?jk=789012"
        assert detect_source_from_url(url) == "indeed"

    def test_detect_source_indeed_com(self):
        url = "https://www.indeed.com/viewjob?jk=345678"
        assert detect_source_from_url(url) == "indeed"

    def test_detect_source_fallback(self):
        url = "https://careers.company.com/job/123"
        assert detect_source_from_url(url) == "tavily_search"

    def test_detect_source_empty(self):
        assert detect_source_from_url("") == "tavily_search"


class TestTavilyScraperIndianJobs:
    @pytest.fixture
    def mock_scraper(self):
        with patch("ingestion.scrapers.tavily_client.httpx.Client") as MockClient:
            mock_http = MockClient.return_value
            mock_resp = MagicMock()
            mock_resp.json.return_value = MOCK_TAVILY_INDIAN_RESPONSE
            mock_resp.raise_for_status = MagicMock()
            mock_http.post.return_value = mock_resp
            scraper = TavilyJobScraper(api_key="fake-key")
            scraper._client = mock_http
            yield scraper, mock_http

    def test_search_jobs_passes_include_domains_payload(self, mock_scraper):
        scraper, mock_http = mock_scraper
        results = scraper.search_jobs(
            role="Software Engineer",
            location="Bangalore",
            count=10,
            include_domains=INDIAN_JOB_DOMAINS,
        )

        assert mock_http.post.called
        call_kwargs = mock_http.post.call_args[1]
        payload = call_kwargs["json"]

        assert payload["include_domains"] == INDIAN_JOB_DOMAINS
        assert "Software Engineer" in payload["query"]
        assert "Bangalore" in payload["query"]

        assert len(results) == 3
        assert results[0].url.startswith("https://www.linkedin.com")
        assert results[1].url.startswith("https://www.naukri.com")
        assert results[2].url.startswith("https://in.indeed.com")

        # Verify source detection for each result
        sources = [detect_source_from_url(r.url) for r in results]
        assert sources == ["linkedin", "naukri", "indeed"]

    def test_indian_location_fetching_and_parsing_returns_at_least_one_job(self):
        """
        Verify that fetching and parsing mock Tavily API results from LinkedIn, Naukri,
        and Indeed returns >= 1 job from an Indian location with INR currency and source attribution.
        """
        raw_results = [
            RawJobResult(
                title=item["title"],
                url=item["url"],
                content=item["content"],
                score=item["score"],
                raw_content=item["raw_content"],
            )
            for item in MOCK_TAVILY_INDIAN_RESPONSE["results"]
        ]

        mock_parsed_swiggy = json.dumps({
            "title": "Software Development Engineer II",
            "company": "Swiggy",
            "skills": ["Python", "Django", "PostgreSQL", "Redis"],
            "experience": "4+ years",
            "location": "Bangalore, India",
            "is_remote": False,
            "salary": "₹1,800,000 - ₹2,800,000 / year",
            "salary_min": 1800000,
            "salary_max": 2800000,
            "salary_currency": "₹",
            "employment_type": "full_time",
            "seniority": "mid",
        })

        with patch("ingestion.parsers.jd_parser.Groq") as MockGroq:
            mock_groq = MockGroq.return_value
            mock_groq.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=mock_parsed_swiggy))]
            )

            parser = JDParser(api_key="fake-groq-key")
            parsed_jobs = parser.batch_parse(raw_results)

            assert len(parsed_jobs) >= 1
            indian_locations = {"Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"}

            has_indian_location = any(
                any(city in (job.location or "") for city in indian_locations)
                for job in parsed_jobs
            )
            assert has_indian_location, "Expected at least 1 job from an Indian location"

            first_job = parsed_jobs[0]
            # Currency symbol '₹' should be normalised to 'INR' by ParsedJobDescription validator
            assert first_job.salary_currency == "INR"
            assert "Bangalore" in (first_job.location or "")

            # Source attribution check
            source_name = detect_source_from_url(first_job.source_url or "")
            assert source_name == "linkedin"


class TestCurrencyNormalisation:
    def test_inr_currency_symbols(self):
        p1 = ParsedJobDescription(
            title="Dev", company="C", raw_text="text", salary_currency="₹"
        )
        assert p1.salary_currency == "INR"

        p2 = ParsedJobDescription(
            title="Dev", company="C", raw_text="text", salary_currency="Rs"
        )
        assert p2.salary_currency == "INR"

        p3 = ParsedJobDescription(
            title="Dev", company="C", raw_text="text", salary_currency="Rs."
        )
        assert p3.salary_currency == "INR"

        p4 = ParsedJobDescription(
            title="Dev", company="C", raw_text="text", salary_currency="Rupees"
        )
        assert p4.salary_currency == "INR"

        p5 = ParsedJobDescription(
            title="Dev", company="C", raw_text="text", salary_currency="INR"
        )
        assert p5.salary_currency == "INR"


class TestCrawlerIndianTaskDefaults:
    def test_default_indian_locations_contain_major_hubs(self):
        assert "bangalore" in INDIAN_CITY_SYNONYMS
        assert "mumbai" in INDIAN_CITY_SYNONYMS
        assert "delhi" in INDIAN_CITY_SYNONYMS
        assert "hyderabad" in INDIAN_CITY_SYNONYMS
        assert "pune" in INDIAN_CITY_SYNONYMS
        assert "india" in INDIAN_CITY_SYNONYMS

    def test_default_indian_domains_contain_portals(self):
        assert "linkedin.com" in INDIAN_JOB_DOMAINS
        assert "naukri.com" in INDIAN_JOB_DOMAINS
        assert "indeed.com" in INDIAN_JOB_DOMAINS
        assert "in.indeed.com" in INDIAN_JOB_DOMAINS
