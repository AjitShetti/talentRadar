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
- **`storage/`** — SQLAlchemy async layer. `storage/database.py` builds the async engine/session factory purely from `config.settings` (no hardcoded credentials). `storage/models.py` has ORM models; `storage/repository.py` implements a `UnitOfWork` pattern used by routers/services; `storage/migrations/` is Alembic (versions numbered `001`–`016` and `019`, plus a dated migration `20260724_add_job_applications.py`; `010` creates the pgvector `job_embeddings` table and skips itself on a Postgres without the extension, `013` adds the liveness columns and `role_sightings`, `014` anchors an interview session to a job, `015` adds the company map columns, people kinds on `company_contacts` and `company_news`, `016` adds `companies.is_tech`, `019_practice` adds Practice's four tables: `practice_days`, `practice_challenges`, `practice_attempts`, `practice_settings`).
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

`node_rag_retrieve → node_decide_sourcing → route_after_retrieval → node_live_search → node_merge_rank → END`

`node_decide_sourcing` is deterministic (`agents/sourcing_policy.py`) — no
second LLM call on the search path — and records its decision in state;
`route_after_retrieval` only reads it (LangGraph discards writes made inside an
edge function). It sends a query to the live scrapers when
the index is thin (< 8 hits), stale (> 14 days), or the query asks for
something recent — **unless** the `tr:sourced:<fingerprint>` lock says this
search already went out within 8 hours (30 minutes if that fan-out came back
empty), which overrides everything but an explicit `force_refresh`. That lock
is what stops a popular query re-scraping on every page load and getting the
deployment's IP blocked.

`POST /search/semantic` passes `intent=SEARCH_JOBS` to `process_query`, which
pins the route; the classifier then only extracts filters. Without it the chat
classifier read bare role names ("data scientist") as small talk and search
returned nothing.

`node_live_search` runs no LLM and persists in the background;
`node_merge_rank` is a pure function (`agents/merge_rank.py`) blending
relevance, recency, source trust and completeness.

### Company map (replaces the Company Intel directory)

Design: `docs/superpowers/specs/2026-10-14-company-map-design.md`. Every Bengaluru employer we can source, on a map, each with a one-line summary, an honest location precision, sourced people and funding news.

- **Rules are pure** in `domain/`: `areas.py` (canonical areas, centroids, bbox, jitter), `company_taxonomy.py` (stage/sector/size), `company_identity.py` (registrable-domain identity and source precedence `curated > site > wikidata > yc > osm > github_org > hiring_list > news > llm`; `stage`/`last_funding_at` follow the newest *dated* funding), `news.py` (funding extraction), `linkedin_links.py` (deep links built at read time).
- **I/O is thin** in `services/company_map/`: an importer per source (`wikidata`, `osm`, `yc`, `github_orgs`, `hiring_list` (the checked-in recruiter list in `data/company_sources`, name and website only), `website`, `geocode`, `news_feeds`, `people`; `yc` and `github_orgs` are directory sources that return `sourced.SourcedCompany`, an `ImportedCompany` plus the facts only that source states), `store.py` (the only writer: `upsert_company` merges by precedence and records `metadata.provenance`), `refresh.py` (`run_step`, never raises), `news_ingest.py`, `read.py` (the map/drawer reads — no LLM and no outside call).
- **Schedules** are GitHub Actions calling cron-token endpoints: `.github/workflows/news.yml` → `POST /api/v1/ingest/news` every 3 h; `.github/workflows/company-map.yml` → `POST /api/v1/ingest/company-map/refresh?step=…` daily (github_orgs, sites, geocode, summaries, github, tech), Wikidata, YC and the hiring list on Mondays, OSM on the 1st. `scripts/refresh_company_map.py` runs the same steps locally.
- **Tech employers only** (2026-10-15): `companies.is_tech` (migration 016) is the verdict of `domain.company_taxonomy.is_tech`; `listed_clause` requires `is_tech IS TRUE`, so the map, List, facets and news show nothing else. A curated catalogue tier is tech; importers set it through `store.upsert_company` (`services/company_map/tech.py`), it only moves towards `True`, and the `tech` refresh step re-reads every row that is not yet `True`.
- **Approximate ring** (2026-10-15, overriding "listed but not pinned"): a listed company at `city` precision is drawn in a 2.5–6 km annulus around the Bengaluru centre, marked "location unknown". The point is computed at read time from the company id in `read.py` and never stored; `geo_precision` stays `city`. `/map` returns `{generated_at, city, total, pinned, approximate, rows}`.
- LinkedIn is never requested by the server. A stored person always has `source_url`, `verified=False`, and comes from a source that named them (Wikidata, the company's own site, a news item, GitHub org membership, or a Tavily result that is itself a LinkedIn profile titled founder/CEO at that company). No guessed emails.
- Nominatim: identifying UA, 1 req/s, ≤ 40 per run. Company sites: robots.txt respected, 5 s, 300 KB, each at most once per 30 days. The LLM never summarises a bare name; summaries spend `DAILY_COMPANY_SUMMARY_BUDGET`.

### v2: liveness, the Role Dossier and the public surface

The v2 promise is "know the job is real, walk in prepared". Design: `docs/superpowers/specs/2026-10-05-v2-role-dossier-design.md`.

- **Liveness** is a verdict with evidence, never a score. `domain/liveness.py::assess` is a pure rule set over six nullable timestamps on `jobs` plus `role_sightings.times_reposted`; `services/liveness.py` keeps that evidence current (`record_sighting` on every write, `reverify_batch` against the ATS APIs) and reads it back (`load_verdicts`). `POST /api/v1/ingest/reverify` is run by `.github/workflows/reverify.yml` with `X-Cron-Token` — there is no worker.
- **Role Dossier** — `GET /api/v1/roles/{id}/dossier` (`services/dossier.py`) composes four independent sections (liveness, fit, way in, readiness); any of them may be `null`. No LLM call. Frontend: `frontend/app/roles/[id]/page.tsx`.
- **Job-anchored prep** — `POST /interview/sessions/start` takes `job_id`; `agents/interview/role_context.py` builds a bounded, fenced description of the posting that is stored on the session row and re-read from there every turn.
- **Referral ask** — `POST /api/v1/roles/{id}/referral-ask` (`services/referral.py`): one on-demand LLM call with a deterministic template fallback. Nothing is ever sent on the user's behalf.
- **Public surface** — `api/routers/public.py` + `services/public_roles.py`, unauthenticated and read-only. Frontend pages `/r/[slug]` and `/hiring/[slug]` are ISR (`frontend/lib/public-api.ts`); `/check` is the posting check.

### Practice (daily question, weekly take-home, leaderboard)

Design: `docs/superpowers/specs/2026-10-15-practice-design.md`. A daily LeetCode question picked for the user's role track and practice gaps (solved on leetcode.com, marked done by the user), one 24 or 48 hour take-home a week judged by an LLM that weights process over code, and a pseudonymous leaderboard.

- **Rules are pure** in `domain/`: `practice_topics.py`, `practice_pick.py`, `practice_schedule.py`, `practice_repo.py`, `practice_signals.py`, `practice_judge.py`, `practice_board.py`. **I/O is thin** in `services/practice/`: `catalogue`, `daily`, `challenges`, `github_reader`, `judge`, `board`, `steps`. `api/routers/practice.py` (`/api/v1/practice/...`) is validation and delegation only.
- **Nothing here requests leetcode.com.** Its robots.txt disallows `/graphql` and `/api/`, so no server code, CI script or fixture capture touches it. A question's link is built from its catalogue slug, and "done" is self-reported.
- **The catalogue** is `data/practice/leetcode_free.json`: positional rows `[id, slug, title, difficulty, [topic slugs]]` and nothing else. A problem statement, example, hint, acceptance rate or paid-only question is never stored (the `Question` type has no field for one). A person builds the file with `scripts/build_practice_catalogue.py` from responses they saved in their own browser; `services/practice/catalogue.py::load_catalogue` reads it once per process. The server never writes it, and while it is missing or unreadable `GET /practice/today` reports `catalogue: "empty"` and the page says the list is not loaded yet; nothing else breaks.
- **Days and weeks are IST** (`domain/practice_schedule.py`, a fixed +05:30 offset): a "done" at 00:10 IST lands on the new day, and UTC midnight changes nothing. A take-home edition opens Friday 18:00 IST and runs seven days; a user's last start is the close minus the hours, their `deadline_at` is `started_at + hours`, and the write-up stays editable for a 60 minute grace after it. The weekly contest is Sunday 02:30 UTC (08:00 IST) for 90 minutes, shown from Saturday 20:00 IST until 09:30 IST on the day. The twelve briefs are `data/practice/challenges/NN-<slug>.json`, validated by `services/practice/challenges.py::load_briefs`.
- **Reading a repo.** GitHub is read through its REST API only (`services/practice/github_reader.py`): never cloned, never run, never a link the candidate wrote. Each URL is built from a strictly parsed `RepoRef` and fixed path pieces, redirects are not followed, and every body is read with a byte limit. `RepoGone` rejects the attempt; `GitHubUnavailable` (a rate limit, a 5xx, a timeout) backs off. `GITHUB_TOKEN` is optional.
- **The snapshot rule** (`domain/practice_repo.py::snapshot`). The commit that is judged is the latest push whose time GitHub stamped (the Activity API `timestamp`) is at or before `deadline_at`; a push at exactly the deadline counts. A push after it, a force-push included, is ignored and only counted. Commit dates are written by the author's machine, so they are kept as `claimed_at` for display and decide nothing: 40 backdated commits in one push move nothing. The repo's own `created_at` must fall after Start (15 minutes of grace before it), a fork is refused, and the `.talentradar` file carrying the attempt's proof code must exist at the judged commit. Each of those is refused or rejected with a reason and no model call.
- **The judge** (`domain/practice_judge.py` holds the rules, `services/practice/judge.py` makes the call) is the only code that calls a model, and only from the scheduled `judge` step (the appeal's second reading runs inside that step). No read path calls an LLM.
  - Six criteria, an integer 0-10 each, weighted `thinking 25, tradeoffs 15, ai_usage 15, build_process 15, meets_brief 15, code_quality 15` (70 process, 30 product); `total = Σ weight × score / 10`, to one decimal.
  - Input is capped at 22,000 characters: brief 3,000, write-up 4,300, README 5,000, tree 2,500 (at most 250 paths), commits 2,500, four key files at 2,000 each, signals 600.
  - Everything from the repo or the write-up is treated as hostile. It is bounded, stripped of control and invisible characters, any run of two or more angle brackets (and their look-alikes) is collapsed, one-line fields are flattened, and each source sits in a fence carrying a nonce: the first 16 hex characters of a SHA-256 over all the candidate's text. The system prompt says only a marker with exactly that code opens or closes a fence, so a README that closes one is just evidence.
  - Text aimed at the grader (`injection_markers`) sets the `injection_text` flag and holds the attempt off the leaderboard until an admin clears it; a clear applies only to the input hash that was held. The appeal reason is stored for an admin and never reaches the model.
  - The call is `settings.groq_model` at temperature 0, JSON mode and `reasoning_effort="medium"`, with no seed. `parse_verdict` reads the reply strictly: the six scores are coerced and clamped to 0-10, notes are cut to length, unknown keys are dropped.
- **Never a fallback score.** A failed or unreadable model call stores nothing: the attempt stays `queued` and is retried after 2, 4, 8, 16 and 24 hours, and the sixth failure makes it `judge_failed`. A stored result is reused only for the same input hash, model and `JUDGE_PROMPT_VERSION`; once judging has begun, bump that version on any change to the prompt or the input layout.
- **Budgets.** `DAILY_JUDGE_BUDGET = 12` model calls a day, key `tr:budget:judge:<YYYY-MM-DD>`, counted like `services/job_enrichment.py::_consume_budget` (increment first, fail open on a cache error) and claimed only when a call is about to be made. A run takes at most `JUDGE_PER_RUN_MAX = 4` attempts (2 without `GITHUB_TOKEN`), `JUDGE_SPACING_SECONDS = 65` apart for the free tier's per-minute token limit.
- **Leaderboard** (`domain/practice_board.py`, `services/practice/board.py`): signed-in only. Everyone is `builder-<4 hex of sha256(user_id)>`; a display name and a repo link are opt-in (`PUT /practice/settings`). A challenge board shows the top 25 and the overall board the top 50, each plus the viewer's own row; overall is the sum of a person's best 4 totals across the last 12 challenges. Only judged, unheld attempts rank.
- **Schedule** is a GitHub Actions workflow, there being no worker: `.github/workflows/practice.yml` runs every two hours (`7 */2 * * *`), wakes the API, then calls `POST /api/v1/ingest/practice?step=challenges&limit=1` and `?step=judge&limit=4` with `X-Cron-Token`. `services/practice/steps.py::run_step` never raises: a part that failed comes back as `{"error": <class name>}` inside a 200, which the workflow turns into a `::warning::`. `challenges` creates this week's and next week's edition, moves `submitted` to `queued` and `started` to `missed` once the grace hour is over, and applies the retention rules, with no outside request. `judge` is the step that calls the model. Without the `CRON_TOKEN` secret the workflow does nothing.
- **Surfaces.** `services/copilot.py` adds Practice cards to the briefing (`practice_deadline` for a take-home due within 12 hours, `practice_contest`, `practice_today`) and the Overview leads with a due take-home and adds at most one other Practice suggestion. Frontend pages are `/practice`, `/practice/challenges/[slug]` and `/practice/leaderboard`; every Practice time is shown in IST through `frontend/lib/practice-time.ts`, and external links use `EXTERNAL_LINK_PROPS`.

### Key architectural conventions

- A posting's publish date comes from the source or is absent. Never write `datetime.now()` into `posted_at`/`source_posted_at` — that was the bug that made every live ATS row look posted today. `build_job_kwargs` omits dates the source did not supply so a later sighting cannot erase an earlier one.
- A failed or rate-limited re-check is `PostingCheck.ERROR`, never `CLOSED`. Only a 404/410 from the ATS (or absence from an Ashby board that loaded) closes a role.
- `jobs.external_id` is **not** the ATS's posting id on live-scraped rows — it is an MD5 of the posting URL. Anything that asks an ATS about a stored row must go through `domain.posting_urls.posting_ref`, which reads the id from the URL; asking by the stored value is a 404, and a 404 closes the role. A row with no real id is `ERROR`, never `CLOSED`.
- `role_sightings` must outlive retention; `services/job_retention.py` never touches it.
- Text from a posting, a resume or a form field that reaches a prompt is flattened, bounded and fenced (`role_context._flat`, `<<<POSTING>>>`), and anything the interviewer needs is restored from the session row each turn — `agent_state` round-trips through the browser and is untrusted.
- The public surface exposes only rows from an employer's own ATS API that positively resolve to India (`is_publishable_role` — every public read goes through it; a row with no stated location is *not* published, unlike in search, and the `country` column is not trusted because scrapers have defaulted it to India), and `public_role()` names every field it emits — never spread an ORM row into a public payload.
- `/public/check` never fetches the URL it is given. `domain/posting_urls.py` matches host and path against strict patterns and the request is built from the captured pieces.
- Public pages must not wait on the API: they are statically generated and revalidated. Do not add a client-side fetch to `/r` or `/hiring`.
- The whole system runs on free tiers. Do not add a feature that needs a paid plan; make it degrade or propose a free-provider migration.

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
