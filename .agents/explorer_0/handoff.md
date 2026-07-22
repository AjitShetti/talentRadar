# Handoff Report: Initial Codebase Exploration (R1 & R2)

**Agent:** `explorer_0`  
**Date:** 2026-07-22  
**Target:** TalentRadar Codebase (`d:\projects\talentRadar`)  
**Analysis Artifact:** `d:\projects\talentRadar\.agents\explorer_0\analysis.md`  

---

## 1. Observation

1. **Tech Stack & Layout:**
   - Python 3.11+, FastAPI (`api/main.py`), SQLAlchemy 2.0 async (`storage/database.py`, `storage/models.py`), ChromaDB (`ingestion/embeddings/chroma_store.py`), LangGraph (`agents/graph.py`), Groq LLM (`agents/orchestrator.py`, `ingestion/parsers/jd_parser.py`), Tavily client (`ingestion/scrapers/tavily_client.py`).
2. **Search Retrieval Logic (R1):**
   - **Structured Search:** `api/routers/search.py` (lines 37-123) delegates to `JobRepository.search()` in `storage/repository.py` (lines 485-578). Filter condition on `title` is `Job.title.ilike(f"%{title}%")` (lines 525-526).
   - **RAG Semantic Search:** `agents/rag_agent.py` -> `RAGAgent.search_jobs()` (lines 52-160) queries ChromaDB (`ChromaJobStore.search`), fetches DB rows (`get_by_external_ids`), and filters via `_apply_filters()` (lines 161-178).
   - `JobRepository.search` performs exact single-string ILIKE on `Job.title` only. It does not check `description_clean`, `skills`, or `tags`, nor does it tokenize queries like `"java dev"`.
   - `RAGAgent._apply_filters` applies strict filtering on `context.skills` (`skills=["Java"]`), dropping results if ChromaDB top-K matches lack explicit `"Java"` in metadata.
   - Seed data in `data/raw` only contains raw postings for `"software_engineer"`, `"data_scientist"`, `"data_engineer"`, `"product_manager"`, `"python_developer"`. Java postings are virtually non-existent in default raw data.
3. **Tavily Client & Scraping Logic (R2):**
   - `ingestion/scrapers/tavily_client.py` -> `TavilyJobScraper.search_jobs()` (lines 71-73) constructs query `f"{role} jobs in {location}"`.
   - `TavilyJobScraper` does not specify target domains (`linkedin.com/jobs`, `naukri.com`, `indeed.com`) or site search operators (`site:naukri.com`).
   - `ingestion/tasks.py` -> `run_crawler` (lines 222-223) defaults to `roles=["Software Engineer", "Data Scientist"]` and `locations=["Remote", "New York"]`. Indian locations (Bangalore, Mumbai, Delhi, India) are not in default tasks.
   - `JDParser` (`ingestion/parsers/jd_parser.py`) and `ParsedJobDescription` (`ingestion/parsers/schemas.py`) already support ISO currency `INR` and location parsing.
4. **Test Suite & Runner:**
   - Test framework: Pytest (`pytest 8.3.4`).
   - Running `pytest` directly without `PYTHONPATH` causes `ModuleNotFoundError: No module named 'config'`.
   - Running `python -m pytest tests/test_rag.py --no-cov` executes 29 tests, passing in ~2.37s.

---

## 2. Logic Chain

1. **Structured Search Defect:**
   - Observation: `JobRepository.search()` applies `Job.title.ilike(f"%{title}%")`.
   - Premise: Multi-word broad queries like `"java dev"` do not equal exact substring `"%java dev%"`. Real titles are `"Senior Java Developer"` or `"Java Backend Engineer"`.
   - Conclusion: Structured search returns 0 results for queries like `"java dev"` because of strict exact substring match and single-column scoping (`Job.title`).

2. **RAG Search Defect & Dataset Gap:**
   - Observation: `RAGAgent._apply_filters()` filters out jobs not matching `context.skills`, and seed store `data/raw` has few to zero Java job postings.
   - Premise: If database contains < 3 Java postings and post-filtering discards non-exact skill matches, RAG search cannot return at least 3 relevant jobs.
   - Conclusion: R1 requires both multi-field keyword/hybrid search logic AND seeding Java/Software Engineering job postings into the database.

3. **Indian Job Source Expansion Defect:**
   - Observation: `TavilyJobScraper.search_jobs()` uses generic string `f"{role} jobs in {location}"` without `site:` operators or `include_domains`, and `run_crawler` lacks Indian locations.
   - Premise: Without targeting LinkedIn, Naukri, or Indeed, or providing Indian location parameters, Tavily searches yield general web search hits rather than structured Indian job postings.
   - Conclusion: R2 requires updating `TavilyJobScraper` query/domain parameters to target `linkedin.com/jobs`, `naukri.com`, and `indeed.com` (or `in.indeed.com`), and configuring Indian location targets in crawler tasks.

---

## 3. Caveats

- **External API Keys:** Groq API key (`GROQ_API_KEY`) and Tavily API key (`TAVILY_API_KEY`) are required for live ingestion and intent extraction. Mock fixtures in `tests/conftest.py` allow running unit tests offline.
- **ChromaDB Docker/Service:** In unit tests, ChromaDB is mocked. For local runtime integration, ChromaDB service or HttpClient setup is configured in `config/settings.py`.

---

## 4. Conclusion

The TalentRadar codebase is well-structured with clear separation of concerns (FastAPI routers, LangGraph agent, SQLAlchemy repositories, Tavily scrapers, and Pydantic parsers). The root causes for search relevance failures (R1) and limited Indian job source coverage (R2) have been fully diagnosed and documented in `d:\projects\talentRadar\.agents\explorer_0\analysis.md`. Implementation plans are ready for downstream execution.

---

## 5. Verification Method

To independently verify the observations and analysis:
1. **Inspect Search Logic:**
   - View `storage/repository.py` lines 525-526 to confirm `Job.title.ilike(f"%{title}%")`.
   - View `agents/rag_agent.py` lines 168-174 to confirm skill filtering in `_apply_filters()`.
2. **Inspect Tavily Client & Crawler:**
   - View `ingestion/scrapers/tavily_client.py` lines 71-73 to confirm generic search query format.
   - View `ingestion/tasks.py` lines 222-223 to confirm default role/location lists.
3. **Execute Unit Tests:**
   - Run: `python -m pytest tests/test_rag.py --no-cov`
   - Confirm all 29 tests pass.
