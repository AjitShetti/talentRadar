# TalentRadar - Quick Start Guide

## 1️⃣ Install Dependencies

### Full Installation (Recommended for Development)
```bash
pip install -e ".[all]"
```

### Minimal Installation (Core Only)
```bash
pip install -e .
```

### Using UV (10-100x Faster)
```bash
# Install uv first
pip install uv

# Then use it for faster installation
uv pip install -e ".[all]"
```

---

## 2️⃣ Set Up Environment Variables

```bash
# Copy the example file
cp .env.example .env

# Edit .env and fill in your API keys:
# - GROQ_API_KEY (from https://console.groq.com)
# - TAVILY_API_KEY (from https://tavily.com)
# - JWT_SECRET_KEY (generate with: python -c "import secrets; print(secrets.token_urlsafe(48))")
```

---

## 3️⃣ Start Infrastructure (Docker)

```bash
# Start PostgreSQL, Redis, and ChromaDB
docker compose up -d

# Check services are running
docker compose ps
```

---

## 4️⃣ Run Database Migrations

```bash
# Apply all migrations
alembic upgrade head

# Check migration status
alembic current
```

---

## 5️⃣ Start the API Server

```bash
# Development server with hot-reload
uvicorn api.main:app --reload --port 8000

# Production server
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

API will be available at: http://localhost:8000
API Docs (Swagger): http://localhost:8000/docs

---

## 6️⃣ Start Airflow (for Ingestion DAGs)

```bash
# Initialize Airflow database (first time only)
airflow db migrate

# Create admin user
airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@talentradar.com

# Start Airflow scheduler and webserver
airflow scheduler
airflow webserver --port 8080
```

Airflow UI: http://localhost:8080

---

## Development Workflow

### Install Pre-commit Hooks
```bash
pre-commit install
```

Hooks will automatically run on every git commit to:
- Format code with ruff
- Run linting checks
- Type check with mypy
- Run fast tests with pytest

### Manual Code Quality Checks
```bash
# Linting
ruff check .
ruff check --fix .  # auto-fix issues

# Type checking
mypy .

# Formatting
black .
isort .

# Run tests
pytest
pytest -v  # verbose
pytest --cov=  # with coverage

# Run specific test markers
pytest -m "not slow"  # skip slow tests
pytest -m "integration"  # only integration tests
```

---

## Common Commands

### Database Management
```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic upgrade -1

# Check current migration
alembic current
```

### Testing
```bash
# All tests
pytest

# Tests with coverage
pytest --cov=. --cov-report=html

# Only fast tests
pytest -m "not slow"

# Only specific test file
pytest tests/test_api.py

# RAG evaluation
python tests/eval/eval_rag.py
```

### Code Formatting
```bash
# Format all Python files
black .
ruff check --fix .

# Check without modifying
black --check .
ruff check .
```

---

## Project Structure

```
talentRadar/
├── api/              # FastAPI REST endpoints
├── agents/           # LangGraph multi-agent system
├── ingestion/        # Airflow DAGs and scrapers
├── storage/          # SQLAlchemy models and repositories
├── ml/               # ML scoring models
├── config/           # Application settings
├── tests/            # Test suite
├── infra/            # Docker and deployment configs
└── docs/             # Documentation
```

---

## Dependency Groups

Install only what you need:

| Command | What it adds |
|---------|--------------|
| `pip install -e .` | Core runtime only (FastAPI, DB, LLM, etc.) |
| `pip install -e ".[dev]"` | + Testing tools (pytest, mock, RAGAS) |
| `pip install -e ".[lint]"` | + Linting (ruff, mypy, type stubs) |
| `pip install -e ".[format]"` | + Formatters (black, isort) |
| `pip install -e ".[docs]"` | + Doc generators (mkdocs) |
| `pip install -e ".[all]"` | **Everything combined** |

---

## Troubleshooting

### Torch Installation Fails
```bash
# Use CPU-only wheel (smaller, no GPU support)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Or skip torch if not needed yet
pip install -e . --no-deps
```

### Airflow on Windows
```bash
# Use Docker instead of native install
docker compose up airflow
```

### Database Connection Issues
```bash
# Check PostgreSQL is running
docker compose ps postgres

# View logs
docker compose logs postgres

# Reset database (DESTRUCTIVE - deletes all data)
docker compose down -v
docker compose up -d
alembic upgrade head
```

### Import Errors
```bash
# Reinstall in editable mode
pip install -e ".[all]" --force-reinstall

# Check Python path
python -c "import sys; print(sys.path)"
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | ✅ | - | Groq LLM API key |
| `TAVILY_API_KEY` | ✅ | - | Tavily search API key |
| `POSTGRES_USER` | ✅ | talentRadar | Database username |
| `POSTGRES_PASSWORD` | ✅ | devpassword | Database password |
| `POSTGRES_DB` | ✅ | talentRadar | Database name |
| `POSTGRES_HOST` | ✅ | postgres | Database host |
| `REDIS_URL` | ✅ | redis://redis:6379/0 | Redis connection URL |
| `CHROMA_HOST` | ✅ | chromadb | ChromaDB host |
| `JWT_SECRET_KEY` | ✅ | change-me... | JWT signing key (32+ chars) |
| `GCS_BUCKET_NAME` | ❌ | talentRadar-raw-jds | GCS bucket for backups |
| `NEXT_PUBLIC_API_URL` | ✅ | http://localhost:8000 | Frontend API URL |

---

## API Endpoints (Once Implemented)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/auth/login` | ❌ | Get JWT token |
| POST | `/api/v1/query` | ✅ | Natural language query |
| GET | `/api/v1/search` | ✅ | Structured job search |
| GET | `/api/v1/trends` | ✅ | Skill trend insights |
| POST | `/api/v1/recommend` | ✅ | Resume match scoring |
| POST | `/api/v1/ingest/trigger` | ✅ | Trigger ingestion DAG |
| GET | `/api/v1/ingest/status` | ✅ | Last ingestion status |
| GET | `/health` | ❌ | Health check |

---

## Useful Links

- **FastAPI Docs:** http://localhost:8000/docs
- **Airflow UI:** http://localhost:8080
- **ChromaDB:** http://localhost:8000
- **PostgreSQL:** localhost:5432
- **Redis:** localhost:6379

---

## Getting Help

1. Check `docs/DEPENDENCIES.md` for detailed dependency info
2. Check `docs/PYPROJECT_SUMMARY.md` for pyproject.toml details
3. Read the main `README.md` for architecture overview
4. Run `pytest -v` to check if tests pass
5. Check logs: `docker compose logs -f`

---

**Next Steps:** After installation, proceed with implementing the empty modules (API, agents, ingestion, ML) following the architecture documented in README.md.
