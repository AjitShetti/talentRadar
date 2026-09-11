# Live Job Sourcing — Design

Date: 2026-09-10
Status: Approved, in implementation

## Problem

TalentRadar's natural-language search only ever reads Postgres. The
multi-source scraping stack exists and works, but nothing in the product
reaches it, and nothing it finds is kept.

Three separate facts produced that state:

1. **The scrapers are disconnected from the UI.** `frontend/app/search/page.tsx`
   calls `/search/semantic` and `/search/structured`, both of which read the
   database. `/search/stream` and `/search/live` fan out across every scraper
   and are called by nothing outside tests.
2. **Live results are never persisted.** `RealtimeScraperEngine.search_all`
   writes to the search cache and returns. No row reaches `jobs`, so the next
   query cannot see what the last one found.
3. **The impersonation tier is not installed.** The deploy work moved
   `scrapling` into the `[stealth]` extra with `INSTALL_STEALTH=false`, because
   `scrapling.fetchers` imports `playwright` and `patchright` at module level
   and Camoufox on top OOMs a 512 MB instance. Removing the browser removed
   TLS impersonation with it, since the extra is one unit.

On top of that, the cache that was supposed to absorb repeat queries is not
actually shared: `render.yaml` declares no key-value service, so
`SearchCacheService` falls back to a process-local dict that dies every time
the free instance sleeps.

## Goal

A natural-language query returns indexed results immediately and, when the
index is thin or stale for that query, streams in fresh postings scraped live
from across the internet — with everything found persisted so the next search
is instant. All of it inside the Render / Neon / Vercel free tiers.

## Constraints

These are hard limits, not preferences. Every decision below is downstream of
one of them.

| Constraint | Value | Consequence |
|---|---|---|
| Render free instance RAM | 512 MB | No headless browser, ever. One uvicorn worker. |
| Render free instance sleep | 15 min idle, 30–60 s cold start | Background crawling cannot be relied on. |
| Neon free storage | 0.5 GB | Unbounded job retention will fail writes. |
| Render Key Value free | 25 MB, no persistence | Cache must be small, compressed, and disposable. |
| Groq free tier | Rate limited | Cannot LLM-parse every scraped posting. |
| Job board terms | Vary | No browser automation against boards that forbid it. |

## Decisions

### D1 — `curl_cffi` directly, not `scrapling.fetchers`

The Cloudflare-defeating capability is TLS/JA3/HTTP2 impersonation, which is
`curl_cffi` (~5 MB). The 300 MB is `playwright` + `patchright` + Camoufox —
the headless-browser tier, which a 512 MB instance cannot run regardless.

`ingestion/scrapling_manager.py` binds to `curl_cffi` directly. This restores
impersonation to the default deployment for the first time, at ~5 MB.

The browser tier stays behind `ENABLE_STEALTH_SCRAPERS`, off, uninstalled,
and unused. JS-hydrated boards that need it (Naukri) are out of scope unless
a JSON endpoint is reachable without one.

**Rejected:** reinstalling the full stealth stack. It requires Render Starter
($7/mo), and the user's deployment is free-tier by design.

### D2 — Index-first with gated live fill-in

Every NL query hits the index first and returns immediately. Live scraping is
a conditional second wave, not the primary path.

**Rejected:** always-live search. 5–10 s per query, hammers the boards, and a
cold instance adds 30–60 s on top.

**Rejected:** index-only with background crawling. Free instances sleep after
15 min idle, so a crawler runs only while someone is already using the app.
Coverage would be patchy and Neon's 0.5 GB would fill with speculative rows.

### D3 — Persist structurally, enrich lazily

Scraped postings are written to Postgres using only fields the scraper already
returned, with **zero LLM calls**, and embedded with the existing local ONNX
model. LLM parsing happens later, under a daily budget, only for postings a
user actually opens.

This is what makes "search the whole internet" affordable: the expensive step
is decoupled from the volume step.

### D4 — Verification is built first

The verification harness and a measured baseline of which sources actually
work land before any behaviour changes. The fan-out roster is decided by that
measurement, not by assumption.

## Architecture

### Agent flow

```
node_classify ──► node_rag_retrieve ──► should_source? ──► node_live_search ──┐
   (LLM)             (pgvector+SQL)      (deterministic)      (scrapers)      │
                            │                                                 ▼
                            └────────────── no ──────────────────► node_merge_rank ──► END
                                                                   (pure function)
```

Each node has exactly one responsibility:

| Node | Input → Output | I/O | LLM |
|---|---|---|---|
| `node_classify` | NL string → `QueryContext` | none | yes (existing) |
| `node_rag_retrieve` | `QueryContext` → indexed jobs | Postgres | no |
| `node_live_search` | `QueryContext` → live jobs + `sources_stats` | scrapers, Redis | no |
| `node_merge_rank` | two job lists → one ranked list | none | no |

`node_merge_rank` performs no I/O. It is a pure function of its inputs, which
makes the ranking logic exhaustively testable without a database or network.

#### `should_source` — the gate

Deterministic. No LLM call.

