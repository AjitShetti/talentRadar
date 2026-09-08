"""
ingestion/embeddings/vector_store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
One entry point for "where do job embeddings live".

``get_vector_store()`` returns the backend named by ``VECTOR_BACKEND``:

``pgvector`` (default)
    :class:`~ingestion.embeddings.pgvector_store.PgVectorJobStore` -- the
    embeddings live in the same managed Postgres as everything else. This is
    the only backend with a genuinely free managed host.

``chroma``
    :class:`~ingestion.embeddings.chroma_store.ChromaJobStore` -- the legacy
    ChromaDB server, kept for self-hosted installs that already run one.

``none``
    ``None``. Semantic search falls through to the relational query in
    ``agents/rag_agent.py`` and the embedding model is never loaded, which is
    what makes the API fit a 512 MB instance when it has to.

Every caller must treat ``None`` as an ordinary outcome, not an error: the
vector store is an optimisation over a relational search that already works.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol, runtime_checkable

from config.settings import get_settings

logger = logging.getLogger(__name__)


@runtime_checkable
class VectorStore(Protocol):
    """What the RAG agent and the ingestion pipeline need from a vector store."""

    def add(self, *, job_id: str, text: str, metadata: dict[str, Any] | None = ...) -> None: ...

    def add_batch(self, items: list[dict[str, Any]]) -> int: ...

    async def aadd_batch(self, items: list[dict[str, Any]]) -> int: ...

    def search(
        self, query: str, *, n_results: int = ..., where: dict[str, Any] | None = ...
    ) -> list[dict[str, Any]]: ...

    async def asearch(
        self, query: str, *, n_results: int = ..., where: dict[str, Any] | None = ...
    ) -> list[dict[str, Any]]: ...

    def get(self, job_id: str) -> dict[str, Any] | None: ...

    def count(self) -> int: ...

    def delete(self, job_id: str) -> None: ...


#: Process-wide store, plus when a failed construction was last attempted.
_store: VectorStore | None = None
_checked_at: float | None = None
#: How long to wait before re-attempting a backend that could not be built.
_RETRY_SECONDS = 60.0


def _build(backend: str) -> VectorStore | None:
    if backend == "none":
        logger.info("VECTOR_BACKEND=none -- semantic search uses the relational path.")
        return None

    if backend == "chroma":
        from ingestion.embeddings.chroma_store import ChromaJobStore

        return ChromaJobStore()

    from ingestion.embeddings.pgvector_store import PgVectorJobStore

    return PgVectorJobStore()


def get_vector_store() -> VectorStore | None:
    """Return the process-wide vector store, or ``None`` if there isn't one.

    Cached for two reasons. Constructing a store binds an ONNX embedding
    model (and, for Chroma, opens an HTTP client), and a ``RAGAgent`` is built
    per request -- doing that per request was a large, invisible cost on the
    search path. And a backend that cannot be built must degrade rather than
    raise: ``chromadb.HttpClient`` pings its server in the constructor, and
    that exception used to escape ``RAGAgent.__init__`` -- outside the ``try``
    in ``search_jobs`` -- so an outage 500'd every semantic search instead of
    falling through to the PostgreSQL fallback.

    A failed construction is not cached permanently; it is retried once the
    retry window elapses, so a backend that comes up later is picked up
    without a redeploy.
    """
    global _store, _checked_at

    backend = get_settings().vector_backend
    if backend == "none":
        return None

    if _store is not None:
        return _store

    now = time.monotonic()
    if _checked_at is not None and now - _checked_at < _RETRY_SECONDS:
        return None
    _checked_at = now

    try:
        _store = _build(backend)
    except Exception as exc:  # broad by design: an absent vector store is survivable
        logger.warning(
            "Vector backend %r is unavailable (%s) -- semantic search will fall "
            "back to PostgreSQL.",
            backend, exc,
        )
        return None
    return _store


def reset_vector_store_cache() -> None:
    """Drop the cached store. For tests and for settings changes at runtime."""
    global _store, _checked_at
    _store = None
    _checked_at = None

