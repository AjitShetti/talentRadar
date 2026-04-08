# Dependency Installation Guide

This document explains how to install TalentRadar dependencies for different workflows.

---

## Quick Start (All Dependencies)

For a complete development environment with all dependencies:

```bash
# Using pip
pip install -e ".[all]"

# Using uv (faster)
uv pip install -e ".[all]"
```

---

## Installation by Use Case

### 1. Production Deployment (Core Only)

Minimal dependencies needed to run the application:

```bash
pip install -e .
```

This installs only the core runtime dependencies (FastAPI, SQLAlchemy, LangGraph, etc.).

---

### 2. Development Work

Adds testing, mocking, and synthetic data generation:

```bash
pip install -e ".[dev]"
```

**Includes:**
- `pytest`, `pytest-asyncio`, `pytest-cov` (test framework)
- `pytest-xdist` (parallel test execution)
- `pytest-mock`, `faker`, `factory-boy` (test utilities)
- `ragas`, `datasets` (RAG evaluation)
- `pytest-postgresql`, `testcontainers` (database test fixtures)

---

### 3. Code Quality & Linting

Adds linting, type checking, and formatting tools:

```bash
pip install -e ".[lint]"
pip install -e ".[format]"
```

**Includes:**
- `ruff` (fast linter + formatter)
- `mypy` (strict type checking)
- `types-*` packages (type stubs for third-party libs)
- `pre-commit` (git hooks management)
- `black`, `isort` (code formatting)

---

### 4. Documentation

For building local documentation:

```bash
pip install -e ".[docs]"
```

**Includes:**
- `mkdocs`, `mkdocs-material` (static site generator)
- `mkdocstrings[python]` (API doc generation)

---

### 5. Full Development Setup (Recommended)

Everything combined:

```bash
pip install -e ".[all]"
```

---

## Dependency Categories

### Core Runtime Dependencies (40 packages)

| Category | Packages | Purpose |
|----------|----------|---------|
| **Web Framework** | `fastapi`, `uvicorn` | REST API server |
| **Validation** | `pydantic`, `pydantic-settings` | Request/response schemas, config |
| **Database** | `sqlalchemy`, `asyncpg`, `psycopg2-binary`, `alembic` | PostgreSQL ORM + migrations |
| **LLM Orchestration** | `langgraph`, `langchain`, `langchain-openai`, `langchain-core` | Multi-agent routing |
| **LLM API** | `groq` | Groq LLM inference |
| **Vector Store** | `chromadb`, `sentence-transformers`, `torch` | Embeddings + semantic search |
| **Ingestion** | `tavily-python`, `beautifulsoup4`, `httpx`, `lxml` | Job scraping |
| **Scheduler** | `apache-airflow`, `apache-airflow-providers-http` | DAG orchestration |
| **Auth** | `python-jose`, `passlib`, `python-multipart` | JWT + password hashing |
| **Queue** | `celery`, `redis` | Async task processing |
| **Cloud Storage** | `google-cloud-storage` | Raw JD blob backup |
| **ML** | `xgboost`, `scikit-learn`, `numpy` | Resume matching |
| **Observability** | `structlog`, `prometheus-client` | Logging + metrics |
| **Rate Limiting** | `slowapi` | API throttling |
| **Utils** | `python-dotenv`, `html2text`, `tenacity`, `python-dateutil` | Helpers |

---

## Tool Configuration

The `pyproject.toml` includes pre-configured tool settings:

### pytest
- Auto-discovers tests in `tests/` directory
- Async mode enabled (`asyncio_mode = "auto"`)
- Coverage reporting enabled by default
- Custom markers: `slow`, `integration`, `rag_eval`

### ruff (Linter)
- Python 3.11 target
- 100 char line length
- Strict rules: bugbear, simplify, async, annotations
- Ignores FastAPI `Depends` pattern warnings

### mypy (Type Checker)
- Strict mode enabled
- Pydantic plugin enabled
- Ignores missing imports for niche libraries

### black (Formatter)
- 100 char line length
- Double quotes preferred
- Excludes migrations and cache dirs

### coverage
- 70% minimum threshold
- Excludes tests, migrations, cache
- HTML report generation

---

## Install Commands Reference

```bash
# Install core only
pip install -e .

# Install with dev tools
pip install -e ".[dev]"

# Install with linting
pip install -e ".[lint]"

# Install with formatting
pip install -e ".[format]"

# Install everything
pip install -e ".[all]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=. --cov-report=html

# Run linting
ruff check .

# Type checking
mypy .

# Format code
black .
ruff check --fix .

# Run slow tests only
pytest -m "slow"

# Run integration tests (requires DB)
pytest -m "integration"

# Run RAG evaluation
pytest -m "rag_eval"
# or directly:
python tests/eval/eval_rag.py
```

---

## Using UV (Faster Alternative)

[UV](https://github.com/astral-sh/uv) is a fast Python package installer:

```bash
# Install uv
pip install uv

# Create virtual environment
uv venv

# Install with all dependencies
uv pip install -e ".[all]"

# Install from lockfile (if exists)
uv pip sync requirements.lock
```

UV is 10-100x faster than pip for large dependency trees like this project.

---

## Troubleshooting

### Torch Installation Issues

If `torch` installation fails on Windows:

```bash
# Use pre-built wheels
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Or skip ML deps if not needed yet
pip install -e . --no-deps
pip install fastapi sqlalchemy pydantic  # install only what you need
```

### Airflow on Windows

Apache Airflow has limited Windows support. Consider:

```bash
# Use WSL2
# Or run Airflow in Docker only (recommended)
docker compose up airflow
```

### PostgreSQL Drivers

Both `asyncpg` (async) and `psycopg2-binary` (sync for Alembic) are required:
- `asyncpg`: Used by FastAPI runtime (async SQLAlchemy)
- `psycopg2-binary`: Used by Alembic migrations (sync only)

---

## Dependency Updates

To check for outdated packages:

```bash
pip list --outdated

# Or with pip-tools
pip-compile pyproject.toml --output-file requirements.txt
pip-compile pyproject.toml --extra=dev --output-file requirements-dev.txt
```

To upgrade all packages:

```bash
pip install --upgrade -e ".[all]"
```
