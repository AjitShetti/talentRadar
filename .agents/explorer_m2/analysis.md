# Technical Design Specification & Refactoring Plan: Requirement R2 (Expand Indian Job Sources)

**Module / Component**: `ingestion/scrapers/tavily_client.py`, `ingestion/tasks.py`, `ingestion/parsers/jd_parser.py`, `ingestion/parsers/schemas.py`, `tests/test_scraper.py`  
**Milestone**: Milestone 2 (R2: Expand Indian Job Sources)  
**Author**: Explorer Subagent  
**Date**: 2026-07-22  

---

## 1. Executive Summary & Objective

Requirement R2 mandates:
> *"Update job fetching logic to include Indian job postings from LinkedIn, Naukri, and Indeed using the existing Tavily client, fetching and parsing at least 1 job posting from an Indian location."*

Currently:
1. `TavilyJobScraper.search()` (`ingestion/scrapers/tavily_client.py`) only accepts `query` and `max_results`, without utilizing Tavily's `include_domains` parameter or site query operators (`site:linkedin.com/jobs`, `site:naukri.com`, `site:indeed.com`, `site:in.indeed.com`).
2. `run_crawler` (`ingestion/tasks.py`) defaults to `["Remote", "New York"]` and hardcodes `_SOURCE_NAME = "ats_crawler"` for all jobs.
3. `JDParser` (`ingestion/parsers/jd_parser.py`) and `schemas.py` support `INR`, but `normalise_currency` lacks mapping for currency symbols (`₹`, `Rs.`, `Rupees`) and few-shot examples do not showcase Indian job postings (Lakhs Per Annum / LPA salary formats, Indian tech hubs).
4. Tests in `tests/test_ingestion.py` mock generic jobs without verifying Indian portal domains (LinkedIn, Naukri, Indeed), source identification, or Indian location extraction.

This specification details a step-by-step refactoring plan to upgrade the ingestion pipeline and test suite to meet Requirement R2.

---

## 2. Architecture & Data Flow Overview

```
                          [Celery Task: run_crawler]
                                     │
           ┌─────────────────────────┴────────────────────────┐
           ▼                                                  ▼
[Indian Locations Default]                       [Indian Job Source Domains]
"Bangalore", "Mumbai", "Delhi",                   linkedin.com, naukri.com,
"Hyderabad", "Pune", "India"                      indeed.com, in.indeed.com
           │                                                  │
           └─────────────────────────┬────────────────────────┘
                                     ▼
                      [TavilyJobScraper.search_jobs()]
                        │ - Payload: include_domains
                        │ - Query: site operators
                        ▼
                        [Tavily API Endpoint]
                                     │
                                     ▼
                             [RawJobResult]
                        │ + detect_source_from_url()
                        │   (linkedin | naukri | indeed)
                        ▼
                            [JDParser.parse_jd()]
                        │ - Prompt: INR & LPA awareness
                        │ - Few-shot: Indian posting
                        ▼
                         [ParsedJobDescription]
                        │ - location: "Bangalore, India"
                        │ - salary_currency: "INR"
                        ▼
                   [PostgreSQL & ChromaDB Persistence]
                  Job(external_id, source=detected_source)
```

---

## 3. File-by-File Refactoring Specification

### File 1: `ingestion/scrapers/tavily_client.py`

#### Changes Required:
1. **Define Constants**:
   Add `INDIAN_JOB_DOMAINS` and `SOURCE_DOMAIN_MAP`.
2. **URL Source Identification Helper**:
   Implement `detect_source_from_url(url: str) -> str` to identify whether a job URL originates from `linkedin`, `naukri`, `indeed`, or generic sources.
3. **Enhance `TavilyJobScraper.search()`**:
   Add `include_domains: Optional[List[str]] = None` and `exclude_domains: Optional[List[str]] = None`. When provided, include `"include_domains": include_domains` in the payload sent to `https://api.tavily.com/search`.
4. **Enhance `TavilyJobScraper.search_jobs()`**:
   Accept `include_domains: Optional[List[str]] = None` and `use_site_operators: bool = False`. If `use_site_operators` is `True`, append site filter string e.g. `(site:linkedin.com/jobs OR site:naukri.com OR site:in.indeed.com OR site:indeed.com)` to the query string.

#### Detailed Code Changes (`ingestion/scrapers/tavily_client.py`):

