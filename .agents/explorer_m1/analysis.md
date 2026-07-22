# Design Specification & Refactoring Plan: Requirement R1 (Improve Job Search Relevance)

## Executive Summary

Requirement R1 mandates updating search retrieval logic so queries for broad technology roles (e.g., `"java dev"`, `"software engineer"`, `"fullstack dev"`) return relevant job postings from the database (at least 3 relevant job postings per query).

Currently, searches for broad technology role queries fail or return `< 3` results due to three root causes:
1. **Rigid Literal ILIKE Search in `JobRepository.search()`**: Queries like `"java dev"` are matched as a single exact phrase `Job.title ILIKE '%java dev%'`, which fails to match common titles like `"Senior Java Developer"`, `"Java Backend Engineer"`, or `"Fullstack Java Engineer"`.
2. **Over-Restrictive Skill Filtering in `RAGAgent._apply_filters()`**: Intent parsing extracts role tokens (e.g. `"dev"`, `"engineer"`) into `context.skills`. The filter then checks if `"dev"` is in `r.skills` (e.g., `["Java", "Spring Boot"]`), discarding all valid Java postings.
3. **Lack of Hybrid Vector + Relational DB Fallback**: `RAGAgent.search_jobs()` relies exclusively on ChromaDB vector search and does not fall back to database search when vector search returns `< 3` results.
4. **Missing Raw Java Seed Job Postings**: The `data/raw/` seed dataset only contains raw postings for `"software_engineer"`, `"data_scientist"`, and `"data_engineer"`. Postings for Java roles are absent from default seed files.

This specification details the exact file-by-file changes required to resolve all four issues.

---

## 1. Storage Layer Refactoring: `storage/repository.py` (`JobRepository.search()`)

### 1.1 Current Implementation Deficiencies
In `storage/repository.py` (lines 525-578):
```python
if title:
    filters.append(Job.title.ilike(f"%{title}%"))
...
if skills:
    filters.append(Job.skills.contains(cast(skills, ARRAY(String))))
```
- `title` uses a single `ILIKE '%java dev%'` substring match against `Job.title` only.
- `skills` uses PostgreSQL ARRAY `@>` containment, requiring every listed token to match as an exact skill string element.
- Search does not scan `description_clean`, `skills`, or `tags` when a text query is provided.

### 1.2 Proposed Refactoring Architecture

#### A. Tokenization & Abbreviation/Synonym Expansion
Add a term expansion helper in `storage/repository.py` or a dedicated search utility module:

```python
SYNONYM_MAP: dict[str, list[str]] = {
    "dev": ["dev", "developer", "development", "engineer"],
    "developer": ["developer", "dev", "engineer"],
    "engineer": ["engineer", "developer", "dev"],
    "swe": ["swe", "software engineer", "software developer", "software dev"],
    "fe": ["fe", "frontend", "front-end", "front end"],
    "be": ["be", "backend", "back-end", "back end"],
    "fs": ["fs", "fullstack", "full-stack", "full stack"],
    "qa": ["qa", "quality assurance", "test engineer", "sdet"],
    "java": ["java", "j2ee", "spring", "spring boot"],
    "python": ["python", "django", "fastapi", "flask"],
    "js": ["javascript", "js", "typescript", "ts", "node"],
}

def tokenize_and_expand_query(query_str: str) -> list[list[str]]:
    """
    Tokenizes a query string into term groups with expanded synonyms.
    Example: 'java dev' -> [
        ['java', 'j2ee', 'spring', 'spring boot'],
        ['dev', 'developer', 'development', 'engineer']
    ]
    """
    stop_words = {"in", "at", "for", "a", "an", "the", "with", "and", "or", "job", "jobs", "role", "roles", "position", "positions"}
    raw_tokens = [
        re.sub(r"[^\w\-\+]", "", t.lower())
        for t in query_str.split()
    ]
    tokens = [t for t in raw_tokens if t and t not in stop_words]

    expanded_groups = []
    for token in tokens:
        synonyms = SYNONYM_MAP.get(token, [token])
        if token not in synonyms:
            synonyms = [token] + synonyms
        expanded_groups.append(synonyms)

    return expanded_groups
```

#### B. Multi-Field Matching Engine using SQLAlchemy Async
For each term group $G_i = [t_{i1}, t_{i2}, \dots]$, generate a field-level OR expression:

$$ \text{MatchGroup}(G_i) = \bigvee_{t \in G_i} \left( \text{title ILIKE } \%t\% \lor \text{description\_clean ILIKE } \%t\% \lor \text{array\_to\_string(skills, ' ') ILIKE } \%t\% \lor \text{array\_to\_string(tags, ' ') ILIKE } \%t\% \right) $$

