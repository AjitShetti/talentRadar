# Deploying TalentRadar for free

Everything in the repository is ready to deploy. What is left is the part only
you can do: creating accounts, pasting keys, and pressing the button.

The target stack, all on genuine free tiers:

| Piece | Host | Free-tier reality |
|---|---|---|
| Postgres + pgvector | **Neon** (or Supabase) | 0.5 GB storage, project sleeps when idle |
| API (FastAPI, Docker) | **Render** free web service | 512 MB RAM, sleeps after 15 min idle, ~30–60 s cold start |
| Frontend (Next.js) | **Vercel** (or a second Render service) | Hobby plan, always warm |
| LLM | **Groq** | Free API tier, rate limited |

Nothing here needs a card.

---

## What changed to make this possible

The audit that preceded this work found four things standing between the repo
and a free deploy. All four are now handled in code:

1. **ChromaDB had no free managed host.** Embeddings moved into Postgres via
   pgvector — see `storage/migrations/versions/010_pgvector_job_embeddings.py`
   and `ingestion/embeddings/pgvector_store.py`. There is no second database
   and no persistent volume any more. `VECTOR_BACKEND=chroma` still works if
   you self-host one.
2. **The image was far too large.** Measured on this machine: **7.69 GB
   before, 1.21 GB now.** PyTorch, TeX Live and the whole browser stack are
   out of the default build — the semantic scorer falls back to the same
   MiniLM weights under ONNX Runtime, resume PDFs render through PyMuPDF
   (`api/utils/pdf_renderer.py`), and scraping falls back to httpx with
   browser-like headers. Each is a build argument away.
3. **The deployment target was not free.** `infra/cloudrun.yaml` — an
   always-on billed instance — is deleted, along with the workflow job that
   deployed it on every push to main. `render.yaml` replaces it.
4. **The stealth scrapers were a memory and a legal exposure.**
   `ENABLE_STEALTH_SCRAPERS` defaults to false, so a public deployment does
   not launch headless browsers or impersonate job boards. Live search keeps
   working on the remaining sources.

Two smaller open items are handled by `render.yaml` rather than by you:
`TRUSTED_PROXY_HOPS=1` is set explicitly, and `JWT_SECRET_KEY` is generated
fresh by Render instead of inheriting a development value.

---

## 1. Database (Neon)