```python
# New Constants and Helper Function

INDIAN_JOB_DOMAINS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "in.indeed.com",
]

def detect_source_from_url(url: str) -> str:
    """
    Identify the origin job platform from a URL.
    Returns: 'linkedin' | 'naukri' | 'indeed' | 'greenhouse' | 'lever' | 'ashby' | 'tavily_search'
    """
    url_lower = url.lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    elif "naukri.com" in url_lower:
        return "naukri"
    elif "indeed.com" in url_lower:
        return "indeed"
    elif "greenhouse.io" in url_lower:
        return "greenhouse"
    elif "lever.co" in url_lower:
        return "lever"
    elif "ashbyhq.com" in url_lower:
        return "ashby"
    return "tavily_search"


class TavilyJobScraper:
    # Updated __init__, search, and search_jobs methods:

    def search(
        self,
        query: str,
        max_results: int = 10,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> List[RawJobResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_raw_content": True
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        
        response = self._client.post("https://api.tavily.com/search", json=payload)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("results", []):
            url = item.get("url", "").strip()
            if not url:
                continue
            
            try:
                result = RawJobResult(
                    title=item.get("title", ""),
                    url=url,
                    content=item.get("content", ""),
                    score=item.get("score", 0.0),
                    published_date=item.get("published_date"),
                    raw_content=item.get("raw_content")
                )
                results.append(result)
            except Exception:
                continue
                
        return results

    def search_jobs(
        self,
        role: str,
        location: str,
        count: int = 10,
        include_domains: Optional[List[str]] = None,
        use_site_operators: bool = False,
    ) -> List[RawJobResult]:
        """
        Search for job postings matching role and location.
        Can optionally filter by target domains (e.g. LinkedIn, Naukri, Indeed)
        via Tavily include_domains payload parameter or inline site: operators.
        """
        query_str = f"{role} jobs in {location}"
        if use_site_operators and include_domains:
            site_query = " OR ".join([f"site:{d}" for d in include_domains])
            query_str = f"{query_str} ({site_query})"

        return self.search(
            query=query_str,
            max_results=count,
            include_domains=include_domains,
        )
```

---

### File 2: `ingestion/tasks.py`

#### Changes Required:
1. **Define Location & Domain Defaults**:
   Add `DEFAULT_INDIAN_LOCATIONS` and `DEFAULT_INDIAN_DOMAINS`.
2. **Update `_run_pipeline`**:
   - Accept `include_domains: list[str] | None = None` and `source_name: str | None = None`.
   - Pass `include_domains` to `scraper.search_jobs()`.
   - Dynamically detect source per job using `detect_source_from_url(pjd.source_url)` so jobs ingested from LinkedIn, Naukri, or Indeed are properly tagged with `source="linkedin"`, `source="naukri"`, `source="indeed"`, etc. in PostgreSQL `Job` records.
3. **Update `run_crawler` Celery Task**:
   - Update default `locations` argument to include Indian cities when none are specified or when `indian_focus=True`.
   - Default `locations`: `["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]`.
   - Default `include_domains`: `["linkedin.com", "naukri.com", "indeed.com", "in.indeed.com"]`.

#### Detailed Code Changes (`ingestion/tasks.py`):