In SQLAlchemy async:
```python
def _build_term_group_clause(terms: list[str]):
    group_or_clauses = []
    for term in terms:
        pattern = f"%{term}%"
        group_or_clauses.extend([
            Job.title.ilike(pattern),
            Job.description_clean.ilike(pattern),
            func.array_to_string(Job.skills, " ").ilike(pattern),
            func.array_to_string(Job.tags, " ").ilike(pattern),
        ])
    return or_(*group_or_clauses)
```

#### C. `JobRepository.search()` Refactored Implementation

```python
async def search(
    self,
    *,
    title: str | None = None,
    query: str | None = None,
    status: JobStatus | None = JobStatus.ACTIVE,
    employment_type: EmploymentType | None = None,
    seniority: SeniorityLevel | None = None,
    country: str | None = None,
    city: str | None = None,
    is_remote: bool | None = None,
    salary_min_gte: float | None = None,
    salary_max_lte: float | None = None,
    skills: list[str] | None = None,
    tags: list[str] | None = None,
    company_id: uuid.UUID | None = None,
    ingestion_run_id: uuid.UUID | None = None,
    posted_after: datetime | None = None,
    posted_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    order_by: str = "posted_at",
    desc_order: bool = True,
    min_results: int = 3,
) -> tuple[Sequence[Job], int]:
    """
    Full-featured job search with multi-field token matching and smart fallback.
    """
    search_text = query or title
    base_filters = []

    if status:
        base_filters.append(Job.status == status)
    if employment_type:
        base_filters.append(Job.employment_type == employment_type)
    if seniority:
        base_filters.append(Job.seniority == seniority)
    if country:
        base_filters.append(Job.country.ilike(f"%{country}%"))
    if city:
        base_filters.append(Job.city.ilike(f"%{city}%"))
    if is_remote is not None:
        base_filters.append(Job.is_remote == is_remote)
    if salary_min_gte is not None:
        base_filters.append(Job.salary_min >= salary_min_gte)
    if salary_max_lte is not None:
        base_filters.append(Job.salary_max <= salary_max_lte)
    if company_id:
        base_filters.append(Job.company_id == company_id)
    if ingestion_run_id:
        base_filters.append(Job.ingestion_run_id == ingestion_run_id)
    if posted_after:
        base_filters.append(Job.posted_at >= posted_after)
    if posted_before:
        base_filters.append(Job.posted_at <= posted_before)

    # Explicit skill filtering if provided
    if skills:
        # Match if any provided skill is present in Job.skills
        skill_or_clauses = [
            func.array_to_string(Job.skills, " ").ilike(f"%{s}%")
            for s in skills
        ]
        base_filters.append(or_(*skill_or_clauses))

    if tags:
        base_filters.append(Job.tags.contains(cast(tags, ARRAY(String))))

    # Multi-field text query matching
    if search_text:
        term_groups = tokenize_and_expand_query(search_text)
        if term_groups:
            # Step 1: Strict AND across all expanded term groups
            and_term_clauses = [
                _build_term_group_clause(group) for group in term_groups
            ]
            strict_where = and_(*base_filters, *and_term_clauses)

            count_stmt = select(func.count()).select_from(Job).where(strict_where)
            total: int = (await self.session.execute(count_stmt)).scalar_one()

            if total >= min_results:
                col = getattr(Job, order_by, Job.posted_at)
                order = desc(col) if desc_order else col
                stmt = select(Job).where(strict_where).order_by(order).limit(limit).offset(offset)
                rows = (await self.session.execute(stmt)).scalars().all()
                return rows, total

            # Step 2: Fallback to OR across term groups if AND returned < min_results
            or_term_clauses = [
                _build_term_group_clause(group) for group in term_groups
            ]
            relaxed_where = and_(*base_filters, or_(*or_term_clauses))

            count_stmt_rel = select(func.count()).select_from(Job).where(relaxed_where)
            total_rel: int = (await self.session.execute(count_stmt_rel)).scalar_one()

            col = getattr(Job, order_by, Job.posted_at)
            order = desc(col) if desc_order else col
            stmt_rel = select(Job).where(relaxed_where).order_by(order).limit(limit).offset(offset)
            rows_rel = (await self.session.execute(stmt_rel)).scalars().all()
            return rows_rel, total_rel

    # Default fallback when no search_text is supplied
    where = and_(*base_filters) if base_filters else True
    count_stmt = select(func.count()).select_from(Job).where(where)
    total = (await self.session.execute(count_stmt)).scalar_one()

    col = getattr(Job, order_by, Job.posted_at)
    order = desc(col) if desc_order else col
    stmt = select(Job).where(where).order_by(order).limit(limit).offset(offset)
    rows = (await self.session.execute(stmt)).scalars().all()
    return rows, total
```

