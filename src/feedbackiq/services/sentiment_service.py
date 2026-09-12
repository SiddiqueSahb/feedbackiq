"""
Compatibility adapter: the HTTP API's shapes, the engine's analysis.

The engine (`feedbackiq.engine`) is strict and typed: it raises, it isolates failures,
and it returns dataclasses. The existing API and Streamlit pages expect dictionaries and
graceful degradation - a missing index should still return a sentiment, a dead LLM should
still return a category. Rather than rewrite the API, this module translates:

    /api/sentiment/analyse   ->  engine (sentiment + gate + category)
                             ->  engine.retriever      (tolerated: [] on failure)
                             ->  engine.insight_generator (tolerated: empty analysis)
                             ->  the legacy response dict

    /api/sentiment/batch-predict -> engine batch sentiment, one forward pass per chunk

One behaviour deliberately changed in Milestone 3: positive feedback no longer receives
a complaint category (the sentiment gate). It reports "General Feedback" instead, because
the taxonomy was discovered from negative reviews and a positive item has no complaint to
categorise. See docs/production/milestone-03.md.
"""

from __future__ import annotations

from functools import lru_cache

from feedbackiq.core.logging import get_logger
from feedbackiq.engine import (
    AnalyticsEngine,
    FeedbackItem,
    ItemAnalysis,
    SingleCallSentimentModel,
    build_default_engine,
    build_sentiment_only_engine,
    normalise_text,
)
from feedbackiq.nlp.sentiment import (
    compare_all,
    get_finetuned,
    get_logistic_regression,
    get_naive_bayes,
    get_roberta,
    get_vader,
)

log = get_logger("service.sentiment")

EMPTY_ANALYSIS = {
    "summary": "",
    "keywords": [],
    "business_insight": "",
    "severity": "",
    "priority": "",
    "department": "",
    "executive_summary": "",
}

# What the API reports when there is no complaint category: either the categoriser
# failed, or the sentiment gate decided the question does not apply.
GENERAL_FEEDBACK = "General Feedback"

# The engine's own sentiment model; any other choice comes from the dissertation's
# five-model comparison and is injected into the engine per request.
DEFAULT_MODELS = ("distilbert", "finetuned")


@lru_cache(maxsize=1)
def get_engine() -> AnalyticsEngine:
    """The full engine (sentiment, categoriser, corpus retrieval, LLM insights)."""
    return build_default_engine()


@lru_cache(maxsize=1)
def get_batch_engine() -> AnalyticsEngine:
    """Sentiment only - what bulk CSV scoring needs."""
    return build_sentiment_only_engine()


def get_sentiment_model(model: str):
    """Return one of the dissertation's five classifiers (unchanged)."""
    models = {
        "vader": get_vader,
        "roberta": get_roberta,
        "naive_bayes": get_naive_bayes,
        "logistic_regression": get_logistic_regression,
        "distilbert": get_finetuned,
        "finetuned": get_finetuned,
    }

    return models.get(model, get_finetuned)()


def engine_for_model(model: str) -> AnalyticsEngine:
    """
    The engine to use for a requested sentiment model.

    The default model is the engine's own. Any other choice - the comparison baselines
    the dissertation kept - is injected as the engine's sentiment component, which is
    what the injectable design is for: same pipeline, same gate, different model.
    """
    engine = get_engine()

    if model in DEFAULT_MODELS:
        return engine

    return AnalyticsEngine(
        sentiment=SingleCallSentimentModel(get_sentiment_model(model), model_version=model),
        categoriser=engine.categoriser,
        retriever=engine.retriever,
        insight_generator=engine.insight_generator,
        default_categories=engine.default_categories,
    )


# ---------------------------------------------------------------- single review


def analyse_review(text: str, model: str = "distilbert") -> dict:
    """The full per-review pipeline, in the response shape the API and UI expect."""
    if not text.strip():
        raise ValueError("Review text cannot be empty.")

    log.info("Analysing review | model=%s | length=%d", model, len(text))

    engine = engine_for_model(model)
    normalised = normalise_text(text)

    # Stage 1 and 2: sentiment, then categorisation behind the sentiment gate.
    result = engine.analyse_batch([FeedbackItem(id="request", text=text)]).results[0]

    sentiment = _sentiment_dict(result, text)
    categories, category = _category_dicts(result)

    # Stages 3 and 4 are tolerated failures here, because the API has always returned a
    # partial analysis rather than an error when retrieval or the LLM is unavailable.
    similar_reviews = _similar_reviews(engine, normalised)
    analysis = _analysis(engine, normalised, sentiment["label"], category, similar_reviews)

    return {
        "review": text,
        "sentiment": sentiment,
        "categories": categories,
        "category": category,
        "similar_reviews": similar_reviews,
        **analysis,
    }


