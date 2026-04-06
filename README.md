# TalentRadar

A multi-agent job intelligence platform that continuously ingests job postings, extracts structured signals from JDs using an LLM, and answers natural language queries about the job market using a RAG pipeline backed by a vector store and relational database.

Built to demonstrate: Airflow pipelines, LLM API integration, RAG with reranking, LangGraph multi-agent routing, FastAPI REST design, ML-based scoring, and cloud deployment on Google Cloud Run.

---

## What it does

TalentRadar solves a concrete problem: job postings are unstructured, scattered across platforms, and go stale fast. Manually reading 50 JDs a day to find relevant roles is slow. TalentRadar automates this by:

1. Collecting fresh job postings every 6 hours via Airflow + Tavily API
2. Parsing each JD using Groq LLM to extract skills, experience, location, and role type as structured JSON
3. Storing structured metadata in PostgreSQL and semantic embeddings in ChromaDB
4. Routing natural language queries through a LangGraph agent graph to the right handler
5. Scoring resume-to-JD compatibility using cosine similarity on sentence-transformer embeddings

Example queries the system can answer:
- "Which Bangalore companies are hiring ML Engineers right now?"
- "What skills are trending in GCC data engineering roles this month?"
- "How well does my resume match this job description?"

---

## Architecture overview

```
External sources (Tavily API)
        │
        ▼
Ingestion layer (Airflow DAG)
  ├── Scrape raw JD text
  ├── Parse with Groq LLM → structured JSON
  └── Validate with Pydantic
        │
        ▼
Storage layer
  ├── PostgreSQL — structured metadata (title, company, skills[], location, salary, posted_at)
  └── ChromaDB   — 384-dim sentence-transformer embeddings (linked via embedding_id)
        │
        ▼
Agent layer (LangGraph)
  ├── Orchestrator — classifies query intent: search | trend | match
  ├── RAG agent    — embed query → ChromaDB retrieval → rerank → Groq generate
  ├── Trend agent  — PostgreSQL GROUP BY skill COUNT → Groq summarise
  └── ML scorer    — cosine similarity: resume skills vs JD skills → score 0–100
        │
        ▼
FastAPI REST layer
  ├── POST /api/v1/query       — natural language query → LangGraph graph
  ├── GET  /api/v1/search      — filtered JD list
  ├── GET  /api/v1/trends      — skill aggregation by role and location
  ├── POST /api/v1/recommend   — resume match scoring
  ├── POST /api/v1/ingest/trigger — manually trigger ingestion DAG
  └── GET  /api/v1/ingest/status  — last run stats
        │
        ▼
Frontend (Next.js 14)
  └── Search, trends, and match score UI — calls FastAPI via lib/api.ts
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Ingestion scheduler | Apache Airflow 2.x (Docker) |
| Job data fetching | Tavily Search API |
| LLM parsing + generation | Groq API (llama-3.1-70b-versatile) |
| Structured storage | PostgreSQL 15 + SQLAlchemy + Alembic |
| Vector storage | ChromaDB |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2, 384 dims) |
| Reranking | cross-encoder/ms-marco-MiniLM-L-6-v2 |
| Agent orchestration | LangGraph |
| ML scorer | cosine similarity / XGBoost (optional) |
| RAG evaluation | RAGAS |
| REST API | FastAPI + Pydantic v2 |
| Auth | JWT (python-jose) |
| Rate limiting | slowapi |
| Task queue | Redis + Celery |
| Raw blob storage | Google Cloud Storage |
| Frontend | Next.js 14 (App Router) + Tailwind CSS |
| Observability | structlog + Prometheus |
| Containerisation | Docker + Docker Compose |
| Cloud deployment | Google Cloud Run + Artifact Registry |
| CI/CD | GitHub Actions |

---

## Folder structure

```
TalentRadar/
├── ingestion/
│   ├── dags/                  # Airflow DAG definitions
│   │   └── fetch_and_parse_dag.py
│   ├── scrapers/
│   │   └── tavily_client.py   # fetch_jobs(query, location) → raw text
│   ├── parsers/
│   │   ├── jd_parser.py       # parse_jd(raw_text) → structured JSON via Groq
│   │   └── schemas.py         # Pydantic models: JobPosting, ParsedJD
│   └── embeddings/
│       ├── embedder.py        # embed_jd(text) → 384-dim vector
│       └── chroma_store.py    # upsert/query ChromaDB collection
├── storage/
│   ├── database.py            # SQLAlchemy engine + session factory
│   ├── models.py              # Job, Company, IngestionRun ORM models
│   ├── repository.py          # CRUD: save_job(), get_jobs_by_filter()
│   └── migrations/            # Alembic migration files
├── agents/
│   ├── graph.py               # LangGraph graph: nodes + edges
│   ├── state.py               # AgentState: query, intent, context, answer
│   ├── orchestrator.py        # classify_intent() → search | trend | match
│   ├── rag_agent.py           # retrieve → rerank → generate
│   ├── trend_agent.py         # PostgreSQL aggregation + Groq summarise
│   ├── ml_scorer.py           # cosine match score
│   └── prompts/               # system prompts as Python constants
├── api/
│   ├── main.py                # FastAPI app, lifespan, middleware
│   ├── dependencies.py        # get_db(), get_current_user()
│   ├── auth.py                # JWT encode/decode
│   ├── routers/               # one file per endpoint group
│   └── schemas/               # request/response Pydantic models
├── ml/
│   ├── scorer.py              # cosine_score(resume_skills, jd_skills) → 0–100
│   ├── xgboost_model.py       # optional: train(), predict()
│   └── evaluation.py          # precision, recall, F1
├── frontend/
│   ├── app/                   # Next.js 14 App Router pages
│   ├── components/            # SearchBar, JobCard, SkillChart, MatchScoreCard
│   └── lib/
│       ├── api.ts             # all fetch calls to FastAPI
│       └── types.ts           # TypeScript types mirroring API schemas
├── infra/
│   ├── Dockerfile             # multi-stage FastAPI build
│   ├── Dockerfile.airflow     # Airflow worker image
│   └── cloudrun.yaml          # Cloud Run service config
├── tests/
│   ├── conftest.py            # shared fixtures
│   ├── test_ingestion.py
│   ├── test_rag.py
│   ├── test_agents.py
│   ├── test_api.py
│   └── eval/
│       ├── eval_rag.py        # RAGAS evaluation script
│       └── eval_dataset.json  # 20 question-answer pairs
├── config/
│   ├── settings.py            # Pydantic BaseSettings — loads from .env
│   └── logging.py             # structlog JSON formatter
├── docs/
│   ├── architecture.png
│   ├── data_flow.png
│   └── logic_flow.png
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

