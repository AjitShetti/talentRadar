# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TalentRadar is an AI-powered job intelligence platform: it ingests job postings from multiple sources, extracts structured signals via LLMs, and answers natural-language queries using RAG. Backend is FastAPI (Python 3.11+) with a LangGraph-based agent layer; frontend is Next.js 14 (App Router). Data lives in PostgreSQL: relational tables plus job embeddings in a `job_embeddings` table via pgvector (`VECTOR_BACKEND=pgvector`, the default). A ChromaDB server is still selectable (`VECTOR_BACKEND=chroma`) but is no longer part of the deployment.

## Commands

Local dev stack (Docker, recommended):

```bash
make up                          # start postgres (pgvector), redis, api, frontend
make down
make logs
make restart service=api         # restart a single compose service
```

Database:

```bash
make migrate                     # alembic upgrade head (runs inside talentradar-api container)
make makemigrations msg="..."    # alembic revision --autogenerate
make downgrade                   # alembic downgrade -1
make seed                        # python -m ingestion.seed_db
```

Code quality:

```bash
make lint          # ruff check .
make lint-fix       # ruff check . --fix
make format          # ruff format .
make typecheck       # mypy .  (strict mode, see pyproject.toml)
make check            # lint + typecheck
```

Testing:

```bash
make test                                   # pytest -m "not slow"
make test-all                               # full suite incl. slow/integration
make test-cov                               # with coverage (fail_under = 70)
make test-one file=tests/test_api.py        # single file
pytest tests/test_api.py::test_name -v      # single test
python tests/test_pipeline_e2e.py --quick   # standalone e2e ingestion test
```

Manual ingestion trigger (no Celery worker runs by default — see Architecture notes):

```bash
python scripts/run_ingestion.py
```

Live-source verification:

```bash
make verify-sources        # probe every source against the real internet
make capture-fixtures      # re-record the fixtures the contract tests replay
pytest tests/test_source_contracts.py    # offline, replays those fixtures
```

Frontend (from `frontend/`):

```bash
npm run dev      # next dev
npm run build
npm run lint
```

Pre-commit hooks (ruff, ruff-format, mypy, pytest-fast) are configured in `.pre-commit-config.yaml`; install with `pre-commit install`.

## Architecture

### Layers

