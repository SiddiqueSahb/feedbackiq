"""Reads pre-computed evaluation results from data/results/ and shapes them for the frontend."""

from __future__ import annotations

import ast
import json
import os

import pandas as pd

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger

log = get_logger("service.evaluation")

RESULTS_DIR = str(settings.results_dir)

# display names as used in the result filenames from evaluate_models.py
SENTIMENT_MODELS = [
    "Naive Bayes",
    "Logistic Regression",
    "VADER",
    "RoBERTa (pre-trained)",
    "DistilBERT (fine-tuned)",
]


def _slug(model_name: str) -> str:
    """Matches the filenames evaluate_models.py already wrote, e.g. 'DistilBERT (fine-tuned)' -> 'distilbert_(fine-tuned)'."""
    return model_name.lower().replace(" ", "_")


def _read_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    return pd.read_csv(path).to_dict("records")


def get_sentiment_evaluation() -> dict:
    """Model comparison table, confusion matrices, and significance tests for the five sentiment classifiers."""

    comparison = _read_json(os.path.join(RESULTS_DIR, "model_comparison.json")) or {}

    confusion_matrices: dict[str, dict] = {}
    for model in SENTIMENT_MODELS:
        cm = _read_json(os.path.join(RESULTS_DIR, f"cm_{_slug(model)}.json"))
        if cm:
            confusion_matrices[model] = cm

    significance_tests = _read_csv(
        os.path.join(RESULTS_DIR, "metrics_summary", "significance_tests.csv")
    )

    return {
        "model_comparison": [
            {"model": name, **metrics} for name, metrics in comparison.items()
        ],
        "confusion_matrices": confusion_matrices,
        "significance_tests": significance_tests,
    }


def get_retrieval_evaluation() -> dict:
    """TF-IDF vs semantic search (FAISS) retrieval quality and latency."""

    tfidf_summary = _read_csv(
        os.path.join(RESULTS_DIR, "evaluation", "tfidf", "overall_summary.csv")
    )
    semantic_summary = _read_csv(
        os.path.join(RESULTS_DIR, "evaluation_semantic_search", "overall_summary.csv")
    )
    dataset_info = _read_csv(
        os.path.join(RESULTS_DIR, "evaluation_semantic_search", "dataset_information.csv")
    )

    return {
        "tfidf": {row["Metric"]: row["Value"] for row in tfidf_summary},
        "semantic_search": {row["Metric"]: row["Value"] for row in semantic_summary},
        "dataset_info": {row["Metric"]: row["Value"] for row in dataset_info},
    }


def get_rag_evaluation() -> dict:
    """RAGAS results comparing the RAG pipeline vs a standalone LLM. Returns {"available": False} if not run yet — this hits live Groq API calls so it's not run automatically."""

    rag_dir = os.path.join(RESULTS_DIR, "llm_vs_rag")
    overall_path = os.path.join(rag_dir, "overall_results.csv")

    if not os.path.exists(overall_path):
        return {"available": False}

    overall_rows = _read_csv(overall_path)
    overall = {row["Metric"]: row["Value"] for row in overall_rows}

    comparison = _read_csv(os.path.join(rag_dir, "comparison_summary.csv"))

    # retrieved_contexts comes back as a stringified list from CSV; we just need the count
    rag_scores = _read_csv(os.path.join(rag_dir, "rag_ragas_scores.csv"))
    rag_per_question = []
    for row in rag_scores:
        try:
            contexts = ast.literal_eval(row.get("retrieved_contexts") or "[]")
            retrieved_count = len(contexts) if isinstance(contexts, list) else 0
        except (ValueError, SyntaxError):
            retrieved_count = 0

        rag_per_question.append({
            "question": row.get("user_input"),
            "answer": row.get("response"),
            "retrieved_count": retrieved_count,
            "faithfulness": row.get("faithfulness"),
            "answer_relevancy": row.get("answer_relevancy"),
            "context_precision": row.get("context_precision"),
            "context_recall": row.get("context_recall"),
        })

    llm_scores = _read_csv(os.path.join(rag_dir, "llm_ragas_scores.csv"))
    llm_per_question = [
        {
            "question": row.get("user_input"),
            "answer": row.get("response"),
            "answer_relevancy": row.get("answer_relevancy"),
        }
        for row in llm_scores
    ]

    return {
        "available": True,
        "overall": overall,
        "comparison": comparison,
        "rag_per_question": rag_per_question,
        "llm_per_question": llm_per_question,
    }
