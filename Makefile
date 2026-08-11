# ─────────────────────────────────────────────────────────────────────────────
# TalentRadar — Development & Deployment Makefile
# ─────────────────────────────────────────────────────────────────────────────
# Usage: make <target>
# ─────────────────────────────────────────────────────────────────────────────

SHELL := /bin/bash
.PHONY: help up down logs migrate seed test lint format typecheck deploy fly-logs clean

# ── Docker Compose ──────────────────────────────────────────────────────────

## Start full local stack (postgres, redis, chromadb, api, celery, frontend)
up:
	docker compose up -d

## Stop all services
down:
	docker compose down

## Tail logs for all services
logs:
	docker compose logs -f

## Restart a single service (usage: make restart service=api)
restart:
	docker compose restart $(service)

# ── Database ────────────────────────────────────────────────────────────────

## Run all pending migrations
migrate:
	docker exec talentradar-api alembic upgrade head

## Create a new migration (usage: make makemigrations msg="add foo")
makemigrations:
	docker exec talentradar-api alembic revision --autogenerate -m "$(msg)"

## Rollback one migration
downgrade:
	docker exec talentradar-api alembic downgrade -1

## Seed database with sample data
seed:
	docker exec talentradar-api python -m ingestion.seed_db

# ── Code Quality ────────────────────────────────────────────────────────────

## Run linter (ruff)
lint:
	ruff check .

## Auto-fix lint issues
lint-fix:
	ruff check . --fix

## Format code (ruff format)
format:
	ruff format .

## Type check (mypy)
typecheck:
	mypy .

## Run all quality checks
check: lint typecheck

# ── Testing ─────────────────────────────────────────────────────────────────

## Run full test suite
test:
	pytest tests/ -v -m "not slow"

## Run tests including slow/integration
test-all:
	pytest tests/ -v

## Run tests with coverage report
test-cov:
	pytest tests/ -v -m "not slow" --cov=. --cov-report=term-missing --cov-report=html

## Run a single test file (usage: make test-one file=tests/test_api.py)
test-one:
	pytest $(file) -v

# ── Deployment (Fly.io) ─────────────────────────────────────────────────────

## Deploy to Fly.io
deploy:
	fly deploy

## Tail Fly.io logs
fly-logs:
	fly logs

## Open Fly.io dashboard
fly-status:
	fly status

## SSH into Fly.io VM
fly-ssh:
	fly ssh console

## Set a secret on Fly.io (usage: make fly-secret key=GROQ_API_KEY val=gsk_...)
fly-secret:
	fly secrets set $(key)=$(val)

# ── Cleanup ─────────────────────────────────────────────────────────────────

## Remove build artifacts, caches, and volumes
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache 2>/dev/null || true

## Full clean including Docker volumes
clean-all: clean
	docker compose down -v --remove-orphans

# ── Help ────────────────────────────────────────────────────────────────────

## Show this help
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //' | column -t -s ' '
