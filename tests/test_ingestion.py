"""
tests/test_ingestion.py
~~~~~~~~~~~~~~~~~~~~~~~
Tests for the ingestion pipeline layer.

Covers (no external API calls — all Groq/Tavily calls are mocked):
- RawJobResult schema validation
- ParsedJobDescription validators (skills dedup, seniority/employment_type
  normalisation, currency, salary range swap)
- JDParser._extract_json (pure function, no LLM needed)
- JDParser._build_messages (pure function)
- JDParser.parse_jd (mocked Groq client)
- JDParser.batch_parse error isolation
- TavilyJobScraper.search result construction (mocked httpx)
- TavilyJobScraper.save_raw / load_raw filesystem round-trip
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult
from ingestion.parsers.jd_parser import JDParser
from ingestion.scrapers.tavily_client import (
    TavilyJobScraper,
    _matches_any_domain,
    _slugify,
    _url_matches_domain,
    _validate_url,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_raw_result() -> RawJobResult:
    return RawJobResult(
        title="Senior Python Engineer",
        url="https://jobs.example.com/python-engineer-123",
        content="We are looking for a Senior Python Engineer...",
        score=0.92,
        published_date="2026-04-15",
        raw_content="Full page content: Senior Python Engineer at Acme Corp...",
    )


@pytest.fixture
def minimal_parsed_job() -> ParsedJobDescription:
    return ParsedJobDescription(
        title="Software Engineer",
        company="Acme Corp",
        raw_text="Looking for an engineer.",
    )


@pytest.fixture
def mock_groq_response() -> str:
    """A valid JSON string that a mocked Groq LLM would return."""
    return json.dumps({
        "title": "Senior Python Engineer",
        "company": "Acme Corp",
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "experience": "5+ years",
        "location": "San Francisco, CA",
        "is_remote": True,
        "salary": "$150,000 - $200,000 / year",
        "salary_min": 150000,
        "salary_max": 200000,
        "salary_currency": "USD",
        "employment_type": "full_time",
        "seniority": "senior",
    })


# ─────────────────────────────────────────────────────────────────────────────
# RawJobResult validation
# ─────────────────────────────────────────────────────────────────────────────

class TestRawJobResult:
    def test_valid_result(self, sample_raw_result):
        assert sample_raw_result.title == "Senior Python Engineer"
        assert sample_raw_result.score == 0.92

    def test_best_content_prefers_raw_content(self, sample_raw_result):
        """raw_content should be preferred over content when both present."""
        assert sample_raw_result.best_content.startswith("Full page content")

    def test_best_content_falls_back_to_content(self):
        r = RawJobResult(
            title="Engineer",
            url="https://example.com/job",
            content="Snippet text",
        )
        assert r.best_content == "Snippet text"

    def test_url_must_be_non_empty(self):
        with pytest.raises(Exception):
            RawJobResult(title="Engineer", url="", content="text")

    def test_url_is_stripped(self):
        r = RawJobResult(title="Engineer", url="  https://example.com/job  ", content="text")
        assert r.url == "https://example.com/job"

    def test_default_score_is_zero(self):
        r = RawJobResult(title="Engineer", url="https://example.com/job", content="text")
        assert r.score == 0.0

    def test_published_date_optional(self):
        r = RawJobResult(title="Engineer", url="https://example.com/job", content="text")
        assert r.published_date is None


# ─────────────────────────────────────────────────────────────────────────────
# ParsedJobDescription validators
# ─────────────────────────────────────────────────────────────────────────────

class TestParsedJobDescription:
    def test_minimal_valid(self, minimal_parsed_job):
        assert minimal_parsed_job.title == "Software Engineer"
        assert minimal_parsed_job.skills == []
        assert minimal_parsed_job.is_remote is False

    def test_skills_deduplication_case_insensitive(self):
        job = ParsedJobDescription(
            title="Engineer",
            company="Co",
            raw_text="...",
            skills=["Python", "python", "PYTHON", "FastAPI", "fastapi"],
        )
        # Should deduplicate, keeping first occurrence of each
        skill_lower = [s.lower() for s in job.skills]
        assert skill_lower.count("python") == 1
        assert skill_lower.count("fastapi") == 1
        assert len(job.skills) == 2

    def test_skills_deduplication_preserves_original_case(self):
        job = ParsedJobDescription(
            title="Engineer",
            company="Co",
            raw_text="...",
            skills=["Python", "python"],
        )
        assert job.skills[0] == "Python"  # first occurrence kept

    def test_skills_non_list_input_returns_empty(self):
        job = ParsedJobDescription(
            title="Engineer",
            company="Co",
            raw_text="...",
            skills=None,  # type: ignore[arg-type]
        )
        assert job.skills == []

    def test_seniority_normalisation(self):
        job = ParsedJobDescription(
            title="Lead Engineer",
            company="Co",
            raw_text="...",
            seniority="Senior",
        )
        assert job.seniority == "senior"

    def test_seniority_hyphen_normalisation(self):
        job = ParsedJobDescription(
            title="Eng",
            company="Co",
            raw_text="...",
            seniority="mid-level",
        )
        # mid_level is not in the valid set, should be None
        assert job.seniority is None

    def test_seniority_invalid_returns_none(self):
        job = ParsedJobDescription(
            title="Eng",
            company="Co",
            raw_text="...",
            seniority="rockstar",
        )
        assert job.seniority is None

    def test_all_valid_seniority_levels(self):
        for level in ["intern", "junior", "mid", "senior", "lead",
                       "principal", "staff", "director", "vp", "c_level"]:
            job = ParsedJobDescription(
                title="Role", company="Co", raw_text="...", seniority=level
            )
            assert job.seniority == level

    def test_employment_type_normalisation(self):
        job = ParsedJobDescription(
            title="Eng", company="Co", raw_text="...", employment_type="Full-Time"
        )
        assert job.employment_type == "full_time"

    def test_employment_type_invalid_returns_none(self):
        job = ParsedJobDescription(
            title="Eng", company="Co", raw_text="...", employment_type="gig"
        )
        assert job.employment_type is None

    def test_salary_currency_normalised_to_uppercase(self):
        job = ParsedJobDescription(
            title="Eng", company="Co", raw_text="...", salary_currency="usd"
        )
        assert job.salary_currency == "USD"

    def test_salary_currency_unknown_returns_none(self):
        job = ParsedJobDescription(
            title="Eng", company="Co", raw_text="...", salary_currency="XYZ"
        )
        assert job.salary_currency is None

    def test_salary_range_swap_when_inverted(self):
        """min > max should be automatically swapped."""
        job = ParsedJobDescription(
            title="Eng",
            company="Co",
            raw_text="...",
            salary_min=200000,
            salary_max=150000,
        )
        assert job.salary_min == 150000
        assert job.salary_max == 200000

    def test_salary_range_consistent_unchanged(self):
        job = ParsedJobDescription(
            title="Eng",
            company="Co",
            raw_text="...",
            salary_min=100000,
            salary_max=150000,
        )
        assert job.salary_min == 100000
        assert job.salary_max == 150000

    def test_to_job_kwargs_maps_fields(self, minimal_parsed_job):
        kwargs = minimal_parsed_job.to_job_kwargs()
        assert kwargs["title"] == "Software Engineer"
        assert "description_raw" in kwargs
        assert "location_raw" in kwargs
        assert "salary_raw" in kwargs


# ─────────────────────────────────────────────────────────────────────────────
# JDParser._extract_json (pure, no LLM)
# ─────────────────────────────────────────────────────────────────────────────

class TestJDParserExtractJson:
    def test_parses_clean_json(self):
        raw = '{"title": "Engineer", "company": "Acme"}'
        result = JDParser._extract_json(raw)
        assert result["title"] == "Engineer"

    def test_extracts_json_from_prose(self):
        raw = 'Here is the result:\n{"title": "Engineer", "company": "Acme"}\nEnd.'
        result = JDParser._extract_json(raw)
        assert result["company"] == "Acme"

    def test_strips_markdown_code_fence(self):
        raw = "```json\n{\"title\": \"Engineer\", \"company\": \"Acme\"}\n```"
        result = JDParser._extract_json(raw)
        assert result["title"] == "Engineer"

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="Could not extract valid JSON"):
            JDParser._extract_json("This is not JSON at all.")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            JDParser._extract_json("")

    def test_nested_json_object(self):
        raw = json.dumps({"title": "Eng", "skills": ["Python", "SQL"], "salary_min": 100000})
        result = JDParser._extract_json(raw)
        assert result["skills"] == ["Python", "SQL"]
        assert result["salary_min"] == 100000


# ─────────────────────────────────────────────────────────────────────────────
# JDParser._build_messages (pure, no LLM)
# ─────────────────────────────────────────────────────────────────────────────

class TestJDParserBuildMessages:
    @pytest.fixture
    def parser(self):
        # Bypass the API key check — we only test pure methods here
        with patch("ingestion.parsers.jd_parser.Groq"):
            return JDParser(api_key="fake-key-for-testing")

    def test_first_message_is_system(self, parser):
        msgs = parser._build_messages("Some job text")
        assert msgs[0]["role"] == "system"

    def test_ends_with_user_message(self, parser):
        msgs = parser._build_messages("Some job text")
        assert msgs[-1]["role"] == "user"
        assert "Some job text" in msgs[-1]["content"]

    def test_includes_few_shot_examples(self, parser):
        msgs = parser._build_messages("Some job text")
        roles = [m["role"] for m in msgs]
        # system + N*(user+assistant) + user
        assert roles.count("user") >= 2
        assert roles.count("assistant") >= 1

    def test_truncates_long_input(self, parser):
        long_text = "x" * 10000
        msgs = parser._build_messages(long_text)
        # The actual user content should be truncated to 6000 chars max
        last_content = msgs[-1]["content"]
        assert len(last_content) <= 6000


# ─────────────────────────────────────────────────────────────────────────────
# JDParser.parse_jd (mocked Groq)
# ─────────────────────────────────────────────────────────────────────────────

class TestJDParserParseJd:
    @pytest.fixture
    def parser_with_mock_groq(self, mock_groq_response):
        """JDParser with Groq client fully mocked to avoid network calls."""
        with patch("ingestion.parsers.jd_parser.Groq") as MockGroq:
            mock_client = MockGroq.return_value
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=mock_groq_response))]
            )
            parser = JDParser(api_key="fake-key-for-testing")
            parser._client = mock_client
            yield parser

    def test_returns_parsed_job_description(self, parser_with_mock_groq):
        result = parser_with_mock_groq.parse_jd(
            "Senior Python Engineer at Acme Corp...",
            source_url="https://jobs.example.com/123",
        )
        assert isinstance(result, ParsedJobDescription)
        assert result.title == "Senior Python Engineer"
        assert result.company == "Acme Corp"
        assert "Python" in result.skills
        assert result.salary_currency == "USD"
        assert result.seniority == "senior"

    def test_attaches_source_url(self, parser_with_mock_groq):
        result = parser_with_mock_groq.parse_jd(
            "Job text",
            source_url="https://stripe.com/jobs/456",
        )
        assert result.source_url == "https://stripe.com/jobs/456"

    def test_attaches_raw_text(self, parser_with_mock_groq):
        raw = "We need a Senior Python Engineer."
        result = parser_with_mock_groq.parse_jd(raw)
        assert result.raw_text == raw

    def test_raises_on_invalid_llm_response(self):
        with patch("ingestion.parsers.jd_parser.Groq") as MockGroq:
            mock_client = MockGroq.return_value
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="not json at all"))]
            )
            parser = JDParser(api_key="fake-key-for-testing")
            parser._client = mock_client
            with pytest.raises(ValueError):
                parser.parse_jd("Some job text")

    def test_raises_when_groq_api_key_missing(self):
        with patch("config.settings.get_settings") as mock_settings:
            mock_settings.return_value.groq_api_key = ""
            with pytest.raises(ValueError, match="GROQ_API_KEY"):
                JDParser()


# ─────────────────────────────────────────────────────────────────────────────
# JDParser.batch_parse — error isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestJDParserBatchParse:
    def test_skips_failed_items_continues_batch(self, mock_groq_response):
        """One failing parse should not abort the entire batch."""
        good_response = mock_groq_response
        bad_response = "this is not json"

        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            content = good_response if call_count % 2 == 1 else bad_response
            return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])

        with patch("ingestion.parsers.jd_parser.Groq") as MockGroq, \
             patch("ingestion.seed_db.extract_job_from_raw", side_effect=ValueError("fallback failed")):
            mock_client = MockGroq.return_value
            mock_client.chat.completions.create.side_effect = fake_create
            parser = JDParser(api_key="fake-key-for-testing")
            parser._client = mock_client
            parser._delay = 0  # disable sleep in tests

            raw_results = [
                RawJobResult(title=f"Job {i}", url=f"https://example.com/job{i}", content=f"Content {i}")
                for i in range(4)
            ]
            results = parser.batch_parse(raw_results)

        # Only the even-indexed calls (good_response) should succeed
        assert len(results) == 2
        assert all(isinstance(r, ParsedJobDescription) for r in results)

    def test_empty_batch_returns_empty_list(self):
        with patch("ingestion.parsers.jd_parser.Groq"):
            parser = JDParser(api_key="fake-key-for-testing")
            assert parser.batch_parse([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# TavilyJobScraper.search — mocked httpx
# ─────────────────────────────────────────────────────────────────────────────

class TestTavilyJobScraper:
    @pytest.fixture
    def tavily_response(self):
        return {
            "results": [
                {
                    "title": "Senior Python Engineer",
                    "url": "https://jobs.stripe.com/123",
                    "content": "We are looking for a senior Python engineer...",
                    "score": 0.95,
                    "published_date": "2026-04-01",
                    "raw_content": "Full content here...",
                },
                {
                    "title": "Backend Engineer",
                    "url": "https://jobs.airbnb.com/456",
                    "content": "Airbnb is hiring a backend engineer.",
                    "score": 0.88,
                    "published_date": None,
                    "raw_content": None,
                },
            ]
        }

    @pytest.fixture
    def scraper_with_mock_http(self, tavily_response):
        with patch("ingestion.scrapers.tavily_client.httpx.Client") as MockClient:
            mock_client_instance = MockClient.return_value
            mock_response = MagicMock()
            mock_response.json.return_value = tavily_response
            mock_response.raise_for_status = MagicMock()
            mock_client_instance.post.return_value = mock_response
            scraper = TavilyJobScraper(api_key="fake-tavily-key")
            scraper._client = mock_client_instance
            yield scraper

    def test_search_returns_raw_job_results(self, scraper_with_mock_http):
        results = scraper_with_mock_http.search("python engineer")
        assert len(results) == 2
        assert all(isinstance(r, RawJobResult) for r in results)

    def test_search_result_fields(self, scraper_with_mock_http):
        results = scraper_with_mock_http.search("python engineer")
        first = results[0]
        assert first.title == "Senior Python Engineer"
        assert first.url == "https://jobs.stripe.com/123"
        assert first.score == 0.95

    def test_search_skips_malformed_items(self):
        bad_response = {
            "results": [
                {"title": "Good Job", "url": "https://example.com/1", "content": "text", "score": 0.8},
                {"title": "No URL", "url": "", "content": "text", "score": 0.5},  # invalid: empty URL
            ]
        }
        with patch("ingestion.scrapers.tavily_client.httpx.Client") as MockClient:
            mock_client_instance = MockClient.return_value
            mock_response = MagicMock()
            mock_response.json.return_value = bad_response
            mock_response.raise_for_status = MagicMock()
            mock_client_instance.post.return_value = mock_response
            scraper = TavilyJobScraper(api_key="fake-key")
            scraper._client = mock_client_instance
            results = scraper.search("engineer")
        # Only the valid item should be returned
        assert len(results) == 1
        assert results[0].title == "Good Job"

    def test_raises_when_api_key_missing(self):
        with patch("config.settings.get_settings") as mock_settings:
            mock_settings.return_value.tavily_api_key = ""
            with pytest.raises(ValueError, match="TAVILY_API_KEY"):
                TavilyJobScraper()

    def test_search_jobs_builds_correct_query(self, scraper_with_mock_http):
        """search_jobs should use the template and call search()."""
        with patch.object(scraper_with_mock_http, "search", return_value=[]) as mock_search:
            scraper_with_mock_http.search_jobs("Python Engineer", "Remote", count=5)
            call_args = mock_search.call_args
            # The query should include the role and location
            assert "Python Engineer" in call_args[0][0]
            assert "Remote" in call_args[0][0]
            assert call_args[1]["max_results"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# TavilyJobScraper.save_raw / load_raw
# ─────────────────────────────────────────────────────────────────────────────

class TestTavilyJobScraperFileIO:
    def test_save_raw_creates_files(self, sample_raw_result, tmp_path):
        scraper = TavilyJobScraper.__new__(TavilyJobScraper)
        scraper._raw_dir = tmp_path
        scraper._api_key = "fake"

        paths = scraper.save_raw(
            [sample_raw_result],
            run_id="test-run-001",
            role="Python Engineer",
            location="Remote",
        )
        assert len(paths) == 1
        assert paths[0].exists()

    def test_save_raw_deduplicates_by_url(self, sample_raw_result, tmp_path):
        """Two results with the same URL should produce the same file (dedup)."""
        scraper = TavilyJobScraper.__new__(TavilyJobScraper)
        scraper._raw_dir = tmp_path
        scraper._api_key = "fake"

        paths = scraper.save_raw(
            [sample_raw_result, sample_raw_result],  # same URL
            run_id="test-run-002",
            role="Engineer",
            location="Remote",
        )
        # Both calls write to the same file path (same MD5 hash)
        assert paths[0] == paths[1]

    def test_save_and_load_roundtrip(self, sample_raw_result, tmp_path):
        scraper = TavilyJobScraper.__new__(TavilyJobScraper)
        scraper._raw_dir = tmp_path
        scraper._api_key = "fake"

        paths = scraper.save_raw(
            [sample_raw_result],
            run_id="test-run-003",
            role="Engineer",
            location="SF",
        )
        loaded = scraper.load_raw(paths[0])
        assert loaded["title"] == sample_raw_result.title
        assert loaded["url"] == sample_raw_result.url
        assert loaded["run_id"] == "test-run-003"
        assert "fetched_at" in loaded

    def test_save_raw_creates_nested_dirs(self, sample_raw_result, tmp_path):
        scraper = TavilyJobScraper.__new__(TavilyJobScraper)
        scraper._raw_dir = tmp_path / "deep" / "nested"
        scraper._api_key = "fake"

        paths = scraper.save_raw(
            [sample_raw_result],
            run_id="run-xyz",
            role="Engineer",
            location="NYC",
        )
        assert paths[0].exists()


# ─────────────────────────────────────────────────────────────────────────────
# _slugify utility
# ─────────────────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_lowercases(self):
        assert _slugify("Python Engineer") == "python_engineer"

    def test_replaces_spaces_with_underscore(self):
        assert _slugify("San Francisco") == "san_francisco"

    def test_removes_special_chars(self):
        assert _slugify("C++ Engineer!") == "c_engineer"

    def test_truncates_to_64_chars(self):
        long = "a" * 100
        assert len(_slugify(long)) == 64


# ─────────────────────────────────────────────────────────────────────────────
# _validate_url helper
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateUrl:
    def test_valid_https_url(self):
        assert _validate_url("https://example.com/jobs/123") is True

    def test_valid_http_url(self):
        assert _validate_url("http://example.com/jobs/123") is True

    def test_malformed_url_no_scheme(self):
        assert _validate_url("example.com/jobs") is False

    def test_empty_string(self):
        assert _validate_url("") is False

    def test_ftp_scheme_rejected(self):
        assert _validate_url("ftp://example.com/file") is False

    def test_url_with_port(self):
        assert _validate_url("https://example.com:8080/api") is True

    def test_url_with_query_params(self):
        assert _validate_url("https://example.com/search?q=python&page=1") is True


# ─────────────────────────────────────────────────────────────────────────────
# _url_matches_domain helper
# ─────────────────────────────────────────────────────────────────────────────

class TestUrlMatchesDomain:
    def test_exact_domain_match(self):
        assert _url_matches_domain("https://linkedin.com/jobs/123", "linkedin.com") is True

    def test_subdomain_match(self):
        assert _url_matches_domain("https://in.indeed.com/jobs", "indeed.com") is True

    def test_www_subdomain_match(self):
        assert _url_matches_domain("https://www.linkedin.com/jobs", "linkedin.com") is True

    def test_no_match_different_domain(self):
        assert _url_matches_domain("https://glassdoor.com/jobs", "linkedin.com") is False

    def test_case_insensitive(self):
        assert _url_matches_domain("https://LinkedIn.com/jobs", "linkedin.com") is True

    def test_empty_url_returns_false(self):
        assert _url_matches_domain("", "linkedin.com") is False


# ─────────────────────────────────────────────────────────────────────────────
# _matches_any_domain helper
# ─────────────────────────────────────────────────────────────────────────────

class TestMatchesAnyDomain:
    def test_matches_first_domain(self):
        domains = ["linkedin.com", "naukri.com", "indeed.com"]
        assert _matches_any_domain("https://linkedin.com/jobs/1", domains) is True

    def test_matches_second_domain(self):
        domains = ["linkedin.com", "naukri.com", "indeed.com"]
        assert _matches_any_domain("https://naukri.com/jobs/1", domains) is True

    def test_no_match(self):
        domains = ["linkedin.com", "naukri.com"]
        assert _matches_any_domain("https://glassdoor.com/jobs", domains) is False

    def test_empty_domains_list(self):
        assert _matches_any_domain("https://linkedin.com/jobs", []) is False

    def test_subdomain_match_in_list(self):
        domains = ["linkedin.com", "indeed.com"]
        assert _matches_any_domain("https://in.indeed.com/jobs", domains) is True


# ─────────────────────────────────────────────────────────────────────────────
# TavilyJobScraper.search domain filtering
# ─────────────────────────────────────────────────────────────────────────────

class TestTavilyJobScraperDomainFiltering:
    def test_include_domains_filters_out_non_matching(self):
        """Results not in include_domains should be filtered out."""
        tavily_response = {
            "results": [
                {"title": "LinkedIn Job", "url": "https://linkedin.com/jobs/1", "content": "text1", "score": 0.9},
                {"title": "Glassdoor Job", "url": "https://glassdoor.com/jobs/2", "content": "text2", "score": 0.8},
                {"title": "Indeed Job", "url": "https://indeed.com/jobs/3", "content": "text3", "score": 0.85},
            ]
        }
        with patch("ingestion.scrapers.tavily_client.httpx.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_response = MagicMock()
            mock_response.json.return_value = tavily_response
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response
            scraper = TavilyJobScraper(api_key="fake-key")
            scraper._client = mock_client
            results = scraper.search("engineer", include_domains=["linkedin.com", "indeed.com"])
        assert len(results) == 2
        urls = [r.url for r in results]
        assert "https://glassdoor.com/jobs/2" not in urls

    def test_exclude_domains_filters_out_matching(self):
        """Results in exclude_domains should be filtered out."""
        tavily_response = {
            "results": [
                {"title": "LinkedIn Job", "url": "https://linkedin.com/jobs/1", "content": "text1", "score": 0.9},
                {"title": "Company Job", "url": "https://acme.com/careers/2", "content": "text2", "score": 0.8},
            ]
        }
        with patch("ingestion.scrapers.tavily_client.httpx.Client") as MockClient:
            mock_client = MockClient.return_value
            mock_response = MagicMock()
            mock_response.json.return_value = tavily_response
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response
            scraper = TavilyJobScraper(api_key="fake-key")
            scraper._client = mock_client
            results = scraper.search("engineer", exclude_domains=["linkedin.com"])
        assert len(results) == 1
        assert results[0].url == "https://acme.com/careers/2"
