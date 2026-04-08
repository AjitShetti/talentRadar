# pyproject.toml Implementation Summary

## What Was Created

A comprehensive `pyproject.toml` file for the TalentRadar project with all necessary dependencies, tool configurations, and build settings.

---

## Files Created/Modified

### 1. `pyproject.toml` (Main File)
**Status:** Created (replaced empty placeholder)

**Contents:**
- ✅ Project metadata (name, version, description, license, authors)
- ✅ **40 core runtime dependencies** organized by category
- ✅ **5 optional dependency groups** for different workflows
- ✅ **8 tool configurations** (pytest, ruff, mypy, coverage, black, isort, hatch, uv)
- ✅ Entry point for CLI usage
- ✅ Build system configuration (hatchling)

### 2. `docs/DEPENDENCIES.md` (Documentation)
**Status:** Created (new file)

**Contents:**
- Installation guides for different use cases
- Dependency category breakdown
- Tool configuration explanations
- Troubleshooting tips
- Command reference

### 3. `.python-version`
**Status:** Created (new file)

**Contents:**
- Specifies Python 3.11 as the target version

### 4. `.pre-commit-config.yaml`
**Status:** Created (new file)

**Contents:**
- Pre-commit hooks for:
  - Trailing whitespace removal
  - YAML/TOML validation
  - Private key detection
  - Ruff linting + formatting
  - mypy type checking
  - Fast pytest runs (non-slow tests only)

### 5. `.env.example`
**Status:** Updated (comprehensive rewrite)

**Contents:**
- All required environment variables with documentation
- Sections for: LLM, PostgreSQL, Redis, ChromaDB, JWT, GCS, Frontend, Logging, Rate Limiting, Airflow, Development
- Default values and usage hints

---

## Dependency Breakdown

### Core Runtime (40 packages)

| Category | Count | Key Packages |
|----------|-------|--------------|
| Web Framework | 2 | fastapi, uvicorn |
| Validation | 3 | pydantic, pydantic-settings, email-validator |
| Database | 4 | sqlalchemy, asyncpg, psycopg2-binary, alembic |
| LLM Orchestration | 4 | langgraph, langchain, langchain-openai, langchain-core |
| LLM API | 1 | groq |
| Vector Store | 3 | chromadb, sentence-transformers, torch |
| Ingestion | 4 | tavily-python, beautifulsoup4, httpx, lxml |
| Scheduler | 2 | apache-airflow, apache-airflow-providers-http |
| Authentication | 3 | python-jose, passlib, python-multipart |
| Task Queue | 2 | celery, redis |
| Cloud Storage | 1 | google-cloud-storage |
| Machine Learning | 3 | xgboost, scikit-learn, numpy |
| Observability | 2 | structlog, prometheus-client |
| Rate Limiting | 1 | slowapi |
| Utilities | 5 | python-dotenv, html2text, tenacity, python-dateutil |

### Optional Dependencies (by group)

| Group | Count | Purpose |
|-------|-------|---------|
| **dev** | 13 | Testing, mocking, RAG evaluation |
| **lint** | 7 | Linting, type checking, pre-commit |
| **format** | 2 | Code formatting (black, isort) |
| **docs** | 3 | Documentation generation |
| **all** | - | Meta-group combining all above |

---

## Tool Configurations Included

### 1. pytest
```toml
- Test discovery: tests/ directory
- Async mode: auto
- Coverage: enabled by default
- Markers: slow, integration, rag_eval
- Output: verbose, strict markers
```

### 2. ruff (Linter + Formatter)
```toml
- Target: Python 3.11
- Line length: 100 chars
- Rules: E, W, F, I, N, UP, B, SIM, TCH, RUF, ANN, ASYNC
- Ignores: FastAPI Depends pattern (B008), self/cls annotations (ANN101/102)
- Import ordering: stdlib → third-party → first-party → local
```

### 3. mypy (Type Checker)
```toml
- Mode: strict
- Requirements: all functions typed, no implicit optional
- Plugin: pydantic.mypy
- Overrides: ignores missing imports for niche libraries
```

### 4. coverage
```toml
- Minimum: 70%
- Excludes: tests, migrations, cache, TYPE_CHECKING blocks
- Reports: terminal + HTML
```

### 5. black
```toml
- Line length: 100 chars
- Target: Python 3.11
- Quotes: double
- Excludes: .git, .venv, migrations, __pycache__
```

### 6. isort
```toml
- Profile: black (compatible formatting)
- Line length: 100 chars
- First-party: agents, api, config, ingestion, ml, storage
```