```python
# New Constants in ingestion/tasks.py

DEFAULT_INDIAN_LOCATIONS = [
    "Bangalore",
    "Mumbai",
    "Delhi",
    "Hyderabad",
    "Pune",
    "India",
]

DEFAULT_INDIAN_DOMAINS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "in.indeed.com",
]

# Updated _run_pipeline signature and execution:
async def _run_pipeline(
    roles: list[str],
    locations: list[str],
    max_results_per_query: int,
    run_id: str,
    include_domains: list[str] | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    all_paths: list[str] = []
    total_fetched = 0

    with TavilyJobScraper() as scraper:
        for role in roles:
            for location in locations:
                try:
                    results = scraper.search_jobs(
                        role=role,
                        location=location,
                        count=max_results_per_query,
                        include_domains=include_domains,
                    )
                    paths = scraper.save_raw(
                        results,
                        run_id=run_id,
                        role=role,
                        location=location,
                    )
                    all_paths.extend(str(p) for p in paths)
                    total_fetched += len(results)
                    logger.info("Fetched %d results for role=%r location=%r", len(results), role, location)
                except Exception as exc:
                    logger.error("fetch_raw failed for role=%r location=%r: %s", role, location, exc)

    if not all_paths:
        return {"status": "success", "message": "No jobs found"}

    # 2. Parse with LLM
    raw_results: list[RawJobResult] = []
    for fp in all_paths:
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
            result = RawJobResult(
                title=data.get("title", ""),
                url=data.get("url", ""),
                content=data.get("content", ""),
                score=float(data.get("score", 0.0)),
                published_date=data.get("published_date"),
                raw_content=data.get("raw_content"),
            )
            raw_results.append(result)
        except Exception as exc:
            logger.warning("Could not load raw file %s: %s", fp, exc)

    parser = JDParser()
    parsed = parser.batch_parse(raw_results)
    parsed_dicts = [p.model_dump() for p in parsed]

    # 3. Save to Postgres
    inserted = updated = skipped = 0
    chroma_items: list[dict[str, Any]] = []

    pipeline_source = source_name or "tavily_crawler"

    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)

        ingestion_run = await uow.ingestion_runs.create(
            source=pipeline_source,
            status=IngestionStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
            run_config={"celery_run_id": run_id, "include_domains": include_domains},
        )
        await session.commit()

        try:
            for data in parsed_dicts:
                try:
                    pjd = ParsedJobDescription(**data)
                except Exception as exc:
                    logger.warning("Skipping invalid parsed job: %s", exc)
                    skipped += 1
                    continue

                # Determine source dynamically per job URL
                from ingestion.scrapers.tavily_client import detect_source_from_url
                job_source = detect_source_from_url(pjd.source_url or "") if pjd.source_url else pipeline_source

                company_slug = _company_domain(pjd.company)
                company, _ = await uow.companies.upsert_by_domain(
                    domain=company_slug,
                    defaults={"name": pjd.company},
                )

                external_id = _stable_id(pjd.source_url or pjd.title + pjd.company)
                job_kwargs = pjd.to_job_kwargs()
                job_kwargs.update(
                    {
                        "company_id": company.id,
                        "ingestion_run_id": ingestion_run.id,
                    }
                )

                job, created = await uow.jobs.upsert_by_external_id(
                    external_id=external_id,
                    source=job_source,
                    defaults=job_kwargs,
                )
                if created:
                    inserted += 1
                else:
                    updated += 1

                # Prepare ChromaDB metadata
                metadata = {
                    "title": pjd.title,
                    "company": pjd.company,
                    "location": pjd.location or "",
                    "is_remote": pjd.is_remote,
                    "seniority": pjd.seniority or "",
                    "employment_type": pjd.employment_type or "",
                    "skills_str": ", ".join(pjd.skills),
                    "source_url": pjd.source_url or "",
                    "salary": pjd.salary or "",
                    "source": job_source,
                }

                chroma_items.append({
                    "job_id": external_id,
                    "text": (job_kwargs.get("description_clean") or pjd.raw_text)[:4096],
                    "metadata": metadata,
                    "internal_job_id": job.id,
                })

            await session.commit()
            ...
```

```python
# Updated run_crawler Celery Task Signature

@shared_task(name="ingestion.tasks.run_crawler", bind=True, max_retries=0)
def run_crawler(
    self,
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    include_domains: list[str] | None = None,
    max_results_per_query: int = 5,
    source_name: str | None = None,
) -> dict[str, Any]:
    roles = roles or ["Software Engineer", "Data Scientist"]
    # Default locations expanded to include Indian tech hubs:
    locations = locations or ["Remote", "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"]
    # Default domains set to Indian job sources + ATS platforms if unspecified:
    include_domains = include_domains or DEFAULT_INDIAN_DOMAINS
    run_id = str(uuid.uuid4())

    logger.info(
        "Starting run_crawler task task_id=%s run_id=%s roles=%s locations=%s domains=%s max=%s",
        self.request.id, run_id, roles, locations, include_domains, max_results_per_query,
    )

    try:
        result = asyncio.run(_run_pipeline(
            roles=roles,
            locations=locations,
            max_results_per_query=max_results_per_query,
            run_id=run_id,
            include_domains=include_domains,
            source_name=source_name,
        ))
        logger.info("run_crawler completed successfully: %s", result)
        return result
    except Exception as exc:
        logger.exception(
            "run_crawler failed task_id=%s run_id=%s error=%s",
            self.request.id, run_id, exc,
        )
        raise
```

---

### File 3: `ingestion/parsers/schemas.py` & `ingestion/parsers/jd_parser.py`

#### Changes Required in `ingestion/parsers/schemas.py`:
Enhance `salary_currency` field validator to map common Indian currency symbols/tokens (`₹`, `Rs`, `Rs.`, `Rupees`, `INR.`) to `"INR"`.