---

## 2. Agent Layer Refactoring: `agents/rag_agent.py` (`RAGAgent`)

### 2.1 Current Implementation Deficiencies
In `agents/rag_agent.py`:
1. `_apply_filters()` (lines 162-179) performs hard skill matching:
   ```python
   if context.skills:
       filtered = [
           r for r in filtered
           if any(s.lower() in [rs.lower() for rs in r.skills] for s in context.skills)
       ]
   ```
   When LLM intent extraction returns `skills=["java", "dev"]` or `skills=["developer"]`, `"dev"` or `"developer"` fails to match entries in `r.skills` (e.g. `["Java", "Spring Boot"]`), causing the filter to discard all results.

2. `search_jobs()` only searches ChromaDB. If ChromaDB returns fewer than `limit` results (or 0 results due to missing embeddings), no secondary relational database lookup is performed.

### 2.2 Refactored Filter & Retrieval Design

#### A. Role Keyword Sanitization in `_apply_filters()`
Define `ROLE_KEYWORDS` to strip out generic job role nouns and abbreviations from mandatory skill checks:

```python
ROLE_KEYWORDS: set[str] = {
    "dev", "developer", "development", "engineer", "engineering",
    "software engineer", "software dev", "software developer",
    "programmer", "specialist", "fullstack", "full-stack", "full stack",
    "backend", "back-end", "back end", "frontend", "front-end", "front end",
    "lead", "senior", "junior", "mid", "principal", "staff", "architect",
}

@staticmethod
def _apply_filters(
    results: list[RetrievalResult], context: QueryContext
) -> list[RetrievalResult]:
    filtered = results

    # Filter out generic role words from context.skills
    real_skills = [
        s for s in context.skills
        if s.lower().strip() not in ROLE_KEYWORDS
    ]

    # Filter by real skills if any remain
    if real_skills:
        filtered = [
            r for r in filtered
            if any(
                s.lower() in [rs.lower() for rs in r.skills]
                or s.lower() in r.title.lower()
                for s in real_skills
            )
        ]

    # Sort by score descending
    filtered.sort(key=lambda r: r.score, reverse=True)
    return filtered
```

#### B. Hybrid Retrieval with Database Fallback in `search_jobs()`

```python
async def search_jobs(self, context: QueryContext) -> AgentResponse:
    try:
        where = {}
        if context.is_remote is not None:
            where["is_remote"] = context.is_remote
        if context.seniority:
            where["seniority"] = context.seniority
        if context.company:
            where["company"] = context.company

        # Step 1: Search ChromaDB vector store
        chroma_results = self._chroma.search(
            query=context.raw_query,
            n_results=context.limit * 2,
            where=where if where else None,
        )

        results = await self._build_results(chroma_results, context)
        results = self._apply_filters(results, context)

        # Step 2: Fallback to Postgres search if vector results < min threshold (3)
        if len(results) < 3:
            logger.info(
                "ChromaDB returned %d results for query %r; falling back to relational DB search",
                len(results), context.raw_query,
            )
            db_results = await self._search_db_fallback(context)
            # Merge DB results, avoiding duplicates
            existing_ids = {r.job_id for r in results}
            for db_res in db_results:
                if db_res.job_id not in existing_ids:
                    results.append(db_res)
                    existing_ids.add(db_res.job_id)

        # Step 3: Truncate to limit
        results = results[:context.limit]

        # Step 4: Generate summary
        summary = None
        if results:
            summary = await self._generate_summary(results, context)

        return AgentResponse(
            success=True,
            intent=IntentType.SEARCH_JOBS,
            results=results,
            summary=summary,
            metadata={"total_found": len(results)},
        )
    except Exception as exc:
        logger.error("RAG search failed: %s", exc, exc_info=True)
        return AgentResponse(
            success=False,
            intent=IntentType.SEARCH_JOBS,
            error=str(exc),
        )

async def _search_db_fallback(self, context: QueryContext) -> list[RetrievalResult]:
    """Fallback search against PostgreSQL via JobRepository.search()."""
    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)
        jobs, _ = await uow.jobs.search(
            query=context.raw_query,
            is_remote=context.is_remote,
            limit=context.limit,
        )
        return [
            RetrievalResult(
                job_id=str(job.external_id or job.id),
                title=job.title,
                company=job.company.name if job.company else "Unknown",
                location=job.location_raw or f"{job.city or ''}, {job.country or ''}".strip(", "),
                is_remote=job.is_remote,
                seniority=job.seniority.value if job.seniority else None,
                skills=job.skills or [],
                source_url=job.source_url,
                score=0.80,
                match_reason="PostgreSQL relational query match",
            )
            for job in jobs
        ]
```