- **`api/`** — FastAPI app. `api/main.py` wires all routers under `/api/v1` (plus `auth` unprefixed), CORS, and a slowapi rate limiter (100/min default). Routers are thin; they delegate to `services/` or `agents/`. `api/schemas/` holds Pydantic request/response models per domain. `api/auth.py` implements JWT bearer auth (bcrypt password hashing, HS256 tokens); `api/dependencies.py` provides shared FastAPI `Depends` (DB session, unit-of-work, current-user).
- **`agents/`** — LangGraph-based orchestration. `agents/graph.py` defines the compiled state graph (`agent_graph`): `node_classify → route_by_intent → {node_rag_retrieve | node_studio_agent | node_error} → END`. `agents/state.py` defines `IntentType`, `QueryContext`, `AgentResponse`. `agents/orchestrator.py` does LLM-based intent classification via Groq and drives the graph. `agents/rag_agent.py` handles semantic job search/retrieval against the vector store (`ingestion/embeddings/vector_store.py` picks the backend; pgvector by default), falling back to a relational query when no vector store is reachable. `agents/studio_agents.py` holds the thin per-intent agents (`CompanyAgent`, `CareerCoachAgent`, `ApplicationAgent`, `PersonalAgent`, `ResumeStudioAgent`) used for non-search intents (company info, career coaching, application tracking, personal-agent next-actions, resume studio). `services/copilot.py` is the Career Copilot behind `/agent`: it builds the deterministic briefing (next-best action, stale applications, interview momentum, skill gaps) and is the only caller that passes `user_id` into `agent_graph`, so the studio agents can answer about the signed-in user. `agents/interview/` is a separate sub-graph for the mock-interview feature (its own `graph.py`, `nodes.py`, `state.py`, LLM provider abstraction, and fallback questions for offline/LLM-failure cases).
- **`services/`** — business-logic layer between API routers and storage; one module per domain (`jobs`, `applications`, `career`, `companies`, `interviews`, `profiles`, `resumes`, `agent_memory`, `copilot`, `search_cache_service`, `llm`). Routers call these rather than touching `storage/` directly. Four modules serve live sourcing specifically: `cache_backend.py` (the one Redis accessor, with a bounded in-memory fallback), `source_health.py` (per-source health counters and the circuit breaker), `job_persistence.py` (structural, LLM-free writes of scraped postings), `job_enrichment.py` and `job_retention.py` (the deferred LLM parse under a daily budget, and the prune that keeps a 0.5 GB database writable).
- **`storage/`** — SQLAlchemy async layer. `storage/database.py` builds the async engine/session factory purely from `config.settings` (no hardcoded credentials). `storage/models.py` has ORM models; `storage/repository.py` implements a `UnitOfWork` pattern used by routers/services; `storage/migrations/` is Alembic (versions numbered `001`–`010` plus a dated migration `20260724_add_job_applications.py`; `010` creates the pgvector `job_embeddings` table and skips itself on a Postgres without the extension).
- **`ingestion/`** — the data pipeline. `ingestion/sources/` implements one adapter per job source (Greenhouse, Lever, Ashby, Cutshort, Tavily, plus a common `base.py` interface); `ingestion/dispatcher.py` fans out across enabled sources, dedupes by URL, and hands results to `ingestion/pipeline.py` (`persist_parsed`) which parses via `ingestion/parsers/jd_parser.py` (LLM-based JD parsing) and upserts into Postgres + embeds via `ingestion/embeddings/` (`vector_store.get_vector_store()` → `PgVectorJobStore`, writing the `job_embeddings` table in the same database). `ingestion/scrapers/` and `ingestion/scrapling_manager.py` provide stealth/anti-bot scraping (Scrapling TLS/HTTP2 impersonation for Cloudflare-protected boards like Indeed/LinkedIn/Instahyre, Camoufox for JS-hydrated sites like Naukri) with multi-tier fallback to plain httpx. Both stealth tiers are optional and off by default: the whole stack (scrapling, playwright, patchright, camoufox) lives in the `[stealth]` extra, and the headless-browser tier additionally needs `ENABLE_STEALTH_SCRAPERS=true` — so a default deployment scrapes with httpx and browser-like headers, neither launching a browser nor impersonating those sites. `ingestion/engine.py` (`RealtimeScraperEngine`) is what the `/api/v1/ingest/trigger` endpoint calls for on-demand, synchronous scraping (there is currently no running Celery worker/beat service in `docker-compose.yml` despite Redis being present — ingestion is triggered manually via API or `scripts/run_ingestion.py`, not on a schedule).
- **`ml/`** — candidate/job matching scorers (skill matcher, experience matcher, XGBoost-based scorer in `ml/scorers.py`), feature extraction and preprocessing used by the resume/match features.
- **`domain/`** — shared enums/entities used across layers (not ORM models — plain domain types). `domain/geo.py` is the single source of truth for what counts as an Indian location (city aliases, state/country detection, `is_indian_job()`), and `domain/experience.py` maps the search UI's experience bands onto `SeniorityLevel` values.
- **`core/`** — cross-cutting utilities: `core/errors.py` (app-wide exception types), `core/pagination.py`.
- **`config/settings.py`** — single `pydantic-settings` `Settings` class, loaded from `.env`, cached via `get_settings()`. Composes `database_url` (asyncpg, for the app) and `database_url_sync` (psycopg2, for Alembic) from either a single `DATABASE_URL` (what every managed provider hands you; it wins when set, and libpq-only params like `sslmode` are stripped for asyncpg) or the discrete `POSTGRES_*` fields — don't hardcode connection strings elsewhere.
- **`frontend/`** — Next.js 14 App Router. Pages under `frontend/app/<feature>/page.tsx` (dashboard, search, applications, interview, resume-studio, company-intel, agent, onboarding, settings, auth). `frontend/lib/api.ts` is the single API client; `frontend/components/AppShell.tsx` and `RequireAuth.tsx` are the main shared shell/auth-gate components.
- **`stitch/`** — static HTML/JSON design mockups (one folder per screen) used as visual references for the frontend; not part of the running app.

### Request flow (semantic query)

`Frontend → POST /api/v1/query → api/routers/query.py → Orchestrator.process_query → agent_graph (node_classify via Groq → route_by_intent) → node_rag_retrieve (RAGAgent → pgvector cosine search + Postgres hydration) or node_studio_agent (intent-specific thin agent) → AgentResponse → JSON`

The retrieval branch does not end at `node_rag_retrieve`. It continues:

`node_rag_retrieve → route_after_retrieval → node_live_search → node_merge_rank → END`

`route_after_retrieval` is deterministic (`agents/sourcing_policy.py`) — no
second LLM call on the search path. It sends a query to the live scrapers when
the index is thin (< 8 hits), stale (> 14 days), or the query asks for
something recent — **unless** the `tr:sourced:<fingerprint>` lock says this
search already went out within 8 hours, which overrides everything but an
explicit `force_refresh`. That lock is what stops a popular query re-scraping
on every page load and getting the deployment's IP blocked.

