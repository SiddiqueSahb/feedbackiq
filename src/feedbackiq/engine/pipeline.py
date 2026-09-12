"""
The analytics engine: feedback in, typed results out.

    items ──▶ normalise ──▶ sentiment (batched) ──▶ sentiment gate ──▶ categorisation
                                                                          │
                                        optional: evidence retrieval ─────┤
                                        optional: LLM insight ────────────┘
                                                                          ▼
                                                          BatchAnalysis (typed, ordered)

Properties the rest of the product can rely on:
  * results come back in input order, one per input item
  * a failure in one record does not lose the batch - that record is marked failed
  * nothing here touches HTTP, a database, or a specific customer's data
  * every batch carries the manifest of models and prompts that produced it
"""

from __future__ import annotations

from typing import Sequence

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.engine.categorisation import CategorisationOutcome, Categoriser
from feedbackiq.engine.errors import EngineError
from feedbackiq.engine.insights import InsightGenerator
from feedbackiq.engine.preprocessing import normalise_text
from feedbackiq.engine.retrieval import NullRetriever, Retriever
from feedbackiq.engine.sentiment import SentimentModel
from feedbackiq.engine.types import (
    BatchAnalysis,
    Category,
    FeedbackItem,
    ItemAnalysis,
    UsageStats,
)
from feedbackiq.engine.versions import manifest

log = get_logger("engine.pipeline")


def gated_sentiments() -> frozenset[str]:
    """
    Which sentiments get an issue category.

    THE SENTIMENT GATE. The dissertation's categoriser assigned complaint categories to
    every item, including clearly positive feedback - a defect the README states and a
    strict xfail test recorded. The fix is not a new model: it is asking the question
    only when it makes sense.

    Default: negative and neutral.
      * positive is excluded because the taxonomy was *discovered* from negative
        reviews (BERTopic over negative feedback, consolidated into 24 complaint
        categories). A positive item has no complaint to categorise, so any label is
        noise - which is exactly what the defect produced.
      * neutral is kept because neutral-sounding text frequently contains a complaint,
        and the 0.35 confidence threshold already returns "Unclassified" when the match
        is weak. Excluding neutral would lose real issues; excluding positive loses
        nothing.

    Configurable via CATEGORISE_SENTIMENTS, so a customer whose taxonomy includes
    praise categories can widen it without a code change.
    """
    return settings.categorise_sentiments