1. Create a project at [neon.tech](https://neon.tech). Pick a region near
   your users (`ap-southeast-1` for India).
2. Copy the **connection string**. It looks like:

   ```
   postgresql://user:password@ep-something.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```

   Keep it as-is. `config/settings.py` rewrites the driver prefix per use
   (asyncpg for the app, psycopg2 for Alembic) and strips `sslmode` for
   asyncpg, which does not understand it; TLS is requested through
   `connect_args` instead.

3. Run the migrations once, from your machine:

   ```bash
   export DATABASE_URL='postgresql://…?sslmode=require'
   export POSTGRES_SSL=true
   export GROQ_API_KEY=…            # Alembic loads the same Settings as the app
   export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
   alembic upgrade head
   ```

   Migration 010 runs `CREATE EXTENSION IF NOT EXISTS vector` and creates
   `job_embeddings` with an HNSW cosine index. On a Postgres without pgvector
   it logs and skips instead of failing — semantic search then uses the
   relational fallback, and everything else works.

   > Render's `preDeployCommand` is a paid feature, which is why this is a
   > manual step rather than part of the deploy.

   **Use the pooled (`-pooler`) endpoint.** It is the right choice on a free
   tier — Neon's pooler is what keeps a handful of app connections from
   exhausting the project's budget — and the usual objection does not apply
   here: asyncpg's prepared-statement cache is what normally breaks against a
   transaction-mode pooler, and 32 concurrent vector searches through Neon's
   pooler ran clean. No `statement_cache_size=0` workaround is needed.

4. Check it:

   ```bash
   python -m scripts.verify_deploy
   ```

   Verified against a live Neon project (PostgreSQL 18.6, pgvector 0.8.6):
   all 11 migrations applied, the HNSW index built, and a write/search round
   trip returned the semantically correct top hit for each query, with JSONB
   metadata filters working. The production image was then run against that
   same database — signup, login and the agent graph all answered, and the
   `/health` endpoint came up clean.

   That script is the pre-flight for everything below: settings load, database
   reachable over TLS, pgvector present, migrations applied, embedding model
   loads at the width the schema expects, and the deployment-shape settings
   (proxy hops, docs, connection budget) are right. Exit code 0 means
   deployable.

## 2. API (Render)

1. Push this branch to GitHub.
2. In Render: **New → Blueprint**, point it at the repository. It reads
   `render.yaml` and creates `talentradar-api` and `talentradar-web`.
3. Render prompts for the values marked `sync: false`:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string |
   | `GROQ_API_KEY` | from [console.groq.com](https://console.groq.com) |
   | `TAVILY_API_KEY` | from [tavily.com](https://tavily.com), or blank |
   | `GITHUB_TOKEN` | a public-read PAT, or blank |
   | `CORS_ORIGINS` | the frontend's URL, e.g. `https://talentradar-web.onrender.com` |

   `JWT_SECRET_KEY` is generated by Render. Everything else — `POSTGRES_SSL`,
   `TRUSTED_PROXY_HOPS`, `VECTOR_BACKEND`, the pool sizes, `DEBUG=false` — is
   already set in the blueprint.

4. The first build takes several minutes and produces a ~1.2 GB image. It
   bakes the ONNX embedding model in, so the first search does not have to
   download it onto an ephemeral filesystem — verified by embedding text
   inside the built container with `--network none`.

5. Verify: `curl https://talentradar-api.onrender.com/health`.

   The first request after idle takes 30–60 seconds — the free plan stops the
   instance, it is not broken.

## 3. Frontend

**On Vercel** (recommended — it is better at Next.js and does not sleep):

1. **Add New → Project**, import the repo, set the root directory to
   `frontend/`.
2. Set `NEXT_PUBLIC_API_URL` to the Render API URL. This is inlined into the
   client bundle at build time, so it must be set *before* the build, not
   after.
3. Deploy, then put the resulting Vercel URL into the API's `CORS_ORIGINS` and
   redeploy the API. Until you do, the browser blocks every response.

**Or on Render**: the blueprint already defines `talentradar-web`, which
builds `frontend/Dockerfile` and wires `NEXT_PUBLIC_API_URL` to the API
service automatically. Nothing else to do.

## 4. Seed some data

An empty database is a working but empty app. Either:

```bash
python -m ingestion.seed_db          # fixture postings
python scripts/run_ingestion.py      # live ATS discovery (Greenhouse/Lever/Ashby/Cutshort)
```

or sign in and use the ingest endpoint, which is admin-only now
(`POST /api/v1/ingest/trigger`).

---

## Knobs worth knowing

| Variable | Default | What it buys |
|---|---|---|
| `VECTOR_BACKEND` | `pgvector` | `none` skips the embedding model entirely — the cheapest way to fit a memory-constrained instance. `chroma` for a self-hosted server. |
| `ENABLE_STEALTH_SCRAPERS` | `false` | Naukri/Indeed/Instahyre via a headless browser. Needs `pip install .[stealth]` and far more than 512 MB. Without that extra, scraping uses httpx with browser-like headers. |
| `PDF_ENGINE` | `auto` | `latex` to require pdflatex; `builtin` to force PyMuPDF even where TeX exists. |
| `ENABLE_SCHEDULER` | `true` | The daily match scan. Set false on every replica but one if you scale out. |
| `TRUSTED_PROXY_HOPS` | `0` | Must be `1` behind any PaaS router, or all users share one rate-limit bucket. `render.yaml` sets it. |
| `DEBUG` | `false` | `true` opens `/docs` and the localhost CORS wildcard. Never in production. |

### Build arguments

```bash
docker build -f infra/Dockerfile \
  --build-arg INSTALL_TEXLIVE=true \    # pdflatex resume rendering (+~1 GB)
  --build-arg INSTALL_SEMANTIC=true \   # PyTorch sentence-transformers (+~1 GB)
  --build-arg INSTALL_STEALTH=true \    # scrapling + playwright + Camoufox (+~300 MB)
  .
```

All three default to `false`.

---

## If something is wrong

| Symptom | Cause |
|---|---|
| Container exits immediately, no logs | `JWT_SECRET_KEY` missing or under 32 characters — `Settings()` raises at import. |
| Every browser request fails, `curl` works | `CORS_ORIGINS` does not list the frontend's exact origin (scheme included). |
| Frontend calls `localhost:8000` | `NEXT_PUBLIC_API_URL` was set after the build. Rebuild. |
| Search returns results but they feel unranked | No embeddings — check `job_embeddings` has rows; the relational fallback is answering. |
| `429` under light load | `TRUSTED_PROXY_HOPS` is 0 behind a proxy. |
| Resume PDF export returns LaTeX only | The renderer raised; check the logs. Three of the four call sites degrade this way by design. |

## What is still worth doing later

Deliberately not done here, because none of it blocks a free launch:

- **Redis for the rate limiter and the SSE task map.** Both are in-process,
  which is why the image runs one worker. Moving them is what unlocks scaling
  out — along with `ENABLE_SCHEDULER=false` on every replica but one.
- **Token revocation.** Tokens last 7 days, live in `localStorage`, and there
  is no refresh or revocation path. Fine for a launch; worth a short-lived
  token plus refresh before the user count grows.
- **Turning mypy into a gate.** It runs in CI but does not fail the build;
  strict mode still reports a large annotation backlog.
