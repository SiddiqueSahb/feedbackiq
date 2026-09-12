"""
Semantic search service — thin wrapper around nlp/embedding_service.py.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from nlp.embedding_service import semantic_search
from logger import get_logger

log = get_logger("service.search")


def search_reviews(
    query: str,
    top_k: int = 10,
    platform_filter: str | None = None,
    sentiment_filter: str | None = None,
    min_rating: float = 1.0,
) -> list[dict]:
    """Run a semantic search over the review corpus and return the matches."""

    if not query.strip():
        raise ValueError("Search query cannot be empty.")

    log.info(
        "Semantic search | query='%s' | top_k=%d | platform=%s | sentiment=%s",
        query[:60], top_k, platform_filter, sentiment_filter,
    )

    return semantic_search(
        query=query,
        top_k=top_k,
        platform_filter=platform_filter,
        sentiment_filter=sentiment_filter,
        min_rating=min_rating,
    )
