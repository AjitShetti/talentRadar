# Capacity and reliability review: 2026-11-04

Scope: the backend at `main` @ `dbe4c76`, the free-tier deployment (Render free, Vercel Hobby, Neon free, Groq), and an estimate of how many users the whole system can serve. Frontend code quality and page speed are covered by a separate review. This review changes no application code.

Line numbers refer to `dbe4c76`.

---

## 0. Summary

**The CPU breaks first, not the RAM, the DB connections or Groq.** The API runs one uvicorn process on about 0.1 CPU. Three kinds of work hold its single event loop for whole seconds:

- bcrypt on every login;
- the ONNX query embedding on every semantic search;
- background embedding of every posting that live sourcing brings in.

While the loop is held, every other user waits, including on `/health`.

**The calendar breaks second.** Neon's free compute allowance (100 CU-hours a month) runs out once the database is awake more than about 13 hours a day on average. Neon then suspends the compute until the next billing period, and every request that needs the DB returns a 500. The allowance is measured in time awake, not in load, so a handful of users spread across the day can cause it. Check this on the Neon console first (see §3.2).

| | Now (Render free, as deployed) | After the quick wins (§5, ranks 1–8, all free) | After a host migration (Oracle Always Free A1, Neon kept) |
|---|---|---|---|
| Event-loop seconds per typical session ¹ | ~8–12 s (more when a search sources live) | ~1.5–2.5 s | same code, ~20–40× the CPU |
| Concurrently active users at p95 < 2 s ² | **~10–15**; a burst of logins or searches stalls everyone | ~30–50 | ~300–500 |
| Daily active users (CPU-bound) ³ | **~150–300** | ~600–1,000 | ~5,000–10,000 |
| What breaks first | CPU (event loop) → Neon CU-hours | Neon CU-hours (time awake) → CPU | Neon free (0.5 GB, 100 CU-h, 0.25 CU) → Groq spend |
| RAM (512 MB on Render) | ~145 MB idle, ~180 MB under reads, ~300 MB with the ONNX model, ~400 MB while embedding a batch | same | 24 GB, so 2–4 workers |
| DB connections | 3 + 2 pool (`render.yaml:113-116`). Not binding: the CPU saturates first | not binding | 5 per worker; still far below what Neon's pooler accepts |

¹ Measured locally under a 0.1-CPU / 512 MB cgroup (§4.2). The emulation is calibrated by cold start: 36 s locally against the documented ~40 s.
² An "active" user makes about one API call every 20 s.
³ Assumes 20% of DAU fall in the peak hour, a 10-minute session of about 15 API calls (one Overview, two searches, a few page reads), the loop kept at or below 50% busy, and no more than one login per user per week (JWTs last 7 days, `config/settings.py:182`).

**The live load test did not run.** This sandbox's network policy denies `talentradar-api.onrender.com`, so no request reached production (§4.1). Every number above comes from local measurements under emulated Render limits and from code reading. I ran nothing against the live API, and no number here stands in for one. §4.1 has the commands to run the polite live test yourself.

**Things that may cost money or suspend the service (zero-cost rule):**
- **Neon CU-hours.** See above and §3.2. If the Neon project's autoscaling maximum is above 0.25 CU, the hours run out faster.
- **Render's 750 instance hours.** The keepalive workflow keeps the API awake about 730 of the 750 hours a month (§3.1). If a second free Render web service exists (`talentradar-web` in `render.yaml:159-162`), the workspace runs out before month end, and Render suspends **every** free service in it.
- **Groq spend (paid by the owner).** Every search makes a second Groq call on the 120b model whose answer is thrown away whenever live results are merged (§1.3). This is money spent for nothing.
- **Host migration targets.** Oracle Always Free and Google Cloud Run both need a card on file. Fly.io no longer has a free tier for new organisations. See §5.

---

## 1. Request path costs

Columns: queries per request, indexes, outside calls, blocking work on the event loop, and LLM calls. Times are measured at 0.1 CPU against a local Postgres 16 with 20,000 jobs (§4.2), so add Neon's round-trip time per query in production.

### 1.1 Login, signup and auth

- **bcrypt at cost 12 runs synchronously on the event loop.**
  - `api/auth.py:47` (`checkpw`) and `api/auth.py:52` (`gensalt(rounds=12)`), called from the `async def` handlers at `api/routers/auth.py:74, 93, 100, 142, 144, 162, 225`.
  - A failed login still hashes, against `_DUMMY_HASH` (`api/routers/auth.py:52, 93`). That is a correct anti-enumeration measure, but it means every login costs one full hash.
  - Measured: 0.25 s per hash unthrottled; **2.9 s per login at 0.1 CPU**.
  - While two logins ran concurrently, `/health` went from **46 ms to 2.87 s average (5.2 s median)** (§4.2). Login throughput tops out at **0.34 per second**.
- **Queries:**
  - login: 1 (`users.email`, unique index);
  - signup: 3.
- **`get_current_user` makes no DB query.** It only decodes the JWT (`api/auth.py:168-177`). That is good for capacity. The cost: a deleted account's token stays valid until it expires.
- There is no `/me` route in the auth router. The auth router is mounted at `/api/auth` (`api/routers/auth.py:47`), not under `/api/v1`.

### 1.2 Overview briefing: `GET /api/v1/agent/briefing`

- **Fully sequential.**
  - `services/copilot.py:844` (`build_briefing`) awaits each producer in turn. There is no `asyncio.gather` in `copilot.py`.
  - Each producer opens its own `AsyncSessionLocal`, so about 13 sessions are checked out one after another.
- **Queries:** about 20 on a typical call, about 28 when the outreach suggestion cache misses (`services/outreach/suggest.py:98`, 3600 s TTL). The breakdown is in Appendix A.
- **No N+1 loops, and every filter column is indexed:**
  - `job_applications.user_id`;
  - `event_saves (user_id, event_id)`;
  - `outreach_touches (user_id, happened_at)`;
  - `practice_days (user_id, day)`.
