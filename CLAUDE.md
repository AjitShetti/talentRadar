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
- **`services/`** — business-logic layer between API routers and storage; one module per domain (`jobs`, `applications`, `career`, `companies`, `interviews`, `profiles`, `resumes`, `agent_memory`, `copilot`, `search_cache_service`, `llm`, and the `events/` package, see Events below). Routers call these rather than touching `storage/` directly. Four modules serve live sourcing specifically: `cache_backend.py` (the one Redis accessor, with a bounded in-memory fallback), `source_health.py` (per-source health counters and the circuit breaker), `job_persistence.py` (structural, LLM-free writes of scraped postings), `job_enrichment.py` and `job_retention.py` (the deferred LLM parse under a daily budget, and the prune that keeps a 0.5 GB database writable).
- **`storage/`** — SQLAlchemy async layer. `storage/database.py` builds the async engine/session factory purely from `config.settings` (no hardcoded credentials). `storage/models.py` has ORM models; `storage/repository.py` implements a `UnitOfWork` pattern used by routers/services; `storage/migrations/` is Alembic (versions numbered `001`–`016` and `018_events`, plus a dated migration `20260724_add_job_applications.py`; `010` creates the pgvector `job_embeddings` table and skips itself on a Postgres without the extension, `013` adds the liveness columns and `role_sightings`, `014` anchors an interview session to a job, `015` adds the company map columns, people kinds on `company_contacts` and `company_news`, `016` adds `companies.is_tech`, `018_events` creates the four Events tables `events`, `event_companies`, `event_saves` and `event_contacts` and alters none of the others; its `down_revision` is `016_company_is_tech` on this branch because `017` belongs to Outreach, and it is re-pointed to `017_outreach` when that merges).
- **`ingestion/`** — the data pipeline. `ingestion/sources/` implements one adapter per job source (Greenhouse, Lever, Ashby, Cutshort, Tavily, plus a common `base.py` interface); `ingestion/dispatcher.py` fans out across enabled sources, dedupes by URL, and hands results to `ingestion/pipeline.py` (`persist_parsed`) which parses via `ingestion/parsers/jd_parser.py` (LLM-based JD parsing) and upserts into Postgres + embeds via `ingestion/embeddings/` (`vector_store.get_vector_store()` → `PgVectorJobStore`, writing the `job_embeddings` table in the same database). `ingestion/scrapers/` and `ingestion/scrapling_manager.py` provide stealth/anti-bot scraping (Scrapling TLS/HTTP2 impersonation for Cloudflare-protected boards like Indeed/LinkedIn/Instahyre, Camoufox for JS-hydrated sites like Naukri) with multi-tier fallback to plain httpx. Both stealth tiers are optional and off by default: the whole stack (scrapling, playwright, patchright, camoufox) lives in the `[stealth]` extra, and the headless-browser tier additionally needs `ENABLE_STEALTH_SCRAPERS=true` — so a default deployment scrapes with httpx and browser-like headers, neither launching a browser nor impersonating those sites. `ingestion/engine.py` (`RealtimeScraperEngine`) is what the `/api/v1/ingest/trigger` endpoint calls for on-demand, synchronous scraping (there is currently no running Celery worker/beat service in `docker-compose.yml` despite Redis being present — ingestion is triggered manually via API or `scripts/run_ingestion.py`, not on a schedule).
- **`ml/`** — candidate/job matching scorers (skill matcher, experience matcher, XGBoost-based scorer in `ml/scorers.py`), feature extraction and preprocessing used by the resume/match features.
- **`domain/`** — shared enums/entities used across layers (not ORM models — plain domain types). `domain/geo.py` is the single source of truth for what counts as an Indian location (city aliases, state/country detection, `is_indian_job()`), and `domain/experience.py` maps the search UI's experience bands onto `SeniorityLevel` values.
- **`core/`** — cross-cutting utilities: `core/errors.py` (app-wide exception types), `core/pagination.py`.
- **`config/settings.py`** — single `pydantic-settings` `Settings` class, loaded from `.env`, cached via `get_settings()`. Composes `database_url` (asyncpg, for the app) and `database_url_sync` (psycopg2, for Alembic) from either a single `DATABASE_URL` (what every managed provider hands you; it wins when set, and libpq-only params like `sslmode` are stripped for asyncpg) or the discrete `POSTGRES_*` fields — don't hardcode connection strings elsewhere.
- **`frontend/`** — Next.js 14 App Router. Pages under `frontend/app/<feature>/page.tsx` (dashboard, search, applications, interview, resume-studio, company-intel, events, agent, onboarding, settings, auth). `frontend/lib/api.ts` is the single API client; `frontend/components/AppShell.tsx` and `RequireAuth.tsx` are the main shared shell/auth-gate components.
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