---

## 3. Raw Seed Data & Database Seeding Plan

### 3.1 Raw Seed Data File Structure (`data/raw/`)
To guarantee at least 3 relevant job postings per broad query (`"java dev"`, `"software engineer"`, `"fullstack dev"`), create new JSON seed files under `data/raw/`:

#### Directory 1: `data/raw/java_developer/remote/`
- `seed_java_dev_01.json`:
  - `title`: `"Senior Java Developer"`
  - `company`: `"FinTech Global Solutions"`
  - `url`: `"https://jobs.fintechglobal.internal/java-dev-01"`
  - `content`: `"FinTech Global is seeking a Senior Java Developer to build high-throughput microservices using Java 17, Spring Boot, PostgreSQL, and Kafka. Remote position with competitive salary."`
- `seed_java_dev_02.json`:
  - `title`: `"Java Backend Engineer"`
  - `company`: `"CloudScale Systems"`
  - `url`: `"https://jobs.cloudscale.internal/java-backend-02"`
  - `content`: `"CloudScale Systems is looking for a Java Backend Engineer to design scalable cloud services with Java, Spring Cloud, AWS, and REST APIs."`
- `seed_java_dev_03.json`:
  - `title`: `"Fullstack Java Engineer"`
  - `company`: `"Enterprise Software Corp"`
  - `url`: `"https://jobs.enterprisesoftware.internal/fullstack-java-03"`
  - `content`: `"Seeking a Fullstack Java Engineer proficient in Java Spring Boot backend and React/TypeScript frontend development."`
- `seed_java_dev_04.json`:
  - `title`: `"Lead Java Software Developer"`
  - `company`: `"PayTech Systems"`
  - `url`: `"https://jobs.paytech.internal/lead-java-04"`
  - `content`: `"PayTech Systems is hiring a Lead Java Software Developer to architect distributed transaction platforms using Java, Hibernate, and Kubernetes."`

#### Directory 2: `data/raw/software_engineer/remote/`
- `seed_swe_01.json`:
  - `title`: `"Senior Software Engineer"`
  - `company`: `"DataStream Tech"`
  - `url`: `"https://jobs.datastream.internal/swe-01"`
  - `content`: `"Hiring a Senior Software Engineer to lead distributed systems design using Python, Go, and Kubernetes."`
- `seed_swe_02.json`:
  - `title`: `"Software Developer - Platform"`
  - `company`: `"Nexus Infrastructure"`
  - `url`: `"https://jobs.nexus.internal/swe-02"`
  - `content`: `"Nexus Infrastructure is hiring a Software Developer to construct core platform APIs and microservices."`
- `seed_swe_03.json`:
  - `title`: `"Lead Software Engineer"`
  - `company`: `"Innovate Health"`
  - `url`: `"https://jobs.innovatehealth.internal/swe-03"`
  - `content`: `"Lead Software Engineer needed to drive cloud architecture and full-stack software development."`

#### Directory 3: `data/raw/fullstack_developer/remote/`
- `seed_fullstack_01.json`:
  - `title`: `"Senior Fullstack Developer"`
  - `company`: `"WebScale Apps"`
  - `url`: `"https://jobs.webscale.internal/fs-01"`
  - `content`: `"WebScale Apps needs a Senior Fullstack Developer skilled in React, Node.js, TypeScript, and PostgreSQL."`
- `seed_fullstack_02.json`:
  - `title`: `"Fullstack Engineer (Java & React)"`
  - `company`: `"OmniCloud Corp"`
  - `url`: `"https://jobs.omnicloud.internal/fs-02"`
  - `content`: `"Fullstack Engineer role building enterprise SaaS products with Java Spring Boot backend and React frontend."`
- `seed_fullstack_03.json`:
  - `title`: `"Full Stack Dev"`
  - `company`: `"NextGen Digital"`
  - `url`: `"https://jobs.nextgen.internal/fs-03"`
  - `content`: `"NextGen Digital seeks a Full Stack Dev experienced in Python, Django, React, and AWS."`

