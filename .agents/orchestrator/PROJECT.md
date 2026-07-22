# TalentRadar Architecture & Milestone Index

## Architecture Overview
- **Backend Framework**: FastAPI (`api/main.py`, `api/routers/search.py`)
- **Database / ORM**: SQLAlchemy 2.0 async (`storage/database.py`, `storage/models.py`, `storage/repository.py`)
- **Vector DB / RAG**: ChromaDB (`ingestion/embeddings/chroma_store.py`), LangGraph RAG Agent (`agents/rag_agent.py`, `agents/graph.py`)
- **Job Ingestion & Scraping**: Tavily Client (`ingestion/scrapers/tavily_client.py`), Task Crawler (`ingestion/tasks.py`), JD Parser (`ingestion/parsers/jd_parser.py`)
- **Test Framework**: Pytest (`pytest 8.3.4`)

## Code Layout
- `api/routers/search.py`: Search API endpoints
- `storage/repository.py`: `JobRepository` with `search()` query builder
- `agents/rag_agent.py`: `RAGAgent` semantic search and post-filtering
- `ingestion/scrapers/tavily_client.py`: Tavily client search & scraping methods
- `ingestion/tasks.py`: Ingestion crawler task configuration
- `data/raw/`: Raw seed job postings
- `tests/`: Pytest test suite (`test_rag.py`, `test_search.py`, `test_scraper.py`, etc.)

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | Codebase & Architecture Exploration | Analyze codebase layout, search flaws, scraper flaws, test command | none | DONE |
| M1 | R1: Job Search Relevance | Refactor `JobRepository.search()`, query tokenization/expansion, multi-field search, seed Java/tech postings | M0 | DONE |
| M2 | R2: Expand Indian Job Sources | Update `TavilyJobScraper`, site operators (`site:linkedin.com/jobs`, `naukri.com`, `indeed.com`), Indian location defaults, parsing | M0 | DONE |
| M3 | E2E Integration & Integrity Verification | Full test suite execution, reviewer verification, forensic audit | M1, M2 | DONE |

## Interface & Quality Contracts
- **R1 Acceptance Criterion**: Queries for broad technology roles (e.g., "java dev", "software engineer") must return at least 3 relevant job postings from the database.
- **R2 Acceptance Criterion**: Job fetching logic must include Indian job postings from LinkedIn, Naukri, and Indeed using Tavily client, fetching and parsing at least 1 job posting from an Indian location (e.g. Bangalore, Mumbai, Delhi, India).
- **Test Command**: `python -m pytest tests/ --no-cov` or `python -m pytest tests/test_rag.py --no-cov`
