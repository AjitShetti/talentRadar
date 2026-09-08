"""
ingestion/embeddings/pgvector_store.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Job-description vector store backed by PostgreSQL + pgvector.

This is what replaced the ChromaDB server. The reasoning is entirely about
hosting: ChromaDB has no free managed tier and the self-hosted server wants a
persistent volume, which free PaaS plans do not give you, while Neon and
Supabase both ship the ``vector`` extension on their free tiers. Keeping the
embeddings in the database the app already connects to means one managed
service instead of two, one connection budget, and no extra container.

Interface note
--------------
The public surface deliberately mirrors
:class:`~ingestion.embeddings.chroma_store.ChromaJobStore` --
``add``/``add_batch``/``search``/``get``/``count``/``delete`` -- so the two
are interchangeable behind ``get_vector_store()`` and the callers did not have
to change shape.

Binding vectors without a driver plugin
---------------------------------------
Every value is bound as ``double precision[]`` and cast in SQL
(``CAST(CAST(:v AS double precision[]) AS vector)``). That double cast is not
decoration: writing ``CAST(:v AS vector)`` makes PostgreSQL infer the
*parameter itself* as type ``vector``, which asyncpg cannot encode without a
registered codec. Forcing the parameter to a float array first means both
asyncpg and psycopg2 send something they already understand, and pgvector's
own ``double precision[] -> vector`` cast does the rest. No ``pgvector``
Python package, no per-connection type registration, no event listeners.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

import sqlalchemy as sa

from config.settings import get_settings
from ingestion.embeddings.embedder import embed_texts
from storage.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

_TABLE = "job_embeddings"

#: How long a "the table isn't there" verdict is trusted before re-probing,
#: so a database that gets migrated later is picked up without a redeploy.
_READY_RETRY_SECONDS = 60.0

# ``CAST(CAST(... AS double precision[]) AS vector)`` -- see the module docstring.
_VECTOR_BIND = "CAST(CAST(:{name} AS double precision[]) AS vector)"


def _as_floats(vector: Sequence[float]) -> list[float]:
    """Coerce an embedding to plain Python floats.

    The embedding function returns numpy arrays; asyncpg will not encode
    ``numpy.float32`` as ``double precision``.
    """
    return [float(v) for v in vector]


def _decode_metadata(raw: Any) -> dict[str, Any]:
    """Return the JSONB metadata as a dict whichever driver produced it."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