Returns **false** immediately, whatever else is true, when
`tr:sourced:<query_hash>` exists in Redis. That key is written for 8 hours
after any live fan-out and is the single mechanism preventing repeated queries
from hammering the boards and getting the deployment's IP blocked.

Otherwise returns **true** when any of:

- fewer than `LIVE_SOURCING_MIN_RESULTS` (default 8) indexed hits
- the freshest indexed hit is older than `LIVE_SOURCING_STALE_DAYS` (14)
- the classified context carries a freshness signal (`latest`, `new`, `this week`)
- the caller passed `force_refresh`

`AgentState` gains `live_jobs`, `sources_stats`, and `sourcing_decision`
(`{sourced: bool, reason: str}`) so the API can report why it did or did not
go to the internet.

### Endpoints

`/search/stream` is rewired to drive the graph rather than calling
`RealtimeScraperEngine` directly, and the frontend search page moves onto it:
indexed results as wave one (~200 ms), live results streaming per-source as
they land.

`/search/semantic` and `/query` keep their existing non-streaming contracts
and gain merged results.

### Persistence

`services/job_persistence.py`, invoked fire-and-forget from `node_live_search`
so it never affects search latency:

1. Filter through the existing `ingestion/validation.py`
2. Dedupe against `tr:seen:<url_hash>` (7 d TTL), then against Postgres on
   `source_url`
3. Map scraper output → `Job` kwargs using only already-present fields; title,
   company, URL, location via `domain/geo`, salary retained as raw text
4. Embed `title + company + skills` with the local ONNX embedder →
   `job_embeddings`
5. One batched transaction, `ON CONFLICT (source_url) DO NOTHING`

Migration `011` adds `enrichment_status` (`raw` | `enriched` | `failed`).
Enrichment runs when a user opens a job, or via the existing APScheduler pass
under a daily ceiling held in `tr:budget:llm:<date>`. It backfills `skills[]`,
`seniority`, `employment_type`, parsed salary, and `description_clean`.

### Retention

Neon fails writes at 0.5 GB, so retention is a correctness requirement:

- scheduled prune deletes `raw` jobs older than 45 days with no attached
  application
- a storage guard checks `pg_database_size`; above 400 MB it prunes
  oldest-raw-first regardless of age
- `description_clean` is truncated to 4 KB on write

### Caching

Render Key Value (free: 25 MB, no persistence) is added to `render.yaml`.
Keys, all namespaced `tr:`:

| Key | TTL | Purpose |
|---|---|---|
| `tr:search:<hash>` | 8 h | Aggregated search payload (existing, compressed) |
| `tr:sourced:<hash>` | 8 h | Live fan-out lock — the IP-block guard |
| `tr:seen:<url_hash>` | 7 d | Cross-search URL dedupe |
| `tr:health:<source>` | 24 h | Source health counters |
| `tr:budget:llm:<date>` | 48 h | Daily LLM enrichment ceiling |

Every read and write degrades to the in-memory fallback when Redis is absent.
Nothing may *require* the cache, consistent with the existing rule for the
vector store.

## Verification

### Layer 1 — offline contract tests

`scripts/capture_fixtures.py` captures one real response per source into
`tests/fixtures/sources/`, committed to the repo.
`tests/test_source_contracts.py` is parametrized over the source registry and
asserts per source: parses ≥1 job; `title`, `company` and `source_url` are
non-empty; the URL survives `ingestion/validation.py`; the dedup hash is
stable across runs.

One registry test asserts **every registered source has a fixture**, so a
source cannot be added without a test and cannot be silently skipped.

### Layer 2 — live health registry

`services/source_health.py` records every live attempt into Redis (attempts,
successes, zero-yields, jobs returned, p50 latency, `last_success_at`) and
derives `healthy` / `degraded` / `failing`.

- three consecutive zero-yields → `degraded`
- raising → `failing`, and the source is **circuit-broken out of the fan-out**
  for a cooldown, protecting both the instance and the deployment's IP

Exposed at `GET /api/v1/ingest/sources/health` (admin) and surfaced in the
search UI as "searched 6 of 8 sources".

### Layer 3 — `make verify-sources`

Hits every source live, prints source / jobs found / latency / status, exits
non-zero if any source yields zero.

This runs **first**, against current code, to produce a baseline of which
sources genuinely work today. That measurement decides the fan-out roster.

## Out of scope

- Naukri and any other board requiring JS hydration
- Celery workers or scheduled crawling (no always-on process on free tier)
- Paid Render / Neon / Vercel plans
- Re-adding `scrapling`, `playwright`, `patchright` or Camoufox to the default
  install

## Success criteria

1. A natural-language query with a thin index returns indexed results in under
   ~500 ms and streams live results afterwards.
2. Jobs found live are in Postgres and semantically searchable on the next
   query without re-scraping.
3. The same query twice within 8 hours triggers exactly one live fan-out.
4. `make verify-sources` reports every source's real status.
5. A source whose site changes goes `degraded` and is visible at the health
   endpoint without any code change.
6. The Docker image gains no browser stack; the deployment stays on free tiers.
