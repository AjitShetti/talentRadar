# TalentRadar

TalentRadar is an AI-powered job intelligence platform that ingests job postings, extracts structured signals via LLMs, and answers natural-language queries using Retrieval-Augmented Generation (RAG). It provides semantic job search, market trend analysis, and intelligent candidate matching.

## Key Features

- **Semantic Job Search**: Natural language queries powered by vector embeddings stored in PostgreSQL via pgvector.
- **AI-Powered Insights**: LLM-generated summaries and market analysis using Groq (Llama 3.1).
- **Smart Candidate Matching**: ML scoring based on skills, seniority, and location.
- **Real-Time Market Trends**: Skill demands, salary insights, geographic distribution.
- **Automated Data Pipeline**: Celery Beat-driven ingestion from multiple sources.

## Tech Stack

- **Language**: Python 3.11+
- **Framework**: FastAPI (Backend), Next.js 14 (Frontend)
- **Database**: PostgreSQL 15 — relational tables and vector embeddings (pgvector)
- **Background Jobs**: Celery & Redis 7
- **AI/ML**: LangGraph, LangChain, Groq, Sentence Transformers
- **Styling**: Tailwind CSS, Lucide Icons
- **Deployment**: Docker, Google Cloud Run

## Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development without Docker)
- Node.js (if developing frontend locally)
- API Keys:
  - Groq API Key
  - Tavily API Key

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/talentRadar.git
cd talentRadar
```

### 2. Environment Setup

Copy the example environment file:

```bash
cp .env.example .env
```

Configure the following variables in `.env`:

| Variable | Description | Example |
| --- | --- | --- |
| `GROQ_API_KEY` | LLM parsing and generation | `gsk_...` |
| `TAVILY_API_KEY` | Job posting search and scraping | `tvly-...` |
| `JWT_SECRET_KEY` | Secret key for signing JWT tokens | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

### 3. Start Development Server with Docker (Recommended)

Start all services including the API, Frontend, Database, Cache, Vector DB, and Celery workers:

```bash
docker-compose up -d
```

### 4. Database Setup

If running via Docker, the database is automatically started. Run migrations to setup the schema:

```bash
docker exec talentradar-api alembic upgrade head
```

### 5. Access the Application

- **Frontend**: http://localhost:3000
- **API Documentation**: http://localhost:8000/docs
- **API Health Check**: http://localhost:8000/health

## Architecture

### Directory Structure

```
talentRadar/
├── agents/                  # AI agent layer (LangGraph, Prompts)
├── api/                     # REST API (FastAPI)
│   ├── routers/             # Endpoint handlers
│   ├── schemas/             # Pydantic request/response models
│   ├── main.py              # FastAPI application
├── config/                  # Application settings
├── data/                    # Shared data directory
├── frontend/                # Next.js web app
│   ├── app/                 # Pages (App Router)
│   ├── components/          # React components
│   └── lib/                 # API client, types, utils
├── infra/                   # Infrastructure (Docker, K8s, Cloud Run)
├── ingestion/               # Data pipeline (Scrapers, Celery Tasks)
├── ml/                      # Machine Learning scoring models
├── storage/                 # Data layer (SQLAlchemy, Alembic)
└── tests/                   # Test suite (pytest)
```

### Data Flow

```
User Query -> Next.js Frontend -> FastAPI Endpoint -> LangGraph Agent -> pgvector (Retrieval) / Groq LLM (Generation) -> Response -> Frontend
```

### Key Components

**API Server**
- Built with FastAPI for high performance.
- Uses Pydantic for validation and serialization.

**AI Agents**
- Orchestrated using LangGraph.
- Agents include Intent Classification, RAG, Market Trend Analysis, and ML-powered job matching.

**Data Ingestion Pipeline**
- Powered by Celery and Redis.
- Scrapes data via Tavily and parses job descriptions using LLMs to extract structured data.

## Environment Variables

### Required

| Variable | Description | Default |
| --- | --- | --- |
| `GROQ_API_KEY` | Groq API Key | - |
| `TAVILY_API_KEY` | Tavily API Key | - |
| `POSTGRES_USER` | PostgreSQL user | `talentRadar` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `devpassword` |
| `POSTGRES_DB` | PostgreSQL database name | `talentRadar` |
| `JWT_SECRET_KEY` | JWT signing secret | - |

Hosted Postgres hands out one DSN rather than five fields: set `DATABASE_URL`
and it wins over the `POSTGRES_*` values above.

### Optional

| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_URL` | Full Postgres DSN; overrides the discrete fields | - |
| `POSTGRES_SSL` | Require TLS on the database connection | `false` |
| `VECTOR_BACKEND` | `pgvector`, `chroma`, or `none` (no embeddings loaded) | `pgvector` |
| `TRUSTED_PROXY_HOPS` | Proxies in front of the API; **must be 1 behind a PaaS** | `0` |
| `ENABLE_STEALTH_SCRAPERS` | Headless-browser scrapers (needs `.[stealth]`) | `false` |
| `PDF_ENGINE` | `auto`, `latex`, or `builtin` resume PDF rendering | `auto` |
| `ENABLE_SCHEDULER` | In-process daily job-match scan | `true` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `DEBUG` | Opens `/docs` and the localhost CORS wildcard — never in production | `false` |
| `RATE_LIMIT_PER_MINUTE` | Max requests per minute per IP | `60` |

See [.env.example](.env.example) for the full annotated list.

## Available Scripts

| Command | Description |
| --- | --- |
| `docker-compose up -d` | Start full local development stack |
| `docker exec talentradar-api alembic upgrade head` | Run database migrations |
| `pytest tests/ -v` | Run all tests |
| `ruff format .` | Format codebase |

## Testing

Install development dependencies:

```bash
pip install -e ".[dev,lint,docs]"
```

Run tests:

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=. --cov-report=html

# End-to-end pipeline test
python tests/test_pipeline_e2e.py --quick
```

## Deployment

**[DEPLOY.md](DEPLOY.md) is the runbook.** The target is a stack that costs
nothing: Neon (Postgres + pgvector), Render (the API, from `render.yaml`),
Vercel (the frontend), Groq (the LLM). Check readiness before you start:

```bash
python -m scripts.verify_deploy    # settings, DB, pgvector, migrations, model
```

### Docker

```bash
docker build -t talentradar-api -f infra/Dockerfile .
docker run -p 8000:8000 --env-file .env talentradar-api
```

The default image deliberately leaves out PyTorch, TeX Live and the Camoufox
browser — together well over two gigabytes, and none of them required: the
semantic scorer runs the same model under ONNX, resume PDFs render through
PyMuPDF, and the browser-driven scrapers are off. Add any of them back with
`--build-arg INSTALL_SEMANTIC=true`, `INSTALL_TEXLIVE=true`,
`INSTALL_STEALTH=true`.

## Troubleshooting

### Database Connection Issues

**Error:** `could not connect to server: Connection refused`

**Solution:**
1. Verify PostgreSQL container is running: `docker ps`
2. Check network configuration in `docker-compose.yml`.

### Missing Dependencies

**Error:** `ModuleNotFoundError: No module named '...'`

**Solution:**
Ensure you have installed all dependencies inside your virtual environment or Docker container:
```bash
pip install -e .
```
