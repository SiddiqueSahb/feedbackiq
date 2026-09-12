"""
How the engine gets supporting evidence - without knowing where it comes from.

    AnalyticsEngine  ->  Retriever (protocol)  ->  today: the dissertation corpus
                                                   later: one customer's own feedback

The engine only requires `search(query, limit) -> Sequence[Evidence]`. That is the
seam a future SaaS milestone needs: organisation-scoped retrieval becomes a different
implementation of this protocol, not a change inside the engine. Nothing here knows
about tenants, and nothing here creates one.
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence, runtime_checkable

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.engine.errors import RetrievalError
from feedbackiq.engine.types import Evidence

log = get_logger("engine.retrieval")


@runtime_checkable
class Retriever(Protocol):
    """Anything that can find feedback similar to a query."""

    def search(self, query: str, limit: int = 5) -> Sequence[Evidence]:
        ...


class NullRetriever:
    """
    Finds nothing, always.

    The default, so an engine with no retrieval configured produces analyses without
    evidence instead of reaching for a global corpus by accident.
    """

    def search(self, query: str, limit: int = 5) -> Sequence[Evidence]:
        return ()


class CallableRetriever:
    """Wraps a plain function `(query, limit) -> Sequence[Evidence]`."""

    def __init__(self, search_function: Callable[[str, int], Sequence[Evidence]]) -> None:
        self._search = search_function

    def search(self, query: str, limit: int = 5) -> Sequence[Evidence]:
        return self._search(query, limit)


class CorpusRetriever:
    """
    Evidence from the dissertation corpus, via the existing FAISS semantic search.

    This is the compatibility implementation: it keeps `/api/sentiment/analyse`
    behaving as it does today. A future milestone adds an implementation backed by one
    organisation's own feedback; the engine will not need to change.

    `search_function` is injectable so tests never load the 1 GB index.
    """

    def __init__(
        self,
        search_function: Callable[..., list[dict]] | None = None,
        *,
        threshold: float | None = None,
        sentiment_filter: str | None = None,
    ) -> None:
        self._search_function = search_function
        self.threshold = (
            threshold if threshold is not None else settings.ANALYSE_SIMILARITY_THRESHOLD
        )
        self.sentiment_filter = sentiment_filter

    def _semantic_search(self, query: str, limit: int) -> list[dict]:
        if self._search_function is None:
            # Imported lazily: importing it pulls in faiss and the embedding model.
            from feedbackiq.nlp.embedding_service import semantic_search

            self._search_function = semantic_search

        kwargs: dict[str, object] = {"query": query, "top_k": limit}
        if self.sentiment_filter:
            kwargs["sentiment_filter"] = self.sentiment_filter

        return self._search_function(**kwargs)

    def search(self, query: str, limit: int = 5) -> Sequence[Evidence]:
        try:
            rows = self._semantic_search(query, limit)
        except Exception as exc:
            # Never silently return "no evidence": that would look identical to a
            # genuine no-match and quietly degrade every analysis.
            raise RetrievalError(f"Retrieval failed: {type(exc).__name__}: {exc}") from exc

        return evidence_from_rows(rows, threshold=self.threshold)


def evidence_from_rows(rows: Sequence[dict], *, threshold: float = 0.0) -> tuple[Evidence, ...]:
    """
    Turn the existing search result dicts into `Evidence`, dropping weak matches.

    Weak matches are excluded rather than passed along, because anything handed to the
    LLM is presented to it as supporting evidence - the same rule the dissertation's
    analysis path applies at 0.35.
    """
    evidence: list[Evidence] = []

    for row in rows:
        score = float(row.get("similarity_score") or 0.0)
        if score < threshold:
            continue

        evidence.append(
            Evidence(
                id=str(row.get("review_id", "")),
                text=str(row.get("text", "")),
                score=round(score, 4),
                metadata={
                    key: row[key]
                    for key in ("platform", "rating", "sentiment_label")
                    if key in row
                },
            )
        )

    return tuple(evidence)
