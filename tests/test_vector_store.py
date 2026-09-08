"""
tests/test_vector_store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Covers the pgvector backend that replaced the ChromaDB server.

No database is needed here. What is worth testing without one is exactly what
would be silently wrong in production: the SQL's parameter binding (a vector
bound the obvious way is rejected by asyncpg at runtime, not at import), the
degradation path when the table is absent, and the backend selection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingestion.embeddings import vector_store as vs
from ingestion.embeddings.pgvector_store import PgVectorJobStore, _as_floats, _decode_metadata


@pytest.fixture(autouse=True)
def _clear_store_cache():
    """The module caches one store per process; tests must not inherit it."""
    vs.reset_vector_store_cache()
    yield
    vs.reset_vector_store_cache()


def _settings(backend: str) -> MagicMock:
    settings = MagicMock()
    settings.vector_backend = backend
    return settings


# ─────────────────────────────────────────────────────────────────────────────
# Backend selection
# ─────────────────────────────────────────────────────────────────────────────

class TestGetVectorStore:
    def test_none_backend_returns_none(self):
        """VECTOR_BACKEND=none must not construct anything or load a model."""
        with patch.object(vs, "get_settings", return_value=_settings("none")):
            assert vs.get_vector_store() is None

    def test_pgvector_is_the_default_backend(self):
        with patch.object(vs, "get_settings", return_value=_settings("pgvector")):
            store = vs.get_vector_store()
        assert isinstance(store, PgVectorJobStore)

    def test_store_is_cached_across_calls(self):
        """A RAGAgent is built per request; building a store per request is not."""
        with patch.object(vs, "get_settings", return_value=_settings("pgvector")):
            first = vs.get_vector_store()
            second = vs.get_vector_store()
        assert first is second

    def test_construction_failure_degrades_to_none(self):
        """A backend that cannot be built must not raise into the search path."""
        with patch.object(vs, "get_settings", return_value=_settings("pgvector")), \
             patch.object(vs, "_build", side_effect=RuntimeError("no server")):
            assert vs.get_vector_store() is None

    def test_chroma_backend_still_selectable(self):
        with patch.object(vs, "get_settings", return_value=_settings("chroma")), \
             patch("ingestion.embeddings.chroma_store.ChromaJobStore") as MockChroma:
            store = vs.get_vector_store()
        assert store is MockChroma.return_value


# ─────────────────────────────────────────────────────────────────────────────
# SQL shape
# ─────────────────────────────────────────────────────────────────────────────

class TestPgVectorSql:
    @pytest.fixture
    def store(self):
        return PgVectorJobStore()

    def test_vectors_are_bound_as_float_arrays_not_as_vectors(self, store):
        """The double cast is load-bearing.

        ``CAST(:v AS vector)`` makes PostgreSQL infer the parameter itself as
        type ``vector``, which asyncpg cannot encode without a registered
        codec — a runtime failure on the first real search, invisible to any
        import-time check. Forcing it through ``double precision[]`` first is
        what both drivers can actually send.
        """
        for sql in (str(store._upsert_sql()), str(store._search_sql(filtered=False))):
            assert "CAST(CAST(:" in sql
            assert "AS double precision[]) AS vector)" in sql

    def test_search_orders_by_cosine_distance(self, store):
        sql = str(store._search_sql(filtered=False))
        assert "<=>" in sql, "must use cosine distance, matching the old Chroma space"
        assert "ORDER BY distance ASC" in sql
        assert "LIMIT :limit" in sql

    def test_metadata_filter_uses_jsonb_containment(self, store):
        assert "metadata @> CAST(:filter AS jsonb)" in str(store._search_sql(filtered=True))
        assert ":filter" not in str(store._search_sql(filtered=False))

    def test_upsert_is_idempotent_on_id(self, store):
        sql = str(store._upsert_sql())
        assert "ON CONFLICT (id) DO UPDATE SET" in sql
        assert "embedding = EXCLUDED.embedding" in sql

    def test_search_params_embed_the_query_and_serialise_the_filter(self, store):
        with patch(
            "ingestion.embeddings.pgvector_store.embed_texts",
            return_value=[[0.1, 0.2, 0.3]],
        ):
            params = store._search_params("python jobs", 5, {"is_remote": True})
        assert params["query"] == [0.1, 0.2, 0.3]
        assert params["limit"] == 5
        assert params["filter"] == '{"is_remote": true}'

    def test_search_params_survive_a_model_that_will_not_load(self, store):
        with patch(
            "ingestion.embeddings.pgvector_store.embed_texts",
            side_effect=RuntimeError("onnx missing"),
        ):
            assert store._search_params("python", 5, None) is None

    def test_limit_is_never_zero(self, store):
        with patch("ingestion.embeddings.pgvector_store.embed_texts", return_value=[[0.0]]):
            assert store._search_params("q", 0, None)["limit"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Degradation
# ─────────────────────────────────────────────────────────────────────────────

class TestDegradesWithoutTheTable:
    """An un-migrated database costs semantic search, not the endpoint."""

    @pytest.fixture
    def store(self):
        store = PgVectorJobStore()
        store._ready = False
        store._ready_checked_at = float("inf")  # inside the retry window
        return store

    @pytest.mark.asyncio
    async def test_asearch_returns_no_matches(self, store):
        assert await store.asearch("python") == []

    def test_search_returns_no_matches(self, store):
        assert store.search("python") == []

    def test_count_is_zero(self, store):
        assert store.count() == 0

    def test_get_is_none(self, store):
        assert store.get("abc") is None

    @pytest.mark.asyncio
    async def test_writes_report_nothing_embedded(self, store):
        assert await store.aadd_batch([{"job_id": "a", "text": "t"}]) == 0
        assert store.add_batch([{"job_id": "a", "text": "t"}]) == 0

    def test_empty_batch_never_touches_the_database(self):
        store = PgVectorJobStore()
        with patch.object(store, "_ready_sync", side_effect=AssertionError("probed")):
            assert store.add_batch([]) == 0

    @pytest.mark.asyncio
    async def test_a_failed_query_degrades_rather_than_raising(self):
        store = PgVectorJobStore()
        store._ready = True
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(side_effect=RuntimeError("connection reset"))
        with patch("ingestion.embeddings.pgvector_store.AsyncSessionLocal", return_value=session), \
             patch("ingestion.embeddings.pgvector_store.embed_texts", return_value=[[0.1]]):
            assert await store.asearch("python") == []


class TestResultShape:
    """What comes back has to be what RAGAgent._build_results consumes."""

    @pytest.mark.asyncio
    async def test_rows_become_retrieval_dicts(self):
        row = MagicMock()
        row.id = "abc123"
        row.document = "Senior Python Engineer at Stripe"
        row.metadata = {"company": "Stripe", "is_remote": True}
        row.distance = 0.25

        store = PgVectorJobStore()
        store._ready = True
        result = MagicMock()
        result.all.return_value = [row]
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(return_value=result)

        with patch("ingestion.embeddings.pgvector_store.AsyncSessionLocal", return_value=session), \
             patch("ingestion.embeddings.pgvector_store.embed_texts", return_value=[[0.1, 0.2]]):
            hits = await store.asearch("python", n_results=5)

        assert hits == [{
            "id": "abc123",
            "document": "Senior Python Engineer at Stripe",
            "metadata": {"company": "Stripe", "is_remote": True},
            "distance": 0.25,
        }]

    @pytest.mark.asyncio
    async def test_the_rag_agent_can_consume_the_result(self):
        """The score the agent computes is 1 - distance, as it was with Chroma."""
        from agents.rag_agent import RAGAgent
        from agents.state import QueryContext

        hits = [{
            "id": "abc123", "document": "d",
            "metadata": {"company": "Stripe", "location": "Bengaluru", "is_remote": True},
            "distance": 0.25,
        }]
        job = MagicMock()
        job.external_id = "abc123"
        job.title = "Senior Python Engineer"
        job.location_raw = "Bengaluru"
        job.is_remote = True
        job.seniority = None
        job.skills = ["Python"]
        job.source_url = "https://example.com/job"

        uow = AsyncMock()
        uow.jobs.get_by_external_ids = AsyncMock(return_value=[job])
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.rag_agent.AsyncGroq"), \
             patch("agents.rag_agent.get_vector_store", return_value=None), \
             patch("agents.rag_agent.AsyncSessionLocal", return_value=session), \
             patch("agents.rag_agent.UnitOfWork", return_value=uow):
            agent = RAGAgent()
            results = await agent._build_results(hits, QueryContext(raw_query="python"))

        assert len(results) == 1
        assert results[0].job_id == "abc123"
        assert results[0].score == pytest.approx(0.75)


class TestReadinessCache:
    def test_a_negative_verdict_is_re_probed_after_the_window(self):
        """A database migrated later must be picked up without a redeploy."""
        store = PgVectorJobStore()
        store._remember(False)
        assert store._cached_ready() is False
        store._ready_checked_at = -1e9  # pretend the retry window has elapsed
        assert store._cached_ready() is None

    def test_a_positive_verdict_is_kept(self):
        store = PgVectorJobStore()
        store._remember(True)
        store._ready_checked_at = -1e9
        assert store._cached_ready() is True


# ─────────────────────────────────────────────────────────────────────────────
# Value handling
# ─────────────────────────────────────────────────────────────────────────────

class TestValueCoercion:
    def test_numpy_floats_become_python_floats(self):
        """asyncpg will not encode numpy.float32 as double precision."""
        numpy = pytest.importorskip("numpy")
        values = _as_floats(numpy.array([0.1, 0.2], dtype=numpy.float32))
        assert all(type(v) is float for v in values)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"a": 1}, {"a": 1}),          # psycopg2 / SQLAlchemy's asyncpg codec
            ('{"a": 1}', {"a": 1}),        # a driver without a jsonb codec
            ("not json", {}),
            (None, {}),
            ("[1, 2]", {}),                 # valid JSON, wrong shape
        ],
    )
    def test_metadata_decoding(self, raw, expected):
        assert _decode_metadata(raw) == expected


# ─────────────────────────────────────────────────────────────────────────────
# Schema agreement
# ─────────────────────────────────────────────────────────────────────────────

def test_migration_dimension_matches_the_model():
    """The column is vector(N); a model of another width silently breaks writes."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m010", "storage/migrations/versions/010_pgvector_job_embeddings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from config.settings import Settings

    default_dim = Settings.model_fields["embedding_dim"].default
    assert module.EMBEDDING_DIM == default_dim
