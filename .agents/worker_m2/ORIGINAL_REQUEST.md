## 2026-07-22T10:42:53Z
You are a Worker subagent for Milestone 2 (R2: Expand Indian Job Sources).
Working Directory: d:\projects\talentRadar\.agents\worker_m2
Project Root: d:\projects\talentRadar

Objective: Implement Requirement R2 ("Update job fetching logic to include Indian job postings from LinkedIn, Naukri, and Indeed using the existing Tavily client, fetching and parsing at least 1 job posting from an Indian location").

Refer to design specification in d:\projects\talentRadar\.agents\explorer_m2\analysis.md and handoff report in d:\projects\talentRadar\.agents\explorer_m2\handoff.md for full details.

Tasks to implement:
1. `ingestion/scrapers/tavily_client.py`:
   - Update `search()` and `search_jobs()` to accept `include_domains: list[str] | None = None` and pass `include_domains` to Tavily API payloads.
   - Support target domains for LinkedIn (`linkedin.com`), Naukri (`naukri.com`), and Indeed (`indeed.com`, `in.indeed.com`).
   - Add helper function `detect_source_from_url(url: str)` to map URLs to source names (`"linkedin"`, `"naukri"`, `"indeed"`, etc.).
2. `ingestion/tasks.py`:
   - Update `run_crawler` to default locations including Indian tech hubs (`["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]`) and default domains (`["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`).
   - Dynamically attribute job source name using `detect_source_from_url()`.
3. `ingestion/parsers/schemas.py` and `ingestion/parsers/jd_parser.py`:
   - Update `normalise_currency()` to map currency symbols `₹`, `Rs`, `Rs.`, `Rupees` -> `"INR"`.
   - Update LLM JD parser prompts and few-shot examples to support Indian locations and LPA salary formats.
4. Testing & Verification:
   - Create `tests/test_scraper.py` with mock Tavily API responses for LinkedIn, Naukri, and Indeed Indian job postings.
   - Assert fetching and parsing returns >= 1 job posting from an Indian location with correct source attribution (`"linkedin"`, `"naukri"`, `"indeed"`), Indian location, and `INR` currency.
   - Run tests: `python -m pytest tests/test_scraper.py tests/test_ingestion.py --no-cov` and `python -m pytest --no-cov`.
   - Document commands executed and exact test outputs in your handoff report.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Write your report to d:\projects\talentRadar\.agents\worker_m2\changes.md and d:\projects\talentRadar\.agents\worker_m2\handoff.md. Send a message to parent when complete.