class PgVectorJobStore:
    """Persists job-description embeddings in the ``job_embeddings`` table.

    Each row has:
      - ``id``       : stable MD5 fingerprint of source_url (matches jobs.embedding_id)
      - ``document`` : the text that was embedded
      - ``metadata`` : flat JSONB of filterable fields
      - ``embedding``: ``vector(N)``, cosine-distance indexed

    Both a synchronous and an asynchronous form of each operation exists. The
    async ones run on the application's existing engine -- no second pool, no
    thread -- and are what the request path uses; the sync ones open a small
    short-lived engine and exist for the ingestion scripts, which are not
    running inside an event loop.
    """

    def __init__(self, *, table: str = _TABLE) -> None:
        self._table = table
        self._settings = get_settings()
        self._sync_engine: sa.engine.Engine | None = None
        #: Remembered "does the table exist" verdict, plus when it was taken.
        self._ready: bool | None = None
        self._ready_checked_at: float | None = None

    # ---------------------------------------------------------------- #
    # Readiness                                                          #
    # ---------------------------------------------------------------- #

    _PROBE = "SELECT to_regclass(:table)"

    def _cached_ready(self) -> bool | None:
        """The remembered verdict, or None if it has to be re-probed."""
        if self._ready is True:
            return True
        if (
            self._ready is False
            and self._ready_checked_at is not None
            and time.monotonic() - self._ready_checked_at < _READY_RETRY_SECONDS
        ):
            return False
        return None

    def _remember(self, ready: bool) -> bool:
        self._ready = ready
        self._ready_checked_at = time.monotonic()
        if not ready:
            logger.warning(
                "%s table is missing -- run `alembic upgrade head`. Semantic "
                "search will use the PostgreSQL relational fallback until then.",
                self._table,
            )
        return ready

    async def _aready(self) -> bool:
        cached = self._cached_ready()
        if cached is not None:
            return cached
        try:
            async with AsyncSessionLocal() as session:
                found = await session.scalar(sa.text(self._PROBE), {"table": self._table})
            return self._remember(found is not None)
        except Exception as exc:  # broad by design: the DB being down is survivable here
            logger.warning("Vector store readiness probe failed: %s", exc)
            return self._remember(False)

    def _ready_sync(self) -> bool:
        cached = self._cached_ready()
        if cached is not None:
            return cached
        try:
            with self._engine().connect() as conn:
                found = conn.execute(sa.text(self._PROBE), {"table": self._table}).scalar()
            return self._remember(found is not None)
        except Exception as exc:  # broad by design
            logger.warning("Vector store readiness probe failed: %s", exc)
            return self._remember(False)

    def _engine(self) -> sa.engine.Engine:
        """Lazily built sync engine for the script/CLI paths.

        Kept tiny on purpose: free Postgres tiers cap total connections low,
        and this engine exists alongside the application's async pool.
        """
        if self._sync_engine is None:
            connect_args: dict[str, Any] = {}
            if self._settings.postgres_ssl:
                connect_args["sslmode"] = "require"
            self._sync_engine = sa.create_engine(
                self._settings.database_url_sync,
                pool_size=1,
                max_overflow=2,
                pool_pre_ping=True,
                pool_recycle=1800,
                connect_args=connect_args,
            )
        return self._sync_engine

    # ---------------------------------------------------------------- #
    # SQL                                                                #
    # ---------------------------------------------------------------- #

    def _upsert_sql(self) -> sa.TextClause:
        vector_bind = _VECTOR_BIND.format(name="embedding")
        return sa.text(
            f"INSERT INTO {self._table} (id, document, metadata, embedding, updated_at) "
            f"VALUES (:id, :document, CAST(:metadata AS jsonb), {vector_bind}, now()) "
            "ON CONFLICT (id) DO UPDATE SET "
            "document = EXCLUDED.document, "
            "metadata = EXCLUDED.metadata, "
            "embedding = EXCLUDED.embedding, "
            "updated_at = now()"
        )

    def _search_sql(self, *, filtered: bool) -> sa.TextClause:
        # `<=>` is cosine distance, matching the "hnsw:space": "cosine" the
        # Chroma collection was created with, so a caller computing
        # `score = 1 - distance` still means the same thing it did.
        vector_bind = _VECTOR_BIND.format(name="query")
        where = "WHERE metadata @> CAST(:filter AS jsonb) " if filtered else ""
        return sa.text(
            f"SELECT id, document, metadata, embedding <=> {vector_bind} AS distance "
            f"FROM {self._table} {where}"
            "ORDER BY distance ASC LIMIT :limit"
        )

    def _rows_to_results(self, rows: Sequence[Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": row.id,
                "document": row.document or "",
                "metadata": _decode_metadata(row.metadata),
                "distance": float(row.distance),
            }
            for row in rows
        ]

    def _search_params(
        self, query: str, n_results: int, where: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Embed the query and assemble bind parameters, or None on failure."""
        try:
            vector = _as_floats(embed_texts([query])[0])
        except Exception as exc:  # broad by design: a model that will not load
            logger.warning("Query embedding failed: %s", exc)
            return None
        params: dict[str, Any] = {"query": vector, "limit": max(1, n_results)}
        if where:
            params["filter"] = json.dumps(where)
        return params

    def _upsert_params(self, items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Embed a batch of documents and build one bind dict per row."""
        texts = [str(item.get("text") or "") for item in items]
        vectors = embed_texts(texts)
        return [
            {
                "id": item["job_id"],
                "document": texts[idx],
                "metadata": json.dumps(item.get("metadata") or {}),
                "embedding": _as_floats(vectors[idx]),
            }
            for idx, item in enumerate(items)
        ]

    # ---------------------------------------------------------------- #
    # Write                                                              #
    # ---------------------------------------------------------------- #

    def add(self, *, job_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        """Upsert a single job-description embedding."""
        self.add_batch([{"job_id": job_id, "text": text, "metadata": metadata or {}}])

    def add_batch(self, items: list[dict[str, Any]]) -> int:
        """Upsert a batch of ``{job_id, text, metadata}`` dicts, returning the count."""
        if not items or not self._ready_sync():
            return 0
        try:
            params = self._upsert_params(items)
            with self._engine().begin() as conn:
                conn.execute(self._upsert_sql(), params)
        except Exception as exc:  # broad by design: ingestion must not die on this
            logger.warning("pgvector batch upsert failed: %s", exc)
            return 0
        logger.info("pgvector upsert: %d documents", len(items))
        return len(items)

    async def aadd_batch(self, items: list[dict[str, Any]]) -> int:
        """Async form of :meth:`add_batch`, on the application's engine."""
        if not items or not await self._aready():
            return 0
        try:
            params = self._upsert_params(items)
            async with AsyncSessionLocal() as session:
                await session.execute(self._upsert_sql(), params)
                await session.commit()
        except Exception as exc:  # broad by design
            logger.warning("pgvector batch upsert failed: %s", exc)
            return 0
        logger.info("pgvector upsert: %d documents", len(items))
        return len(items)

    # ---------------------------------------------------------------- #
    # Read / search                                                      #
    # ---------------------------------------------------------------- #

    def search(
        self,
        query: str,
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Cosine-similarity search returning id/document/metadata/distance."""
        if not self._ready_sync():
            return []
        params = self._search_params(query, n_results, where)
        if params is None:
            return []
        try:
            with self._engine().connect() as conn:
                rows = conn.execute(self._search_sql(filtered=bool(where)), params).all()
        except Exception as exc:  # broad by design: degrade to "no matches"
            logger.warning("pgvector search failed: %s", exc)
            return []
        return self._rows_to_results(rows)

    async def asearch(
        self,
        query: str,
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Async form of :meth:`search` -- what the request path uses."""
        if not await self._aready():
            return []
        params = self._search_params(query, n_results, where)
        if params is None:
            return []
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(self._search_sql(filtered=bool(where)), params)
                rows = result.all()
        except Exception as exc:  # broad by design
            logger.warning("pgvector search failed: %s", exc)
            return []
        return self._rows_to_results(rows)

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Fetch one stored document by id, or None."""
        if not self._ready_sync():
            return None
        stmt = sa.text(f"SELECT id, document, metadata FROM {self._table} WHERE id = :id")
        try:
            with self._engine().connect() as conn:
                row = conn.execute(stmt, {"id": job_id}).first()
        except Exception as exc:  # broad by design
            logger.warning("pgvector get failed: %s", exc)
            return None
        if row is None:
            return None
        return {
            "id": row.id,
            "document": row.document or "",
            "metadata": _decode_metadata(row.metadata),
        }

    def count(self) -> int:
        """Number of stored embeddings (0 when the table is absent)."""
        if not self._ready_sync():
            return 0
        try:
            with self._engine().connect() as conn:
                total = conn.execute(sa.text(f"SELECT count(*) FROM {self._table}")).scalar()
            return int(total or 0)
        except Exception as exc:  # broad by design
            logger.warning("pgvector count failed: %s", exc)
            return 0

    # ---------------------------------------------------------------- #
    # Delete                                                             #
    # ---------------------------------------------------------------- #

    def delete(self, job_id: str) -> None:
        """Remove one embedding by id."""
        if not self._ready_sync():
            return
        try:
            with self._engine().begin() as conn:
                conn.execute(sa.text(f"DELETE FROM {self._table} WHERE id = :id"), {"id": job_id})
        except Exception as exc:  # broad by design
            logger.warning("pgvector delete failed: %s", exc)

    def reset_collection(self) -> None:
        """Delete every embedding. Use only in tests."""
        if not self._ready_sync():
            return
        with self._engine().begin() as conn:
            conn.execute(sa.text(f"TRUNCATE TABLE {self._table}"))
        logger.warning("pgvector table '%s' was emptied.", self._table)

    def dispose(self) -> None:
        """Close the sync engine, if one was ever opened."""
        if self._sync_engine is not None:
            self._sync_engine.dispose()
            self._sync_engine = None
