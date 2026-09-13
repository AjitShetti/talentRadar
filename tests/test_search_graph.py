"""
tests/test_search_graph.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Regression tests for "the search page returns nothing".

The production failures were all in the wiring around retrieval rather than in
retrieval itself, so these drive the compiled ``agent_graph`` end to end with
the network-facing pieces stubbed:

* ``/search/semantic`` ran queries through the *chat* intent classifier, which
  reads a bare role name ("data scientist", "devops") as small talk and routed
  it to ``node_general`` - an empty, successful reply.
* ``route_after_retrieval`` recorded its decision by mutating ``state``, which
  LangGraph discards, and ``node_merge_rank`` returned nothing when no live
  rows arrived - so the API's ``sourcing`` block was always blank.
* A live fan-out that found nothing still took the 8-hour lock, so a query that
  hit a momentarily dead set of boards stayed empty for 8 hours.
* The requested ``limit`` never reached retrieval, capping results at 10.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from agents.graph import agent_graph
from agents.orchestrator import Orchestrator
from agents.sourcing_policy import (
    EMPTY_SOURCED_LOCK_TTL_SECONDS,
    SOURCED_LOCK_TTL_SECONDS,
    sourced_lock_key,
)
from agents.state import AgentResponse, IntentType, QueryContext, RetrievalResult
from services.cache_backend import CacheBackend


@pytest.fixture(autouse=True)
async def _memory_cache() -> AsyncIterator[None]:
    """Run every test against the in-process cache, starting empty."""
    await CacheBackend.reset()
    # Marked initialised with no client: every call takes the memory fallback
    # rather than trying a Redis a developer may have running locally.
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


def _indexed(n: int) -> list[RetrievalResult]:
    return [
        RetrievalResult(job_id=f"job-{i}", title=f"Data Scientist {i}", company="Acme", score=0.9)
        for i in range(n)
    ]


class _StubRAG:
    """Stands in for RAGAgent; records the context it was asked with."""

    calls: ClassVar[list[QueryContext]] = []
    results: ClassVar[list[RetrievalResult]] = []

    async def search_jobs(self, context: QueryContext) -> AgentResponse:
        _StubRAG.calls.append(context)
        return AgentResponse(
            success=True,
            intent=context.intent,
            results=_StubRAG.results[: context.limit],
            summary="indexed",
        )


def _classifier_says(intent: IntentType) -> Callable[[Orchestrator, str], Awaitable[QueryContext]]:
    async def _classify(self: Orchestrator, query: str) -> QueryContext:
        return QueryContext(raw_query=query, intent=intent, keywords=query.split())

    return _classify


def _live_returns(jobs: list[dict[str, Any]]) -> classmethod[Any, ..., Awaitable[dict[str, Any]]]:
    async def _search_all(cls: type, **_: object) -> dict[str, Any]:
        return {"jobs": jobs, "sources_stats": {"linkedin": {"count": len(jobs)}}}

    return classmethod(_search_all)


@pytest.fixture
def stub_rag() -> Iterator[type[_StubRAG]]:
    _StubRAG.calls = []
    _StubRAG.results = []
    with patch("agents.rag_agent.RAGAgent", _StubRAG), \
         patch("agents.orchestrator.AsyncGroq"), \
         patch("agents.graph._schedule_live_persistence"):
        yield _StubRAG


async def test_search_intent_override_beats_a_general_classification(stub_rag: type[_StubRAG]) -> None:
    stub_rag.results = _indexed(12)
    with patch.object(Orchestrator, "_classify_intent", _classifier_says(IntentType.GENERAL)):
        response = await Orchestrator().process_query(
            query="data scientist", intent=IntentType.SEARCH_JOBS
        )

    assert response.intent is IntentType.SEARCH_JOBS
    assert len(response.results) == 10
    assert stub_rag.calls, "retrieval never ran"


async def test_search_override_survives_a_classifier_outage(stub_rag: type[_StubRAG]) -> None:
    stub_rag.results = _indexed(12)

    async def _boom(self: Orchestrator, query: str) -> QueryContext:
        raise RuntimeError("groq is down")

    with patch.object(Orchestrator, "_classify_intent", _boom):
        response = await Orchestrator().process_query(
            query="devops", intent=IntentType.SEARCH_JOBS
        )

    assert response.intent is IntentType.SEARCH_JOBS
    assert response.results


async def test_chat_queries_are_still_classified(stub_rag: type[_StubRAG]) -> None:
    """Without an override the classifier keeps deciding - /query and the copilot rely on it."""
    with patch.object(Orchestrator, "_classify_intent", _classifier_says(IntentType.GENERAL)):
        response = await Orchestrator().process_query(query="hello there")

    assert response.intent is IntentType.GENERAL
    assert not stub_rag.calls


async def test_requested_limit_reaches_retrieval(stub_rag: type[_StubRAG]) -> None:
    stub_rag.results = _indexed(40)
    with patch.object(Orchestrator, "_classify_intent", _classifier_says(IntentType.SEARCH_JOBS)):
        response = await Orchestrator().process_query(
            query="data scientist", intent=IntentType.SEARCH_JOBS, limit=30
        )

    assert stub_rag.calls[0].limit == 30
    assert len(response.results) == 30


async def test_sourcing_metadata_is_reported_when_the_index_answers(stub_rag: type[_StubRAG]) -> None:
    stub_rag.results = _indexed(12)
    live = _live_returns([])
    with patch.object(Orchestrator, "_classify_intent", _classifier_says(IntentType.SEARCH_JOBS)), \
         patch("ingestion.engine.RealtimeScraperEngine.search_all", live):
        response = await Orchestrator().process_query(query="data scientist")

    decision = response.metadata.get("sourcing_decision")
    assert decision and decision["sourced"] is False
    assert decision["reason"]
    assert response.metadata.get("indexed_count") == 10
    assert response.metadata.get("live_count") == 0


async def test_thin_index_goes_live_and_reports_why(stub_rag: type[_StubRAG]) -> None:
    live_jobs = [
        {"id": "live-1", "title": "Data Scientist", "company_name": "Beta", "city": "Pune",
         "source": "linkedin", "source_url": "https://www.linkedin.com/jobs/view/1"},
    ]
    with patch.object(Orchestrator, "_classify_intent", _classifier_says(IntentType.SEARCH_JOBS)), \
         patch("ingestion.engine.RealtimeScraperEngine.search_all", _live_returns(live_jobs)):
        response = await Orchestrator().process_query(query="data scientist")

    assert [r.title for r in response.results] == ["Data Scientist"]
    decision = response.metadata["sourcing_decision"]
    assert decision["sourced"] is True
    assert "below" in decision["reason"], "the policy's reason was lost between nodes"
    assert response.metadata["live_count"] == 1


async def test_empty_fan_out_takes_only_a_short_lock(stub_rag: type[_StubRAG]) -> None:
    captured: dict[str, int] = {}
    real_set = CacheBackend.set

    async def _spy_set(key: str, value: str, ttl_seconds: int) -> None:
        captured[key] = ttl_seconds
        await real_set(key, value, ttl_seconds)

    with patch.object(Orchestrator, "_classify_intent", _classifier_says(IntentType.SEARCH_JOBS)), \
         patch("ingestion.engine.RealtimeScraperEngine.search_all", _live_returns([])), \
         patch.object(CacheBackend, "set", staticmethod(_spy_set)):
        await Orchestrator().process_query(query="data scientist")

    key = sourced_lock_key("data scientist", "India", None)
    assert captured.get(key) == EMPTY_SOURCED_LOCK_TTL_SECONDS
    assert EMPTY_SOURCED_LOCK_TTL_SECONDS < SOURCED_LOCK_TTL_SECONDS


async def test_semantic_endpoint_forces_the_search_intent() -> None:
    from httpx import ASGITransport, AsyncClient

    from api.main import app

    seen: dict[str, Any] = {}

    async def _process(self: Orchestrator, query: str, **kwargs: object) -> AgentResponse:
        seen.update(kwargs)
        return AgentResponse(success=True, intent=IntentType.SEARCH_JOBS, results=_indexed(1))

    with patch("agents.orchestrator.AsyncGroq"), \
         patch.object(Orchestrator, "process_query", _process):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/search/semantic", json={"query": "data scientist", "limit": 30}
            )

    assert response.status_code == 200
    assert seen.get("intent") is IntentType.SEARCH_JOBS
    assert seen.get("limit") == 30
    assert len(response.json()["results"]) == 1


def test_graph_still_compiles_with_every_node() -> None:
    nodes = set(agent_graph.get_graph().nodes)
    assert {"node_classify", "node_rag_retrieve", "node_live_search", "node_merge_rank"} <= nodes