### 3.2 DB Seeding Utility: `scripts/seed_jobs.py`
Create a helper script `scripts/seed_jobs.py` (or test fixture helper) that ingests all JSON files in `data/raw/` into Postgres (`jobs` table) and populates ChromaDB embeddings:

```python
"""
scripts/seed_jobs.py
~~~~~~~~~~~~~~~~~~~~
Seeds PostgreSQL and ChromaDB with raw job postings from data/raw/.
"""
import asyncio
from pathlib import Path
from ingestion.tasks import _run_pipeline

async def seed_database():
    raw_dir = Path("data/raw")
    # Identify all subdirectories in data/raw
    roles_and_locations = []
    for role_dir in raw_dir.iterdir():
        if role_dir.is_dir() and not role_dir.name.startswith("."):
            for loc_dir in role_dir.iterdir():
                if loc_dir.is_dir():
                    roles_and_locations.append((role_dir.name.replace("_", " "), loc_dir.name.replace("_", " ")))

    print(f"Seeding DB from raw directories: {roles_and_locations}")
    # Process files via ingestion pipeline logic
    # ...
```

---

## 4. Test Suite Specification: `tests/test_search.py`

Create a new comprehensive test file `tests/test_search.py` containing unit and integration tests.

### 4.1 Unit Tests

#### `TestTokenizationAndExpansion`
- `test_tokenize_query_basic()`: `"java dev"` -> `[["java", "j2ee", "spring", "spring boot"], ["dev", "developer", "development", "engineer"]]`
- `test_tokenize_query_strips_stop_words()`: `"software engineer in new york"` -> `[["software", ...], ["engineer", ...]]`
- `test_synonym_expansion_abbreviations()`: `"swe"` expanded to include `"software engineer"`.

#### `TestJobRepositorySearchBroadQueries` (Async DB Session)
- `test_search_java_dev_returns_at_least_3_jobs(db_session)`:
  - Seed 4 Java jobs (`"Senior Java Developer"`, `"Java Backend Engineer"`, `"Fullstack Java Engineer"`, `"Lead Java Architect"`).
  - Run `await job_repo.search(query="java dev")`.
  - Assert `len(jobs) >= 3`.
  - Assert all returned job titles contain Java/Java-related keywords.
- `test_search_software_engineer_returns_at_least_3_jobs(db_session)`:
  - Seed 4 software engineering jobs.
  - Run `await job_repo.search(query="software engineer")`.
  - Assert `len(jobs) >= 3`.
- `test_search_fullstack_dev_returns_at_least_3_jobs(db_session)`:
  - Seed 4 fullstack jobs.
  - Run `await job_repo.search(query="fullstack dev")`.
  - Assert `len(jobs) >= 3`.

#### `TestRAGAgentRoleKeywordFiltering`
- `test_apply_filters_strips_role_keywords()`:
  - Pass `context.skills = ["java", "dev"]`.
  - Verify `_apply_filters()` does not discard jobs with `skills=["Java", "Spring Boot"]`.

### 4.2 Integration Tests (API Level)

#### `TestStructuredSearchEndpointBroadQueries`
- `test_structured_search_java_dev(api_client)`:
  - POST `/api/v1/search/structured` with `{"query": "java dev", "limit": 10}`.
  - Assert `status_code == 200`.
  - Assert `len(response.json()["jobs"]) >= 3`.
- `test_structured_search_software_engineer(api_client)`:
  - POST `/api/v1/search/structured` with `{"query": "software engineer", "limit": 10}`.
  - Assert `status_code == 200`.
  - Assert `len(response.json()["jobs"]) >= 3`.
- `test_structured_search_fullstack_dev(api_client)`:
  - POST `/api/v1/search/structured` with `{"query": "fullstack dev", "limit": 10}`.
  - Assert `status_code == 200`.
  - Assert `len(response.json()["jobs"]) >= 3`.

#### `TestSemanticSearchEndpointBroadQueries`
- `test_semantic_search_java_dev(api_client)`:
  - POST `/api/v1/search/semantic` with `{"query": "java dev", "limit": 10}`.
  - Assert `status_code == 200`.
  - Assert `len(response.json()["results"]) >= 3`.

---

## 5. Verification & Quality Gates

1. **Unit Test Gate**: Run `pytest tests/test_search.py tests/test_rag.py -v`. All tests must pass cleanly.
2. **Relevance Gate**: Verify queries `"java dev"`, `"software engineer"`, and `"fullstack dev"` return $\ge 3$ relevant postings.
3. **API Contract Gate**: Ensure no existing API schemas or response contracts are broken.