---

## Local setup

### Prerequisites

- Docker and Docker Compose installed
- Python 3.11+
- Node.js 18+ (for frontend)
- A Groq API key (free tier works): https://console.groq.com
- A Tavily API key (free tier works): https://tavily.com

### 1. Clone the repo

```bash
git clone https://github.com/yourusername/TalentRadar.git
cd TalentRadar
```

### 2. Set environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
# LLM
GROQ_API_KEY=your_groq_api_key

# Data fetching
TAVILY_API_KEY=your_tavily_api_key

# PostgreSQL
POSTGRES_USER=talentRadar
POSTGRES_PASSWORD=your_password
POSTGRES_DB=talentRadar
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Redis
REDIS_URL=redis://redis:6379/0

# ChromaDB
CHROMA_HOST=chromadb
CHROMA_PORT=8000

# JWT
JWT_SECRET_KEY=your_secret_key_min_32_chars
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=60

# GCS (optional — for raw blob storage)
GCS_BUCKET_NAME=talentRadar-raw-jds
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 3. Start all services

```bash
docker compose up --build
```

This starts: PostgreSQL, Redis, ChromaDB, FastAPI, Airflow scheduler + worker, and the Next.js frontend.

| Service | URL |
|---|---|
| FastAPI (API + docs) | http://localhost:8000/docs |
| Airflow UI | http://localhost:8080 |
| Frontend | http://localhost:3000 |
| ChromaDB | http://localhost:8001 |

### 4. Run database migrations

```bash
docker compose exec api alembic upgrade head
```

### 5. Trigger first ingestion manually

```bash
curl -X POST http://localhost:8000/api/v1/ingest/trigger \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "ML Engineer", "location": "Bangalore"}'
```

Or open the Airflow UI at http://localhost:8080 and trigger `fetch_and_parse_dag` manually.

---

## API reference

All endpoints require a JWT token in the `Authorization: Bearer <token>` header except `/auth/login` and `/health`.

### Authentication

```bash
POST /auth/login
Content-Type: application/json

{"username": "admin", "password": "your_password"}
```

Returns: `{"access_token": "...", "token_type": "bearer"}`

---

### Query — natural language search

