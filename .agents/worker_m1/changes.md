# Changes Implemented: Requirement R1 (Improve Job Search Relevance)

## Summary of Changes

Milestone 1 / Requirement R1 has been fully implemented and verified. All search retrieval logic now performs intelligent query tokenization, synonym and abbreviation expansion, multi-field search across relational database fields (`title`, `description_clean`, `skills`, `tags`), and automatic AND-to-OR fallback matching. In addition, `RAGAgent._apply_filters()` sanitizes generic role keywords from skill filters, and `RAGAgent.search_jobs()` falls back to PostgreSQL relational search when vector store search returns fewer than 3 results.

---

## 1. Storage Layer (`storage/repository.py`)

- **Synonym & Abbreviation Map (`SYNONYM_MAP`)**:
  Added comprehensive mappings for role nouns and abbreviations (e.g., `"dev"` $\rightarrow$ `["dev", "developer", "development", "engineer"]`, `"swe"` $\rightarrow$ `["swe", "software engineer", "software developer", "software dev"]`, `"java"` $\rightarrow$ `["java", "j2ee", "spring", "spring boot"]`, `"fs"` $\rightarrow$ `["fs", "fullstack", "full-stack", "full stack"]`, etc.).
- **Query Tokenization (`tokenize_and_expand_query`)**:
  Tokenizes input query strings, strips punctuation/formatting, filters out domain stop words (`"in"`, `"at"`, `"for"`, `"job"`, `"role"`, etc.), and expands each token into synonym groups.
- **Multi-Field Matching Clause (`_build_term_group_clause`)**:
  Constructs SQLAlchemy OR clauses matching term groups across `Job.title`, `Job.description_clean`, `Job.skills` (via `func.array_to_string`), and `Job.tags` (via `func.array_to_string`).
- **`JobRepository.search()` Refactoring**:
  - Accepts `query: str | None = None` in addition to `title: str | None = None` (`search_text = query or title`).
  - Executes strict AND matching across expanded term groups first.
  - If total matches $\ge \text{min\_results}$ (default 3), returns strict match results.
  - If total matches $< \text{min\_results}$, automatically falls back to relaxed OR matching across expanded term groups.

---

## 2. Agent Layer (`agents/rag_agent.py`)

- **Role Keyword Sanitization (`ROLE_KEYWORDS`)**:
  Defined `ROLE_KEYWORDS` containing generic role nouns (`"dev"`, `"developer"`, `"engineer"`, `"software"`, `"fullstack"`, `"backend"`, `"frontend"`, `"lead"`, `"senior"`, etc.).
- **`RAGAgent._apply_filters()` Refactoring**:
  - Sanitizes `context.skills` by removing entries present in `ROLE_KEYWORDS`.
  - Only filters by skills if genuine technical skills remain after sanitization (preventing broad queries like `"java dev"` from dropping valid Java postings due to `"dev"` not being present in job skills lists).
  - Also checks match in both `r.skills` and `r.title`.
  - Supports structured filtering for `is_remote`, `seniority`, and `company`.
- **Relational Database Search Fallback (`_search_db_fallback`)**:
  Added fallback in `RAGAgent.search_jobs()`. When ChromaDB vector search (after python-side filtering) yields $< 3$ results, `_search_db_fallback` executes relational search via `JobRepository.search()`, merging missing job postings into results up to `context.limit`. Handles offline/DB connection exceptions gracefully.
- **Embedder Compatibility**:
  Added `embed_texts` import to ensure test suite mock compatibility.
- **Chroma Result Normalization in `_build_results()`**:
  Normalized input type to support both Chroma response dicts (`{"ids": [["..."]]}`) and converted result lists (`[{"id": "..."}]`).

---

## 3. Seed Job Postings (`data/raw/`)

Created 12 realistic raw job posting JSON files across 3 role categories under `data/raw/`:
- `data/raw/java_developer/remote/`:
  - `seed_java_dev_01.json` ("Senior Java Developer" - FinTech Global Solutions)
  - `seed_java_dev_02.json` ("Java Backend Engineer" - CloudScale Systems)
  - `seed_java_dev_03.json` ("Fullstack Java Engineer" - Enterprise Software Corp)
  - `seed_java_dev_04.json` ("Lead Java Software Developer" - PayTech Systems)
- `data/raw/software_engineer/remote/`:
  - `seed_swe_01.json` ("Senior Software Engineer" - DataStream Tech)
  - `seed_swe_02.json` ("Software Developer - Platform" - Nexus Infrastructure)
  - `seed_swe_03.json` ("Lead Software Engineer" - Innovate Health)
  - `seed_swe_04.json` ("Software Engineer (Backend)" - CoreLogic Systems)
- `data/raw/fullstack_developer/remote/`:
  - `seed_fullstack_01.json` ("Senior Fullstack Developer" - WebScale Apps)
  - `seed_fullstack_02.json` ("Fullstack Engineer (Java & React)" - OmniCloud Corp)
  - `seed_fullstack_03.json` ("Full Stack Dev" - NextGen Digital)
  - `seed_fullstack_04.json` ("Principal Fullstack Engineer" - Apex Solutions)

---

## 4. Test Suite (`tests/test_search.py`)

Created `tests/test_search.py` containing 11 comprehensive unit and integration tests:
1. `TestTokenizationAndExpansion`:
   - `test_tokenize_query_basic`
   - `test_tokenize_query_strips_stop_words`
   - `test_synonym_expansion_abbreviations`
2. `TestJobRepositorySearchBroadQueries`:
   - `test_search_java_dev_returns_at_least_3_jobs` (asserts $\ge 3$ jobs returned)
   - `test_search_software_engineer_returns_at_least_3_jobs` (asserts $\ge 3$ jobs returned)
   - `test_search_fullstack_dev_returns_at_least_3_jobs` (asserts $\ge 3$ jobs returned)
   - `test_and_to_or_fallback_matching` (verifies fallback behavior)
3. `TestRAGAgentRoleKeywordFiltering`:
   - `test_apply_filters_strips_role_keywords`
   - `test_apply_filters_with_only_role_keywords_retains_all`
4. `TestStructuredSearchEndpointBroadQueries`:
   - `test_structured_search_java_dev`
   - `test_structured_search_software_engineer`
   - `test_structured_search_fullstack_dev`
5. `TestSemanticSearchEndpointBroadQueries`:
   - `test_semantic_search_java_dev_with_db_fallback`

---

## Verification Results

- Command executed: `python -m pytest tests/test_search.py tests/test_rag.py --no-cov`
- Result: **44 passed in 9.87s** (100% pass rate)
- Full suite executed: `python -m pytest --no-cov`
- Result: **94 passed in 10.96s** (100% pass rate, 0 failures)
