"""
ingestion/embeddings/chroma_store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Thin wrapper around chromadb for storing and querying job description embeddings.

Uses chromadb's built-in ``DefaultEmbeddingFunction`` (all-MiniLM-L6-v2 via
sentence-transformers) so no separate embedding step is required — chromadb
downloads the model on first use and caches it.

For production you can swap in a custom embedding function (OpenAI, Groq, etc.)
by passing ``embedding_fn`` to ``ChromaJobStore.__init__``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import chromadb

from config.settings import get_settings
from ingestion.embeddings.embedder import get_embedding_function

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "job_descriptions"

#: Cached store, plus when a failed connection was last attempted.
_store: "ChromaJobStore | None" = None
_store_checked_at: float | None = None
#: How long to wait before re-probing a Chroma server that was unreachable.
_STORE_RETRY_SECONDS = 60.0


class ChromaJobStore:
    """
    Persists parsed job descriptions as vector embeddings in ChromaDB.

    Each document stored has:
      - ``id``        : stable MD5 fingerprint of source_url (matches jobs.embedding_id)
      - ``document``  : full description text fed to the embedding model
      - ``metadata``  : searchable flat fields (title, company, skills_str, location …)

    Example
    -------
    ::

        store = ChromaJobStore()
        store.add(job_id="abc123", text="Senior SWE at Stripe...", metadata={...})
        results = store.search("python kubernetes remote", n_results=5)
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        embedding_fn: Any | None = None,
    ) -> None:
        settings = get_settings()
        _host = host or settings.chroma_host
        _port = port or settings.chroma_port

        self._client = chromadb.HttpClient(host=_host, port=_port)

        # Default: all-MiniLM-L6-v2 (chromadb downloads on first use).
        # Comes from the cached accessor: DefaultEmbeddingFunction() loads an
        # ONNX model, so constructing one per store was a real per-request cost.
        _emb_fn = embedding_fn or get_embedding_function()

        self._embedding_fn = _emb_fn
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=_emb_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ChromaJobStore connected: %s:%d | collection=%s",
            _host, _port, _COLLECTION_NAME,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Write
    # ─────────────────────────────────────────────────────────────────────────

    def add(
        self,
        *,
        job_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Upsert a single job description embedding.

        Parameters
        ----------
        job_id:
            Stable identifier (matches ``jobs.embedding_id`` in Postgres).
        text:
            Full plain-text of the job description to embed.
        metadata:
            Flat dict of filterable fields (title, company, skills_str, …).
            Values must be str, int, float, or bool — no nested objects.
        """
        self._collection.upsert(
            ids=[job_id],
            documents=[text],
            metadatas=[metadata or {}],
        )
        logger.debug("ChromaDB upsert: id=%s", job_id)

    def add_batch(
        self,
        items: list[dict[str, Any]],
    ) -> int:
        """
        Upsert a batch of job descriptions.

        Parameters
        ----------
        items:
            List of dicts, each with keys: ``job_id``, ``text``, ``metadata``.

        Returns
        -------
        int
            Number of items successfully upserted.
        """
        if not items:
            return 0

        ids = [i["job_id"] for i in items]
        docs = [i["text"] for i in items]
        metas = [i.get("metadata", {}) for i in items]

        chunk_size = 100
        for idx in range(0, len(ids), chunk_size):
            chunk_ids = ids[idx : idx + chunk_size]
            chunk_docs = docs[idx : idx + chunk_size]
            chunk_metas = metas[idx : idx + chunk_size]
            self._collection.upsert(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)

        logger.info("ChromaDB batch upsert: %d documents", len(ids))
        return len(ids)

    # ─────────────────────────────────────────────────────────────────────────
    # Read / search
    # ─────────────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic similarity search over stored job descriptions.

        Parameters
        ----------
        query:
            Free-text query (e.g. "python backend engineer remote").
        n_results:
            Number of nearest neighbours to return.
        where:
            Optional chromadb metadata filter, e.g. ``{"company": "Stripe"}``.

        Returns
        -------
        list[dict]
            Each dict has: ``id``, ``document``, ``metadata``, ``distance``.
        """
        # ``n_results`` is a ceiling, not a demand: chroma returns fewer when
        # the collection is smaller. The old ``min(n_results, self.count())``
        # spent a whole extra HTTP round trip per search to learn that, and
        # collapsed to n_results=1 on an empty collection.
        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": max(1, n_results),
        }
        if where:
            kwargs["where"] = where

        try:
            results = self._collection.query(**kwargs)
        except Exception as exc:
            # A missing/unreachable collection must degrade to "no matches",
            # not take the whole search endpoint down with it.
            logger.warning("ChromaDB query failed: %s", exc)
            return []

        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        output: list[dict[str, Any]] = []
        for i, doc_id in enumerate(ids):
            output.append({
                "id": doc_id,
                "document": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else 1.0,
            })
        return output

    async def asearch(
        self,
        query: str,
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Async form of :meth:`search`.

        chromadb's client is synchronous and talks HTTP, so calling it from a
        request handler blocks the event loop for every other user. The
        pgvector backend implements this natively; here it is a thread.
        """
        return await asyncio.to_thread(
            lambda: self.search(query, n_results=n_results, where=where)
        )

    async def aadd_batch(self, items: list[dict[str, Any]]) -> int:
        """Async form of :meth:`add_batch` — see :meth:`asearch`."""
        return await asyncio.to_thread(self.add_batch, items)

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Fetch a single stored document by its ID."""
        result = self._collection.get(ids=[job_id], include=["documents", "metadatas"])
        if not result["ids"]:
            return None
        return {
            "id": result["ids"][0],
            "document": result["documents"][0],
            "metadata": result["metadatas"][0],
        }

    def count(self) -> int:
        """Return the total number of documents in the collection."""
        return self._collection.count()

    # ─────────────────────────────────────────────────────────────────────────
    # Delete
    # ─────────────────────────────────────────────────────────────────────────

    def delete(self, job_id: str) -> None:
        """Remove a document by ID."""
        self._collection.delete(ids=[job_id])

    def reset_collection(self) -> None:
        """⚠️ Drop and recreate the collection. Use only in tests."""
        self._client.delete_collection(_COLLECTION_NAME)
        # Recreate with the *same* embedding function. Omitting it silently
        # rebound the collection to chromadb's default, so a store constructed
        # with a custom embedding_fn stopped using it after a reset.
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.warning("ChromaDB collection '%s' was reset.", _COLLECTION_NAME)


def get_chroma_store() -> ChromaJobStore | None:
    """Process-wide :class:`ChromaJobStore`, or ``None`` if Chroma is down.

    Two jobs:

    * **Cache.** Constructing a store opens an HTTP client and binds an ONNX
      embedding model. A ``RAGAgent`` is built per query, so doing that per
      request was a large, invisible cost on the search path.
    * **Degrade, do not crash.** ``chromadb.HttpClient`` pings the server in
      its constructor and raises if it cannot reach one. That exception used
      to escape ``RAGAgent.__init__`` — outside the ``try`` in
      ``search_jobs`` — so a Chroma outage 500'd every semantic search
      instead of falling through to the PostgreSQL fallback that
      ``_search_db_fallback`` already implements. Returning ``None`` here
      makes the vector store genuinely optional.

    The failed lookup is *not* cached permanently: it is retried once the
    retry window elapses, so a Chroma container that comes up later is picked
    up without a redeploy.
    """
    global _store, _store_checked_at

    now = time.monotonic()
    if _store is not None:
        return _store
    if _store_checked_at is not None and now - _store_checked_at < _STORE_RETRY_SECONDS:
        return None

    _store_checked_at = now
    try:
        _store = ChromaJobStore()
    except Exception as exc:
        logger.warning(
            "ChromaDB unavailable (%s) - semantic search will fall back to PostgreSQL", exc
        )
        return None
    return _store