- **Outside work:** no HTTP and no LLM. The only one-off blocking work is the `lru_cache`d JSON read of the practice catalogues (`services/practice/catalogue.py:147-157`).
- **Measured:** 2.5 requests per second at saturation, 1.4 s median and 3.1 s p95 at concurrency 4, on a local DB.
  - **With Neon, add about 20 × its round-trip time.** If the Neon project is not in `aws-ap-southeast-1` (next to Render Singapore), a 150–250 ms round trip makes the briefing alone 3–5 s.
- **The Overview also calls `/dashboard`.**
  - Its `asyncio.gather` (`api/routers/dashboard.py:76`) is not guarded.
  - `target_role_readiness` (`api/routers/dashboard.py:86`) can call the LLM. It is cached in-process for 30 min (`services/career.py:192-194, 242`).

### 1.3 Search: `POST /api/v1/search/semantic` → `agent_graph`

**No auth on the route** (`api/routers/search.py:224`). Any anonymous client can drive Groq calls and live scraping. Only the per-IP limiter stands in front of it.

| Step | Cost | Evidence |
|---|---|---|
| Classify | Rules first, then a Groq call on `gpt-oss-20b` (max_tokens 600) when no rule matches. Not skipped when `intent=SEARCH_JOBS`: the override replaces only the intent. | `agents/orchestrator.py:144-213, 219`; `agents/graph.py:91-113` |
| Query embedding | all-MiniLM-L6-v2 through ONNX, **synchronous on the event loop**. The model loads lazily on the first search; startup does not warm it. Measured: 38 ms on one core unthrottled; **0.4–3.5 s at 0.1 CPU** (see the note under §4.2). First search after a deploy: model load of **24–32 s at 0.1 CPU**, plus about 100 MB RSS. | `ingestion/embeddings/pgvector_store.py:323` (`_search_params` → `embed_texts`, no `to_thread`); `ingestion/embeddings/embedder.py:30-37`; the write path does use `to_thread` (`pgvector_store.py:278`) |
| pgvector search | 1 query, `ORDER BY embedding <=> q LIMIT n*2`. HNSW index, falling back to ivfflat, falling back to none (`storage/migrations/versions/010…:93-112`). The `metadata @> …` filter has no GIN index and is applied after the ANN step, so filtered searches come back thin. | `pgvector_store.py:197-207` |
| Hydration | 1 batched query on unique `(external_id, source)` | `agents/rag_agent.py:222` |
| Relational fallback (< 3 hits) | 3–5 queries using `ILIKE '%term%'` on title, description and the skills/tags arrays. **No trigram or full-text index exists**, so these are sequential scans, the `COUNT` included. | `storage/repository.py:470-481, 705-737` |
| RAG summary | A second Groq call, `gpt-oss-120b`, max_tokens 500, on every search with results. **Thrown away** when live results are merged (`agents/graph.py:314-319` rewrites `summary`). | `agents/rag_agent.py:129-131, 321` |
| Sourcing decision | One cache `EXISTS`. `MIN_INDEXED_RESULTS = 8`, so a request with `limit < 8` always goes live unless the lock is set. The staleness test never fires, because the indexed result dicts carry no `posted_at`. | `agents/sourcing_policy.py:42, 122`; `agents/graph.py:152-162, 354-371` |
| Live search (the request waits for it) | 6 sources concurrently, each under `asyncio.wait_for` (5–8 s), with no total timeout, so it can take about 8 s. The ATS source fans out to 23 fetches. Each fetch opens a new curl_cffi session, with no connection reuse. | `ingestion/engine.py:155, 239-262`; `ingestion/sources/registry.py:69-122`; `ingestion/scrapling_manager.py:141` |
| `tr:sourced` lock | Set **after** the fan-out (`agents/graph.py:225`), so it is not single-flight: N identical concurrent searches all scrape. | `agents/sourcing_policy.py:31, 39` |
| Background persistence | `asyncio.create_task` the request does not await (`agents/graph.py:259`). N+1 per posting: an `EXISTS` and a `SET` per URL, 1–3 company queries plus an upsert plus `record_sighting`, and one `set_embedding_id` UPDATE per item, all on one long-held session. Several hundred queries for about 60 postings. **Then it embeds every posting**: batches of 8 (`embedder.py:27`) at **3–27 s per batch at 0.1 CPU**, so 25–200 s of the instance's CPU for each fan-out. It runs in a thread, but the thread draws on the same 0.1-CPU quota as the loop. | `services/job_persistence.py:238-362` |
| Merge and rank | Pure CPU, small | `agents/merge_rank.py` |

**Groq client set-up.**
- A new `AsyncGroq` is built per object or per call (`agents/orchestrator.py:43`, `agents/rag_agent.py:58`, `services/llm.py:36`), with no `timeout` and no `max_retries`. The SDK's defaults apply: a long timeout and 2 retries.
- `services/llm.py:_chat` wraps its calls in tenacity with 3 attempts, so a single slow Groq call can take minutes.

### 1.4 Company map: `GET /api/v1/company-intel/map`

- **Cached:** `tr:map:<city>`, TTL 3600 s (`services/company_map/read.py:54, 289-294`).
- **On a miss, 2 queries, with no LIMIT:**
  - Every listed company in the city (`read.py:216-235`).
  - The city test is `ilike '%term%'` over `array_to_string(office_cities)` OR `hq_city` OR `area IS NOT NULL` (`read.py:119-126`). No index can serve it.
  - A grouped job count `IN (<every id>)` (`read.py:153-161`), served by `ix_jobs_company_status`.
- **No stampede lock:** concurrent misses all rebuild.
- **Work on every request, hit or miss:** `json.loads` of the cached value, then `json.dumps` plus a sha256 for the ETag, all on the loop (`api/routers/company_intel.py:194-195`).
  - The ETag is computed even for a 304.
  - `Cache-Control: private, no-cache` (`:196`) makes every page view revalidate.
- **Measured:** 40 requests per second. The local map was nearly empty, so treat that as an upper bound. A real map of about 2k companies at about 100 B a row is about 200 KB of JSON to re-serialise per hit.
- `/map/for-me` has no cache (`read.py:333-358`).

### 1.5 Events: `GET /api/v1/events`

