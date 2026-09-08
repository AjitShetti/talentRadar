"""
scripts/verify_deploy.py
~~~~~~~~~~~~~~~~~~~~~~~~
Pre-flight check for a TalentRadar deployment.

Run it with the deployment's environment (locally against the hosted
database, or from a shell on the host itself):

    python -m scripts.verify_deploy

It answers, in order, the questions that actually decide whether a deploy
comes up:

  1. Does ``Settings()`` load at all? Two fields are required with no
     default, and a missing one raises at import — the container exits before
     serving a request, which reads as a mysterious crash loop.
  2. Can we reach the database, over TLS, with these credentials?
  3. Is pgvector installed and has ``alembic upgrade head`` been run? Without
     the ``job_embeddings`` table semantic search silently degrades to the
     relational fallback, which is survivable but worth knowing.
  4. Will the embedding model load, and is it the size the schema expects?
  5. Are the deployment-shape settings right for a PaaS — one proxy hop,
     docs closed, a connection budget the free tier allows?
  6. Which optional features are on, and which are off.

Exit code 0 means "deployable"; 1 means at least one blocking check failed.
Warnings never fail the run: a missing Tavily key costs a feature, not the
deployment.
"""

from __future__ import annotations

import asyncio
import shutil
import sys

OK = "  OK   "
WARN = " WARN  "
FAIL = " FAIL  "

_failures = 0
_warnings = 0


def _report(status: str, check: str, detail: str = "") -> None:
    global _failures, _warnings
    if status == FAIL:
        _failures += 1
    elif status == WARN:
        _warnings += 1
    line = f"[{status}] {check}"
    if detail:
        line += f"\n         {detail}"
    print(line)


def _section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_settings() -> object | None:
    _section("Configuration")
    try:
        from config.settings import get_settings

        settings = get_settings()
    except Exception as exc:  # broad by design: this is the point of the check
        _report(
            FAIL, "Settings load",
            f"{type(exc).__name__}: {exc}\n"
            "         GROQ_API_KEY and JWT_SECRET_KEY (32+ chars) are required.",
        )
        return None

    _report(OK, "Settings load")

    if len(settings.jwt_secret_key) < 32:
        _report(FAIL, "JWT_SECRET_KEY length", "must be at least 32 characters")
    elif settings.jwt_secret_key.startswith("change-me"):
        _report(
            FAIL, "JWT_SECRET_KEY",
            "still the example value from .env.example. Generate a fresh one:\n"
            '         python -c "import secrets; print(secrets.token_urlsafe(48))"',
        )
    else:
        _report(OK, "JWT_SECRET_KEY set")

    if settings.debug:
        _report(
            WARN, "DEBUG is on",
            "/docs is public and the localhost CORS wildcard is active. "
            "Set DEBUG=false in production.",
        )
    else:
        _report(OK, "DEBUG off (docs closed, no localhost CORS wildcard)")

    if settings.trusted_proxy_hops == 0:
        _report(
            WARN, "TRUSTED_PROXY_HOPS=0",
            "Behind a PaaS router every user shares one rate-limit bucket and "
            "the service starts 429-ing under light load. Set it to 1.",
        )
    else:
        _report(OK, f"TRUSTED_PROXY_HOPS={settings.trusted_proxy_hops}")

    budget = settings.db_pool_size + settings.db_max_overflow
    if budget > 20:
        _report(
            WARN, f"Connection budget {budget} per process",
            "Free Postgres tiers cap total connections low (Supabase 60, Neon ~100).",
        )
    else:
        _report(OK, f"Connection budget {budget} per process")

    origins = settings.cors_origins_list
    if not origins or origins == ["http://localhost:3000"]:
        _report(
            WARN, "CORS_ORIGINS is still the local default",
            "The browser will block every response from the deployed frontend. "
            "Set it to the frontend's origin.",
        )
    else:
        _report(OK, f"CORS_ORIGINS = {', '.join(origins)}")

    if not settings.groq_api_key:
        _report(FAIL, "GROQ_API_KEY", "empty — every LLM feature fails")
    else:
        _report(OK, "GROQ_API_KEY set")

    if not settings.tavily_api_key:
        _report(WARN, "TAVILY_API_KEY unset", "Tavily-sourced discovery is disabled")
    if not settings.github_token:
        _report(
            WARN, "GITHUB_TOKEN unset",
            "Company Intel's open-source panel is limited to 60 requests/hour per IP",
        )

    return settings