```python
# In ingestion/parsers/schemas.py

_CURRENCY_SYMBOL_MAP = {
    "₹": "INR",
    "RS": "INR",
    "RS.": "INR",
    "RUPEES": "INR",
    "INR.": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
}

@field_validator("salary_currency", mode="before")
@classmethod
def normalise_currency(cls, v: object) -> str | None:
    if v is None:
        return None
    upper = str(v).upper().strip()
    if upper in _CURRENCY_SYMBOL_MAP:
        return _CURRENCY_SYMBOL_MAP[upper]
    return upper if upper in _KNOWN_CURRENCIES else None
```

#### Changes Required in `ingestion/parsers/jd_parser.py`:
1. **Prompt Updates**: Clarify Indian compensation formats (Lakhs Per Annum / LPA: 1 Lakh = 100,000 INR) in `_SYSTEM_PROMPT`.
2. **Few-Shot Examples**: Add a explicit few-shot example for an Indian posting (e.g. Swiggy or Flipkart in Bangalore, India with INR salary in LPA).

```python
# In ingestion/parsers/jd_parser.py

_SYSTEM_PROMPT = """\
...
### Rules:
- Return ONLY the JSON object, with NO additional text, preamble, or explanation.
- If a field cannot be determined from the text, use null (not an empty string).
- Never invent data. Only extract what is explicitly stated.
- Normalise salary to annual figures when the posting states monthly, hourly, or LPA (Lakhs Per Annum) rates. Note: 1 Lakh = 100,000 INR (e.g., 15-25 LPA = 1,500,000 to 2,500,000 INR).
- Extract ALL skills mentioned in the requirements or responsibilities sections.
"""

# Add Example 3 to _FEW_SHOT_EXAMPLES:
    {
        "user": """\
Senior Backend Engineer – Swiggy
Location: Bangalore, Karnataka, India (Hybrid)

About the role:
Swiggy is looking for a Senior Backend Engineer to join our Delivery Platform team in Bangalore.

Key Responsibilities:
- Build high-scale microservices using Java, Go, and PostgreSQL
- Optimize Redis caching and Kafka message queues for high-concurrency order tracking
- Deploy services on AWS (EKS, S3, DynamoDB)

Requirements:
- 4-7 years of backend engineering experience
- Strong knowledge of Java, Go, Microservices, PostgreSQL, and Kafka

Compensation: ₹2,000,000 - ₹3,500,000 per annum (20 - 35 LPA) + stock options""",
        "assistant": """\
{
  "title": "Senior Backend Engineer",
  "company": "Swiggy",
  "skills": ["Java", "Go", "PostgreSQL", "Redis", "Kafka", "AWS", "EKS", "S3", "DynamoDB", "Microservices"],
  "experience": "4-7 years",
  "location": "Bangalore, India",
  "is_remote": false,
  "salary": "₹2,000,000 - ₹3,500,000 per annum (20 - 35 LPA)",
  "salary_min": 2000000,
  "salary_max": 3500000,
  "salary_currency": "INR",
  "employment_type": "full_time",
  "seniority": "senior"
}""",
    },
```

---

### File 4: `tests/test_scraper.py` (Test Suite Specification)

Create or expand `tests/test_scraper.py` to test Indian job fetching, source identification, and parsing end-to-end with realistic Tavily API mock fixtures.

#### Mock Data Fixtures (LinkedIn, Naukri, Indeed):

```python
# Fixture data for Indian Job Postings

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
            "content": "TechCorp Mumbai is looking for a Senior Data Scientist in Mumbai, India. Skills: Python, PyTorch, SQL, Machine Learning. Compensation: ₹2,500,000 - ₹4,000,000 / year.",
            "score": 0.92,
            "published_date": "2026-07-05",
            "raw_content": "Full text: TechCorp hiring Senior Data Scientist in Mumbai, India...",
        },
        {
            "title": "Full Stack Developer - Indeed India",
            "url": "https://in.indeed.com/viewjob?jk=789012abcdef",
            "content": "FinTech India is hiring a Full Stack Developer in Hyderabad, India. React, Node.js, AWS. Salary: ₹1,500,000 - ₹2,200,000 per year.",
            "score": 0.89,
            "published_date": "2026-07-10",
            "raw_content": "Full text: FinTech India hiring Full Stack Developer in Hyderabad, India...",
        },
    ]
}
```

#### Test Suite Implementation (`tests/test_scraper.py`):

