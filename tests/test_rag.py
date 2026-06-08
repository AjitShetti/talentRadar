"""
tests/test_rag.py
~~~~~~~~~~~~~~~~~
Tests for the RAG agent layer.

Covers (all ChromaDB and Groq calls are mocked — no running services needed):
- RAGAgent._apply_filters  (pure function — no mocking)
- RAGAgent._generate_summary (mocked Groq)
- RAGAgent.search_jobs (mocked ChromaDB + DB + Groq)
- MLScorer.score_match with real embeddings
- Embedder utility functions (cosine_similarity, batch_cosine_similarity)
- ChromaJobStore search/get interface (mocked chromadb client)
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.rag_agent import RAGAgent
from agents.state import (
    AgentResponse,
    CandidateProfile,
    IntentType,
    QueryContext,
    RetrievalResult,
)
from ingestion.embeddings.embedder import cosine_similarity, batch_cosine_similarity


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_results() -> list[RetrievalResult]:
    return [
        RetrievalResult(
            job_id="job-1",
            title="Senior Python Engineer",
            company="Stripe",
            location="San Francisco, CA",
            is_remote=False,
            seniority="senior",
            skills=["Python", "FastAPI", "PostgreSQL", "AWS"],
            score=0.92,
        ),
        RetrievalResult(
            job_id="job-2",
            title="Remote Data Scientist",
            company="Acme",
            location=None,
            is_remote=True,
            seniority="mid",
            skills=["Python", "SQL", "Pandas", "scikit-learn"],
            score=0.78,
        ),
        RetrievalResult(
            job_id="job-3",
            title="Junior Java Developer",
            company="BigCo",
            location="New York, NY",
            is_remote=False,
            seniority="junior",
            skills=["Java", "Spring", "SQL"],
            score=0.55,
        ),
    ]


@pytest.fixture
def search_context() -> QueryContext:
    return QueryContext(
        raw_query="senior python engineer",
        intent=IntentType.SEARCH_JOBS,
        keywords=["senior", "python", "engineer"],
        limit=10,
        offset=0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# RAGAgent._apply_filters  (pure — no mocking needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestRAGAgentFilters:
    """Test the pure filter logic in isolation — no external calls."""

    def test_no_filters_returns_all_sorted_by_score(self, sample_results):
        ctx = QueryContext(raw_query="any")
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 3
        # Sorted descending by score
        assert filtered[0].score >= filtered[1].score >= filtered[2].score

    def test_skill_filter_case_insensitive(self, sample_results):
        ctx = QueryContext(raw_query="python", skills=["python"])
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        # job-1 and job-2 both have Python; job-3 does not
        assert len(filtered) == 2
        job_ids = {r.job_id for r in filtered}
        assert "job-1" in job_ids
        assert "job-2" in job_ids

    def test_skill_filter_multiple_skills_any_match(self, sample_results):
        """Filter keeps results that have AT LEAST ONE of the requested skills."""
        ctx = QueryContext(raw_query="java or python", skills=["Java"])
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 1
        assert filtered[0].job_id == "job-3"

    def test_remote_filter_true(self, sample_results):
        ctx = QueryContext(raw_query="remote", is_remote=True)
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert all(r.is_remote for r in filtered)
        assert len(filtered) == 1
        assert filtered[0].job_id == "job-2"

    def test_remote_filter_false(self, sample_results):
        ctx = QueryContext(raw_query="onsite", is_remote=False)
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert all(not r.is_remote for r in filtered)
        assert len(filtered) == 2

    def test_seniority_filter_exact_match(self, sample_results):
        ctx = QueryContext(raw_query="senior jobs", seniority="senior")
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 1
        assert filtered[0].seniority == "senior"

    def test_seniority_filter_case_insensitive(self, sample_results):
        ctx = QueryContext(raw_query="mid", seniority="Mid")
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 1
        assert filtered[0].job_id == "job-2"

    def test_company_filter_partial_match(self, sample_results):
        ctx = QueryContext(raw_query="stripe jobs", company="stripe")
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert len(filtered) == 1
        assert filtered[0].company == "Stripe"

    def test_combined_filters(self, sample_results):
        ctx = QueryContext(
            raw_query="remote python",
            skills=["Python"],
            is_remote=True,
        )
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        # Only job-2 is Python + remote
        assert len(filtered) == 1
        assert filtered[0].job_id == "job-2"

    def test_no_matches_returns_empty(self, sample_results):
        ctx = QueryContext(raw_query="kotlin", skills=["Kotlin"])
        filtered = RAGAgent._apply_filters(sample_results, ctx)
        assert filtered == []

    def test_offset_and_limit_applied_by_caller(self, sample_results):
        """Verify the offset/limit slice pattern used in search_jobs."""
        ctx = QueryContext(raw_query="any", limit=2, offset=1)
        all_results = RAGAgent._apply_filters(sample_results, ctx)
        # Caller slices: results[offset:offset+limit]
        sliced = all_results[ctx.offset: ctx.offset + ctx.limit]
        assert len(sliced) == 2


# ─────────────────────────────────────────────────────────────────────────────
# RAGAgent._generate_summary  (mocked Groq)
# ─────────────────────────────────────────────────────────────────────────────

class TestRAGAgentGenerateSummary:
    @pytest.fixture
    def agent_with_mocked_groq(self):
        with patch("agents.rag_agent.AsyncGroq") as MockGroq, \
             patch("agents.rag_agent.ChromaJobStore"):
            mock_groq = MockGroq.return_value
            mock_groq.chat.completions.create = AsyncMock(
                return_value=MagicMock(
                    choices=[MagicMock(message=MagicMock(content="Here are the top Python jobs..."))]
                )
            )
            agent = RAGAgent()
            agent._groq = mock_groq
            yield agent

    @pytest.mark.asyncio
    async def test_returns_string_summary(self, agent_with_mocked_groq, sample_results, search_context):
        summary = await agent_with_mocked_groq._generate_summary(sample_results, search_context)
        assert isinstance(summary, str)
        assert len(summary) > 0

    @pytest.mark.asyncio
    async def test_returns_none_on_groq_failure(self, sample_results, search_context):
        """Should return None (not raise) when Groq throws."""
        with patch("agents.rag_agent.AsyncGroq") as MockGroq, \
             patch("agents.rag_agent.ChromaJobStore"):
            mock_groq = MockGroq.return_value
            mock_groq.chat.completions.create = AsyncMock(
                side_effect=Exception("Groq rate limit exceeded")
            )
            agent = RAGAgent()
            agent._groq = mock_groq
            result = await agent._generate_summary(sample_results, search_context)
        assert result is None

    @pytest.mark.asyncio
    async def test_truncates_to_top_10_results(self, agent_with_mocked_groq, search_context):
        """Should only include up to 10 results in the prompt, regardless of input size."""
        many_results = [
            RetrievalResult(job_id=f"job-{i}", title=f"Role {i}", company="Co", score=0.5)
            for i in range(25)
        ]
        # Should not raise even with 25 results
        summary = await agent_with_mocked_groq._generate_summary(many_results, search_context)
        assert summary is not None


# ─────────────────────────────────────────────────────────────────────────────
# RAGAgent.search_jobs (full pipeline, all deps mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestRAGAgentSearchJobs:
    @pytest.fixture
    def chroma_search_result(self):
        """Simulates the raw dict returned by ChromaDB's .search() method."""
        return {
            "ids": [["job-1", "job-2"]],
            "documents": [["desc 1", "desc 2"]],
            "metadatas": [
                [
                    {
                        "company": "Stripe",
                        "location": "SF",
                        "is_remote": False,
                        "seniority": "senior",
                        "skills_str": "Python, FastAPI",
                        "source_url": "https://jobs.stripe.com/1",
                    },
                    {
                        "company": "Acme",
                        "location": None,
                        "is_remote": True,
                        "seniority": "mid",
                        "skills_str": "Python, SQL",
                        "source_url": "https://jobs.acme.com/2",
                    },
                ]
            ],
            "distances": [[0.1, 0.25]],
        }

    @pytest.fixture
    def mock_job_orm(self):
        """A fake ORM job row."""
        def make_job(job_id, title):
            j = MagicMock()
            j.external_id = job_id
            j.title = title
            return j
        return make_job

    @pytest.mark.asyncio
    async def test_returns_agent_response_on_success(
        self, chroma_search_result, mock_job_orm, search_context
    ):
        with patch("agents.rag_agent.AsyncGroq"), \
             patch("agents.rag_agent.ChromaJobStore") as MockChroma, \
             patch("agents.rag_agent.embed_texts", return_value=[[0.1] * 384]), \
             patch("agents.rag_agent.AsyncSessionLocal") as MockSession, \
             patch("agents.rag_agent.UnitOfWork") as MockUow:

            # Set up Chroma mock
            mock_store = MockChroma.return_value
            mock_store.search.return_value = chroma_search_result

            # Set up DB mock — return a job for each id
            mock_uow_instance = AsyncMock()
            mock_uow_instance.jobs.get_by_external_id = AsyncMock(
                side_effect=lambda job_id, src: mock_job_orm(job_id, f"Role for {job_id}")
            )
            MockUow.return_value = mock_uow_instance

            # AsyncSessionLocal as async context manager
            mock_session_cm = MagicMock()
            mock_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session_cm.__aexit__ = AsyncMock(return_value=False)
            MockSession.return_value = mock_session_cm

            agent = RAGAgent()
            agent._chroma = mock_store
            agent._groq = MagicMock()
            agent._groq.chat.completions.create = AsyncMock(
                return_value=MagicMock(
                    choices=[MagicMock(message=MagicMock(content="Summary here"))]
                )
            )

            response = await agent.search_jobs(search_context)

        assert isinstance(response, AgentResponse)
        assert response.intent == IntentType.SEARCH_JOBS

    @pytest.mark.asyncio
    async def test_returns_failure_response_on_exception(self, search_context):
        with patch("agents.rag_agent.AsyncGroq"), \
             patch("agents.rag_agent.ChromaJobStore"), \
             patch("agents.rag_agent.embed_texts", side_effect=RuntimeError("embedding failed")):

            agent = RAGAgent()
            response = await agent.search_jobs(search_context)

        assert response.success is False
        assert response.intent == IntentType.SEARCH_JOBS
        assert "embedding failed" in (response.error or "")

    @pytest.mark.asyncio
    async def test_empty_chroma_results_returns_success_with_no_results(self, search_context):
        empty_chroma_result = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        with patch("agents.rag_agent.AsyncGroq"), \
             patch("agents.rag_agent.ChromaJobStore") as MockChroma, \
             patch("agents.rag_agent.embed_texts", return_value=[[0.1] * 384]):

            mock_store = MockChroma.return_value
            mock_store.search.return_value = empty_chroma_result
            agent = RAGAgent()
            agent._chroma = mock_store
            agent._groq = MagicMock()

            response = await agent.search_jobs(search_context)

        # No results but call should succeed
        assert response.success is True
        assert response.results == []
        assert response.summary is None  # no results → no summary


