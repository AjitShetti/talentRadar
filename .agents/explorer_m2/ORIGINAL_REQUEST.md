## 2026-07-22T10:41:47Z
You are an Explorer subagent for Milestone 2 (R2: Expand Indian Job Sources).
Working Directory: d:\projects\talentRadar\.agents\explorer_m2
Project Root: d:\projects\talentRadar

Objective: Produce a detailed, file-by-file refactoring plan and design specification to resolve Requirement R2:
"Update job fetching logic to include Indian job postings from LinkedIn, Naukri, and Indeed using the existing Tavily client, fetching and parsing at least 1 job posting from an Indian location."

Tasks:
1. Examine `ingestion/scrapers/tavily_client.py` (`TavilyJobScraper`). Analyze how search parameters, site operators (`site:linkedin.com/jobs`, `site:naukri.com`, `site:indeed.com`, `site:in.indeed.com`), `include_domains`, and source identification (LinkedIn, Naukri, Indeed) can be added/updated.
2. Examine `ingestion/tasks.py` (`run_crawler` and crawler tasks). Show how Indian location targets ("Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India") and Indian job source domains can be integrated into task execution defaults and function arguments.
3. Examine `ingestion/parsers/jd_parser.py` and job ingestion pipeline. Verify that parsing extracts Indian locations (e.g., "Bangalore, India", "Mumbai, India", "India") and INR currency properly.
4. Examine `tests/test_scraper.py` (or existing scraper test files). Design unit/integration tests with realistic Tavily API mock fixtures for Indian job postings on LinkedIn, Naukri, and Indeed, asserting that fetching and parsing returns >= 1 job posting from an Indian location.
5. Write your complete design specification to d:\projects\talentRadar\.agents\explorer_m2\analysis.md and summarize in d:\projects\talentRadar\.agents\explorer_m2\handoff.md. Send a message to parent when finished.
