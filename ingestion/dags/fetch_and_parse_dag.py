"""
ingestion/dags/fetch_and_parse_dag.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Airflow DAG: fetch raw JDs via Tavily → parse with Groq LLM → save to Postgres → embed to ChromaDB.

Pipeline
--------
  fetch_raw  ──►  parse_with_llm  ──►  save_to_postgres  ──►  embed_to_chromadb

Retry policy (per task)
-----------------------
  retries          = 3
  retry_delay      = 5 minutes
  (exponential backoff disabled to keep delays predictable)

DAG params (configurable via Airflow UI → Trigger DAG w/ config)
----------------------------------------------------------------
  roles                 – list[str]   job roles to search for
  locations             – list[str]   geographic filters
  max_results_per_query – int         Tavily results per (role, location) pair

Customisation
-------------
  - Swap ``_DEFAULT_MODEL`` in ``jd_parser.py`` to use a larger Groq model.
  - Change ``schedule`` below to run more/less frequently.
  - Add ``email_on_failure=True`` and ``email=["ops@your-org.com"]`` to
    ``default_args`` once SMTP is configured.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

# ─── Make the repo root importable inside Airflow tasks ──────────────────────
_REPO_ROOT = Path(__file__).parent.parent.parent  # …/talentRadar/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from airflow.decorators import dag
from airflow.utils.dates import days_ago

from ingestion.dags.common.pipeline_tasks import (
    embed_to_chromadb,
    fetch_raw,
    parse_with_llm,
    save_to_postgres,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DAG-level configuration
# ─────────────────────────────────────────────────────────────────────────────

_DAG_ID = "talentradar_fetch_and_parse"

# Default search parameters — overridable from the Airflow UI at trigger time
_DEFAULT_ROLES = [
    "Software Engineer",
    "Data Scientist",
    "MLOps Engineer",
    "Backend Engineer",
    "Machine Learning Engineer",
]
_DEFAULT_LOCATIONS = ["Remote", "San Francisco", "New York", "India"]
_DEFAULT_MAX_RESULTS = 5   # keep low for the initial run; raise in production

# ─────────────────────────────────────────────────────────────────────────────
# Retry / scheduling defaults
# ─────────────────────────────────────────────────────────────────────────────

default_args: dict[str, Any] = {
    "owner": "talentradar",
    "depends_on_past": False,
    "start_date": days_ago(1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": False,
    "execution_timeout": timedelta(minutes=30),  # Timeouts prevent zombie tasks
}

# ─────────────────────────────────────────────────────────────────────────────
# DAG definition
# ─────────────────────────────────────────────────────────────────────────────

@dag(
    dag_id=_DAG_ID,
    description=(
        "TalentRadar: fetch raw job descriptions via Tavily, "
        "parse with Groq LLM, and persist to PostgreSQL."
    ),
    default_args=default_args,
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["talentradar", "ingestion", "tavily", "llm"],
    params={
        "roles": _DEFAULT_ROLES,
        "locations": _DEFAULT_LOCATIONS,
        "max_results_per_query": _DEFAULT_MAX_RESULTS,
    },
    doc_md=__doc__,
)
def fetch_and_parse_pipeline():

    # 1. Fetch raw data
    raw_data = fetch_raw(
        roles=_DEFAULT_ROLES,
        locations=_DEFAULT_LOCATIONS,
        max_results_per_query=_DEFAULT_MAX_RESULTS,
    )

    # 2. Parse fetched data with LLM
    parsed_data = parse_with_llm(upstream=raw_data)

    # 3. Save parsed data to Postgres
    saved_data = save_to_postgres(upstream=parsed_data)
    
    # 4. Embed the parsed data into ChromaDB
    # We can run this after save_to_postgres or in parallel if needed,
    # but the backfill requires the DB insertion first.
    # Therefore, we make embed_to_chromadb depend on the completion of save_to_postgres.
    # We pass parsed_data as upstream to embed, but use bitshift to enforce ordering.
    embed_task = embed_to_chromadb(upstream=parsed_data)
    saved_data >> embed_task

fetch_and_parse_dag = fetch_and_parse_pipeline()