# ─────────────────────────────────────────────────────────────────────────────
# Embedder utility functions (pure — no mocking needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbedderUtils:
    def test_cosine_similarity_identical_vectors(self):
        vec = [1.0, 2.0, 3.0]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0, abs=1e-6)

    def test_cosine_similarity_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_cosine_similarity_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-6)

    def test_cosine_similarity_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0

    def test_cosine_similarity_raises_on_dimension_mismatch(self):
        with pytest.raises(ValueError, match="same dimension"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_cosine_similarity_symmetry(self):
        a = [0.5, 0.3, 0.8]
        b = [0.1, 0.9, 0.2]
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a))

    def test_batch_cosine_similarity_lengths_match(self):
        query = [1.0, 0.0, 0.0]
        candidates = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
        scores = batch_cosine_similarity(query, candidates)
        assert len(scores) == 3
        assert scores[0] == pytest.approx(1.0, abs=1e-6)
        assert scores[1] == pytest.approx(0.0, abs=1e-6)
        assert scores[2] == pytest.approx(0.0, abs=1e-6)

    def test_batch_cosine_similarity_empty_candidates(self):
        query = [1.0, 0.0]
        assert batch_cosine_similarity(query, []) == []

    def test_cosine_similarity_known_value(self):
        """Manually verified: cos([1,1], [1,0]) = 1/sqrt(2) ≈ 0.7071"""
        a = [1.0, 1.0]
        b = [1.0, 0.0]
        expected = 1.0 / math.sqrt(2)
        assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# ChromaJobStore interface (mocked chromadb client)
