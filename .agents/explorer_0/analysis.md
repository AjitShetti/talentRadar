# TalentRadar Codebase Exploration & Technical Analysis

**Author:** Explorer Agent (`explorer_0`)  
**Date:** 2026-07-22  
**Project:** TalentRadar (`d:\projects\talentRadar`)  

---

## Executive Summary

This report presents a comprehensive investigation of the **TalentRadar** codebase to support two upcoming feature objectives:
1. **R1: Job Search Relevance** — Enhancing search retrieval logic so queries for broad technology roles (e.g., `"java dev"`, `"software engineer"`) reliably return at least 3 relevant job postings from the database.
2. **R2: Expand Indian Job Sources** — Updating job fetching and parsing logic to ingest Indian job postings from **LinkedIn**, **Naukri**, and **Indeed** using the existing Tavily client.

---

## 1. System Architecture & Tech Stack

### 1.1 Languages & Core Frameworks
* **Language:** Python `>=3.11` (Running Python 3.12 in `.venv`)
* **Web Framework:** FastAPI `0.115.0+` with Uvicorn (ASGI)
* **Database & ORM:** PostgreSQL schema managed via SQLAlchemy `2.0+` (`asyncpg` driver for async runtime queries, `psycopg2-binary` for sync Alembic migrations).
* **Vector Store & Embeddings:** ChromaDB `0.5.0+` using SentenceTransformers (`all-MiniLM-L6-v2` 384-dim embeddings) and cosine distance HNSW index.
* **LLM & Agent Orchestration:** LangGraph `0.2.0+` with Groq API client (`llama-3.1-8b-instant` for parsing/intent extraction, `llama-3.3-70b-versatile` for summary generation).
* **Ingestion & Web Search:** `tavily-python` API client, `beautifulsoup4`, Celery `5.4+` + Redis `5.0+` task queues.
* **Testing Stack:** Pytest `8.2+`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `httpx`.

### 1.2 Project Layout & Entry Points
```
talentRadar/
├── api/
│   ├── main.py                  # Application entry point (FastAPI app router setup)
│   ├── dependencies.py          # UOW & DB session dependency injection
│   ├── routers/
│   │   ├── search.py            # Structured (/search/structured) & Semantic (/search/semantic) endpoints
│   │   ├── query.py             # Natural language AI query router (/query/)
│   │   ├── ingest.py            # Ingestion trigger (/ingest/trigger) & audit history (/ingest/runs)
│   │   ├── match.py             # Resume matching & tailoring endpoints
│   │   └── auth.py, trends.py   # Auth, user management & trend analytics
│   └── schemas/                 # Pydantic v2 request/response schemas
├── agents/
│   ├── graph.py                 # LangGraph state graph (node_classify -> node_rag_retrieve -> END)
│   ├── orchestrator.py          # Orchestrator & intent classification (_classify_intent)
│   ├── rag_agent.py             # RAG retrieval pipeline (ChromaDB + PostgreSQL + Groq LLM)
│   ├── state.py                 # Agent state dataclasses & RetrievalResult schema
│   └── tasks.py                 # Background agent tasks
├── storage/
│   ├── models.py                # ORM models (Company, Job, IngestionRun, User, InterviewSession)
│   ├── database.py              # Async engine & session factory (AsyncSessionLocal)
│   └── repository.py            # BaseRepository, JobRepository, CompanyRepository, IngestionRunRepository, UnitOfWork
├── ingestion/
│   ├── scrapers/
│   │   └── tavily_client.py     # TavilyJobScraper HTTP client
│   ├── parsers/
│   │   ├── jd_parser.py         # LLM-powered Job Description Parser (Groq llama-3.1-8b-instant)
│   │   └── schemas.py           # RawJobResult & ParsedJobDescription Pydantic schemas
│   ├── embeddings/
│   │   ├── chroma_store.py      # ChromaJobStore wrapper
│   │   └── embedder.py          # Cosine similarity helper utilities
│   ├── celery_app.py            # Celery instance configuration
│   └── tasks.py                 # run_crawler task pipeline (_run_pipeline)
├── data/
│   └── raw/                     # File storage for raw JSON scraped job descriptions
├── tests/                       # Pytest suite (test_rag.py, test_api.py, test_ingestion.py, etc.)
└── pyproject.toml               # Poetry/Hatch/UV dependencies and tool configurations
```

---

## 2. Deep-Dive: Search Retrieval & Root Cause Analysis (R1)

### 2.1 Existing Search Retrieval Paths

TalentRadar has two distinct search execution paths:

