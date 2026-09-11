"""job enrichment status: makes live-scraped rows storable without an LLM call

Revision ID: 011_job_enrichment_status
Revises: 010_pgvector_job_embeddings
Create Date: 2026-09-11 00:00:00.000000

Why this exists
---------------
Live search returns ~60 postings per query. Running the Groq JD parser over
each one, as ``ingestion/pipeline.py`` does, would be ~60 LLM calls per search
and would exhaust a free Groq tier within a handful of queries - so live
results were never written at all, and the index stayed empty.

This column splits the write in two. A posting lands as ``raw``: title,
company, URL, location and skills exactly as the board gave them, plus a local
ONNX embedding, at zero API cost. It becomes ``enriched`` later, when someone
actually opens it or when the scheduled pass has budget - and only then does a
model see it.

``failed`` marks a row enrichment could not parse, so the budgeted pass does
not retry it forever.

The index is partial, on ``raw`` only: the enrichment pass asks exactly one
question ("which rows still need work?"), and on a 0.5 GB database an index
over every row to answer it would be most of a table scan's cost in storage.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011_job_enrichment_status"
down_revision: Union[str, None] = "010_pgvector_job_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "enrichment_status",
            sa.String(length=16),
            nullable=False,
            # Existing rows came through the LLM pipeline, so they are already
            # enriched. Defaulting them to "raw" would queue the entire
            # existing table for re-parsing on the first scheduled pass.
            server_default="enriched",
            comment="raw = structural scrape only; enriched = LLM-parsed; failed = unparseable",
        ),
    )

    op.create_index(
        "ix_jobs_enrichment_pending",
        "jobs",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("enrichment_status = 'raw'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_enrichment_pending", table_name="jobs")
    op.drop_column("jobs", "enrichment_status")
