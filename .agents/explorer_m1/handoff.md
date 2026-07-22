# Handoff Report: Requirement R1 (Improve Job Search Relevance)

## 1. Observation
1. **`storage/repository.py` (`JobRepository.search()`, lines 525-578)**:
   - Search currently executes literal string match `Job.title.ilike(f"%{title}%")` against `Job.title` only.
   - Searching `"java dev"` attempts `ILIKE '%java dev%'`, which fails to match `"Senior Java Developer"`, `"Java Backend Engineer"`, or `"Fullstack Java Engineer"`.
   - Skill filtering uses PostgreSQL array containment `contains` (`@>`), requiring exact match for every skill string.
2. **`agents/rag_agent.py` (`RAGAgent._apply_filters()`, lines 168-174)**:
   - `_apply_filters()` strictly requires every skill in `context.skills` to match a skill in `r.skills`.
   - When intent classification extracts `skills=["java", "dev"]` or `skills=["dev"]`, `"dev"` fails to match entries in job skills lists (e.g., `["Java", "Spring Boot"]`), causing the filter to discard all valid results.
   - `RAGAgent.search_jobs()` relies solely on ChromaDB vector search and lacks fallback to PostgreSQL relational search when vector results are `< 3`.
3. **`data/raw/` Seed Dataset**:
   - Seed directories under `data/raw/` contain raw postings for `"software_engineer"`, `"data_scientist"`, and `"data_engineer"`.
   - Postings for `"java_developer"` / `"java_backend_engineer"` or `"fullstack_developer"` are absent from default seed files.
4. **`tests/` Test Suite**:
   - Tests in `tests/test_rag.py` and `tests/test_api.py` test mocked RAG agent and API validation, but there is no `tests/test_search.py` testing broad query matching (`"java dev"`, `"software engineer"`, `"fullstack dev"`) returning $\ge 3$ jobs.

## 2. Logic Chain
1. Searching for broad role terms like `"java dev"` fails when query terms are matched as an unexpanded literal phrase against title alone, or when generic role words (`"dev"`, `"engineer"`) are forced into exact skill array checks.
2. Tokenizing queries into term groups (e.g. `"java dev"` $\rightarrow$ `["java"]` and `["dev"]`) and expanding synonyms/abbreviations (`"dev"` $\rightarrow$ `["dev", "developer", "development", "engineer"]`) allows matching across `title`, `description_clean`, `skills`, and `tags`.
3. Sanitizing `context.skills` by removing generic role nouns (`"dev"`, `"engineer"`, `"developer"`, `"software"`) prevents `RAGAgent._apply_filters()` from discarding valid job results.
4. Adding a database search fallback in `RAGAgent.search_jobs()` ensures that when ChromaDB vector search yields `< 3` results, PostgreSQL multi-field search retrieves relevant postings.
5. Populating `data/raw/java_developer/`, `data/raw/software_engineer/`, and `data/raw/fullstack_developer/` with realistic seed job postings guarantees that at least 3 matching jobs exist in the database for each broad query.
6. Implementing `tests/test_search.py` validates that both structured and semantic search return $\ge 3$ relevant postings for broad queries.

## 3. Caveats
- SQLite in unit tests does not natively support PostgreSQL GIN indexes or ARRAY types, so `func.array_to_string(Job.skills, ' ')` is used for cross-database compatibility in unit/integration tests.
- Groq LLM intent extraction in `orchestrator.py` may vary if LLM outputs non-standard skill lists; sanitizing role keywords in `_apply_filters()` handles this variation safely.

## 4. Conclusion
To satisfy Requirement R1, the following changes must be implemented:
1. **`storage/repository.py`**: Refactor `JobRepository.search()` to use query tokenization, synonym/abbreviation expansion, and multi-field matching across `Job.title`, `Job.description_clean`, `Job.skills`, and `Job.tags` with AND-to-OR fallback.
2. **`agents/rag_agent.py`**: Update `_apply_filters()` to strip role keywords from `context.skills` and add relational database search fallback in `search_jobs()`.
3. **`data/raw/`**: Add raw JSON seed files for `java_developer`, `software_engineer`, and `fullstack_developer` (at least 4 jobs per role).
4. **`tests/test_search.py`**: Create unit and integration test suite asserting $\ge 3$ relevant jobs returned for `"java dev"`, `"software engineer"`, and `"fullstack dev"`.

Detailed code specification and implementation blueprints are documented in `d:\projects\talentRadar\.agents\explorer_m1\analysis.md`.

## 5. Verification Method
1. Run pytest suite:
   ```bash
   pytest tests/test_search.py tests/test_rag.py -v
   ```
2. Verify API endpoints return $\ge 3$ relevant jobs for broad queries:
   ```bash
   pytest tests/test_search.py -k "test_structured_search or test_semantic_search" -v
   ```
3. Invalidation Conditions:
   - If searching `"java dev"` returns $< 3$ jobs or returns non-Java roles.
   - If `_apply_filters()` drops valid Java jobs due to `"dev"` skill keyword check.
