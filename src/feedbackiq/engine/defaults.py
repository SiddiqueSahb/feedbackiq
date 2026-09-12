"""
The default engine: the dissertation's models, wired into the engine's interfaces.

One function, so the API, a script or a notebook all get the same configuration:

    engine = build_default_engine()
    result = engine.analyse_batch(items)

Everything it assembles is injectable, so tests build engines with fakes instead and
never load a model. Model loading stays lazy - constructing the engine touches no
weights; the first `analyse_batch` does.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Sequence

from feedbackiq.core.logging import get_logger
from feedbackiq.engine.categorisation import ZeroShotCategoriser, categories_from_dicts
from feedbackiq.engine.insights import InsightGenerator
from feedbackiq.engine.llm import LLMAdapter
from feedbackiq.engine.pipeline import AnalyticsEngine
from feedbackiq.engine.retrieval import CorpusRetriever, NullRetriever, Retriever
from feedbackiq.engine.sentiment import (
    BatchSentimentModel,
    SentimentWithFallback,
    SingleCallSentimentModel,
)
from feedbackiq.engine.types import Category

log = get_logger("engine.defaults")


@lru_cache(maxsize=1)
def default_categories() -> tuple[Category, ...]:
    """
    The 24-category complaint taxonomy discovered in the dissertation.

    Loaded from the file the discovery script produced, through the existing loader (so
    there is one implementation of "read the taxonomy"), and converted into the
    engine's `Category` type. Cached because it does not change while the process runs.

    This is a *default*, not a requirement: `analyse_batch(categories=...)` takes any
    taxonomy, which is how a customer's own categories will arrive later.

    Milestone 5A: read from `core.taxonomy` - the one packaged source the database seed
    also uses - instead of `nlp.categoriser`, which loaded it from gitignored research
    data and silently substituted 7 categories when that was absent (CI, container image,
    fresh clone). Raises `TaxonomyError` rather than categorising against a substitute.
    See docs/production/milestone-05a.md.
    """
    from feedbackiq.core.taxonomy import load_default_taxonomy

    return categories_from_dicts(load_default_taxonomy())


def build_sentiment_model():
    """Fine-tuned DistilBERT in batches, with VADER as the fallback."""
    from feedbackiq.nlp.sentiment import get_vader

    primary = BatchSentimentModel()
    fallback = SingleCallSentimentModel(_LazyVader(get_vader), model_version="vader")

    return SentimentWithFallback(primary=primary, fallback=fallback)


class _LazyVader:
    """Defers loading VADER until the fallback is actually needed."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self._model = None

    def predict(self, text: str):
        if self._model is None:
            self._model = self._factory()
        return self._model.predict(text)


def build_default_engine(
    *,
    retriever: Retriever | None = None,
    categories: Sequence[Category] | None = None,
    with_insights: bool = True,
) -> AnalyticsEngine:
    """
    Assemble the engine used in production today.

    retriever      defaults to the dissertation corpus (`CorpusRetriever`). Pass
                   `NullRetriever()` for analysis without evidence, or a
                   customer-specific implementation once one exists.
    categories     defaults to the discovered 24-category taxonomy.
    with_insights  False leaves the LLM out entirely - no key needed, no cost.
    """
    llm = LLMAdapter()

    insight_generator = None
    if with_insights:
        if llm.available:
            insight_generator = InsightGenerator(llm)
        else:
            log.warning("No LLM configured; the engine will run without insight generation.")

    return AnalyticsEngine(
        sentiment=build_sentiment_model(),
        categoriser=ZeroShotCategoriser(),
        retriever=retriever if retriever is not None else CorpusRetriever(),
        insight_generator=insight_generator,
        default_categories=categories if categories is not None else default_categories(),
    )


def build_sentiment_only_engine() -> AnalyticsEngine:
    """
    Sentiment alone: no categorisation, no retrieval, no LLM.

    What bulk scoring needs (the CSV upload path), and the cheapest possible engine.
    """
    return AnalyticsEngine(
        sentiment=build_sentiment_model(),
        categoriser=None,
        retriever=NullRetriever(),
        insight_generator=None,
    )