- 6 batched queries (`services/events/read.py:391-404, 299-347`), and 3 sequential cache GETs for source health (`read.py:351-367`).
- Indexed: `starts_at`, `(listed, starts_at)`, `duplicate_of`, `owner_user_id`.
- Not indexed: `topics` (array `@>`, no GIN), and the `ilike` filters on title and organiser.
- No response cache.
- **Measured:** 8.9 requests per second, 0.41 s median, 0.69 s p95.

### 1.6 Outreach: `GET /api/v1/outreach/pack` and `GET /api/v1/outreach`

- **Pack, steady state:** about 11 queries over 4 sequential sessions (`services/outreach/pack.py:200-209, 417-444, 507-533`).
  - `_load_pack` runs twice.
  - With follow-ups due, about 5 more. The channels query filters `outreach_touches.person_id`, **which has no index** (`pack.py:244-250`).
- **The IST day's first pack build** ranks up to 400 candidate companies in Python (`services/outreach/suggest.py:252-302, 604-628`).
  - Stacks reads every active job's skills (`suggest.py:371-377`).
  - Rounds and people have no row limit (`suggest.py:399-435`).
  - The build happens once per user per day.
- **`/outreach` home** loads all of the user's threads with every person and touch, unbounded (`services/outreach/touches.py:416-421`). It is capped in practice by 100 threads × 60 touches.
- **No LLM on any read.**
- **Measured:** pack 5.4 requests per second, 0.61 s median, 1.29 s p95. That is a steady-state pack on a fresh user; the first build of the day is heavier.

### 1.7 Practice

- **`/today`:** 4 queries (`services/practice/daily.py:226-258`). `pick_question` re-sorts the whole catalogue on every request (`domain/practice_pick.py:296, 308`).
  - `data/practice/leetcode_free.json` is not in the repo, so in this checkout the catalogue is empty.
  - Measured: 10.6 requests per second, 0.40 s median.
- **`/history`:** reads every done day and attempt, not only the shown window (`daily.py:455-480`).
- **Leaderboard:** cached 10 min (`services/practice/board.py:69, 414-427`).
  - Every hit still re-ranks in Python (`board.py:429`).
  - `_opted_in` uses an OR filter with no index (`board.py:140-149`).
- **No LLM on any read.** The judge and Get feedback are the only model calls in Practice, and both are budgeted.

### 1.8 Public reads (unauthenticated)

The `/r` and `/hiring` pages are ISR on Vercel, so their load is build and revalidate traffic rather than per-visitor traffic.

| Endpoint | Cost | Measured at 0.1 CPU |
|---|---|---|
| `GET /api/v1/public/roles?limit=` | **Always loads 2,000 full `Job` ORM rows** plus companies (selectin), then filters for India in Python (`services/public_roles.py:47, 161-191`). It filters `closed_at IS NULL` and three `source LIKE` prefixes and orders by `last_seen_at DESC`; neither column is indexed. No cache. | **3.7 s average, 0.54 requests per second**, about 100 KB for `limit=500` |
| `GET /api/v1/public/roles/{slug}` | 3 queries, plus `liveness.refresh_if_due` (`public_roles.py:149` → `services/liveness.py:312-323`), which makes an **outbound ATS request (4 s cap) on the request path** whenever the role was last verified more than 72 h ago | 12.2 requests per second, 0.30 s median, 0.50 s p95 (rows freshly verified, so no outbound call) |
| `GET /api/v1/public/companies/{slug}` | 4 queries, at most 60 jobs, no outside call | not run |
| `POST /api/v1/public/check` | On the strict limit (10 per 300 s). Looks up `source_url =` (not indexed) and `LIKE '%id%'`; `lower(source)` defeats `ix_jobs_source`. Always makes an outbound ATS call. | not run |
| `GET /health` | Static dict, no DB (`api/main.py:256`), exempt from the limiter | 109 requests per second at concurrency 5, 46 ms average |

There is no `/public/hiring/{slug}` API route. The `/hiring` page reads `/public/companies/{slug}`.

---

## 2. Resource limits

### 2.1 Render free: CPU, RAM and workers

- **One uvicorn worker** (`infra/Dockerfile:157`), one event loop, about 0.1 CPU.
  - The CPU figure comes from third-party reports; Render's own docs give no number. The local emulation matched the documented cold start (36 s against about 40 s), so 0.1 is about right.
- **Memory**, measured as cgroup usage at 0.1 CPU and 512 MB:

| State | Memory |
|---|---|
| Idle after start | **143 MB** |
| Peak after the read benchmarks | **178 MB** |
| Import of `api.main`, unthrottled | 191 MB max RSS |
| ONNX model loaded (standalone process) | 248 MB RSS |
| Estimated with the API and the model resident | about 300 MB |
| A batch-of-8 embedding (per `embedder.py:21-25`) | about +100 MB, so about 400 MB peak |

- **A second worker is a bad idea on Render free.**
  - It duplicates about 150–300 MB, which risks OOM at 512 MB.
  - It gains nothing on a 0.1 CPU share.
  - Keep `--workers 1` here. Use 2–4 workers only on a host with at least 1 vCPU and at least 1 GB.
- **Cold start: 36 s** to the first `/health` 200 at 0.1 CPU. Python imports alone take 3.4 s unthrottled.
  - The first search after a start adds 24–32 s of model load.
  - The keepalive hides both except after a deploy.

### 2.2 Postgres: SQLAlchemy pool and Neon free

- **Pool settings:** `pool_size=3, max_overflow=2` (`render.yaml:113-116`), `pool_pre_ping=True`, `pool_recycle=1800` (`storage/database.py:41-49`).
  - No `timeout`/`command_timeout` in `connect_args` (`database.py:26-35`). A slow Neon wake holds requests open until the driver gives up.
  - The pool's own wait defaults to 30 s, after which the request gets a `TimeoutError` and a 500.
- **Neon's pooled endpoint** (PgBouncer) takes thousands of client connections, so **5 is the binding number, not Neon.**
  - Five is enough for 0.1 CPU: the briefing holds one session at a time.
  - The risk is background persistence holding one session for a whole live-search write, while the overnight sweep and the digest share the same pool (`api/main.py:108-140`).
