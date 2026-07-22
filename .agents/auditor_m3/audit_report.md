# Forensic Audit Report — Milestone 3 (E2E Integration & Forensic Audit)

**Audit Date**: 2026-07-22  
**Auditor**: Forensic Auditor (`auditor_m3`)  
**Scope**: Requirement R1 (Job Search Relevance) & Requirement R2 (Expand Indian Job Sources)  
**Overall Verdict**: **CLEAN (Integrity)** / **REQUEST_CHANGES (Test Suite Dependency Gap)**

---

## 1. Executive Summary

An independent forensic integrity audit was conducted on all code changes and test implementations for Requirement R1 and Requirement R2. 

- **Integrity Assessment**: **CLEAN**. No hardcoded test results, fake return values, dummy/facade implementations, or short-circuited business logic were detected anywhere in the codebase. All external network boundaries (Tavily HTTP client and Groq LLM API) are cleanly mocked in unit tests without short-circuiting internal domain logic.
- **Execution & Test Readiness Assessment**: **REQUEST_CHANGES**. While R2 tests (`tests/test_scraper.py`) pass 100% (11/11 passed), R1 tests (`tests/test_search.py`) produce 8 ERRORS due to a missing test dependency (`aiosqlite`) in `pyproject.toml`.

---

## 2. Forensic Audit Task Results

### Task 1: R1 Code & Test Inspection (Job Search Relevance)

**Files Inspected**:
- `storage/repository.py`
- `agents/rag_agent.py`
- `data/raw/`
- `tests/test_search.py`

**Audit Findings**:
1. **Query Tokenization & Synonym Expansion (`storage/repository.py`)**:
   - `SYNONYM_MAP` dictionary (lines 424-437) maps technical terms and role abbreviations (`dev`, `engineer`, `swe`, `fe`, `be`, `fs`, `qa`, `java`, `python`, `js`, `software`) to expanded synonym sets.
   - `tokenize_and_expand_query()` (lines 440-460) tokenizes input queries, strips stop words (`in`, `at`, `for`, `a`, `the`, `job`, `role`, etc.), and expands tokens into synonym groups.
   - `_build_term_group_clause()` (lines 463-474) creates SQL `or_` clauses across `Job.title`, `Job.description_clean`, `Job.skills`, and `Job.tags`.
2. **Multi-Field Matching & Fallback (`storage/repository.py`)**:
   - `JobRepository.search()` (lines 579-675) implements a two-step search strategy:
     - **Step 1 (Strict AND)**: Queries jobs requiring all expanded term groups (`and_(*and_term_clauses)`). If the result count is `>= min_results` (default 3), the results are returned.
     - **Step 2 (Relaxed OR Fallback)**: If Step 1 returns `< min_results`, it automatically falls back to an `or_(*or_term_clauses)` query across term groups.
   - `JobRepository.get_by_external_ids()` (lines 515-532): Added an optional `source: str | None = None` parameter. When `source` is `None`, jobs are queried across all sources (essential for cross-source lookups like RAG).
3. **RAG Agent Enhancements (`agents/rag_agent.py`)**:
   - Added `ROLE_KEYWORDS` set (lines 35-42) to identify generic role tokens ("dev", "developer", "engineer", "software", etc.).
   - `_apply_filters()` (lines 259-272): Filters out generic `ROLE_KEYWORDS` from `context.skills` before applying skill containment filtering. This prevents broad role queries (e.g. "Java Developer") from failing on job records whose explicit skill list is `["Java", "Spring Boot"]` without the literal word "developer".
   - `_search_db_fallback()` (lines 139-166): Added a fallback path to query PostgreSQL via `JobRepository.search()` when ChromaDB vector search returns fewer than 3 results.
   - `_build_results()` (lines 206): Fixed cross-source job hydration by calling `get_by_external_ids(ids)` without constraining to a single source.
4. **Data Verification (`data/raw/`)**:
   - Structure verified: raw job listings are properly stored at `data/raw/<role_slug>/<loc_slug>/<run_id>_<md5>.json`.
5. **Test Implementation (`tests/test_search.py`)**:
   - 8 comprehensive test cases written covering tokenization, synonym expansion, broad role queries ("java dev", "software engineer", "fullstack dev"), AND-to-OR fallback, role keyword filtering, and search API endpoints.
   - **Finding**: All 8 tests use `create_async_engine("sqlite+aiosqlite:///:memory:")` for in-memory database testing. However, `aiosqlite` is NOT declared in `pyproject.toml` under `dependencies` or `optional-dependencies.dev`, resulting in `ModuleNotFoundError: No module named 'aiosqlite'` when running `pytest`.

---

### Task 2: R2 Code & Test Inspection (Expand Indian Job Sources)

**Files Inspected**:
- `ingestion/scrapers/tavily_client.py`
- `ingestion/tasks.py`
- `ingestion/parsers/schemas.py`
- `ingestion/parsers/jd_parser.py`
- `tests/test_scraper.py`

**Audit Findings**:
1. **Source Detection & Domain Filtering (`ingestion/scrapers/tavily_client.py`)**:
   - Added `INDIAN_JOB_DOMAINS` constant (lines 20-25): `["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`.
   - Implemented `detect_source_from_url(url)` (lines 28-48): accurately classifies URLs into `'linkedin'`, `'naukri'`, `'indeed'`, `'greenhouse'`, `'lever'`, `'ashby'`, or `'tavily_search'`.
   - Updated `TavilyJobScraper.search_jobs()` (lines 112-129): accepts `include_domains` and passes domain filter constraints to Tavily API payload.
