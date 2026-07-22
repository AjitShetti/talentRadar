# Implementation Changes: Requirement R2 (Expand Indian Job Sources)

**Author**: Worker Subagent (Milestone 2)  
**Date**: 2026-07-22  
**Target Milestone**: Milestone 2 (R2: Expand Indian Job Sources)  

---

## 1. Overview of Changes

Requirement R2 requires updating the job fetching, ingestion task, and parsing pipeline to include Indian job postings from **LinkedIn** (`linkedin.com`), **Naukri** (`naukri.com`), and **Indeed** (`indeed.com`, `in.indeed.com`) via Tavily API, and ensuring at least 1 job posting from an Indian location is fetched and parsed with `INR` currency and proper source attribution.

---

## 2. File-by-File Summary of Modifications

### 1. `ingestion/scrapers/tavily_client.py`
- **Added Constants**: Defined `INDIAN_JOB_DOMAINS = ["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`.
- **Added URL Helper**: Implemented `detect_source_from_url(url: str) -> str` to map job URLs to platform source keys (`"linkedin"`, `"naukri"`, `"indeed"`, `"greenhouse"`, `"lever"`, `"ashby"`, `"tavily_search"`).
- **Updated `search()`**: Extended `TavilyJobScraper.search()` to accept `include_domains: Optional[List[str]] = None` and `exclude_domains: Optional[List[str]] = None`, adding them to the JSON payload sent to the Tavily `/search` API endpoint.
- **Updated `search_jobs()`**: Extended `TavilyJobScraper.search_jobs()` to accept `include_domains` and optional `use_site_operators: bool = False` (supporting `site:` query operator concatenation). Passed `query_str` as positional argument to preserve test compatibility.

### 2. `ingestion/tasks.py`
- **Added Constants**: Defined `DEFAULT_INDIAN_LOCATIONS = ["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]` and `DEFAULT_INDIAN_DOMAINS = ["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`.
- **Updated `_run_pipeline()`**:
  - Accepts `include_domains: list[str] | None = None` and `source_name: str | None = None`.
  - Passes `include_domains` to `scraper.search_jobs()`.
  - Stores `include_domains` inside `ingestion_run.run_config`.
  - Dynamically tags `job.source` using `detect_source_from_url(pjd.source_url)` per job when upserting to PostgreSQL and preparing ChromaDB metadata.
- **Updated `run_crawler()` Task**:
  - Signature updated with `include_domains` and `source_name`.
  - Defaults `locations` to `locations or DEFAULT_INDIAN_LOCATIONS`.
  - Defaults `include_domains` to `include_domains or DEFAULT_INDIAN_DOMAINS`.
  - Passes `include_domains` and `source_name` to `_run_pipeline()`.

### 3. `ingestion/parsers/schemas.py`
- **Added Currency Mapping**: Defined `_CURRENCY_SYMBOL_MAP` (`₹`, `RS`, `RS.`, `RUPEES`, `INR.` -> `"INR"`; `$`, `US$`, `USD.` -> `"USD"`; `€` -> `"EUR"`; `£` -> `"GBP"`).
- **Updated `normalise_currency()` Validator**: Updated field validator for `salary_currency` on `ParsedJobDescription` to check `_CURRENCY_SYMBOL_MAP` before checking ISO code set `_KNOWN_CURRENCIES`, automatically converting currency symbols and variations to standard ISO codes (e.g. `₹` -> `"INR"`).

### 4. `ingestion/parsers/jd_parser.py`
- **Updated `_SYSTEM_PROMPT`**: Added rules for LPA (Lakhs Per Annum) compensation conversion (`1 Lakh = 100,000 INR`, e.g. `20 - 35 LPA` -> `2,000,000 to 3,500,000 INR`).
- **Added Few-Shot Example**: Introduced Example 3 to `_FEW_SHOT_EXAMPLES` showcasing an Indian job posting for Swiggy in Bangalore, India with ₹ / LPA compensation.
- **Dynamic Settings Import**: Updated `JDParser.__init__` to import `get_settings` dynamically so unit test patching works reliably.

### 5. `pyproject.toml`
- **Added `pythonpath = ["."]`**: Added `pythonpath = ["."]` under `[tool.pytest.ini_options]` to allow pytest to resolve top-level packages cleanly.

### 6. `tests/test_scraper.py`
- **Created Unit/Integration Test Suite**:
  - `TestIndianSourceDetection`: Verifies URL-to-source mapping for LinkedIn, Naukri, Indeed, and generic URLs.
  - `TestTavilyScraperIndianJobs`: Verifies Tavily client POST payload includes `include_domains`, and asserts mock fetching and parsing returns >= 1 job from an Indian location with `INR` currency and proper source attribution.
  - `TestCurrencyNormalisation`: Asserts `₹`, `Rs`, `Rs.`, `Rupees`, `INR` all normalise to `"INR"`.
  - `TestCrawlerIndianTaskDefaults`: Verifies default locations include Indian tech hubs and default domains include Indian job portals.

---

## 3. Test Verification Results

Executed commands:
```powershell
uv run pytest tests/test_scraper.py tests/test_ingestion.py --no-cov
uv run pytest --no-cov
```

**Results**:
- `tests/test_scraper.py tests/test_ingestion.py`: **63 passed, 0 failed** in 4.70s.
- Entire test suite (`uv run pytest --no-cov`): **203 passed, 0 failed** in 5.34s.