```bash
POST /api/v1/query
Content-Type: application/json

{
  "question": "Find backend roles in Bangalore needing Docker but not 5 years experience",
  "filters": {
    "location": "Bangalore",
    "max_experience": 4
  }
}
```

Response:
```json
{
  "answer": "Found 12 backend roles in Bangalore requiring Docker...",
  "sources": ["job_id_1", "job_id_2"],
  "intent": "search",
  "confidence": 0.91
}
```

---

### Search — structured filter

```bash
GET /api/v1/search?skills=python,docker&location=bangalore&max_experience=3&limit=20
```

Response: list of matching `JobPosting` objects with title, company, skills, salary, posted_at.

---

### Trends — skill aggregation

```bash
GET /api/v1/trends?role=ml-engineer&location=bangalore&days=30
```

Response:
```json
{
  "top_skills": [
    {"skill": "Python", "count": 148, "rank": 1},
    {"skill": "PyTorch", "count": 97, "rank": 2}
  ],
  "insight": "Python and PyTorch dominate ML Engineer JDs in Bangalore this month...",
  "period_days": 30,
  "total_jds_analysed": 312
}
```

---

### Recommend — resume match scoring

```bash
POST /api/v1/recommend
Content-Type: application/json

{
  "resume_skills": ["Python", "Airflow", "PostgreSQL", "Docker", "LangChain"],
  "top_k": 10
}
```

Response: list of `JobPosting` objects ranked by match score (0–100).

---

### Ingest

```bash
POST /api/v1/ingest/trigger    # trigger a DAG run
GET  /api/v1/ingest/status     # last run: rows fetched, errors, duration
GET  /health                   # API + DB + ChromaDB ping
```

---

## Data flow

A single job posting goes through this pipeline:

```
Tavily API fetch (raw JD text)
        │
        ▼
Save raw blob → GCS (unprocessed backup)
        │
        ▼
Groq LLM parser
  prompt: extract title, company, skills[], experience, location, salary as JSON
        │
        ▼
Pydantic validation (type coercion, schema check)
        │
        ├──────────────────────────────┐
        ▼                              ▼
PostgreSQL insert              Sentence transformer
(structured metadata row)      (all-MiniLM-L6-v2)
                                       │
                                       ▼
                               ChromaDB upsert
                               (384-dim vector + embedding_id)
                                       │
                               ← embedding_id written back to PostgreSQL row
```

The `embedding_id` link between PostgreSQL and ChromaDB is what enables hybrid queries — structured filters (location, salary, date) applied in Postgres, semantic similarity applied in ChromaDB.

---

## Agent logic flow

When a user sends a query to `POST /api/v1/query`:

```
User query
    │
    ▼
JWT auth middleware
    │
    ▼
Orchestrator node (LangGraph)
  → calls Groq to classify intent: "search" | "trend" | "match"
    │
    ├── search ──► RAG agent
    │                 embed query (sentence-transformer)
    │                 → ChromaDB top-k retrieval
    │                 → cross-encoder reranker
    │                 → Groq generate (context = top JDs)
    │
    ├── trend ───► Trend agent
    │                 PostgreSQL GROUP BY skill, COUNT(*)
    │                 → Groq summarise top skills
    │
    └── match ───► ML scorer
                      embed resume skills
                      → cosine similarity vs all JD embeddings
                      → rank by score 0–100
    │
    ▼
JSON response assembled: {answer, sources, intent, confidence}
    │
    ▼
Returned to client
```

The orchestrator is not hardcoded `if/else` — it calls Groq with a classification prompt. Low-confidence classifications fall back to asking the user to clarify.

---

## RAG pipeline details

The RAG pipeline inside the RAG agent has four steps:

1. **Embed** — user query is embedded using the same model as the JD corpus (all-MiniLM-L6-v2)
2. **Retrieve** — ChromaDB returns top-10 most similar JDs by cosine similarity
3. **Rerank** — cross-encoder (ms-marco-MiniLM-L-6-v2) reranks the top-10 to top-5 for higher precision
4. **Generate** — Groq LLM receives the top-5 JDs as context and generates a grounded answer

RAGAS evaluation is run offline using `tests/eval/eval_rag.py` against a 20-pair question-answer dataset. Metrics tracked: faithfulness, answer relevancy, context precision.

---

## ML scorer details

Resume-to-JD match scoring works as follows:

- Both resume skills and JD skills are embedded as sentence-transformer vectors
- Cosine similarity is computed between the two vectors
- Score is normalised to 0–100