#### Path A: Structured SQL Search (`/search/structured`)
* **Location:** `api/routers/search.py` -> `search_jobs_structured()`
* **Repository Method:** `storage/repository.py` -> `JobRepository.search()`
* **Query Mechanism:**
  ```python
  # storage/repository.py (Lines 525-526)
  if title:
      filters.append(Job.title.ilike(f"%{title}%"))
  ```

#### Path B: Semantic / RAG Search (`/query/` & `/search/semantic`)
* **Location:** `agents/graph.py` -> `node_classify` -> `node_rag_retrieve` -> `agents/rag_agent.py` -> `RAGAgent.search_jobs()`
* **Retrieval Steps:**
  1. `Orchestrator._classify_intent()` extracts intent, keywords, skills via LLM.
  2. `RAGAgent.search_jobs()` queries ChromaDB via `ChromaJobStore.search(query=context.raw_query, n_results=limit * 2)`.
  3. `_build_results()` fetches matching jobs from PostgreSQL via `JobRepository.get_by_external_ids()`.
  4. `_apply_filters()` applies post-retrieval Python filtering on skills, remote, seniority, company.

### 2.2 Why Broad Queries (e.g., "java dev", "software engineer") Currently Return < 3 Results or Fail

Our investigation identified **4 main root causes**:

1. **Exact Substring Matching on `Job.title` in SQL Search:**
   * `JobRepository.search()` filters by `Job.title.ilike(f"%{title}%")`.
   * Searching for `"java dev"` searches for the verbatim contiguous substring `"%java dev%"`.
   * Real job postings in the database have titles like `"Senior Java Developer"`, `"Java Backend Engineer"`, `"Java Software Engineer"`. None contain the exact string `"java dev"`.
   * Result: Structured search returns **0 results**.

2. **Single-Column Scope in SQL Search:**
   * `JobRepository.search()` only checks `Job.title` when a search query is provided.
   * It does **not** search `Job.description_clean`, `Job.skills` array, or `Job.tags`.
   * A job titled `"Backend Engineer"` whose description and skills list `"Java"` will be completely missed by a query for `"java"`.

3. **Overly Restrictive Skill Filtering in RAG Search:**
   * In `RAGAgent._apply_filters()` (`agents/rag_agent.py` lines 168-174):
     ```python
     if context.skills:
         filtered = [
             r for r in filtered
             if any(s.lower() in [rs.lower() for rs in r.skills] for s in context.skills)
         ]
     ```
   * If intent classification extracts `skills=["Java"]`, any retrieved candidate job whose `skills` array does not explicitly contain `"Java"` (or if skills extraction missed it during parsing) is discarded.
   * Because ChromaDB `n_results` is small (`limit * 2 = 20`), if only 1-2 retrieved documents have `"Java"` in their metadata skills list, post-filtering leaves fewer than 3 results.

4. **Seed / Ingested Dataset Gaps:**
   * Existing raw dataset under `data/raw/` contains jobs for roles `"software_engineer"`, `"data_scientist"`, `"data_engineer"`, `"product_manager"`, `"python_developer"`.
   * Postings specifically for `"Java"` or `"Java Developer"` are missing or sparse in the initial seed store.

---

## 3. Deep-Dive: Tavily Client & Job Scraping Logic (R2)

### 3.1 Existing Tavily Client Implementation
* **Location:** `ingestion/scrapers/tavily_client.py` -> `TavilyJobScraper`
* **Current Query Construction:**
  ```python
  # ingestion/scrapers/tavily_client.py (Lines 71-73)
  def search_jobs(self, role: str, location: str, count: int = 10) -> List[RawJobResult]:
      query = f"{role} jobs in {location}"
      return self.search(query, max_results=count)
  ```

### 3.2 Key Deficiencies for Indian Job Sources (LinkedIn, Naukri, Indeed)

1. **No Domain Targeting or Site Operators:**
   * `search_jobs()` currently generates generic queries like `"Software Engineer jobs in New York"`.
   * It does not target major Indian job boards:
     - **LinkedIn India:** `linkedin.com/jobs` or `in.linkedin.com/jobs`
     - **Naukri:** `naukri.com`
     - **Indeed India:** `indeed.com` or `in.indeed.com`
   * Neither domain site operators (`site:naukri.com OR site:linkedin.com/jobs OR site:indeed.com`) nor Tavily API domain filters (`include_domains`) are utilized.

2. **No Indian Location Default Configurations:**
   * `ingestion/tasks.py` (`run_crawler`) defaults to:
     ```python
     roles = roles or ["Software Engineer", "Data Scientist"]
     locations = locations or ["Remote", "New York"]
     ```
   * Indian tech hubs (e.g. `"India"`, `"Bangalore"`, `"Mumbai"`, `"Delhi NCR"`, `"Hyderabad"`, `"Pune"`) are absent.

