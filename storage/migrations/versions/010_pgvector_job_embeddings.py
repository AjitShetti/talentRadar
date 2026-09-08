"""pgvector job embeddings: replaces the ChromaDB server

Revision ID: 010_pgvector_job_embeddings
Revises: 009_daily_job_matches
Create Date: 2026-09-08 00:00:00.000000

Why this exists
---------------
ChromaDB has no free managed host — Chroma Cloud has no free tier, and the
self-hosted server needs a persistent volume that free PaaS plans do not
offer. Neon and Supabase both ship pgvector on their free tiers, so the
embeddings move into the database the application already has.

The table is created only if the ``vector`` extension is available. On a
Postgres without pgvector the migration logs and skips rather than failing:
the application already degrades to the relational search path when the
vector store is unreachable, so a missing extension costs semantic search,
not the deployment.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = "010_pgvector_job_embeddings"
down_revision: Union[str, None] = "009_daily_job_matches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Must match ``Settings.embedding_dim`` (all-MiniLM-L6-v2 → 384). Changing
#: the model means a new migration, not an edit to this one.
EMBEDDING_DIM = 384


class _Vector(UserDefinedType):
    """Local copy of the ``vector(N)`` DDL spelling.

    Migrations are kept self-contained on purpose: importing
    ``storage.models.Vector`` here would tie this revision to whatever the
    model file says today rather than to the schema it actually created.
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: object) -> str:
        return f"vector({self.dim})"


def _vector_extension_ready() -> bool:
    """Enable pgvector, returning False if this server does not offer it.

    The availability check is a *read* on ``pg_available_extensions`` rather
    than a ``try: CREATE EXTENSION``: a failed statement aborts the enclosing
    migration transaction, so every statement after the ``except`` would fail
    too. Asking first keeps the transaction clean.
    """
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ).scalar()
    if not available:
        logger.warning(
            "pgvector is not available on this server. Skipping the "
            "job_embeddings table — semantic search will use the relational "
            "fallback until the extension is installed."
        )
        return False
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return True


def _create_vector_index() -> None:
    """Build an ANN index, tolerating servers that support neither kind.

    Each attempt runs inside a SAVEPOINT so a failure rolls back only that
    statement instead of poisoning the migration's transaction. The index is
    an optimisation, not a requirement: without it the search degrades to a
    sequential scan, which is fine at this table's size.
    """
    bind = op.get_bind()
    for ddl, kind in (
        (
            "CREATE INDEX IF NOT EXISTS ix_job_embeddings_vector "
            "ON job_embeddings USING hnsw (embedding vector_cosine_ops)",
            "hnsw",
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_job_embeddings_vector "
            "ON job_embeddings USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)",
            "ivfflat",
        ),
    ):
        try:
            with bind.begin_nested():
                bind.execute(sa.text(ddl))
            logger.info("Created %s index on job_embeddings.embedding", kind)
            return
        except Exception as exc:  # broad by design: index type support varies
            logger.warning("%s index unavailable: %s", kind, exc)
    logger.warning("No vector index created; similarity searches will scan.")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        logger.info("Not PostgreSQL (%s) — skipping pgvector migration.", bind.dialect.name)
        return

    # jobs.embedding_id keeps the same value it always held (the MD5
    # fingerprint of the source URL); only what it points at changed. The
    # comment is part of the DDL, so it is updated here rather than left to
    # drift from storage/models.py and show up in every autogenerate diff.
    op.alter_column(
        "jobs", "embedding_id",
        existing_type=sa.String(length=256),
        existing_nullable=True,
        comment="Vector-store row id (job_embeddings.id) for semantic search lookups",
        existing_comment="ChromaDB document ID for semantic search lookups",
    )

    if not _vector_extension_ready():
        return

    op.create_table(
        "job_embeddings",
        sa.Column(
            "id", sa.String(length=256), nullable=False,
            comment="Stable MD5 fingerprint of source_url; matches jobs.embedding_id",
        ),
        sa.Column("document", sa.Text(), nullable=False, comment="The text that was embedded"),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
            comment="Flat, filterable fields (title, company, is_remote, skills_str …)",
        ),
        sa.Column(
            "embedding", _Vector(EMBEDDING_DIM), nullable=False,
            comment="all-MiniLM-L6-v2 embedding of `document`",
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_embeddings"),
    )

    # Cosine distance (<=>) is what every query uses — it is what the Chroma
    # collection was created with ("hnsw:space": "cosine") and what
    # RetrievalResult.score assumes — so the index has to be built for the
    # matching operator class or the planner will ignore it.
    _create_vector_index()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_job_embeddings_vector")
    op.execute("DROP TABLE IF EXISTS job_embeddings")
    op.alter_column(
        "jobs", "embedding_id",
        existing_type=sa.String(length=256),
        existing_nullable=True,
        comment="ChromaDB document ID for semantic search lookups",
        existing_comment="Vector-store row id (job_embeddings.id) for semantic search lookups",
    )
    # The extension is intentionally left installed: another table or another
    # application on the same database may be using it.