- **Neon free limits** (per Neon's FAQ, 2026): 0.5 GB storage, **100 CU-hours of compute per project per month**, suspends after 5 min idle, 5 GB egress a month.
  - When CU-hours run out, compute is suspended until the next period, and new connections fail.
  - At 0.25 CU that is **400 awake hours a month, about 13 h a day**. See §3.2.
- **Storage:**
  - Locally, 20,000 jobs with about 2 KB of description each took 24 MB, without embeddings.
  - With a 384-dimension vector (1.5 KB), HNSW index overhead and real descriptions, expect about 8–12 KB per job. Under 0.5 GB that is roughly **40–60k jobs**.
- **Retention** (`services/job_retention.py`):
  - Prunes raw jobs after 45 days, and the oldest raw jobs regardless of age above `STORAGE_HIGH_WATER_BYTES = 400 MB` (`:54`), at most 5,000 a run.
  - Gaps:
    - **Enriched rows are never deleted** (`:21-22`).
    - It runs only inside the overnight sweep (`services/sweep.py:141-143`), so a missed sweep prunes nothing.
    - `pg_database_size` returns 0 on error (`:73-80`), which reads as "plenty of room".
    - Deletes do not shrink the database file until autovacuum reuses the space.
  - Storage is not the first limit at today's volumes, but nothing stops slow growth of enriched rows.

### 2.3 Groq: budgets against rate limits

- **Default models:** `groq_model` and `groq_interview_model` are `openai/gpt-oss-120b`; `groq_fast_model` is `openai/gpt-oss-20b` (`config/settings.py:51-68`).
- **Groq's published limits for both gpt-oss models:**
  - free tier: 30 RPM, 1K RPD, 8K TPM, 200K TPD;
  - developer tier (a payment method on file): about 1K RPM, 250K TPM.
  - The owner pays for credits, so the developer tier is assumed. Check the org's limits page in the Groq console.

| Budget | Default | Key |
|---|---|---|
| `DAILY_ENRICHMENT_BUDGET` | 200 a day, global, `gpt-oss-20b` hard-coded (`ingestion/parsers/jd_parser.py:53`) | `tr:budget:llm:<date>` (`services/job_enrichment.py:32-42`) |
| Judge | 12 a day, 4 a run (2 without `GITHUB_TOKEN`), 65 s apart | `tr:budget:judge:<UTC day>` (`services/practice/judge.py:121-148`) |
| Company summaries / news LLM | 60 / 20 a day | `services/company_map/summary.py:30`, `news_ingest.py:46` |
| Outreach drafts | 20 per user per IST day, 8 per user from the shared pool, 150 a day global | `services/llm_budget.py:35-40`, `services/outreach/drafts.py:93` |
| Design feedback | 3 per user a day, 1 a minute, 150 a day | `services/practice/design_feedback.py:58-59` |

- **On the free tier, the budgeted work fits easily.** About 450 budgeted calls a day are spread over two models with 1K RPD each.
- **The unbudgeted paths are the risk:**
  - search: classify plus a summary, so 2 calls per search;
  - `/agent/chat`: 3 calls;
  - interview turns on the 120b model, up to 8 per session, with no daily cap;
  - resume analyze, tailor and cover letter;
  - `/match/*` and `/career/recommend`;
  - the overnight sweep's `JDParser.batch_parse` for small non-ATS batches (`jd_parser.py:321`), outside `DAILY_ENRICHMENT_BUDGET`.
- **What that means:**
  - On the free tier, about 400–500 searches a day would use up the 120b model's 1K RPD by themselves.
  - On paid credits Groq is not a capacity limit but a cost line. Removing the wasted summary call (§5 rank 3) is the cheapest saving.
  - Unauthenticated `/search/semantic` means anyone can spend those credits. The limiter allows 100 a minute per IP per path.
- **Every budget fails open on a cache error** (`services/llm_budget.py:50-54`, `job_enrichment.py:63-66`, `judge.py:259-262`).

### 2.4 Without Redis

- **Is Redis deployed?** `render.yaml:149-153` wires `REDIS_URL` from a free Render Key Value (`talentradar-cache`). `DEPLOY.md` never asks for it to be created and lists no `REDIS_URL` (`DEPLOY.md:117-121, 271`). Whether production has it is **unverified**. Check the Render dashboard.
- **Without it,** `CacheBackend` falls back to an in-process dict capped at **512 keys** (`services/cache_backend.py:33-50`). The following become per-process and are lost on every restart:
  - every budget;
  - every breaker;
  - the 8 h `tr:sourced` lock;
  - the search cache;
  - URL dedupe.
- **Two specific failure modes:**
  1. **Eviction defeats the IP-block guard.**
     - Once over 512 keys, the dict drops expired entries, then the *soonest-to-expire* ones (`cache_backend.py:47-50`).
     - A live search writes about 60 `tr:seen:<url>` keys with 7-day TTLs (`services/job_persistence.py:316-318`).
     - After about 8 live searches the dict is full of 7-day keys, and the next eviction removes the shortest-lived keys first. Those are the 8 h `tr:sourced` locks, the 8 h search payloads and the 2-minute rate windows.
     - So popular queries re-scrape: exactly what the lock exists to prevent.
     - Budget counters (48 h) are evicted before `tr:seen`, which silently resets them. Budgets fail open.
  2. **One failed ping disables Redis for the life of the process.** `_initialised = True` is set before the ping (`cache_backend.py:63-66, 83-87`).
     - A Redis that is briefly unreachable at boot leaves the process on the dict until the next deploy.
     - Also, `incr` on the fallback is get-then-set, not atomic (`:166`).
- **Even with Redis,** `allkeys-lru` on a 25 MB instance can evict budget keys (`render.yaml:187-202`).

### 2.5 Rate limiter

- **The brief's "slowapi limit of 100/min" is not slowapi.** It is a custom fixed-window `RateLimitMiddleware` (`api/rate_limit.py:6-21`).
  - In-memory per process.
  - Keyed on **(client IP, path)** (`api/rate_limit.py:129`), not on IP alone.
  - 100 per 60 s by default; 10 per 300 s on the auth paths (`config/settings.py:189-194`).
  - `TRUSTED_PROXY_HOPS=1` on Render (`render.yaml:90-91`).
- **Path keying makes it weak as a capacity guard.**
  - One client can make 100 requests a minute to *each* distinct role slug.
  - One client can run 100 semantic searches a minute, each with 2 Groq calls and possibly a 6-source fan-out.
  - 100 per minute per path is about 1.7 requests per second per path per IP, but the instance itself saturates at about 0.3 logins a second or 1–3 searches a second. One busy client can saturate the instance without ever seeing a 429.

### 2.6 In-process scheduler

- `AsyncIOScheduler` runs on the web event loop (`api/main.py:108-140`), with `ENABLE_SCHEDULER=true` (`render.yaml:96`).
- **`daily_job_matching` at 21:45 UTC (03:15 IST):**
  - With `SWEEP_INGEST_ENABLED=true` (`render.yaml:108-109`) it scrapes, LLM-parses and embeds inside the web process (`services/sweep.py:86-99`).
  - It then ranks every user one at a time.
  - It runs during India's quietest hour, which is good. But it shares the 0.1 CPU and the 5-connection pool, and it depends on the keepalive having kept the instance awake.
- **`daily_digest`** builds one briefing per recipient (about 20 queries each) and sends mail through Resend, at most 80 a run (`services/digest.py:41`).

---

## 3. Reliability

### 3.1 Single points of failure

| Failure | What happens | Graceful? |
|---|---|---|
| **Render cold start** (deploys, or a missed keepalive) | About 36–60 s to the first byte, plus 24–32 s on the first search for the model load. The ISR public pages are unaffected. | Degrades: slow, not broken |
| **Render's 750 hours** | Keepalive pings every 600 s, holds for 20,400 s, then starts its own successor (`.github/workflows/keepalive.yml:43, 72-97`). That is about 24 h a day, about 730 h a month. The repo is public, so Actions minutes are free. A second free Render service in the workspace would use up the 750 hours, and Render then **suspends every free service until next month**. | **Hard outage** |
| **`CRON_TOKEN` unset or mismatched** | All six scheduled workflows (company-map, events, news, outreach, practice, reverify) exit in seconds **and report success**. Data goes stale silently. | Silent |
| **Neon suspended (idle)** | The first query after more than 5 idle minutes pays Neon's wake-up (hundreds of ms to a few seconds). `pool_pre_ping` recovers stale connections. The keepalive pings `/health` only, which does not touch the DB, so Neon still sleeps. That is correct, and it must stay that way: a DB-touching keepalive would cost about 180 CU-h a month, over the 100 allowed. | Degrades |
| **Neon CU-hours used up** | Compute is suspended until the next billing period. Every DB-backed request returns an **opaque 500** (`api/main.py:231` catches everything and gives no 503 or Retry-After). `/health` stays green (static), so Render and the keepalive report healthy. | **Hard outage, invisible to health checks** |
| **Neon storage full (0.5 GB)** | Writes fail; reads continue. Signup, saves and applications return 500. Retention runs only in the nightly sweep. | Partial outage |
| **DB unreachable mid-request** | The copilot cards swallow errors and return empty (`services/copilot.py:310-312, 564-566, 613-615, 721-723, 839-841`); dossier sections become `null`. Everything else returns 500. The pgvector readiness probe caches `True` forever (`pgvector_store.py:137-147`). | Mixed |
| **Groq down or slow** | No explicit client timeout. The SDK's retries stacked under tenacity (`services/llm.py:58-85`) can hold a search for minutes. Briefing, map, events, outreach reads and practice reads make no LLM call and are unaffected. Search still returns results with no summary (`rag_agent.py` catches the error). | Degrades, but slowly |
| **Job boards blocking or failing** | Breaker per source: opens after 3 consecutive failures, for 15 min (`services/source_health.py:338, 343, 453-454`). After the 15 minutes the source is tried again; with `consecutive_failures` still at 3 or more, a single failure reopens it, and one success resets it. Each source is capped at 5–8 s. | Degrades well |
| **Redis absent or dropped** | Everything still works (§2.4), but locks, budgets and breakers are per process and erodable by eviction. | Degrades, with the guard weakened |
| **Resend down** | 10 s timeout, no retry (`services/email.py:25, 52-58`). A reset mail or digest is lost. | Degrades |
| **Event loop held** (bcrypt, embedding, large JSON dumps) | Every concurrent request waits, `/health` included. Render's health check has a timeout, so a long stall during a login burst could restart the instance. | **Shared-fate latency** |

### 3.2 Neon compute hours: the calendar ceiling

| Usage | CU-hours a month at 0.25 CU | Fits in 100? |
|---|---|---|
| Workflows only: news 8 wakes a day, practice 12, reverify 4, events 2, company-map 1, outreach 1, about 5–10 min each | about 0.5–1 a day, so 15–30 a month | yes |
| Users active somewhere across 10 h a day (Indian waking hours with gaps) | about 75 | yes |
| Users active across 16 h a day | about 120 | **no: suspended around day 25** |
| Autoscaling max above 0.25 (the free plan allows up to 2 CU) | proportionally more | worse |

What to check on the Neon console, at no cost:
1. Compute used so far this month.
2. That the autoscaling max is 0.25 CU.
3. That autosuspend is on at 5 min.
4. That the project's region is `aws-ap-southeast-1`.

If usage trends over 100, the free remedy is to move hot, cacheable reads off the DB (§5 ranks 6–8) so that idle stretches reach 5 minutes more often. Another option is a host whose own Postgres replaces Neon (§5, Oracle).

### 3.3 Timeouts and retries on outside calls

- **Groq:**
  - Orchestrator and RAG: no timeout (`agents/orchestrator.py:43`, `agents/rag_agent.py:58`).
  - `_chat`: tenacity with 3 attempts, 1–6 s backoff, on 429, 5xx and network errors (`services/llm.py:58-85`), on top of the SDK's 2 retries.
  - Interview client: 20 s, 2 retries (`agents/interview/llm_provider.py:52-59`).
  - JDParser: `max_retries=0`.
- **ATS sources:** sync `httpx.Client(timeout=30)` (`ingestion/sources/greenhouse.py:74`, `lever.py:68`, `ashby.py:69`), each wrapped in a 5–10 s `wait_for` in the fan-out. Shared scraper client: 8 s, connect 4 s.
- **Tavily:** 15 s, sync httpx (`ingestion/scrapers/tavily_client.py:85`).
- **GitHub reader:** 6 s per phase with a 10 s deadline.
- **Resend:** 10 s.
- **Liveness recheck on the public path:** 4 s.
- **Postgres:** no connect or command timeout.

---

## 4. Numbers

### 4.1 Live load test: not run

**What happened.** Every request from this cloud sandbox to `https://talentradar-api.onrender.com` was refused by the environment's egress proxy: `CONNECT … 403`, a network-policy denial.

**To allow it,** add `talentradar-api.onrender.com` under *Allowed domains* in this cloud environment's Network access settings (environment menu → Edit; see https://code.claude.com/docs/en/cloud-environments#network-access). Or run the test yourself.

**A polite test** (about 4 minutes, at most 2 concurrent, well under the limiter, touching only unauthenticated reads):

```bash
go install github.com/rakyll/hey@latest
API=https://talentradar-api.onrender.com
curl -s -o /dev/null -w 'wake %{time_total}s\n' $API/health        # wake it first
hey -z 60s -c 2 -q 1 $API/health                                    # ≤ 2 rps
SLUG=$(curl -s "$API/api/v1/public/roles?limit=1" | python3 -c 'import sys,json;d=json.load(sys.stdin);print((d.get("roles",d) if isinstance(d,dict) else d)[0]["slug"])')
hey -z 60s -c 2 -q 0.5 "$API/api/v1/public/roles/$SLUG"            # ≤ 1 rps, under 100/min
hey -n 5 -c 1 "$API/api/v1/public/roles?limit=200"                  # heavy: 2,000-row load each
```

`-q` is requests per second *per worker*. Keep `c × q × 60` under 100 per path. Avoid `/public/check` (it calls an ATS) and `/search/semantic` (it calls Groq and scrapers).

### 4.2 Local measurements (emulated Render free)

**Set-up:**
- uvicorn with one worker, in a cgroup v1 limited to `cpu.cfs_quota_us = 10000 / 100000` (0.1 CPU) and `memory.limit_in_bytes = 512 MB`.
- Postgres 16 locally (no pgvector, so `VECTOR_BACKEND=none`), migrated to `025_profile_routine`, seeded with 300 companies and 20,000 jobs.
- Local Redis.
- The limiter was raised so that saturation could be measured.
- `hey` from the same machine, so there is **no network round trip**. Neon will add its round-trip time per query.

| Endpoint | Concurrency / duration | Requests per second | Avg | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| `GET /health` | 5 / 30 s | 109.4 | 46 ms | — | 98 ms | 102 ms |
| `GET /health` **while 2 logins run** | 2 / 15 s | 0.70 | **2.87 s** | **5.21 s** | — | max 5.90 s |
| `POST /api/auth/login` | 2 / n=6 | **0.34** | 5.90 s | — | — | — |
| `POST /api/auth/login`, sequential | 1 | — | 2.89 s | — | — | — |
| `POST /api/auth/signup` | 1 | — | 3.63 s | — | — | — |
| `GET /api/v1/public/roles/{slug}` | 4 / 30 s | 12.2 | 328 ms | 301 ms | 504 ms | 890 ms |
| `GET /api/v1/public/roles?limit=500` | 2 / n=10 | **0.54** | **3.68 s** | 4.10 s | — | — |
| `GET /api/v1/agent/briefing` | 4 / n=40 | 2.5 | 1.57 s | 1.41 s | 3.09 s | — |
| `GET /api/v1/outreach/pack` | 4 / n=40 | 5.4 | 719 ms | 607 ms | 1.29 s | — |
| `GET /api/v1/events` | 4 / 30 s | 8.9 | 447 ms | 405 ms | 694 ms | 781 ms |
| `GET /api/v1/practice/today` (empty catalogue) | 4 / 30 s | 10.6 | 378 ms | 395 ms | 500 ms | 801 ms |
| `GET /api/v1/company-intel/map` (nearly empty map, cached) | 4 / 30 s | 39.8 | 101 ms | 100 ms | 194 ms | 288 ms |

| One-off measurement | Unthrottled (16-core dev box) | 0.1 CPU |
|---|---|---|
| Cold start to first `/health` 200 | — | **36.4 s** (Render documents about 40 s) |
| `import api.main` | 3.4 s, 191 MB max RSS | — |
| bcrypt hash or verify (cost 12) | 0.25 s | about 2.9 s (seen in login) |
| ONNX model load | 5.9 s (includes download) | 24–32 s, 248 MB RSS |
| Embed one query | 34 ms (38 ms on 1 core) | **3.5 s** |
| Embed a batch of 8 postings | — | **24–27 s** |
| API memory, idle / after the read benchmarks | — | 143 MB / 178 MB |

**Caveat on the ONNX numbers.** At 0.1 CPU they are about 10× worse than a linear scaling of the single-core figure (which would predict about 0.4 s). That is consistent with onnxruntime's thread pool spin-waiting against the CFS quota. Render's real scheduler may behave better or worse.

I quote **0.4–3.5 s per query embedding** as the range. Either end makes embedding the most expensive single step on the request path, and capping onnxruntime's intra-op threads to 1 (§5 rank 2) is the first thing to try.

### 4.3 Capacity estimate

**Cost per typical session**, in event-loop seconds at 0.1 CPU. The session is about 15 calls: one Overview, two searches, a few page reads.

| Part of the session | Event-loop seconds |
|---|---|
| Overview: briefing (about 0.4) plus dashboard (about 0.2) | about 0.6 |
| Two searches: embedding, rest of graph and serialisation (Groq time is I/O and does not hold the loop) | 1–7 |
| About 10 page reads (events, practice, pack, roles, map) at 0.1–0.2 each | about 1.5 |
| Login, about once a week, amortised | about 0.4 |
| **Total** | **about 3.5–9.5 s, call it 8–12 s with real Neon latency and JSON sizes** |

A search that sources live adds 25–200 s of CPU in the background (§1.3). The 8 h lock bounds this to roughly the number of *distinct* queries per 8 h, but only while the lock survives (§2.4).

**Peak hour:**
- At 50% loop utilisation, there are 1,800 loop-seconds per hour.
- 1,800 ÷ 10 s ≈ **180 sessions in the peak hour**.
- 20% of DAU in the peak hour gives **about 900 DAU** with no live sourcing.
- Add 15–30 distinct live fan-outs an hour at 25–200 s each, and that alone is 375–6,000 CPU-seconds: from a third of the budget to all of it.
- That gives **about 150–300 DAU now**.

**Concurrency.** At about one call every 20 s per active user and an average call of 0.1–0.4 s of loop time, the loop is 50% busy at about 25–100 active users. Latency spikes from logins (2.9 s) and searches (up to 3.5 s plus the Groq round trips) push p95 past 2 s at around **10–15 concurrently active users**.

**Groq** is not a capacity limit on paid credits. On the free tier it would cap search at about 450 a day.

**Neon** is a calendar limit, independent of DAU once usage spans more than about 13 h a day.

---

## 5. Free improvements, ranked by gain ÷ effort

Effort: S is under an hour, M a few hours, L a day or more. "Capacity" is the effect on the §0 estimate.

| # | Change | Effort | Gain | Capacity effect |
|---|---|---|---|---|
| 1 | **Take bcrypt off the event loop.** Wrap `checkpw`/`hashpw` in `await anyio.to_thread.run_sync(...)` (or `run_in_threadpool`) at the 7 call sites in `api/routers/auth.py`. Consider cost 11 (half the CPU; OWASP's floor for bcrypt is 10), rehashing on login. | S | Logins stop freezing everyone; with cost 11, half the CPU per login | p95 for everyone during login bursts drops from about 3–5 s to the request's own time. Concurrency +30–50% |
| 2 | **Embed the query off the loop, with one onnxruntime thread.** Call `embed_texts` through `asyncio.to_thread` in `pgvector_store.asearch` (`:323`), as the write path already does (`:278`). Set `intra_op_num_threads=1` (or `OMP_NUM_THREADS=1`) for the ONNX session. Cache query vectors by normalised query in `CacheBackend` (1.5 KB each). Warm the model in the lifespan *after* the first `/health`. | S–M | Search stops blocking the loop; plausibly about 3.5 s down to about 0.4 s per query at 0.1 CPU (measure); repeat queries cost 0 | Search-heavy sessions 3–8 s cheaper. DAU ×1.5–2 |
| 3 | **Remove the wasted RAG summary call.** Skip `_generate_summary` in `node_rag_retrieve` when live sourcing will run, or generate it once after merge (`agents/rag_agent.py:129-131`; `agents/graph.py:314-319`). Also set an explicit Groq `timeout` (about 20 s) and `max_retries=0` where tenacity already retries (`services/llm.py:36`, `orchestrator.py:43`, `rag_agent.py:58`). | S | **Saves one 120b call per search (Groq money)**, 1–3 s of search latency, and stops the stacked retries | Search latency −1–3 s. Free-tier Groq ceiling would double |
| 4 | **Move background embedding (and the overnight sweep's ingest) off the web instance.** Persist live results with `embedding_id = NULL`, and embed them in a scheduled GitHub Actions job (the repo is public, so runners are free with 4 vCPU) that runs a script against `DATABASE_URL`. Set `SWEEP_INGEST_ENABLED=false` on Render and run the ingest half of the sweep the same way. | M | Removes 25–200 s of instance CPU per live fan-out, and the nightly scrape and embed | The single biggest lever on this host. CPU-bound DAU ×2–3; live sourcing stops competing with users |
| 5 | **Make `tr:sourced` single-flight.** Set the lock with `SET NX` *before* the fan-out (`agents/graph.py:225`), and extend it after. Add a short `tr:lock:map` around map rebuilds. | S | N identical concurrent searches scrape once | Protects CPU and the IP-block guard under bursts |
| 6 | **Use a shared cache, and fix the fallback's eviction.** Create the free Render Key Value that `render.yaml` already describes (private network, no extra cost). Or use Upstash Redis free (about 500K commands a month as of 2025; verify), pipelining the per-URL `EXISTS`/`SET` (`services/job_persistence.py:238-245, 316-318`) into one `MGET`/`MSET` so a live search costs about 5 commands, not about 120. Give `tr:seen:*` its own bounded LRU so it cannot evict locks and budgets (`services/cache_backend.py:47-50`). Retry Redis init after a failed ping (`:63-66`). | S–M | Locks, budgets and breakers survive restarts and stay correct | Reliability more than capacity; stops re-scrapes that waste CPU |
| 7 | **Cache and slim `/public/roles`.** Cache the list in `CacheBackend` for 10 min. Select only the 5 columns it emits, not full ORM rows plus selectin. Add a partial index `jobs (last_seen_at DESC) WHERE closed_at IS NULL`. | S | 3.7 s and 2,000 rows → about 5 ms on a hit | Removes the worst read; protects ISR rebuilds and sitemap crawls |
| 8 | **Cache the briefing and right-size the map's revalidation.** Cache `build_briefing` per user for 60 s, invalidated on the user's own writes; it is about 20 sequential queries with no cache. On `/map`, store the serialised JSON and its ETag together, return 304 before decoding, and use `Cache-Control: private, max-age=300`. | S–M | Overview about 1.4 s → about 50 ms on repeat views; map hits become a cache read | +20–30% concurrency; fewer Neon wakes, which saves CU-hours |
| 9 | **Indexes for the scans found.** `outreach_touches (person_id)`; GIN on `events.topics`; `pg_trgm` GIN on `jobs.title` (and description if used) for the `ILIKE` fallback; GIN on `job_embeddings.metadata` if filtered vector search is kept; `practice_settings` for `_opted_in`. Check the size cost against 0.5 GB first: a trigram index on descriptions is large. | M | Seq scans → index scans as tables grow | Small now; matters once jobs pass about 20k |
| 10 | **Fail clearly when the DB is gone.** Map `asyncpg`/`OSError`/`TimeoutError`/`sqlalchemy.exc.OperationalError` to 503 with `Retry-After` in the global handler (`api/main.py:231`). Add `timeout=10, command_timeout=30` to `connect_args` (`storage/database.py:26-35`). Add a separate `/health/deep` that checks the DB, for a human or monitor to call, **not** for the keepalive or Render's health check (either would keep Neon awake about 180 CU-h a month). | S | The frontend can say "waking up" instead of failing; no hanging requests | Reliability |
| 11 | **Watch the hours.** Confirm only one free Render web service exists (the API; the frontend is on Vercel). Delete or never create `talentradar-web` on Render. Keep the keepalive. Monitor Neon CU-hours monthly. | S | Avoids a whole-workspace suspension and a Neon compute suspension | Removes two hard-outage modes |
| 12 | **Limiter keyed per IP (plus a per-IP cap on expensive paths)** rather than per (IP, path). Require auth, or a much stricter per-IP limit (for example 10 a minute), on `/search/semantic`. | S | One client can no longer spend Groq credits or saturate the instance | Protects capacity and money |
| 13 | **Workers.** Keep 1 on Render free (RAM and CPU, §2.1). On a host with at least 1 vCPU and 1 GB: 2 workers; on Oracle A1 (4 OCPU): 3–4, with the pool at 3+2 each (still well under Neon's pooler) and `ENABLE_SCHEDULER` on in exactly one (it must not run in each worker). | S | ×N on a real CPU | Only meaningful after a migration |

### 5.1 Host migration options, all at zero cost (verify current terms before moving)

| Host | Free allowance (as commonly published; verify) | Fit | Cost or risk flag | Capacity effect |
|---|---|---|---|---|
| **Oracle Cloud Always Free (Ampere A1)** | Up to 4 OCPU and 24 GB RAM, 200 GB block storage, always on, regions include Mumbai and Hyderabad | Best: no sleep, no hour cap, real CPU, room for 3–4 workers, local Redis, even self-hosted Postgres plus pgvector (which would remove Neon's 0.5 GB and 100 CU-h limits, at the price of running backups yourself) | **A card is needed to sign up.** Stay on Always-Free shapes only. Oracle may reclaim idle free instances. A1 capacity is sometimes unavailable in a region. You run the OS and TLS (Caddy) yourself. | About 20–40× the CPU: **concurrent ~300–500, DAU ~5–10k**; Neon then becomes the limit unless Postgres moves too |
| **Google Cloud Run (free tier)** | About 180k vCPU-seconds, 360k GiB-seconds and 2M requests a month; scales to zero | Poor: CPU is throttled outside requests, so the `create_task` background persistence and the in-process scheduler starve unless "CPU always allocated", which is billed | **Billing account and card required; easy to overspend.** `infra/cloudrun.yaml` was already retired for this reason (`render.yaml:5-8`) | Up to about 1 vCPU per request while it lasts; cold starts remain |
| **Koyeb (free instance)** | 1 instance, 512 MB, 0.1 vCPU, Frankfurt or Washington | No gain over Render; farther from India | Free terms have changed before | About the same as now, with more latency to India |
| **Fly.io** | No free tier for new organisations (pay-as-you-go since 2024) | — | **Costs money: excluded** | — |
| **Hugging Face Spaces (Docker, CPU basic)** | About 2 vCPU and 16 GB, sleeps after about 48 h idle, US region | Workable for a demo, not for an India-facing API | Public Space; ToS and uptime not meant for production APIs | About 10–20× the CPU, but about 250 ms from India |

**Recommendation.**
1. Do ranks 1–8 on Render now. They are all S/M, all free, and they roughly triple to quintuple the CPU-bound DAU.
2. Check Neon's CU-hours this month.
3. If the product grows past about 500 DAU, migrate the API to Oracle Always Free A1 (Mumbai or Hyderabad), keeping Neon at first, and later consider Postgres on the same VM.

---

## Appendix A: briefing query breakdown (`services/copilot.py:844-924`)

| Producer | Queries | Evidence |
|---|---|---|
| `recommend_next_action` | 3 (more in a rare fallback branch) | `services/profiles.py:58-68`, `services/applications.py:197` |
| Outreach `due_items` | 2 | `services/outreach/touches.py:287-291` |
| `pack_counts` | 1–2 | `services/outreach/pack.py:203-208` |
| No-pack branch: target, reached today, suggestions | 4, plus about 7–9 on a cache miss | `touches.py:297, 356`; `services/outreach/suggest.py:516-540, 640-641` |
| Events | 1–2 | `services/copilot.py:471, 542` |
| `day_agenda` | 1 | `services/applications.py:281` |
| Practice deadline | 1 | `services/copilot.py:664` |
| Practice suggestion | 1–4 | `services/practice/daily.py:226-257` |
| Applications | 1 | `services/copilot.py:215` |
| Interview | 0–1 | `services/copilot.py:591` |
| `active_dismissals` | 1, read twice per briefing | `services/agent_memory.py:164` |

## Appendix B: how the local measurements were taken

- Postgres 16 in `/var/tmp` on port 5433; `alembic upgrade head` (migration 010 skips itself without pgvector).
- Seed: 300 companies and 20,000 jobs. A quarter each came from `greenhouse:`, `lever:`, `ashby:` and `cutshort`, all located in Bengaluru, India, `last_verified_at = now()`.
- API: `uvicorn api.main:app --workers 1`, whose shell PID was placed in `/sys/fs/cgroup/cpu/trcap` (quota 10 ms per 100 ms) and `/sys/fs/cgroup/memory/trcap` (512 MB).
- Env: `VECTOR_BACKEND=none`, `ENABLE_SCHEDULER=false`, a local Redis, and a dummy `GROQ_API_KEY`. No Groq call or outside request was made by the benchmarked endpoints.
- `RATE_LIMIT_*` was raised for the saturation runs.
- Load generator: `hey` 0.1.4 on the same host.
- The ONNX timings came from a separate Python process in the same CPU cgroup, with the model downloaded from Chroma's public S3 bucket.

## Sources for third-party limits

- Neon free plan limits: https://neon.com/faqs/free-plan-limits-and-quotas and https://neon.com/docs/introduction/plans
- Groq rate limits: https://console.groq.com/docs/rate-limits
- Render free tier: https://render.com/docs/free