2. **Ingestion Task Pipeline (`ingestion/tasks.py`)**:
   - Added `DEFAULT_INDIAN_LOCATIONS` (lines 25-33): `["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]`.
   - Added `DEFAULT_INDIAN_DOMAINS` (lines 35-40): `["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`.
   - `_run_pipeline()` (lines 53-233):
     - Executes scraping with `include_domains`.
     - Detects source dynamically per job via `detect_source_from_url(pjd.source_url)`.
     - Attaches detected source to ChromaDB metadata (`"source": job_source`).
3. **INR Currency Normalization & Few-Shot Parsing (`ingestion/parsers/schemas.py`, `ingestion/parsers/jd_parser.py`)**:
   - `_CURRENCY_SYMBOL_MAP` (lines 67-78 in `schemas.py`) maps `"₹"`, `"RS"`, `"RS."`, `"RUPEES"`, `"INR."` to `"INR"`.
   - `ParsedJobDescription.normalise_currency()` (lines 223-231 in `schemas.py`) normalizes currency inputs to ISO-4217 code (`'INR'`).
   - Added Example 3 to `_FEW_SHOT_EXAMPLES` in `jd_parser.py` (lines 174-208): demonstrates parsing Indian tech postings with LPA compensation (e.g., 20 - 35 LPA normalized to 2,000,000 - 3,500,000 INR).
4. **Test Verification (`tests/test_scraper.py`)**:
   - 11 unit tests written covering source detection, Tavily domain payload handling, INR symbol normalization, and task default constants.
   - All 11 tests pass cleanly (11/11 passed in 1.75s).

---

## 3. Integrity & Anti-Cheat Checklist

| Integrity Criteria | Status | Forensic Observation / Evidence |
| :--- | :--- | :--- |
| **No Hardcoded Test Results** | **PASS** | Production logic in `repository.py`, `rag_agent.py`, `tavily_client.py`, `tasks.py`, `jd_parser.py`, and `schemas.py` uses dynamic query builders, real API calls, and schema validators. No static test responses or expected outputs embedded in production code. |
| **No Dummy / Facade Logic** | **PASS** | Real SQLAlchemy ORM queries, real HTTP calls via `httpx.Client`, real LLM calls via Groq `Groq` client, and real Pydantic validation. No facade wrappers or stub returns. |
| **Clean Network Mocking** | **PASS** | In `tests/test_scraper.py`, `httpx.Client` and `Groq` are mocked at the HTTP/LLM network layer. Internal scraper methods (`search_jobs`, `save_raw`), source detector (`detect_source_from_url`), parser (`batch_parse`), and schema validator run full logic. |
| **No Self-Certifying Work** | **PASS** | Tests assert actual data structures, domain outputs, and query behaviors without mocking internal business logic. |

---

## 4. Test Suite Execution Summary

Execution command: `uv run --extra dev pytest --no-cov`

- **Total Tests Executed**: 226 tests
- **Passed**: 193
- **Failed**: 25 (Pre-existing test suite issues in `tests/test_api.py` and `tests/test_resume_matcher.py`)
- **Errors**: 8 (All in `tests/test_search.py` due to missing `aiosqlite` dependency)

### R2 Test Suite (`tests/test_scraper.py`):
```text
tests/test_scraper.py::TestIndianSourceDetection::test_detect_source_linkedin PASSED
tests/test_scraper.py::TestIndianSourceDetection::test_detect_source_naukri PASSED
tests/test_scraper.py::TestIndianSourceDetection::test_detect_source_indeed_in PASSED
tests/test_scraper.py::TestIndianSourceDetection::test_detect_source_indeed_com PASSED
tests/test_scraper.py::TestIndianSourceDetection::test_detect_source_fallback PASSED
tests/test_scraper.py::TestIndianSourceDetection::test_detect_source_empty PASSED
tests/test_scraper.py::TestTavilyScraperIndianJobs::test_search_jobs_passes_include_domains_payload PASSED
tests/test_scraper.py::TestTavilyScraperIndianJobs::test_indian_location_fetching_and_parsing_returns_at_least_one_job PASSED
tests/test_scraper.py::TestCurrencyNormalisation::test_inr_currency_symbols PASSED
tests/test_scraper.py::TestCrawlerIndianTaskDefaults::test_default_indian_locations_contain_major_hubs PASSED
tests/test_scraper.py::TestCrawlerIndianTaskDefaults::test_default_indian_domains_contain_portals PASSED
11 passed in 1.75s
```

### R1 Test Suite Error Analysis (`tests/test_search.py`):
```text
ModuleNotFoundError: No module named 'aiosqlite'
_ ERROR at setup of TestJobRepositorySearchBroadQueries.test_search_java_dev_returns_at_least_3_jobs _
...
sqlite+aiosqlite:///:memory:
```
**Root Cause**: `aiosqlite` is required for in-memory SQLite async testing in `test_search.py` but is omitted from `pyproject.toml`. Adding `"aiosqlite>=0.20.0"` to `[project.optional-dependencies] dev` in `pyproject.toml` resolves this error.

---

## 5. Final Audit Verdict & Actionable Remediation

- **Code & Logic Integrity Verdict**: **CLEAN**
  - The implementation of Requirement R1 and Requirement R2 is genuine, robust, and free of any hardcoded or fake logic.
- **Build / Test Verification Verdict**: **REQUEST_CHANGES**
  - **Required Remediation**: Add `"aiosqlite>=0.20.0"` to `dev` dependencies in `pyproject.toml` so that `tests/test_search.py` executes cleanly in automated test environments.

---