### Events (Bengaluru tech events)

Design: `docs/superpowers/specs/2026-10-15-events-design.md`. A tracker of Bengaluru tech meetups, talks and demo nights from Luma, GDG and Eventbrite, listed by date with a deterministic relevance verdict. A user can save an event or mark it going, download an `.ics`, add one by link or by hand, and note the people they met. No LLM, Tavily or Nominatim call anywhere in it; nothing is ever registered or sent on the user's behalf.

- **Rules are pure** in `domain/`: `event_time.py` (IST as a fixed +05:30 offset; a day is an IST day; an unstated end is start + 2 h, used for "has it ended?" and never printed as a fact), `event_facts.py` (the parser output type), `ical.py` (the RFC 5545 subset: parse and build), `event_relevance.py` (`assess` returns a `Verdict` with reasons, never a score: *where* decides whether the event is stored, *what* decides whether it is `listed`), `event_identity.py` (`same_event` across sources; `company_candidates`), `event_urls.py` (`parse_event_url`, strict patterns, exact hosts).
- **I/O is thin** in `services/events/`: `luma.py`, `gdg.py`, `eventbrite.py` (one parser and fetcher per source, tested on recorded fixtures), `http.py` (`get_bounded`: project User-Agent `TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)`, 15 s, 1 MB cap that fails rather than truncates), `sources.py` (the curated lists in `data/events/sources.json`), `store.py` (the only writer of source events: `upsert_event`), `refresh.py` (`run_step`), `read.py` (the list, drawer and `.ics` reads), `user.py` (save, going, import a link, manual events, people met) and `met.py` (the Outreach contract). `api/routers/events.py` stays thin; the migration is `018_events`.
- **What each source is asked** (the limits live in `refresh.py`, `gdg.py` and `luma.py`; the workflow passes the same ceilings):
  - *Luma*: only `https://api.lu.ma/ics/get?entity=discover|calendar&id=<id>` for an id in `data/events/sources.json` matching `(discplace|cal)-[A-Za-z0-9]{6,40}`; a feed at most once per 11 h (cache key `tr:events:luma:<id>:read`), 1 req/s, at most 26 feeds a run, no redirects.
  - *GDG*: only `https://gdg.community.dev/<chapter-slug>/` and `/events/details/<slug>/`, never a path `gdg.community.dev` disallows (`gdg.DISALLOWED`, checked by `assert_allowed` before every request), 2 s between requests, at most 30 a run, an event page at most once per 3 days unless the chapter page says its start moved, no redirects. `addressCountry` is never read (GDG prints `US` for a Bengaluru venue).
  - *Eventbrite*: only the official API, `https://www.eventbriteapi.com/v3/…` with `Authorization: Bearer <EVENTBRITE_TOKEN>`, 1 req/s, at most 60 calls a run. Without the token the step is skipped and makes no request, and no `www.eventbrite.*` page is ever requested. The curated organiser and venue lists in `sources.json` are empty until someone vets ids.
  - Each source has a breaker in `SourceHealthService` (`events:luma`, `events:gdg`, `events:eventbrite`); three failures open it. `/events` shows each source's status from that breaker: `unknown` until the source has been read (and whenever the cache cannot say), `off` for Eventbrite without a token. Health is only as shared as the cache: without Redis, `scripts/refresh_events.py` and the API do not see each other's reads.