def _sentiment_dict(result: ItemAnalysis, text: str) -> dict:
    """The legacy sentiment shape, falling back to VADER if the engine produced none."""
    if result.sentiment is not None:
        return {
            "label": result.sentiment.label,
            "confidence": result.sentiment.confidence,
            "scores": result.sentiment.scores,
            "model": result.sentiment.model_version,
        }

    log.warning("Sentiment unavailable (%s); falling back to VADER.", result.error)

    return get_vader().predict(text)


def _category_dicts(result: ItemAnalysis) -> tuple[list[dict], str]:
    """
    The legacy `categories` list and `category` string.

    Three cases: a real category, the gate skipping a positive item, or a failure.
    The last two both report "General Feedback", as the API always did when
    categorisation produced nothing.
    """
    if result.category is None:
        if result.categorisation_skipped:
            log.info("Categorisation skipped: %s", result.categorisation_skipped)
        elif not result.ok:
            log.warning("Categorisation unavailable: %s", result.error)

        return [{"category": GENERAL_FEEDBACK, "confidence": 0.0}], GENERAL_FEEDBACK

    ranked = [result.category, *result.candidate_categories[1:]] if result.is_unclassified \
        else list(result.candidate_categories) or [result.category]

    categories = [
        {"category": match.name, "score": match.score, "description": match.description}
        for match in ranked
    ]

    return categories, result.category.name


def _similar_reviews(engine: AnalyticsEngine, text: str) -> list[dict]:
    """Evidence in the legacy row shape. A retrieval failure returns nothing, as before."""
    try:
        evidence = engine.retriever.search(text, engine.evidence_limit)
    except Exception as exc:
        log.warning("Semantic search failed: %s", exc)
        return []

    return [
        {
            "review_id": item.id,
            "text": item.text,
            "platform": item.metadata.get("platform", "unknown"),
            "rating": item.metadata.get("rating", 0),
            "sentiment_label": item.metadata.get("sentiment_label", "unknown"),
            "similarity_score": item.score,
        }
        for item in evidence
    ]


def _analysis(
    engine: AnalyticsEngine,
    text: str,
    sentiment_label: str,
    category: str,
    similar_reviews: list[dict],
) -> dict:
    """The LLM analysis fields, or empty strings when the LLM is unavailable."""
    if engine.insight_generator is None:
        return EMPTY_ANALYSIS.copy()

    from feedbackiq.engine.retrieval import evidence_from_rows

    try:
        insight = engine.insight_generator.generate(
            text,
            sentiment=None,
            category_name=category,
            evidence=evidence_from_rows(similar_reviews),
        )
    except Exception as exc:
        log.warning("LLM analysis failed: %s", exc)
        return EMPTY_ANALYSIS.copy()

    return {
        "summary": insight.summary,
        "keywords": list(insight.keywords),
        "business_insight": insight.business_insight,
        "severity": insight.severity,
        "priority": insight.priority,
        "department": insight.department,
        "executive_summary": insight.executive_summary,
    }


# ---------------------------------------------------------------- other endpoints


def compare_models(text: str) -> dict:
    """Compare all five sentiment models (dissertation feature, unchanged)."""
    if not text.strip():
        raise ValueError("Review text cannot be empty.")

    log.info("Comparing sentiment models")

    return compare_all(text)


def predict_only(text: str, model: str = "distilbert") -> dict:
    """Sentiment only - no categorisation, retrieval or LLM call."""
    if not text.strip():
        raise ValueError("Review text cannot be empty.")

    return get_sentiment_model(model).predict(text)


def batch_predict(texts: list[str], model: str = "distilbert") -> list[dict]:
    """
    Score many texts at once, through the engine.

    This is where batching pays: one tokenised forward pass per chunk instead of one per
    row. A row that cannot be scored is retried on its own and, if it still fails, comes
    back labelled "error" - so one bad row never costs the whole upload.
    """
    if not texts:
        return []

    engine = get_batch_engine() if model in DEFAULT_MODELS else engine_for_model(model)
    items = [FeedbackItem(id=str(index), text=text) for index, text in enumerate(texts)]

    results = list(engine.analyse_batch(items).results)

    for position, result in enumerate(results):
        if result.ok:
            continue
        # Retry alone, so one unscoreable row cannot mark its neighbours failed.
        log.warning("Re-scoring row %s after a batch failure: %s", result.feedback_id, result.error)
        results[position] = engine.analyse_batch([items[position]]).results[0]

    rows: list[dict] = []
    for text, result in zip(texts, results):
        if result.ok and result.sentiment is not None:
            rows.append({
                "text": text,
                "label": result.sentiment.label,
                "confidence": result.sentiment.confidence,
            })
        else:
            log.warning("Batch row failed: %s", result.error)
            rows.append({"text": text, "label": "error", "confidence": 0.0})

    return rows
