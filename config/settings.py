"""
config/settings.py
~~~~~~~~~~~~~~~~~~
Central application settings powered by pydantic-settings.
All values are read from environment variables (or a .env file).
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit


def _strip_query_params(dsn: str, drop: tuple[str, ...]) -> str:
    """Return ``dsn`` without the named query parameters."""
    parts = urlsplit(dsn)
    if not parts.query:
        return dsn
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in drop]
    return urlunsplit(parts._replace(query=urlencode(kept)))

from pydantic import AliasChoices, Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # LLM / AI                                                             #
    # ------------------------------------------------------------------ #
    groq_api_key: str = Field(description="Groq API key")
    tavily_api_key: str = Field(default="", description="Tavily search API key")
    github_token: str = Field(
        default="",
        description=(
            "Optional GitHub PAT for the Company Intel open-source panel. "
            "Unauthenticated requests are capped at 60/hour per IP; a token "
            "lifts that to 5000/hour. Read-only public scope is enough."
        ),
    )

    # Model ids are settings (not literals) so a decommissioned Groq model can
    # be swapped from .env without a code change.
    groq_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Default Groq chat model for reasoning-heavy calls",
    )
    groq_fast_model: str = Field(
        default="openai/gpt-oss-20b",
        description="Smaller/cheaper Groq model for classification and short calls",
    )
    groq_interview_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Groq model used by the mock-interview agent",
    )

    # ------------------------------------------------------------------ #
    # PostgreSQL                                                           #
    # ------------------------------------------------------------------ #
    postgres_user: str = Field(default="talentRadar")
    postgres_password: str = Field(default="", description="PostgreSQL password")
    postgres_db: str = Field(default="talentRadar")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)

    # Managed Postgres (Neon, Supabase, Railway, Render …) hands out one DSN
    # rather than five discrete fields, so accept it directly. When set it
    # wins over the fields above, and the driver prefix is rewritten per use
    # (asyncpg for the app, psycopg2 for Alembic) so one env var serves both.
    database_url_override: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "database_url_override"),
        description="Full Postgres DSN; overrides the discrete POSTGRES_* fields",
    )

    # Managed providers require TLS. asyncpg does not read libpq's sslmode, so
    # it is passed through connect_args by storage/database.py instead.
    postgres_ssl: bool = Field(
        default=False,
        description="Require TLS on the database connection (needed by hosted Postgres)",
    )

    # Connection budget *per process*. Free Postgres tiers cap total
    # connections low (Supabase 60, Neon ~100), and the ceiling here is
    # pool_size + max_overflow multiplied by the worker count — the old
    # 10+20 across 4 workers asked for 120 and exhausted every free tier.
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=5)

    def _dsn(self, driver: str) -> str:
        """Build a SQLAlchemy URL for ``driver``.

        Credentials are percent-encoded: managed providers generate passwords
        containing ``@``, ``/`` and ``#``, every one of which silently
        corrupts a hand-formatted DSN (the ``@`` splits userinfo from host, so
        the app connects to the wrong hostname and reports a DNS error).
        """
        if self.database_url_override:
            dsn = self.database_url_override
            for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql://", "postgres://"):
                if dsn.startswith(prefix):
                    dsn = f"postgresql+{driver}://" + dsn[len(prefix):]
                    break
            if driver == "asyncpg":
                # asyncpg has no ``sslmode``/``channel_binding`` kwargs, and
                # SQLAlchemy forwards unknown query params straight through to
                # connect() -- so a stock Neon/Supabase DSN raised TypeError
                # before it ever reached the database. TLS is requested via
                # connect_args in storage/database.py instead.
                dsn = _strip_query_params(dsn, ("sslmode", "channel_binding", "options"))
            return dsn
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+{driver}://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        """Async-ready SQLAlchemy URL (asyncpg driver)."""
        return self._dsn("asyncpg")

    @computed_field  # type: ignore[misc]
    @property
    def database_url_sync(self) -> str:
        """Sync SQLAlchemy URL (psycopg2) — used by Alembic migrations."""
        return self._dsn("psycopg2")

    # ------------------------------------------------------------------ #
    # Redis                                                                #
    # ------------------------------------------------------------------ #
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ------------------------------------------------------------------ #
    # Vector store                                                         #
    # ------------------------------------------------------------------ #
    # Which backend stores job-description embeddings.
    #
    #   pgvector — embeddings live in the same managed Postgres as everything
    #              else, in the ``job_embeddings`` table. This is the default
    #              because it is the only option with a genuinely free
    #              managed host: Neon and Supabase both ship the ``vector``
    #              extension on their free tiers, while Chroma Cloud has no
    #              free tier and self-hosted Chroma needs a persistent volume
    #              that free PaaS plans do not provide.
    #   chroma   — the legacy ChromaDB server (docker-compose / self-hosted).
    #   none     — no vector search at all. Semantic search degrades to the
    #              relational fallback in agents/rag_agent.py, and the
    #              embedding model is never loaded — which is how you fit the
    #              API into a 512 MB instance if you have to.
    vector_backend: Literal["pgvector", "chroma", "none"] = Field(
        default="pgvector",
        description="Where job embeddings are stored: pgvector | chroma | none",
    )
    # Dimensionality of the embedding model (all-MiniLM-L6-v2 → 384). Changing
    # this requires a new migration: the pgvector column is typed vector(N).
    embedding_dim: int = Field(default=384, gt=0)

    # Legacy ChromaDB server connection, used only when vector_backend=chroma.
    chroma_host: str = Field(default="localhost")
    chroma_port: int = Field(default=8000)

    # ------------------------------------------------------------------ #
    # JWT                                                                  #
    # ------------------------------------------------------------------ #
    jwt_secret_key: str = Field(min_length=32, description="JWT Secret key, at least 32 characters")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiry_minutes: int = Field(default=10080)

    # ------------------------------------------------------------------ #
    # Rate limiting                                                        #
    # ------------------------------------------------------------------ #
    # Requests allowed per client IP per window, for ordinary endpoints.
    rate_limit_default_requests: int = Field(default=100)
    rate_limit_default_window_seconds: int = Field(default=60)
    # Much stricter budget for credential endpoints (login / signup) so an
    # attacker cannot mount an online password-guessing attack.
    rate_limit_auth_requests: int = Field(default=10)
    rate_limit_auth_window_seconds: int = Field(default=300)
    # Largest resume upload accepted, in bytes (default 5 MiB).
    max_resume_upload_bytes: int = Field(default=5 * 1024 * 1024)
    # Number of proxy hops in front of the API. 0 means "no proxy — use the
    # peer address". Set it to 1 behind a single PaaS router (Render, Railway,
    # Fly, Cloud Run) so the limiter keys on the real client instead of
    # lumping every user into one bucket keyed on the proxy's address.
    # Never set it higher than the number of proxies you actually control:
    # each hop you claim is one X-Forwarded-For entry a client can forge.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=4)

    # ------------------------------------------------------------------ #
    # Scraping                                                             #
    # ------------------------------------------------------------------ #
    # The Camoufox/Scrapling stealth path (Naukri, Indeed India, Instahyre)
    # launches a headless Firefox per call, which OOMs a 512 MB instance, and
    # it works by impersonating a browser against sites whose terms forbid it.
    # Off by default so a public deployment does neither by accident; local
    # runs and self-hosted instances can turn it back on. When it is off the
    # remaining sources (ATS APIs, LinkedIn guest, foundit, freshersworld)
    # still run, so live search degrades rather than failing.
    enable_stealth_scrapers: bool = Field(
        default=False,
        description="Enable the headless-browser stealth scrapers (Naukri, Indeed, Instahyre)",
    )

    # ------------------------------------------------------------------ #
    # PDF rendering                                                        #
    # ------------------------------------------------------------------ #
    # pdflatex produces the nicest resume PDFs but a TeX Live install that can
    # compile the template costs well over a gigabyte of image, which no free
    # build tier will carry. "auto" uses pdflatex when it is on PATH and falls
    # back to the built-in PyMuPDF renderer otherwise; "latex" and "builtin"
    # force one engine and fail rather than silently switching.
    pdf_engine: Literal["auto", "latex", "builtin"] = Field(
        default="auto",
        description="Resume PDF renderer: auto | latex | builtin",
    )

    # ------------------------------------------------------------------ #
    # GCS (optional blob storage)                                          #
    # ------------------------------------------------------------------ #
    gcs_bucket_name: str = Field(default="talentRadar-raw-jds")
    google_application_credentials: str = Field(default="")

    # ------------------------------------------------------------------ #
    # Daily job matching                                                   #
    # ------------------------------------------------------------------ #
    # When the in-process APScheduler fires the daily target-role job search
    # (server clock, matches the DB host's timezone — same convention as the
    # rest of this file, which has no explicit timezone setting).
    # Set false on every replica but one when running more than a single
    # process, so the daily scan does not run N times in parallel.
    enable_scheduler: bool = Field(
        default=True, description="Run the in-process daily job-match scheduler"
    )
    daily_match_hour: int = Field(default=8, description="Hour (0-23) the daily job-match scan runs")
    daily_match_minute: int = Field(default=0, description="Minute (0-59) the daily job-match scan runs")

    # ------------------------------------------------------------------ #
    # Frontend / CORS                                                      #
    # ------------------------------------------------------------------ #
    next_public_api_url: str = Field(default="http://localhost:8000")
    # Comma-separated list of allowed CORS origins, e.g.:
    # CORS_ORIGINS=http://localhost:3000,https://app.talentradar.com
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated allowed CORS origins",
    )
    # Development conveniences that must not survive into production: the
    # wildcard localhost CORS origin, and the interactive /docs and /redoc
    # pages. Both default off; set DEBUG=true for local work.
    debug: bool = Field(default=False, description="Enable dev-only conveniences")

    @computed_field  # type: ignore[misc]
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse cors_origins into a list for the middleware."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
