# Handoff Report: Requirement R2 (Expand Indian Job Sources)

**Author**: Explorer Subagent  
**Date**: 2026-07-22  
**Target Audience**: Parent Agent / Implementer  
**Status**: Hard Handoff (Investigation Complete)  

---

## 1. Observation

Direct code observations from `d:\projects\talentRadar`:

1. **`ingestion/scrapers/tavily_client.py`**:
   - Lines 37-44: `search(self, query: str, max_results: int = 10)` constructs API payload `{"api_key": ..., "query": query, "search_depth": "advanced", "max_results": max_results, "include_raw_content": True}`. It does not accept or pass Tavily's `include_domains` parameter.
   - Lines 71-73: `search_jobs(self, role: str, location: str, count: int = 10)` generates a query `f"{role} jobs in {location}"` without target domain filtering or site operators.
2. **`ingestion/tasks.py`**:
   - Line 23: `_SOURCE_NAME = "ats_crawler"` is hardcoded and applied universally to all ingested jobs.
   - Lines 222-224: `run_crawler` sets default locations to `locations = locations or ["Remote", "New York"]`, missing Indian job markets.
3. **`ingestion/parsers/schemas.py` & `ingestion/parsers/jd_parser.py`**:
   - `schemas.py` Line 67: `_KNOWN_CURRENCIES` contains `"INR"`, but lines 212-216 `normalise_currency` only checks uppercase equality and does not translate symbol representations like `₹`, `Rs.`, or `Rupees`.
   - `jd_parser.py` Lines 95-173: `_FEW_SHOT_EXAMPLES` only contains US (SF, CA) and generic USD remote examples, lacking explicit LPA (Lakhs Per Annum) or INR examples.
4. **`tests/test_ingestion.py`**:
   - Lines 420-504: Existing tests verify generic scraper operations but do not mock Indian job portals (`linkedin.com`, `naukri.com`, `indeed.com`, `in.indeed.com`) or test Indian location and INR currency extraction.

---

## 2. Logic Chain

1. **Requirement Analysis**: R2 requires fetching Indian job postings from LinkedIn (`site:linkedin.com/jobs` / `linkedin.com`), Naukri (`site:naukri.com` / `naukri.com`), and Indeed (`site:indeed.com` / `site:in.indeed.com` / `indeed.com`, `in.indeed.com`) via Tavily, and extracting at least 1 job with an Indian location ("Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India") and `INR` currency.
2. **Scraper Extension**: Adding `include_domains` parameter support to `TavilyJobScraper.search()` and `search_jobs()` allows Tavily API queries to be constrained directly to target Indian portals. Adding `detect_source_from_url()` allows URLs to be categorized as `linkedin`, `naukri`, or `indeed`.
3. **Task Default Integration**: Updating `run_crawler` in `ingestion/tasks.py` to default locations `["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]` and default domains `["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]` ensures default crawler runs capture Indian job postings.
4. **Parser & Currency Enhancements**: Updating `normalise_currency` to map `₹` / `Rs` -> `INR` and adding prompt instructions + an explicit Indian few-shot example in `jd_parser.py` guarantees accurate extraction of Indian locations and `INR` salary figures (including LPA formats).
5. **Test Design**: Building `tests/test_scraper.py` with mock Tavily API responses for LinkedIn, Naukri, and Indeed Indian jobs ensures unit/integration tests assert `len(parsed_jobs) >= 1`, location in Indian cities/India, and `salary_currency == "INR"`.

---

## 3. Caveats

- **Network Mode**: The investigation was conducted in `CODE_ONLY` mode. Live external network calls to Tavily API were not executed; test specs rely on deterministic API response mocks.
- **Tavily Domain Filters**: Tavily supports `include_domains` as a list of domain names (e.g. `["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`). For `linkedin.com`, `site:linkedin.com/jobs` can also be used as a query operator if Tavily domain inclusion returns non-job pages.

---

## 4. Conclusion

The problem, code locations, design modifications, and test cases for Requirement R2 are fully defined in `d:\projects\talentRadar\.agents\explorer_m2\analysis.md`. The design is complete, actionable, and ready for immediate implementation by the implementer agent.

---

## 5. Verification Method

To verify the implementation once completed:

1. **Execute Unit/Integration Tests**:
   ```powershell
   pytest tests/test_scraper.py tests/test_ingestion.py -v
   ```
2. **Execute Full Pipeline Tests**:
   ```powershell
   pytest tests/test_pipeline_e2e.py -v
   ```
3. **Inspect Output Files**:
   - `d:\projects\talentRadar\.agents\explorer_m2\analysis.md`
   - `d:\projects\talentRadar\.agents\explorer_m2\handoff.md`
4. **Invalidation Conditions**:
   - Any test failure in `test_scraper.py` asserting Indian job fetching, source detection (`linkedin`, `naukri`, `indeed`), or Indian location / `INR` currency parsing.