class AnalyticsEngine:
    """
    Stateless analysis over a batch of feedback.

    Every collaborator is injected, so the engine can run with real models, with fakes
    in tests, or with a customer-specific retriever later:

        engine = AnalyticsEngine(sentiment=..., categoriser=..., retriever=...)
        result = engine.analyse_batch(items, categories=categories)
    """

    def __init__(
        self,
        *,
        sentiment: SentimentModel,
        categoriser: Categoriser | None = None,
        retriever: Retriever | None = None,
        insight_generator: InsightGenerator | None = None,
        default_categories: Sequence[Category] = (),
        evidence_limit: int | None = None,
    ) -> None:
        self.sentiment = sentiment
        self.categoriser = categoriser
        self.retriever = retriever or NullRetriever()
        self.insight_generator = insight_generator
        self.default_categories = tuple(default_categories)
        self.evidence_limit = evidence_limit or settings.RAG_TOP_K

    # ---------------------------------------------------------------- public

    def analyse_batch(
        self,
        items: Sequence[FeedbackItem],
        *,
        categories: Sequence[Category] | None = None,
        with_evidence: bool = False,
        with_insights: bool = False,
    ) -> BatchAnalysis:
        """
        Analyse every item and return one result per input, in the same order.

        `with_evidence` and `with_insights` are off by default: retrieval and an LLM
        call per item are the expensive parts, and a bulk import does not need them.
        """
        if not items:
            return BatchAnalysis(results=(), usage=UsageStats(), versions=manifest())

        taxonomy = tuple(categories) if categories is not None else self.default_categories
        texts = [normalise_text(item.text) for item in items]

        # Stage 1: sentiment for the whole batch in one go.
        results = self._score_sentiment(items, texts)

        # Stage 2: categorisation, for the gated sentiments only.
        results = self._categorise(results, texts, taxonomy)

        # Stages 3 and 4 are per item, because both are optional and can fail alone.
        if with_evidence or with_insights:
            results = [
                self._enrich(result, text, with_evidence=with_evidence, with_insights=with_insights)
                for result, text in zip(results, texts)
            ]

        usage = self.insight_generator.llm.usage if self.insight_generator else UsageStats()

        return BatchAnalysis(results=tuple(results), usage=usage, versions=manifest())

    def analyse_one(
        self,
        item: FeedbackItem,
        *,
        categories: Sequence[Category] | None = None,
        with_evidence: bool = True,
        with_insights: bool = True,
    ) -> ItemAnalysis:
        """One item, evidence and insight on by default - the interactive path."""
        batch = self.analyse_batch(
            [item],
            categories=categories,
            with_evidence=with_evidence,
            with_insights=with_insights,
        )

        return batch.results[0]

    # ---------------------------------------------------------------- stages

    def _score_sentiment(
        self, items: Sequence[FeedbackItem], texts: Sequence[str]
    ) -> list[ItemAnalysis]:
        try:
            predictions = self.sentiment.predict_batch(texts)
        except Exception as exc:
            # The whole batch failed (a model that will not load, say). Every record is
            # marked failed rather than the caller receiving an exception with no
            # information about which items were affected.
            log.exception("Sentiment scoring failed for the whole batch.")
            message = f"sentiment failed: {type(exc).__name__}: {exc}"
            return [ItemAnalysis(feedback_id=item.id).with_error(message) for item in items]

        if len(predictions) != len(items):
            raise EngineError(
                f"sentiment model returned {len(predictions)} predictions for {len(items)} items"
            )

        return [
            ItemAnalysis(feedback_id=item.id, sentiment=prediction)
            for item, prediction in zip(items, predictions)
        ]

    def _categorise(
        self,
        results: list[ItemAnalysis],
        texts: Sequence[str],
        categories: Sequence[Category],
    ) -> list[ItemAnalysis]:
        if self.categoriser is None or not categories:
            return results

        allowed = gated_sentiments()

        # Only gated, still-healthy records go to the categoriser.
        indices: list[int] = []
        for index, result in enumerate(results):
            if not result.ok or result.sentiment is None:
                continue
            if result.sentiment.label.lower() not in allowed:
                results[index] = _replace(
                    results[index],
                    categorisation_skipped=(
                        f"sentiment '{result.sentiment.label}' is not categorised "
                        f"(gate: {', '.join(sorted(allowed))})"
                    ),
                )
                continue
            indices.append(index)

        if not indices:
            return results

        try:
            outcomes = self.categoriser.categorise_batch([texts[i] for i in indices], categories)
        except Exception as exc:
            log.exception("Categorisation failed for %d items.", len(indices))
            message = f"categorisation failed: {type(exc).__name__}: {exc}"
            for index in indices:
                results[index] = results[index].with_error(message)
            return results

        for index, outcome in zip(indices, outcomes):
            results[index] = _apply_categorisation(results[index], outcome)

        return results

    def _enrich(
        self,
        result: ItemAnalysis,
        text: str,
        *,
        with_evidence: bool,
        with_insights: bool,
    ) -> ItemAnalysis:
        """Retrieval and insight generation for one record, isolated from the others."""
        if not result.ok:
            return result

        evidence = result.evidence
        if with_evidence:
            try:
                evidence = tuple(self.retriever.search(text, self.evidence_limit))
            except Exception as exc:
                log.warning("Retrieval failed for %s: %s", result.feedback_id, exc)
                return result.with_error(f"retrieval failed: {type(exc).__name__}: {exc}")

            result = _replace(result, evidence=evidence)

        if with_insights and self.insight_generator is not None:
            try:
                insight = self.insight_generator.generate(
                    text,
                    sentiment=result.sentiment,
                    category_name=result.category.name if result.category else "",
                    evidence=evidence,
                )
            except Exception as exc:
                log.warning("Insight generation failed for %s: %s", result.feedback_id, exc)
                return result.with_error(f"insight failed: {type(exc).__name__}: {exc}")

            result = _replace(result, insight=insight)

        return result


# ---------------------------------------------------------------- small helpers


def _replace(result: ItemAnalysis, **changes) -> ItemAnalysis:
    from dataclasses import replace

    return replace(result, **changes)


def _apply_categorisation(result: ItemAnalysis, outcome: CategorisationOutcome) -> ItemAnalysis:
    return _replace(
        result,
        category=outcome.top,
        candidate_categories=outcome.candidates,
        is_unclassified=outcome.is_unclassified,
    )