`node_live_search` runs no LLM and persists in the background;
`node_merge_rank` is a pure function (`agents/merge_rank.py`) blending
relevance, recency, source trust and completeness.

### Key architectural conventions

- Routers stay thin: validation + delegation only. Business logic belongs in `services/`, retrieval/generation logic in `agents/`.
- The agent graph's `AgentState` is a `TypedDict` passed through every node as a partial-update dict — new node output must match this shape (see `agents/graph.py`).
- `IntentType` (in `agents/state.py`) is the single source of truth for what intents exist and how `route_by_intent` dispatches them; adding a new intent requires updating the enum, the routing function, and (if it's a "studio" style intent) `agents/studio_agents.py`.
- Ingestion sources all implement a common `JobSource` interface (`ingestion/sources/base.py`) with a `discover()` method, letting `dispatcher.py` treat ATS APIs (Greenhouse/Lever/Ashby), search-based sources (Tavily), and board scrapers (Cutshort) uniformly.
- TalentRadar is India-only: `ingestion/pipeline.py` drops postings that don't resolve to India, `ParsedJobDescription.to_job_kwargs()` fills `country`/`city` from `domain.geo`, and job search passes `india_only=True` into `JobRepository.search()`. Query-time filtering only excludes *known-foreign* rows so legacy rows without location data still surface.
- Two DB URLs exist by design: async (`asyncpg`) for the running app, sync (`psycopg2`) for Alembic — don't swap them.
- Nothing may *require* the vector store. `get_vector_store()` returns `None` whenever the backend is absent, unreachable, or set to `none`, and every caller treats that as an ordinary outcome — semantic search falls back to `JobRepository.search()`. Same rule for the embedding model and for pdflatex: an optional runtime dependency degrades a feature, never the deployment.
- Vectors are bound as `double precision[]` and cast in SQL (`CAST(CAST(:v AS double precision[]) AS vector)`). `CAST(:v AS vector)` makes PostgreSQL infer the parameter itself as `vector`, which asyncpg cannot encode — a runtime failure no import-time check catches. See `ingestion/embeddings/pgvector_store.py`.
- The deployment is sized for a free tier (see `DEPLOY.md` and `render.yaml`): PyTorch, TeX Live and the Camoufox browser are build-argument opt-ins, not defaults. Before adding a dependency that costs hundreds of megabytes, check whether the feature can degrade instead — `ml/scorers.py` (ONNX instead of torch) and `api/utils/pdf_renderer.py` (PyMuPDF instead of pdflatex) are the two worked examples.
- Live sourcing is **index-first**. A search answers from Postgres immediately and only then decides whether to scrape; scraped rows are written *structurally* (`enrichment_status='raw'`, no LLM call) and parsed later under `DAILY_ENRICHMENT_BUDGET`. Never add a per-posting LLM call to the live path — a single search returns ~60 postings, and that is the difference between free and rate-limited.
- `ingestion/sources/registry.py` is the single list of live scrapers. The fan-out, the health service and the contract tests all read it; adding a source means adding it there and recording a fixture, or `tests/test_source_contracts.py` fails. `requires_browser` — not the source's tier or name — is what excludes a source from a 512 MB instance.
- TLS/HTTP2 impersonation (`curl_cffi`, ~5 MB, always installed) and the headless browser (`ENABLE_STEALTH_SCRAPERS`, off, uninstalled) are **separate tiers**. Impersonation is what reaches Cloudflare-protected boards; only Naukri needs an actual browser. Don't conflate them — that conflation is what disabled Indeed and Instahyre despite neither needing a browser.
- Scrapers must parse off the event loop (`ingestion/scrapers/_parsing.py`). `asyncio.wait_for` cannot interrupt synchronous work, so a blocking parse overruns its own timeout and stalls every concurrent scraper and request with it.
- Nothing may *require* the cache, exactly as with the vector store. Every `CacheBackend` call degrades to an in-process dict.
- Groq model ids live in `config/settings.py` (`groq_model`, `groq_fast_model`, `groq_interview_model`) — don't hardcode them; a decommissioned model should be swappable from `.env`.
- mypy runs in `strict` mode (see `[tool.mypy]` in `pyproject.toml`); new code should carry full type annotations.
- Import ordering/first-party grouping is enforced by ruff's isort config (`known-first-party = ["agents", "api", "config", "ingestion", "ml", "storage", "tests"]`).
