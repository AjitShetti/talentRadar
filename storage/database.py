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
engine = create_async_engine(
    settings.database_url,
    echo=False,          # set True only for debugging SQL
    pool_pre_ping=True,  # gracefully reconnect after idle disconnects
    pool_size=10,
    max_overflow=20,
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
    """Async context-manager; use in FastAPI Depends or standalone scripts."""
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
