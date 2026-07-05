# TalentRadar

TalentRadar is an AI-powered job intelligence platform that ingests job postings, extracts structured signals via LLMs, and answers natural-language queries using Retrieval-Augmented Generation (RAG). It provides semantic job search, market trend analysis, and intelligent candidate matching.

## Key Features

- **Semantic Job Search**: Natural language queries powered by vector embeddings and ChromaDB.
- **AI-Powered Insights**: LLM-generated summaries and market analysis using Groq (Llama 3.1).
- **Smart Candidate Matching**: ML scoring based on skills, seniority, and location.
- **Real-Time Market Trends**: Skill demands, salary insights, geographic distribution.
- **Automated Data Pipeline**: Celery Beat-driven ingestion from multiple sources.

## Tech Stack

- **Language**: Python 3.11+
- **Framework**: FastAPI (Backend), Next.js 14 (Frontend)
- **Database**: PostgreSQL 15 (Relational), ChromaDB (Vector)
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
User Query -> Next.js Frontend -> FastAPI Endpoint -> LangGraph Agent -> ChromaDB (Retrieval) / Groq LLM (Generation) -> Response -> Frontend
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

### Optional

| Variable | Description | Default |
| --- | --- | --- |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `DEBUG` | FastAPI debug mode | `false` |
| `RATE_LIMIT_PER_MINUTE` | Max requests per minute per IP | `60` |

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

### Docker

Build and run manually:

```bash
docker build -t talentradar-api -f infra/Dockerfile .
docker run -p 8000:8000 --env-file .env talentradar-api
```

### Google Cloud Run

```bash
# Build and push
gcloud builds submit --tag gcr.io/YOUR_PROJECT/talentradar-api .

# Deploy
gcloud run deploy talentradar-api \
  --image gcr.io/YOUR_PROJECT/talentradar-api \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars POSTGRES_HOST=your-db,POSTGRES_PASSWORD=your-pass,GROQ_API_KEY=your-key
```

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
