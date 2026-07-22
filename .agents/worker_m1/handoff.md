# Handoff Report: Requirement R1 (Improve Job Search Relevance)

## 1. Observation
- **`storage/repository.py`**:
  - `tokenize_and_expand_query()` tokenizes input query strings, strips punctuation, removes domain stop words (`in`, `at`, `for`, `job`, `role`, etc.), and expands tokens using `SYNONYM_MAP` (`dev` $\rightarrow$ `dev, developer, development, engineer`, `java` $\rightarrow$ `java, j2ee, spring, spring boot`, `swe` $\rightarrow$ `swe, software engineer, software developer`, etc.).
  - `_build_term_group_clause()` builds multi-field search conditions matching expanded terms across `Job.title`, `Job.description_clean`, `Job.skills`, and `Job.tags`.
  - `JobRepository.search()` accepts `query: str | None = None` (using `search_text = query or title`), executes strict AND matching across term groups, and automatically falls back to relaxed OR matching if total matches $< \text{min\_results}$ (default 3).
- **`agents/rag_agent.py`**:
  - `ROLE_KEYWORDS` set contains generic role nouns (`dev`, `developer`, `engineer`, `software`, `fullstack`, `backend`, `frontend`, etc.).
  - `_apply_filters()` strips `ROLE_KEYWORDS` from `context.skills` before applying skill filters, preventing valid Java jobs from being discarded when broad queries extract `"dev"` as a skill. Supports structured filters for `is_remote`, `seniority`, and `company`.
  - `search_jobs()` checks result count after vector search and `_apply_filters()`. If results $< 3$, it executes `_search_db_fallback(context)` to query PostgreSQL via `JobRepository.search()`, merging relational matches up to `context.limit`.
- **Seed Data (`data/raw/`)**:
  - Created 12 raw JSON seed job postings in `data/raw/java_developer/remote/`, `data/raw/software_engineer/remote/`, and `data/raw/fullstack_developer/remote/`.
- **Test Suite (`tests/test_search.py`)**:
  - Implemented unit and integration tests verifying query tokenization, repository broad query search, filter sanitization, API structured search, and API semantic search with DB fallback.

## 2. Logic Chain
1. Searching for broad role queries like `"java dev"`, `"software engineer"`, and `"fullstack dev"` failed previously because search attempted exact phrase matching against `title` alone, or forced generic role nouns into rigid skill array checks.
2. Expanding query terms with synonyms and searching across `Job.title`, `Job.description_clean`, `Job.skills`, and `Job.tags` enables term-level precision.
3. Implementing AND-to-OR fallback in `JobRepository.search()` guarantees that multi-term queries fetch relevant jobs even if no single job contains every query word verbatim.
4. Sanitizing `ROLE_KEYWORDS` in `RAGAgent._apply_filters()` prevents intent classification output (`skills=["java", "dev"]`) from rejecting valid Java job postings that list skills as `["Java", "Spring Boot"]`.
5. Fallback in `RAGAgent.search_jobs()` to `JobRepository.search()` when ChromaDB yields $< 3$ results ensures that relational search backs up vector search.
6. Test execution via `python -m pytest tests/test_search.py tests/test_rag.py --no-cov` confirms all 44 tests pass.

## 3. Caveats
- ChromaDB vector search in production requires vector embeddings generated during ingestion tasks. In unit test environments where ChromaDB embeddings are empty or mocked, `_search_db_fallback()` ensures relational database search handles broad queries cleanly without throwing errors.
- SQLite used in unit test DB fixtures does not natively support PostgreSQL GIN indexes; `func.array_to_string()` is used for multi-database compatibility.

## 4. Conclusion
Requirement R1 is completely implemented, verified, and ready. Broad technology role queries (`"java dev"`, `"software engineer"`, `"fullstack dev"`) reliably return $\ge 3$ relevant job postings across both structured and semantic search endpoints.

## 5. Verification Method
1. Run target search test suite:
   ```powershell
   python -m pytest tests/test_search.py tests/test_rag.py --no-cov
   ```
   **Output**: `44 passed in 9.87s`
2. Run full project test suite:
   ```powershell
   python -m pytest --no-cov
   ```
   **Output**: `94 passed in 10.96s`
3. Files to inspect:
   - `storage/repository.py`
   - `agents/rag_agent.py`
   - `data/raw/java_developer/remote/`
   - `data/raw/software_engineer/remote/`
   - `data/raw/fullstack_developer/remote/`
   - `tests/test_search.py`