async def check_database(settings: object) -> None:
    _section("Database")
    import sqlalchemy as sa

    from storage.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            version = await session.scalar(sa.text("SELECT version()"))
    except Exception as exc:  # broad by design
        _report(
            FAIL, "Database connection",
            f"{type(exc).__name__}: {exc}\n"
            "         Check DATABASE_URL (or the POSTGRES_* fields) and POSTGRES_SSL.",
        )
        return

    _report(OK, "Database connection", str(version).split(" on ")[0])

    if not getattr(settings, "postgres_ssl", False):
        _report(
            WARN, "POSTGRES_SSL is false",
            "Every managed provider needs TLS; only a local Postgres does not.",
        )
    else:
        _report(OK, "TLS requested on the database connection")

    async with AsyncSessionLocal() as session:
        extension = await session.scalar(
            sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        table = await session.scalar(sa.text("SELECT to_regclass('job_embeddings')"))
        try:
            revision = await session.scalar(sa.text("SELECT version_num FROM alembic_version"))
        except Exception:  # broad by design: table absent means "never migrated"
            revision = None

    if revision:
        _report(OK, "Migrations applied", f"alembic revision {revision}")
    else:
        _report(FAIL, "Migrations not applied", "run: alembic upgrade head")

    backend = getattr(settings, "vector_backend", "pgvector")
    if backend != "pgvector":
        _report(OK, f"VECTOR_BACKEND={backend}", "pgvector checks skipped")
        return

    if extension:
        _report(OK, "pgvector extension", f"version {extension}")
    else:
        _report(
            WARN, "pgvector extension is not installed",
            "Semantic search will use the relational fallback. Neon and "
            "Supabase both offer it; migration 010 enables it when available.",
        )

    if table:
        async with AsyncSessionLocal() as session:
            rows = await session.scalar(sa.text("SELECT count(*) FROM job_embeddings"))
        _report(OK, "job_embeddings table", f"{rows} embeddings stored")
    elif extension:
        _report(
            WARN, "job_embeddings table is missing",
            "run: alembic upgrade head",
        )


def check_embeddings(settings: object) -> None:
    _section("Embeddings")
    backend = getattr(settings, "vector_backend", "pgvector")
    if backend == "none":
        _report(OK, "VECTOR_BACKEND=none", "no model is loaded; relational search only")
        return

    try:
        from ingestion.embeddings.embedder import embed_texts

        vector = embed_texts(["deployment smoke test"])[0]
    except Exception as exc:  # broad by design
        _report(
            FAIL, "Embedding model",
            f"{type(exc).__name__}: {exc}\n"
            "         The model downloads on first use; a host with no egress "
            "needs it baked into the image (the Dockerfile does this).",
        )
        return

    expected = int(getattr(settings, "embedding_dim", 384))
    if len(vector) == expected:
        _report(OK, "Embedding model", f"{len(vector)} dimensions")
    else:
        _report(
            FAIL, "Embedding dimension mismatch",
            f"model returns {len(vector)}, schema expects vector({expected}). "
            "Changing the model needs a new migration.",
        )


def check_optional_features(settings: object) -> None:
    _section("Optional features")

    if shutil.which("pdflatex"):
        _report(OK, "PDF export", "pdflatex found — LaTeX rendering")
    else:
        engine = getattr(settings, "pdf_engine", "auto")
        if engine == "latex":
            _report(
                FAIL, "PDF export",
                "PDF_ENGINE=latex but pdflatex is not installed.",
            )
        else:
            _report(OK, "PDF export", "no TeX — rendering through PyMuPDF")

    if getattr(settings, "enable_stealth_scrapers", False):
        _report(
            WARN, "Stealth scrapers are ON",
            "Each call launches a headless Firefox (OOMs a 512 MB instance) "
            "and impersonates a browser against sites that forbid it.",
        )
    else:
        _report(OK, "Stealth scrapers off", "ATS APIs and guest boards still run")

    if getattr(settings, "enable_scheduler", True):
        _report(
            OK, "Daily match scheduler on",
            "Correct for a single instance. Set ENABLE_SCHEDULER=false on every "
            "replica but one if you ever scale out.",
        )
    else:
        _report(OK, "Daily match scheduler off")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run() -> int:
    print("TalentRadar deployment pre-flight\n" + "=" * 62)

    settings = check_settings()
    if settings is None:
        print("\nConfiguration could not be loaded; nothing else can be checked.")
        return 1

    await check_database(settings)
    check_embeddings(settings)
    check_optional_features(settings)

    print("\n" + "=" * 62)
    if _failures:
        print(f"{_failures} blocking problem(s), {_warnings} warning(s). Not ready to deploy.")
        return 1
    print(f"Ready to deploy. {_warnings} warning(s) — read them, none are blocking.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