- **A pasted link is never fetched as given.** `parse_event_url` matches it against a strict pattern (https, exact host, no userinfo or port, visible ASCII, ≤ 512 chars) and lifts out a key; `user.import_link` builds any request from that key alone (an Eventbrite API call by numeric id, a GDG event page by slug). A Luma link is never requested: it is looked up among the events a feed already stored. Anything else is `needs_details` and the user fills the manual form. Links the user types into a person or a manual event go through `_https_link` in `api/schemas/event_schemas.py`: https, no login or port, a host made only of non-empty `[a-z0-9-]` labels, and for a profile field also on its own site (`linkedin.com`; `x.com` or `twitter.com`; `github.com`).
- **Cancelled only on an explicit source signal**: iCal `STATUS:CANCELLED`, JSON-LD `EventCancelled`, Eventbrite `status == "canceled"`. An event missing from a feed stays listed and is never cancelled; the store only sees sightings and has nothing to cancel an absence with. A field a source stops stating keeps its stored value.
- **An event never creates a company.** `store.link_companies` links only to a listed tech company (`companies.is_tech IS TRUE`) found through `services.company_map.store.find_existing`; the organiser's website is read as a string and never fetched. The company drawer's "Hosted by" block reads `GET /events?company_id=…`.
- **Duplicates**: the earliest-stored row is the primary; a later row from another source with the same start, title words and place gets `duplicate_of` and is hidden, shown as "Also on". An event somebody saved or noted people at is never hidden behind another.
- **People met are private.** `event_contacts` rows are filtered by `user_id` in every query; another user's person or private (`owner_user_id`) event is a 404, exactly as for an id that does not exist. They are what the user typed: never enriched, never looked up, no guessed email, LinkedIn only as a pasted link or a search link built at read time. **Outreach contract** (`services/events/met.py`, `GET /api/v1/events/people-met`): Events publishes `MetAtEvent` rows and imports nothing from Outreach; `source_ref` (`event_contact:<uuid>`) is the idempotency key, and `context_line` is the text for the touch "met at an event". `FEATURES.outreach` in `frontend/lib/features.ts` is `false` until that branch lands.
- **Workflow**: `.github/workflows/events.yml` calls `POST /api/v1/ingest/events/refresh?step=…` (cron token, like `company-map.yml`) at 23:20 and 11:20 UTC: `luma`, `gdg`, `eventbrite`, `link`, `relevance`, and `prune` once a day. A step never raises: it returns `{"step": …, "error": …}`, commits in units, and stops starting new units after 480 s. `purge` (admin only, `source=eventbrite`) is never scheduled. `python scripts/refresh_events.py --step all|luma|gdg|…` runs the same steps locally against whatever database the environment names. `prune_events` (in `services/job_retention.py`) keeps every event a user saved or noted, drops unlisted ones once over, others 14 days after they end, cancelled ones 2 days after they were due to start.
- **Day plan** (`services/copilot._event_cards`, only the user's own saves): `event_today` (starts on today's IST date and not over, or began earlier and still running; "Tonight" from 17:00 IST for an evening start, else "Today"; an earlier-day event says "On now · until …" and never a start clock; a cancelled one is a warning in the same slot), `event_soon` (the soonest in the next 7 days) and `event_met_prompt` (an event the user was going to, over within 2 days, nobody noted yet). `event_today` cards come best first, because the Overview (`buildActions` in `frontend/app/dashboard/page.tsx`) shows only the first: going before saved, then starts-today before a long-running one.
- **Frontend**: `frontend/app/events/page.tsx` (list and map, filters, drawer, people met; deep link `/events?event=<id>`, `#people`), `components/EventMap.tsx` over the shared `lib/maplibre.ts` (an event without coordinates is not drawn; the map says how many are missing), `lib/event-map.ts` and `lib/event-time.ts` (pure rules), `EventsSection` in `app/company-intel/page.tsx`. Tests: `frontend/e2e/events.spec.ts` (against mocked `/api/v1/events`, but a real account, so it needs the stack up) and `event-map.rules.spec.ts` (pure).
- **Gotcha, array columns**: the model declares `events.topics` as `StringArray` (`varchar[]`), the migration creates it `text[]`. Postgres has no `text[] @> varchar[]`, so a bare `Event.topics.contains([…])` answered 500 on every topic filter while SQLite, which the tests use, was happy. `read.topic_clause` casts the value to `text[]`, and `tests/test_event_read_api.py` runs it against a temporary `text[]` table in Postgres. Do the same for any new array comparison.

### v2: liveness, the Role Dossier and the public surface

The v2 promise is "know the job is real, walk in prepared". Design: `docs/superpowers/specs/2026-10-05-v2-role-dossier-design.md`.

- **Liveness** is a verdict with evidence, never a score. `domain/liveness.py::assess` is a pure rule set over six nullable timestamps on `jobs` plus `role_sightings.times_reposted`; `services/liveness.py` keeps that evidence current (`record_sighting` on every write, `reverify_batch` against the ATS APIs) and reads it back (`load_verdicts`). `POST /api/v1/ingest/reverify` is run by `.github/workflows/reverify.yml` with `X-Cron-Token` — there is no worker.
- **Role Dossier** — `GET /api/v1/roles/{id}/dossier` (`services/dossier.py`) composes four independent sections (liveness, fit, way in, readiness); any of them may be `null`. No LLM call. Frontend: `frontend/app/roles/[id]/page.tsx`.
- **Job-anchored prep** — `POST /interview/sessions/start` takes `job_id`; `agents/interview/role_context.py` builds a bounded, fenced description of the posting that is stored on the session row and re-read from there every turn.
- **Referral ask** — `POST /api/v1/roles/{id}/referral-ask` (`services/referral.py`): one on-demand LLM call with a deterministic template fallback. Nothing is ever sent on the user's behalf.
- **Public surface** — `api/routers/public.py` + `services/public_roles.py`, unauthenticated and read-only. Frontend pages `/r/[slug]` and `/hiring/[slug]` are ISR (`frontend/lib/public-api.ts`); `/check` is the posting check.

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
