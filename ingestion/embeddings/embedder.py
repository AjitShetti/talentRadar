"""
ingestion/embeddings/embedder.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Text embedding utilities using ChromaDB's built-in embedding function
(all-MiniLM-L6-v2 by default).

Provides batch embedding generation for job descriptions and candidate
profiles, plus cosine similarity computation for matching.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

# Default embedding function used by ChromaDB
_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

#: Texts handed to the model per call. A live search persists ~55 postings at
#: once, and as a single ONNX batch they added ~316 MB of peak memory on top of
#: the ~200 MB model — enough to kill a 512 MB Render instance seconds after the
#: search answered. Eight at a time peaks near 100 MB and is no slower.
EMBED_BATCH_SIZE = 8


@lru_cache(maxsize=1)
def get_embedding_function() -> embedding_functions.DefaultEmbeddingFunction:
    """Return the process-wide ChromaDB embedding function.

    Cached deliberately. ``DefaultEmbeddingFunction()`` loads the MiniLM ONNX
    model on construction, so calling it per request (which ``embed_texts``
    did, on the hot search path) re-initialised the model every single time.
    """
    return embedding_functions.DefaultEmbeddingFunction()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of texts.

    Parameters
    ----------
    texts : list[str]
        Text strings to embed (e.g. job descriptions, candidate profiles).

    Returns
    -------
    list[list[float]]
        Dense vector embeddings, one per input text.
    """
    ef = get_embedding_function()
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        embeddings.extend(ef(texts[start : start + EMBED_BATCH_SIZE]))
    logger.info("Generated %d embeddings with model %s", len(embeddings), _DEFAULT_EMBEDDING_MODEL)
    return embeddings


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.

    Parameters
    ----------
    vec_a, vec_b : list[float]
        Dense embedding vectors (must be same length).

    Returns
    -------
    float
        Cosine similarity score in [-1, 1]. Higher = more similar.
    """
    import math

    if len(vec_a) != len(vec_b):
        raise ValueError("Vectors must have the same dimension")

    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def batch_cosine_similarity(
    query_vec: list[float], candidates: list[list[float]]
) -> list[float]:
    """
    Compute cosine similarity between a query vector and many candidate vectors.

    Parameters
    ----------
    query_vec : list[float]
        The query embedding (e.g. candidate profile).
    candidates : list[list[float]]
        List of candidate embeddings (e.g. job postings).

    Returns
    -------
    list[float]
        Similarity scores, one per candidate, in the same order.
    """
    return [cosine_similarity(query_vec, c) for c in candidates]
