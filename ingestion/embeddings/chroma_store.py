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

import logging
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

from config.settings import get_settings

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "job_descriptions"


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

        # Default: all-MiniLM-L6-v2 (chromadb downloads on first use)
        _emb_fn = embedding_fn or embedding_functions.DefaultEmbeddingFunction()

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

        self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
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
        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": min(n_results, self.count() or 1),
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        output: list[dict[str, Any]] = []
        for i, doc_id in enumerate(results["ids"][0]):
            output.append({
                "id": doc_id,
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
        return output

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
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.warning("ChromaDB collection '%s' was reset.", _COLLECTION_NAME)
