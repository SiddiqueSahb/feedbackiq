"""
Embedding generation and FAISS semantic search service.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger

log = get_logger("nlp.embedding_service")

_resources_lock = threading.Lock()


# lru_cache alone won't stop two threads both loading this (model + FAISS
# index + 407MB parquet) on a cold cache; the lock serializes it.
@lru_cache(maxsize=1)
def _load_resources_uncached():

    import faiss
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding resources: model, FAISS index, dataframe...")

    model = SentenceTransformer(settings.EMBEDDING_MODEL)

    if not settings.index_file.exists():
        raise FileNotFoundError(
            f"FAISS index not found at {settings.index_file}\n"
            "Run: python scripts/build_index.py"
        )

    index = faiss.read_index(str(settings.index_file))
    log.info("FAISS index loaded | vectors=%d | dim=%d", index.ntotal, index.d)

    if not settings.data_file.exists():
        raise FileNotFoundError(
            f"Dataset not found at {settings.data_file}"
        )

    df = pd.read_parquet(settings.data_file)
    log.info("Review dataframe loaded | rows=%d", len(df))

    return model, index, df


def _load_resources():
    """Thread-safe wrapper — see comment above _load_resources_uncached."""
    with _resources_lock:
        return _load_resources_uncached()


def semantic_search(
    query: str,
    top_k: int = 10,
    platform_filter: Optional[str] = None,
    sentiment_filter: Optional[str] = None,
    min_rating: float = 1.0,
) -> list[dict]:
    """
    Semantic search over customer reviews, optionally filtered by platform
    (amazon | yelp | twitter_airline), sentiment (positive | neutral |
    negative), and minimum rating.
    """

    if not query.strip():
        return []

    model, index, df = _load_resources()

    query_embedding = np.ascontiguousarray(
        model.encode([query], normalize_embeddings=True).astype(np.float32)
    )

    fetch_k = min(top_k * 5, index.ntotal)

    scores, indices = index.search(query_embedding, fetch_k)

    results = []

    for score, idx in zip(scores[0], indices[0]):

        if idx < 0 or idx >= len(df):
            continue

        row = df.iloc[idx]

        if platform_filter and row["platform"] != platform_filter:
            continue

        if sentiment_filter and row["sentiment_label"] != sentiment_filter:
            continue

        if row["rating"] < min_rating:
            continue

        results.append(
            {
                "review_id": row["review_id"],
                "text": row["text"],
                "platform": row["platform"],
                "rating": row["rating"],
                "sentiment_label": row["sentiment_label"],
                "similarity_score": round(float(score), 4),
            }
        )

        if len(results) >= top_k:
            break

    log.debug(
        "Semantic search | query='%s' | fetch_k=%d | returned=%d",
        query[:60], fetch_k, len(results),
    )

    return results


def embed_text(text: str) -> np.ndarray:
    """Normalized embedding for a single text; used by the FastAPI analyse endpoint."""

    model, _, _ = _load_resources()

    return model.encode([text],normalize_embeddings=True)[0]