```python
"""
tests/test_scraper.py
~~~~~~~~~~~~~~~~~~~~~~
Unit and integration tests for TavilyJobScraper Indian job sources (LinkedIn, Naukri, Indeed).
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
from ingestion.tasks import DEFAULT_INDIAN_LOCATIONS, DEFAULT_INDIAN_DOMAINS


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

        # Verify POST payload sent to Tavily API
        assert mock_http.post.called
        call_kwargs = mock_http.post.call_args[1]
        payload = call_kwargs["json"]

        assert payload["include_domains"] == INDIAN_JOB_DOMAINS
        assert "Software Engineer" in payload["query"]
        assert "Bangalore" in payload["query"]

        # Verify returned RawJobResults
        assert len(results) == 3
        assert results[0].url.startswith("https://www.linkedin.com")
        assert results[1].url.startswith("https://www.naukri.com")
        assert results[2].url.startswith("https://in.indeed.com")

    def test_indian_location_fetching_and_parsing_returns_at_least_one_job(self):
        """
        Verify that fetching and parsing mock Tavily API results from LinkedIn, Naukri,
        and Indeed returns >= 1 job from an Indian location with INR currency.
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

        # Mock Groq LLM parsing
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
            "salary_currency": "INR",
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

            # Assertions
            assert len(parsed_jobs) >= 1
            indian_locations = {"Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India"}
            
            # Verify at least 1 job is from an Indian location
            has_indian_location = any(
                any(city in (job.location or "") for city in indian_locations)
                for job in parsed_jobs
            )
            assert has_indian_location, "Expected at least 1 job from an Indian location"

            # Verify currency normalization to INR
            first_job = parsed_jobs[0]
            assert first_job.salary_currency == "INR"
            assert first_job.location == "Bangalore, India"


class TestCrawlerIndianTaskDefaults:
    def test_default_indian_locations_contain_major_hubs(self):
        assert "Bangalore" in DEFAULT_INDIAN_LOCATIONS
        assert "Mumbai" in DEFAULT_INDIAN_LOCATIONS
        assert "Delhi" in DEFAULT_INDIAN_LOCATIONS
        assert "Hyderabad" in DEFAULT_INDIAN_LOCATIONS
        assert "Pune" in DEFAULT_INDIAN_LOCATIONS
        assert "India" in DEFAULT_INDIAN_LOCATIONS

    def test_default_indian_domains_contain_portals(self):
        assert "linkedin.com" in DEFAULT_INDIAN_DOMAINS
        assert "naukri.com" in DEFAULT_INDIAN_DOMAINS
        assert "indeed.com" in DEFAULT_INDIAN_DOMAINS
        assert "in.indeed.com" in DEFAULT_INDIAN_DOMAINS
```

---

## 4. Verification Protocol & Acceptance Criteria

To independently verify the refactored code after implementation:

1. **Run Unit & Scraper Tests**:
   ```powershell
   pytest tests/test_scraper.py tests/test_ingestion.py -v
   ```
   *Expected Result*: All tests pass, validating domain payloads, source detection, INR currency parsing, and Indian location extraction.

2. **Run Ingestion Pipeline Integration Test**:
   ```powershell
   pytest tests/test_pipeline_e2e.py -v
   ```
   *Expected Result*: End-to-end ingestion task processes Indian locations and stores job records with source tagged as `linkedin`, `naukri`, or `indeed`.

3. **Verify Database Records**:
   Run database query to verify stored job records:
   ```sql
   SELECT id, title, location_raw, salary_currency, source, source_url 
   FROM jobs 
   WHERE source IN ('linkedin', 'naukri', 'indeed', 'tavily_search')
     AND location_raw ILIKE '%India%' OR location_raw ILIKE '%Bangalore%' OR location_raw ILIKE '%Mumbai%';
   ```
   *Expected Result*: Returns >= 1 job posting with `location_raw` matching an Indian location and `salary_currency` equal to `INR`.

---

## 5. Summary of Files to Modify

| File Path | Description of Modification |
|---|---|
| `ingestion/scrapers/tavily_client.py` | Add `include_domains` to Tavily API payload, site operator support, and `detect_source_from_url` logic. |
| `ingestion/tasks.py` | Include Indian location targets ("Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune", "India") and Indian domain list (`linkedin.com`, `naukri.com`, `indeed.com`, `in.indeed.com`) as task defaults. Dynamically attribute `Job.source`. |
| `ingestion/parsers/schemas.py` | Expand `salary_currency` validator to map currency symbols (`₹`, `Rs`, `Rupees`) to `"INR"`. |
| `ingestion/parsers/jd_parser.py` | Update `_SYSTEM_PROMPT` rules for LPA / INR figures and add an explicit Indian job few-shot example. |
| `tests/test_scraper.py` | Add unit/integration tests with realistic Tavily API mock fixtures for LinkedIn, Naukri, and Indeed Indian jobs. |
