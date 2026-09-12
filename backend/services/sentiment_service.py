"""
Customer review analysis service.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    ),
)

from nlp.sentiment import (
    compare_all,
    get_finetuned,
    get_logistic_regression,
    get_naive_bayes,
    get_roberta,
    get_vader,
)
from nlp.categoriser import categorise
from nlp.embedding_service import semantic_search
from nlp.summariser import analyse_review_with_llm
from logger import get_logger

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

def get_sentiment_model(model: str):
    """Return the requested sentiment model."""

    models = {
        "vader": get_vader,
        "roberta": get_roberta,
        "naive_bayes": get_naive_bayes,
        "logistic_regression": get_logistic_regression,
        "distilbert": get_finetuned,
        "finetuned": get_finetuned,
    }

    return models.get(model, get_finetuned)()


def analyse_review(text: str, model: str = "distilbert") -> dict:
    """Run the complete customer review analysis pipeline."""

    if not text.strip():
        raise ValueError("Review text cannot be empty.")

    log.info( "Analysing review | model=%s | length=%d",model,len(text),)

    # Sentiment
    try:
        sentiment = get_sentiment_model(model).predict(text)

    except Exception:
        log.exception("Sentiment analysis failed. Falling back to VADER.")
        sentiment = get_vader().predict(text)

    # Complaint categorisation
    try:
        categories = categorise(text, top_k=3)
        category = (categories[0]["category"] if categories else "General Feedback")

    except Exception:
        log.exception("Complaint categorisation failed.")
        categories = [{"category": "General Feedback", "confidence": 0.0}]
        category = "General Feedback"

    # Retrieve similar reviews (RAG)
    try:
        similar_reviews = semantic_search(query=text,top_k=5,sentiment_filter=sentiment["label"],)

    except Exception:
        log.exception("Semantic search failed.")
        similar_reviews = []

    # LLM analysis
    try:
        analysis = analyse_review_with_llm(
            text=text,
            sentiment=sentiment["label"],
            category=category,
            similar_reviews=similar_reviews,
        )

    except Exception:
        log.exception("LLM analysis failed.")
        analysis = EMPTY_ANALYSIS.copy()

    return {
    "review": text,
    "sentiment": sentiment,
    "categories": categories,
    "category": category,
    "similar_reviews": similar_reviews,
    **analysis,
}


def compare_models(text: str) -> dict:
    """Compare all five sentiment models."""

    if not text.strip():
        raise ValueError("Review text cannot be empty.")

    log.info("Comparing sentiment models")

    try:
        return compare_all(text)

    except Exception:
        log.exception("Model comparison failed.")
        raise


def predict_only(text: str, model: str = "distilbert") -> dict:
    """Sentiment only — skips categorisation, retrieval and the LLM call. Used by /predict and /batch-predict for a fast label."""

    if not text.strip():
        raise ValueError("Review text cannot be empty.")

    return get_sentiment_model(model).predict(text)


def batch_predict(texts: list[str], model: str = "distilbert") -> list[dict]:
    """Score a list of review texts with one model. A bad row gets an "error" label instead of failing the whole batch."""

    results = []

    for text in texts:
        try:
            prediction = predict_only(text, model)
        except Exception:
            log.exception("Batch prediction failed for one row.")
            prediction = {"label": "error", "confidence": 0.0, "scores": {}, "model": model}

        results.append({
            "text": text,
            "label": prediction.get("label", "error"),
            "confidence": prediction.get("confidence", 0.0),
        })

    return results