An optional XGBoost classifier can replace the cosine scorer. It is trained on synthetic (resume_skills, jd_skills, label) pairs where label=1 means a strong match. It is only used if F1 > 0.75 on the held-out eval set.

---

## Running tests

```bash
# All tests
docker compose exec api pytest tests/ -v

# With coverage
docker compose exec api pytest tests/ --cov=. --cov-report=term-missing

# RAG evaluation only (separate from pytest — runs RAGAS metrics)
docker compose exec api python tests/eval/eval_rag.py
```

---

## Deployment

### Google Cloud Run

```bash
# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Build and push image
docker build -f infra/Dockerfile -t gcr.io/YOUR_PROJECT_ID/talentRadar-api .
docker push gcr.io/YOUR_PROJECT_ID/talentRadar-api

# Deploy
gcloud run deploy talentRadar-api \
  --image gcr.io/YOUR_PROJECT_ID/talentRadar-api \
  --platform managed \
  --region asia-south1 \
  --min-instances 1 \
  --memory 512Mi \
  --set-env-vars GROQ_API_KEY=...,JWT_SECRET_KEY=...
```

Environment variables in production are set via Cloud Run secrets — never hardcoded in the image.

### CI/CD (GitHub Actions)

On every push to `main`:

1. `pytest` runs against the test suite
2. Docker image is built and pushed to Google Artifact Registry
3. Cloud Run service is redeployed with the new image

The workflow file is at `.github/workflows/deploy.yml`.

### Frontend deployment (Vercel)

```bash
cd frontend
vercel --prod
```

Set `NEXT_PUBLIC_API_URL` to your Cloud Run service URL in Vercel environment settings.

---

## Environment variables reference

All variables are loaded via `config/settings.py` using Pydantic `BaseSettings`. The application raises a clear error on startup if any required variable is missing.

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | Groq API key for LLM calls |
| `TAVILY_API_KEY` | Yes | Tavily API key for job fetching |
| `POSTGRES_USER` | Yes | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `POSTGRES_DB` | Yes | PostgreSQL database name |
| `POSTGRES_HOST` | Yes | PostgreSQL host (use `postgres` in Docker) |
| `REDIS_URL` | Yes | Redis connection URL |
| `CHROMA_HOST` | Yes | ChromaDB host |
| `JWT_SECRET_KEY` | Yes | Min 32 chars. Used to sign JWT tokens |
| `GCS_BUCKET_NAME` | No | GCS bucket for raw JD blob storage |
| `NEXT_PUBLIC_API_URL` | Yes (frontend) | FastAPI base URL for frontend calls |

---

## Known limitations

- Tavily free tier is rate-limited to 1000 requests/month. For higher volume, swap in a custom scraper.
- ChromaDB is run in-memory in local Docker setup. For production, use ChromaDB with persistent volume or switch to Pinecone/Weaviate.
- The XGBoost scorer requires manually curated or synthetically generated training data. The cosine scorer is used by default.
- Airflow is run in LocalExecutor mode. For production scale, switch to CeleryExecutor with the Redis broker already in the stack.
- No multi-tenancy. All users share the same job corpus. User-specific resume data is not persisted between sessions.

---

## For AI agents working on this codebase

**Entry points:**
- API: `api/main.py` — FastAPI app, all routers registered here
- Agent graph: `agents/graph.py` — LangGraph graph definition, start here to understand routing
- Ingestion DAG: `ingestion/dags/fetch_and_parse_dag.py` — Airflow DAG, defines the full ingestion pipeline
- Settings: `config/settings.py` — all environment variables, loaded once at startup

**Shared dependencies:**
- `storage/database.py` — SQLAlchemy session, imported by both `api/` and `ingestion/`
- `storage/repository.py` — all DB read/write operations, never write raw SQL outside this file
- `config/settings.py` — never read `os.environ` directly, always import from here

**Coding conventions:**
- All API request and response shapes are Pydantic models in `api/schemas/`
- All DB models are SQLAlchemy ORM classes in `storage/models.py`
- All prompts are constants in `agents/prompts/` — never inline prompt strings in agent files
- All ChromaDB operations go through `ingestion/embeddings/chroma_store.py`
- Tests use shared fixtures from `tests/conftest.py` — add new fixtures there, not inline

**Do not:**
- Import from `api/` inside `ingestion/` or `agents/` — dependency direction is one-way downward
- Write raw `os.environ` calls anywhere — use `config/settings.py`
- Add new endpoints without a corresponding Pydantic schema in `api/schemas/`
- Store secrets in code or Docker images — use environment variables and Cloud Run secrets

---

## License

MIT