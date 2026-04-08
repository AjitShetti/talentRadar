"""
storage/migrations/env.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Alembic environment bootstrap.

Key design choices
------------------
* Uses the SYNC database URL from settings (`database_url_sync`) because
  Alembic's standard migration runner is synchronous.
* Imports ``storage.models`` so Alembic can detect all model changes
  automatically (auto-generate mode).
* Settings are resolved via `config.settings.get_settings()`, which reads
  from `.env` — no credentials are hard-coded here.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make sure the project root is on sys.path so absolute imports work whether
# Alembic is invoked from the project root or from inside storage/.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # talentRadar/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Import application objects AFTER sys.path is patched.
# ---------------------------------------------------------------------------
from config.settings import get_settings  # noqa: E402
from storage.database import Base          # noqa: E402
import storage.models  # noqa: E402, F401  ← registers all ORM models

# ---------------------------------------------------------------------------
# Alembic config object (wraps alembic.ini values)
# ---------------------------------------------------------------------------
alembic_cfg = context.config

# Wire in Python logging from alembic.ini [loggers] section
if alembic_cfg.config_file_name is not None:
    fileConfig(alembic_cfg.config_file_name)

# Override the sqlalchemy.url with the value from settings so we never need
# to duplicate credentials in alembic.ini.
settings = get_settings()
alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url_sync)

# The MetaData object that contains all table definitions Alembic should track
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migration (no live DB connection — generates SQL script)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = alembic_cfg.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migration (connects to a real DB)
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_cfg.get_section(alembic_cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # no persistent pool during migrations
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
