"""
tests/test_celery_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for Celery background tasks in the ingestion pipeline.

Covers:
- run_crawler task with default arguments
- _run_pipeline happy path with mocked external calls
- Error handling in the pipeline
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingestion.parsers.schemas import RawJobResult


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_tavily_scraper():
    """Mock TavilyJobScraper for pipeline tests."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.search_jobs = MagicMock(return_value=[
        RawJobResult(
            title="Python Engineer",
            url="https://linkedin.com/jobs/123",
            content="Looking for a Python engineer",
            score=0.9,
        )
    ])
    mock.save_raw = MagicMock(return_value=[Path("/tmp/test_run/123.json")])
    return mock


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork for pipeline tests."""
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.ingestion_runs.create = AsyncMock(return_value=MagicMock(id="run-uuid-123"))
    mock.ingestion_runs.finish = AsyncMock()
    mock.ingestion_runs.fail = AsyncMock()
    mock.companies.upsert_by_domain = AsyncMock(return_value=(MagicMock(id="company-uuid"), True))
    mock.jobs.upsert_by_external_id = AsyncMock(return_value=(MagicMock(id="job-uuid"), True))
    mock.jobs.set_embedding_id = AsyncMock()
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Test run_crawler task
# ─────────────────────────────────────────────────────────────────────────────

class TestRunCrawlerTask:
    """Test the run_crawler Celery task via .run() to bypass the broker."""

    def test_run_crawler_with_default_arguments(self):
        """Test run_crawler applies default values when None is passed."""
        with patch("ingestion.tasks._run_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.return_value = {"fetched": 10, "inserted": 5}
            from ingestion.tasks import run_crawler

            result = run_crawler.run()

            assert result["fetched"] == 10
            assert result["inserted"] == 5
            mock_pipeline.assert_called_once()
            call_kwargs = mock_pipeline.call_args[1]
            assert call_kwargs["roles"] == ["Software Engineer", "Data Scientist"]
            assert len(call_kwargs["locations"]) > 0
            assert len(call_kwargs["include_domains"]) > 0

    def test_run_crawler_propagates_custom_arguments(self):
        """Test run_crawler passes custom arguments to _run_pipeline."""
        with patch("ingestion.tasks._run_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.return_value = {"fetched": 5, "inserted": 3}
            from ingestion.tasks import run_crawler

            custom_roles = ["Data Engineer"]
            custom_locations = ["New York"]
            custom_domains = ["linkedin.com"]

            result = run_crawler.run(
                roles=custom_roles,
                locations=custom_locations,
                include_domains=custom_domains,
                max_results_per_query=10,
            )

            call_kwargs = mock_pipeline.call_args[1]
            assert call_kwargs["roles"] == custom_roles
            assert call_kwargs["locations"] == custom_locations
            assert call_kwargs["include_domains"] == custom_domains
            assert call_kwargs["max_results_per_query"] == 10

    def test_run_crawler_propagates_exception(self):
        """Test run_crawler re-raises pipeline exceptions for Celery FAILURE state."""
        with patch("ingestion.tasks._run_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.side_effect = RuntimeError("Pipeline crashed")
            from ingestion.tasks import run_crawler

            with pytest.raises(RuntimeError, match="Pipeline crashed"):
                run_crawler.run()


# ─────────────────────────────────────────────────────────────────────────────
# Test _run_pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestRunPipeline:
    """Test the _run_pipeline async function."""

    @pytest.mark.asyncio
    async def test_happy_path_with_mocked_external_calls(self, mock_tavily_scraper):
        """Test _run_pipeline completes successfully with mocked dependencies."""
        with patch("ingestion.tasks.TavilyJobScraper", return_value=mock_tavily_scraper), \
             patch("ingestion.tasks.AsyncSessionLocal") as mock_session_factory, \
             patch("ingestion.tasks.UnitOfWork") as mock_uow_class, \
             patch("ingestion.tasks.ChromaJobStore") as mock_chroma_class, \
             patch("ingestion.tasks.JDParser") as mock_parser_class:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_factory.return_value = mock_session

            mock_uow = AsyncMock()
            mock_uow.ingestion_runs.create = AsyncMock(return_value=MagicMock(id="run-uuid"))
            mock_uow.ingestion_runs.finish = AsyncMock()
            mock_uow.companies.upsert_by_domain = AsyncMock(return_value=(MagicMock(id="company-uuid"), True))
            mock_uow.jobs.upsert_by_external_id = AsyncMock(return_value=(MagicMock(id="job-uuid"), True))
            mock_uow.jobs.set_embedding_id = AsyncMock()
            mock_uow_class.return_value = mock_uow

            mock_parser = mock_parser_class.return_value
            mock_parsed = MagicMock()
            mock_parsed.model_dump.return_value = {
                "title": "Python Engineer",
                "company": "TestCo",
                "raw_text": "Job description",
            }
            mock_parser.batch_parse.return_value = [mock_parsed]

            mock_store = mock_chroma_class.return_value
            mock_store.add_batch.return_value = 1

            mock_tavily_scraper.load_raw.return_value = {
                "title": "Python Engineer",
                "url": "https://linkedin.com/jobs/123",
                "content": "Job content",
                "score": 0.9,
            }

            with patch.object(Path, "read_text", return_value=json.dumps({
                "title": "Python Engineer",
                "url": "https://linkedin.com/jobs/123",
                "content": "Job content",
                "score": 0.9,
            })):
                from ingestion.tasks import _run_pipeline
                result = await _run_pipeline(
                    roles=["Python Engineer"],
                    locations=["Remote"],
                    max_results_per_query=5,
                    run_id="test-run-id",
                )

            assert result["fetched"] == 1
            assert result["parsed"] == 1
            mock_uow.ingestion_runs.finish.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_results_returns_early(self, mock_tavily_scraper):
        """Test _run_pipeline returns early when no jobs found."""
        mock_tavily_scraper.search_jobs.return_value = []
        mock_tavily_scraper.save_raw.return_value = []

        with patch("ingestion.tasks.TavilyJobScraper", return_value=mock_tavily_scraper):
            from ingestion.tasks import _run_pipeline
            result = await _run_pipeline(
                roles=["Python Engineer"],
                locations=["Remote"],
                max_results_per_query=5,
                run_id="test-run-id",
            )

        assert result["status"] == "success"
        assert result["message"] == "No jobs found"

    @pytest.mark.asyncio
    async def test_fetch_error_continues_to_next_role_location(self, mock_tavily_scraper):
        """Test _run_pipeline catches fetch errors per role/location and continues."""
        mock_tavily_scraper.search_jobs.side_effect = Exception("Network error")
        mock_tavily_scraper.save_raw.return_value = []

        with patch("ingestion.tasks.TavilyJobScraper", return_value=mock_tavily_scraper):
            from ingestion.tasks import _run_pipeline
            result = await _run_pipeline(
                roles=["Python Engineer"],
                locations=["Remote"],
                max_results_per_query=5,
                run_id="test-run-id",
            )

        assert result["status"] == "success"
        assert result["message"] == "No jobs found"


# ─────────────────────────────────────────────────────────────────────────────
# Test helper functions
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineHelpers:
    """Test helper functions in tasks.py."""

    def test_company_domain_creates_deterministic_key(self):
        from ingestion.tasks import _company_domain
        assert _company_domain("Acme Corp") == "acme-corp.talentradar.internal"

    def test_company_domain_normalizes_lowercase(self):
        from ingestion.tasks import _company_domain
        assert _company_domain("TechCorp Inc") == "techcorp-inc.talentradar.internal"

    def test_stable_id_returns_hash(self):
        from ingestion.tasks import _stable_id
        result = _stable_id("https://example.com/job/123")
        assert len(result) == 32
        assert result == _stable_id("https://example.com/job/123")
