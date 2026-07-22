# Handoff Report: Requirement R2 (Expand Indian Job Sources)

**Author**: Worker Subagent (Milestone 2)  
**Date**: 2026-07-22  
**Recipient**: Parent Agent / QA / Orchestrator  
**Status**: Hard Handoff (Task Complete)  

---

## 1. Observation

Direct file paths, line numbers, commands, and verbatim test outputs:

1. **`ingestion/scrapers/tavily_client.py`**:
   - Lines 20-47: Defined `INDIAN_JOB_DOMAINS = ["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]` and `detect_source_from_url(url: str) -> str` helper.
   - Lines 52-87: Updated `TavilyJobScraper.search()` to include `"include_domains": include_domains` in Tavily API request payload when provided.
   - Lines 89-106: Updated `TavilyJobScraper.search_jobs()` to accept `include_domains` and `use_site_operators`, passing parameters to `search()`.

2. **`ingestion/tasks.py`**:
   - Lines 25-40: Defined `DEFAULT_INDIAN_LOCATIONS = ["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]` and `DEFAULT_INDIAN_DOMAINS = ["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`.
   - Lines 49-140: Updated `_run_pipeline()` to pass `include_domains` to Tavily scraper, record `include_domains` in `ingestion_run.run_config`, and dynamically tag `job.source` using `detect_source_from_url(pjd.source_url)` for each job inserted into PostgreSQL and ChromaDB metadata.
   - Lines 202-230: Updated `run_crawler()` Celery task signature to accept `include_domains` and `source_name`, defaulting locations to Indian tech hubs and default domains to Indian portals.

3. **`ingestion/parsers/schemas.py` & `ingestion/parsers/jd_parser.py`**:
   - `schemas.py` Lines 67-78 & 225-231: Defined `_CURRENCY_SYMBOL_MAP` and updated `normalise_currency` validator on `ParsedJobDescription` to map `₹`, `Rs`, `Rs.`, `Rupees` -> `"INR"`.
   - `jd_parser.py` Lines 87 & 171-205: Updated `_SYSTEM_PROMPT` rules for LPA (Lakhs Per Annum) salary conversion (1 Lakh = 100,000 INR) and added Example 3 to `_FEW_SHOT_EXAMPLES` for Swiggy (Bangalore, India).

4. **`pyproject.toml`**:
   - Line 179: Added `pythonpath = ["."]` under `[tool.pytest.ini_options]`.

5. **`tests/test_scraper.py`**:
   - Created comprehensive unit/integration test file with mock Tavily API responses for LinkedIn, Naukri, and Indeed Indian postings.

6. **Test Commands & Verification Outputs**:
   - Command: `uv run pytest tests/test_scraper.py tests/test_ingestion.py --no-cov`
     Output: `63 passed, 7 warnings in 4.70s`
   - Command: `uv run pytest --no-cov`
     Output: `203 passed, 2 warnings in 5.34s`

---

## 2. Logic Chain

1. **Observation 1 & 2**: `tavily_client.py` and `tasks.py` lacked domain filtering (`include_domains`) and defaulted to non-Indian locations.
2. **Step 1**: Adding `include_domains` parameter and `INDIAN_JOB_DOMAINS` constant allows Tavily search requests to target LinkedIn, Naukri, and Indeed specifically. Adding `detect_source_from_url()` extracts the exact platform source (`"linkedin"`, `"naukri"`, `"indeed"`) from job URLs.
3. **Step 2**: Updating `run_crawler` task defaults in `tasks.py` ensures any default crawler run queries Indian tech hubs (`"Bangalore"`, `"Mumbai"`, `"Delhi"`, `"Hyderabad"`, `"Pune"`, `"India"`) across target job portals and dynamically attributes `Job.source`.
4. **Observation 3**: `schemas.py` and `jd_parser.py` did not convert currency symbols like `₹` or `Rs.` to `"INR"`, nor did the prompt explicitly instruct the LLM on LPA salary conversion.
5. **Step 3**: Adding `_CURRENCY_SYMBOL_MAP` in `schemas.py` normalises all Indian currency symbol variations to `"INR"`. Updating prompt rules and few-shot examples in `jd_parser.py` equips Llama 3.1 8B with explicit LPA conversion instructions and Bangalore few-shot context.
6. **Observation 5 & 6**: All test cases in `tests/test_scraper.py` and `tests/test_ingestion.py` pass cleanly, asserting fetching and parsing returns >= 1 job from an Indian location with `"INR"` currency and correct source attribution (`"linkedin"`, `"naukri"`, `"indeed"`). The overall project test suite (203 tests) passes 100%.

---

## 3. Caveats

- **Network Mode**: Work was executed in `CODE_ONLY` network mode. API calls to Tavily and Groq were tested using deterministic, high-fidelity mocks. Live API calls during production runtime rely on `TAVILY_API_KEY` and `GROQ_API_KEY` configured in `.env`.

---

## 4. Conclusion

Requirement R2 ("Update job fetching logic to include Indian job postings from LinkedIn, Naukri, and Indeed using the existing Tavily client, fetching and parsing at least 1 job posting from an Indian location") is fully implemented, verified, and integrated without regressions.

---

## 5. Verification Method

To independently verify this implementation:

1. **Run Targeted Tests**:
   ```powershell
   uv run pytest tests/test_scraper.py tests/test_ingestion.py --no-cov
   ```
   *Expected Output*: 63 tests pass.

2. **Run Full Test Suite**:
   ```powershell
   uv run pytest --no-cov
   ```
   *Expected Output*: 203 tests pass.

3. **Invalidation Conditions**:
   - Failure in `test_scraper.py` checking `include_domains` payload, source detection (`linkedin`, `naukri`, `indeed`), Indian location extraction, or `INR` currency normalisation.
