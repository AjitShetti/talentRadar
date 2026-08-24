"""
tests/test_validation.py
~~~~~~~~~~~~~~~~~~~~~~~~
Regression and unit tests for the shared URL validator, seed_db rejection,
and pipeline persistence protection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult
from ingestion.pipeline import persist_parsed
from ingestion.seed_db import extract_job_from_raw, seed_database
from ingestion.validation import (
    BLOCKED_DOMAINS,
    is_valid_job_url,
    validate_job_url,
)


class TestJobURLValidator:
    """Test unit validation rules for host blocklist, allowlist, and path shapes."""

    @pytest.mark.parametrize(
        "url,expected_blocked",
        [
            ("https://en.wikipedia.org/wiki/Software_engineer", True),
            ("https://www.reddit.com/r/cscareerquestions/comments/12345/hiring_thread/", True),
            ("https://youtube.com/watch?v=abc123xyz", True),
            ("https://medium.com/@developer/how-to-pass-coding-interviews", True),
            ("https://quora.com/What-is-the-salary-of-a-software-engineer", True),
            ("https://github.com/vinta/awesome-python", True),
            ("https://twitter.com/tech_jobs/status/123456", True),
            ("https://x.com/tech_jobs/status/123456", True),
        ],
    )
    def test_blocked_non_job_domains(self, url: str, expected_blocked: bool):
        is_valid, reason = validate_job_url(url)
        assert is_valid is False
        assert not is_valid_job_url(url)
        assert "Blocked" in reason or "not a recognized" in reason

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/jobs/software-engineer-jobs-bangalore",
            "https://www.linkedin.com/jobs/search?keywords=python",
            "https://www.linkedin.com/jobs/collections/recommended",
            "https://www.indeed.com/q-python-developer-jobs.html",
            "https://in.indeed.com/jobs?q=python&l=Bangalore",
            "https://in.indeed.com/cmp/TechCorp",
            "https://www.naukri.com/python-developer-jobs-in-bangalore",
            "https://www.naukri.com/browse-jobs",
            "https://boards.greenhouse.io/stripe",
            "https://boards.greenhouse.io/stripe/",
            "https://jobs.lever.co/dropbox",
            "https://jobs.ashbyhq.com/linear",
            "https://cutshort.io/jobs",
            "https://cutshort.io/salaries",
        ],
    )
    def test_rejected_search_and_listing_pages(self, url: str):
        is_valid, reason = validate_job_url(url)
        assert is_valid is False
        assert not is_valid_job_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/jobs/view/3912345678",
            "https://www.linkedin.com/jobs/view/software-engineer-at-swiggy-391234",
            "https://in.indeed.com/viewjob?jk=789012abcdef",
            "https://www.indeed.com/viewjob?jk=1234567890abcdef",
            "https://boards.greenhouse.io/stripe/jobs/4567890",
            "https://boards.greenhouse.io/airbnb/jobs/123456",
            "https://jobs.lever.co/dropbox/8b51ef92-1234-4567-8901-abcdef123456",
            "https://jobs.lever.co/pinterest/frontend-engineer-123",
            "https://jobs.ashbyhq.com/linear/1a2b3c4d-5e6f-7890-abcd",
            "https://www.naukri.com/job-listings-senior-data-scientist-tech-corp-mumbai-654321",
            "https://cutshort.io/job/senior-python-developer-12345",
            "https://jobs.webscale.talentradar.internal/fs-01",
        ],
    )
    def test_accepted_valid_job_postings(self, url: str):
        is_valid, reason = validate_job_url(url)
        assert is_valid is True, f"Failed for {url}: {reason}"
        assert is_valid_job_url(url) is True


class TestRegressionContaminatedSeedCorp:
    """Ensure contaminated fixtures never get parsed or inserted into the DB."""

    def test_extract_job_from_raw_rejects_wikipedia(self):
        raw = RawJobResult(
            title="Software Engineering - Wikipedia",
            url="https://en.wikipedia.org/wiki/Software_engineering",
            content="Software engineering is an engineering branch associated with development of software product.",
        )
        with pytest.raises(ValueError, match="Cannot extract job from invalid URL"):
            extract_job_from_raw(raw)

    def test_extract_job_from_raw_rejects_reddit(self):
        raw = RawJobResult(
            title="Is tech hiring dead in 2026? : r/cscareerquestions",
            url="https://www.reddit.com/r/cscareerquestions/comments/abc123/is_tech_hiring_dead/",
            content="Discussion thread about software engineer job market in Bangalore and USA.",
        )
        with pytest.raises(ValueError, match="Cannot extract job from invalid URL"):
            extract_job_from_raw(raw)

    def test_extract_job_from_raw_rejects_indeed_search_page(self):
        raw = RawJobResult(
            title="Python Developer Jobs, Employment in Bangalore | Indeed.com",
            url="https://www.indeed.com/q-python-developer-jobs.html",
            content="Search 1,234 Python Developer jobs available in Bangalore on Indeed.com.",
        )
        with pytest.raises(ValueError, match="Cannot extract job from invalid URL"):
            extract_job_from_raw(raw)

    def test_extract_job_from_raw_accepts_valid_posting(self):
        raw = RawJobResult(
            title="Senior Backend Engineer - Python | Swiggy",
            url="https://www.linkedin.com/jobs/view/3912345678",
            content="Swiggy is hiring a Senior Backend Engineer with Python, FastAPI, and PostgreSQL experience in Bangalore.",
        )
        pjd = extract_job_from_raw(raw)
        assert pjd.title == "Senior Backend Engineer - Python"
        assert pjd.company == "Swiggy"
        assert "Python" in pjd.skills
        assert pjd.location == "Bangalore"
        assert pjd.source_url == "https://www.linkedin.com/jobs/view/3912345678"

    @pytest.mark.asyncio
    async def test_persist_parsed_filters_out_invalid_urls(self):
        """Verify persist_parsed skips Wikipedia, Reddit, and search listing pages."""
        items = [
            ParsedJobDescription(
                title="Wikipedia Article",
                company="Wikipedia",
                source_url="https://en.wikipedia.org/wiki/Software_engineer",
                raw_text="Article about software engineering.",
            ),
            ParsedJobDescription(
                title="Reddit Discussion",
                company="Reddit",
                source_url="https://www.reddit.com/r/cscareerquestions/comments/123",
                raw_text="Reddit thread content.",
            ),
            ParsedJobDescription(
                title="Indeed Search Page",
                company="Indeed",
                source_url="https://www.indeed.com/q-python-jobs.html",
                raw_text="Search result page.",
            ),
            ParsedJobDescription(
                title="Senior Java Developer",
                company="Stripe",
                location="Bengaluru, India",
                source_url="https://boards.greenhouse.io/stripe/jobs/987654",
                raw_text="Stripe is looking for a Senior Java Developer.",
            ),
        ]

        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        mock_uow.ingestion_runs.create = AsyncMock(return_value=MagicMock(id="run-123"))
        mock_uow.ingestion_runs.finish = AsyncMock()
        mock_uow.companies.upsert_by_domain = AsyncMock(return_value=(MagicMock(id="comp-123"), True))
        mock_uow.jobs.upsert_by_external_id = AsyncMock(return_value=(MagicMock(id="job-123"), True))
        mock_uow.jobs.set_embedding_id = AsyncMock()

        with patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_uow), \
             patch("ingestion.pipeline.UnitOfWork", return_value=mock_uow), \
             patch("ingestion.pipeline.ChromaJobStore") as MockChroma:
            
            mock_chroma_instance = MagicMock()
            mock_chroma_instance.add_batch.return_value = 1
            MockChroma.return_value = mock_chroma_instance

            counts = await persist_parsed(items, source="test_source", run_id="run-123")

            # 3 invalid URLs should be skipped, 1 valid URL inserted
            assert counts["skipped"] == 3
            assert counts["inserted"] == 1
            assert mock_uow.jobs.upsert_by_external_id.call_count == 1
            call_kwargs = mock_uow.jobs.upsert_by_external_id.call_args[1]
            assert call_kwargs["defaults"]["source_url"] == "https://boards.greenhouse.io/stripe/jobs/987654"
