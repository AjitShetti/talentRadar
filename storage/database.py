"""
storage/database.py
~~~~~~~~~~~~~~~~~~~
SQLAlchemy async engine and session factory.
Reads connection details exclusively from config.settings — no hard-coded
credentials anywhere in the storage layer.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config.settings import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def _connect_args() -> dict[str, object]:
    """asyncpg connect kwargs.

    asyncpg ignores libpq's ``sslmode=`` query parameter, so a Neon/Supabase
    DSN that carries one still tries to connect in the clear and is refused.
    TLS has to be requested through connect_args instead.
    """
    dsn = settings.database_url
    wants_tls = settings.postgres_ssl or "sslmode=require" in dsn or "ssl=true" in dsn
    return {"ssl": "require"} if wants_tls else {}


# Pool sizing is per process, and the real ceiling is
# (pool_size + max_overflow) x worker count. Free Postgres tiers allow far
# fewer connections than the old 10+20 asked for across four workers.
engine = create_async_engine(
    settings.database_url,
    echo=False,          # set True only for debugging SQL
    pool_pre_ping=True,  # gracefully reconnect after idle disconnects
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=1800,   # hosted Postgres drops idle connections inside an hour
    connect_args=_connect_args(),
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Declarative base (shared across all ORM models)
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Single metadata registry for the whole application."""
    pass


# ---------------------------------------------------------------------------
# Dependency / context-manager helpers
# ---------------------------------------------------------------------------
@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async context-manager; use in standalone scripts or service-layer code."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_dep() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async DB session.

    Use as ``db: AsyncSession = Depends(get_db_dep)`` in route handlers.
    Unlike ``get_db``, this is a plain async generator — FastAPI's Depends()
    requires a generator, not an asynccontextmanager.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_engine() -> None:
    """Dispose the engine — call on application shutdown."""
    await engine.dispose()