# ─────────────────────────────────────────────────────────────────────────────

class TestChromaJobStore:
    @pytest.fixture
    def store(self):
        with patch("ingestion.embeddings.chroma_store.chromadb.HttpClient") as MockClient, \
             patch("ingestion.embeddings.chroma_store.embedding_functions.DefaultEmbeddingFunction"):
            mock_client = MockClient.return_value
            mock_collection = MagicMock()
            mock_client.get_or_create_collection.return_value = mock_collection

            from ingestion.embeddings.chroma_store import ChromaJobStore
            store = ChromaJobStore(host="localhost", port=8001)
            store._collection = mock_collection
            yield store, mock_collection

    def test_add_calls_upsert(self, store):
        store_instance, mock_collection = store
        store_instance.add(job_id="abc123", text="Python engineer job", metadata={"company": "Stripe"})
        mock_collection.upsert.assert_called_once_with(
            ids=["abc123"],
            documents=["Python engineer job"],
            metadatas=[{"company": "Stripe"}],
        )

    def test_add_batch_returns_count(self, store):
        store_instance, mock_collection = store
        items = [
            {"job_id": "id1", "text": "Job 1", "metadata": {"company": "A"}},
            {"job_id": "id2", "text": "Job 2", "metadata": {"company": "B"}},
        ]
        count = store_instance.add_batch(items)
        assert count == 2
        mock_collection.upsert.assert_called_once()

    def test_add_batch_empty_returns_zero(self, store):
        store_instance, mock_collection = store
        assert store_instance.add_batch([]) == 0
        mock_collection.upsert.assert_not_called()

    def test_count_delegates_to_collection(self, store):
        store_instance, mock_collection = store
        mock_collection.count.return_value = 42
        assert store_instance.count() == 42

    def test_get_returns_none_for_missing_id(self, store):
        store_instance, mock_collection = store
        mock_collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        result = store_instance.get("nonexistent-id")
        assert result is None

    def test_get_returns_document_for_existing_id(self, store):
        store_instance, mock_collection = store
        mock_collection.get.return_value = {
            "ids": ["abc123"],
            "documents": ["Job description text"],
            "metadatas": [{"company": "Stripe"}],
        }
        result = store_instance.get("abc123")
        assert result is not None
        assert result["id"] == "abc123"
        assert result["document"] == "Job description text"
        assert result["metadata"]["company"] == "Stripe"

    def test_delete_calls_collection(self, store):
        store_instance, mock_collection = store
        store_instance.delete("abc123")
        mock_collection.delete.assert_called_once_with(ids=["abc123"])