3. **Parsing Compatibility for Indian Postings:**
   * `JDParser` (`ingestion/parsers/jd_parser.py`) and `ParsedJobDescription` (`ingestion/parsers/schemas.py`) already support ISO currency code `INR` (Indian Rupee) and currency normalization.
   * `ParsedJobDescription.to_job_kwargs()` maps salary, location, and skills correctly into Postgres ORM fields.

---

## 4. Test Suites, Test Runners & Execution Commands

### 4.1 Test Infrastructure
* **Test Runner:** Pytest (`pytest 8.3.4`)
* **Test Directory:** `tests/`
* **Test Files Identified:**
  1. `tests/test_rag.py` — RAG agent unit tests, filter logic, embedder utils, Chroma store interface.
  2. `tests/test_api.py` — FastAPI endpoints integration tests (search, query, ingest, match).
  3. `tests/test_ingestion.py` — Tavily client, JD parser, ingestion pipeline unit tests.
  4. `tests/test_pipeline_e2e.py` — Full end-to-end ingestion and RAG query pipeline tests.
  5. `tests/test_resume_matcher.py` — ML resume matching score tests.
  6. `tests/smoke_test_ingestion.py` — Smoke tests for ingestion flow.
  7. `tests/eval/eval_rag.py` — RAG evaluation script (Ragas metrics).

### 4.2 Exact Execution Commands

> **Important Note on Import Paths & Coverage Threshold:**  
> Running `pytest` directly without python module context can fail with `ModuleNotFoundError: No module named 'config'`. Running `python -m pytest` ensures project root is on `sys.path`.  
> `pyproject.toml` configures `--cov=.` with `fail_under = 70`. When running individual test files, pass `--no-cov` to prevent coverage failure.

* **Run entire test suite:**
  ```bash
  python -m pytest
  ```

* **Run single test module (e.g., RAG tests):**
  ```bash
  python -m pytest tests/test_rag.py --no-cov
  ```

* **Run single test function by name:**
  ```bash
  python -m pytest tests/test_rag.py -k "test_skill_filter_case_insensitive" --no-cov
  ```

* **Run ingestion tests:**
  ```bash
  python -m pytest tests/test_ingestion.py --no-cov
  ```

* **Run API integration tests:**
  ```bash
  python -m pytest tests/test_api.py --no-cov
  ```

* **Run full test suite with coverage report:**
  ```bash
  python -m pytest --cov=. --cov-report=term-missing
  ```

---

## 5. Recommended Implementation Plans

### 5.1 Plan for R1: Job Search Relevance
1. **Tokenized / Multi-field SQL Search (`storage/repository.py`):**
   - Update `JobRepository.search()` to split search query into individual terms/keywords.
   - Match terms across `Job.title`, `Job.description_clean`, and `Job.skills` using ILIKE or PostgreSQL Full-Text Search (`tsvector`/`tsquery`).
   - Add synonym/abbreviation mapping for broad role terms (e.g., `"dev"` ↔ `"developer"`, `"swe"` ↔ `"software engineer"`).
2. **Hybrid / Flexible RAG Filtering (`agents/rag_agent.py`):**
   - Soften skill filtering in `RAGAgent._apply_filters()` so query term synonyms (e.g., `"java"` matching Java skills or Java in title/description) score higher rather than dropping results aggressively.
   - Increase ChromaDB over-fetch limit (`n_results = max(context.limit * 5, 50)`).
3. **Database Seed Expansion:**
   - Seed database with job postings for Java, Broad Software Engineering, and related tech roles so database queries reliably return at least 3 relevant postings.

### 5.2 Plan for R2: Expand Indian Job Sources
1. **Domain-Targeted Tavily Search (`ingestion/scrapers/tavily_client.py`):**
   - Extend `TavilyJobScraper.search_jobs()` to accept optional target domains or construct site-specific search queries:
     `site:linkedin.com/jobs OR site:naukri.com OR site:indeed.com` (or pass `include_domains=["linkedin.com", "naukri.com", "indeed.com"]`).
2. **Indian Location Configurations (`ingestion/tasks.py`):**
   - Add Indian tech hub locations (`"India"`, `"Bangalore"`, `"Mumbai"`, `"Delhi NCR"`, `"Hyderabad"`, `"Pune"`) to default/supported locations in `run_crawler`.
3. **Ingestion & Verification:**
   - Run ingestion for Indian job postings and verify parsing of INR salaries, Indian city/country fields, and database insertion.