### 7. hatch (Build System)
```toml
- Packages: agents, api, config, ingestion, ml, storage
- Target: wheel
```

### 8. uv (Fast Installer)
```toml
- Prefer binary wheels for faster installs
```

---

## Installation Commands

### Quick Start
```bash
# Install everything (recommended for first-time setup)
pip install -e ".[all]"

# Or using uv (10-100x faster)
uv pip install -e ".[all]"
```

### Production Deployment
```bash
pip install -e .
```

### Development Setup
```bash
# Core + testing tools
pip install -e ".[dev]"

# Add linting
pip install -e ".[dev,lint]"

# Add formatting
pip install -e ".[dev,lint,format]"

# Everything
pip install -e ".[all]"
```

### Pre-commit Hooks
```bash
# Install hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

---

## Verification

The `pyproject.toml` has been validated:
- ✅ Valid TOML syntax (verified with Python's `tomllib`)
- ✅ 40 core dependencies listed
- ✅ 5 optional dependency groups configured
- ✅ 8 tool configurations present
- ✅ Compatible with Python 3.11+
- ✅ Uses modern build system (hatchling)

---

## Next Steps

With `pyproject.toml` in place, you can now:

1. **Install dependencies:**
   ```bash
   pip install -e ".[all]"
   ```

2. **Set up pre-commit hooks:**
   ```bash
   pre-commit install
   ```

3. **Run linting:**
   ```bash
   ruff check .
   mypy .
   ```

4. **Run tests (once implemented):**
   ```bash
   pytest
   ```

5. **Format code:**
   ```bash
   black .
   ruff check --fix .
   ```

---

## Design Decisions

### Why hatchling?
- Modern, fast build backend
- Simple configuration
- Good optional dependency support
- Active maintenance

### Why version ranges with upper bounds?
- Prevents breaking changes from major version bumps
- Ensures reproducible builds
- Forces conscious decision to upgrade major versions

### Why strict mypy?
- Catches type errors early
- Improves code quality
- Better IDE support
- Easier refactoring

### Why ruff over flake8 + isort?
- 10-100x faster (written in Rust)
- Single tool replaces multiple linters
- Built-in formatter
- Drop-in compatible with existing configs

### Why include torch?
- Required by sentence-transformers for embedding generation
- Large dependency (~2GB), but unavoidable for ML features
- Can use CPU-only wheels to reduce size

### Why both asyncpg and psycopg2?
- `asyncpg`: Async driver for FastAPI runtime
- `psycopg2`: Sync driver required by Alembic migrations
- Both needed for different parts of the stack

---

## Potential Issues & Mitigations

### Large Dependency Tree
**Problem:** Full install pulls in ~200+ transitive dependencies (~3GB with torch)

**Mitigation:**
- Use optional dependencies to install only what's needed
- Use `--no-deps` flag for minimal installs
- Consider torch CPU wheels to reduce size

### Airflow on Windows
**Problem:** Apache Airflow has limited Windows support

**Mitigation:**
- Run Airflow in Docker (recommended)
- Use WSL2 for local development
- Skip Airflow if only working on API/agents

### Torch Installation Failures
**Problem:** Torch may fail to build on some systems

**Mitigation:**
- Use pre-built wheels: `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- Skip ML dependencies if not needed initially
- Use Docker for consistent environments

---

## Maintenance

### Updating Dependencies
```bash
# Check for outdated packages
pip list --outdated

# Upgrade specific packages
pip install --upgrade fastapi sqlalchemy

# Upgrade all packages
pip install --upgrade -e ".[all]"
```

### Adding New Dependencies
1. Add to `dependencies` array in `pyproject.toml`
2. Use version ranges: `"package>=X.Y.0,<Z.0.0"`
3. Run `pip install -e .` to install
4. Commit changes

### Removing Dependencies
1. Remove from `dependencies` array
2. Run `pip install -e .` to sync
3. Check for unused imports in code
4. Commit changes

---

## References

- [PEP 621](https://peps.python.org/pep-0621/) - Project metadata standard
- [PEP 735](https://peps.python.org/pep-0735/) - Dependency groups standard
- [hatchling docs](https://hatch.pypa.io/latest/) - Build system
- [ruff docs](https://docs.astral.sh/ruff/) - Linter configuration
- [mypy docs](https://mypy.readthedocs.io/) - Type checking
- [pytest docs](https://docs.pytest.org/) - Testing framework
