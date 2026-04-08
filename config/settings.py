"""
config/settings.py
~~~~~~~~~~~~~~~~~~
Central application settings powered by pydantic-settings.
All values are read from environment variables (or a .env file).
"""

from functools import lru_cache
from pydantic import Field, computed_field
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
    groq_api_key: str = Field(default="", description="Groq API key")
    tavily_api_key: str = Field(default="", description="Tavily search API key")

    # ------------------------------------------------------------------ #
    # PostgreSQL                                                           #
    # ------------------------------------------------------------------ #
    postgres_user: str = Field(default="talentRadar")
    postgres_password: str = Field(default="devpassword")
    postgres_db: str = Field(default="talentRadar")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        """Async-ready SQLAlchemy URL (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def database_url_sync(self) -> str:
        """Sync SQLAlchemy URL (psycopg2) — used by Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ------------------------------------------------------------------ #
    # Redis                                                                #
    # ------------------------------------------------------------------ #
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ------------------------------------------------------------------ #
    # ChromaDB                                                             #
    # ------------------------------------------------------------------ #
    chroma_host: str = Field(default="localhost")
    chroma_port: int = Field(default=8000)

    # ------------------------------------------------------------------ #
    # JWT                                                                  #
    # ------------------------------------------------------------------ #
    jwt_secret_key: str = Field(default="change-me-min-32-chars-secret!!")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiry_minutes: int = Field(default=60)

    # ------------------------------------------------------------------ #
    # GCS (optional blob storage)                                          #
    # ------------------------------------------------------------------ #
    gcs_bucket_name: str = Field(default="talentRadar-raw-jds")
    google_application_credentials: str = Field(default="")

    # ------------------------------------------------------------------ #
    # Frontend / CORS                                                      #
    # ------------------------------------------------------------------ #
    next_public_api_url: str = Field(default="http://localhost:8000")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
