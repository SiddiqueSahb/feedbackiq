"""
Consolidated metric report for the five sentiment classifiers - adds what
scripts/evaluate_models.py doesn't: side-by-side precision/recall/F1,
balanced accuracy, per-class tables, and McNemar significance tests between
model pairs. Reads only from data/results/, no re-inference.

Outputs (data/results/metrics_summary/):
    overall_metrics.csv          headline metrics per model, full test set
    per_class_metrics.csv        precision/recall/F1/support per class
    confusion_counts.csv         raw confusion matrix counts, long format
    significance_tests.csv       McNemar pairwise comparisons

Usage: python evaluate/generate_all_metrics.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import binomtest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS = PROJECT_ROOT / "data" / "results"
OUTPUT_DIR = RESULTS / "metrics_summary"

MODELS = [
    ("vader", "VADER"),
    ("naive_bayes", "Naive Bayes"),
    ("logistic_regression", "Logistic Regression"),
    ("roberta_(pre-trained)", "RoBERTa (pretrained)"),
    ("distilbert_(fine-tuned)", "DistilBERT (fine-tuned)"),
]
CLASSES = ["negative", "neutral", "positive"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("all_metrics")


# metrics derived from a confusion matrix
def balanced_accuracy(cm: np.ndarray) -> float:
    """Mean per-class recall. Unaffected by class imbalance."""
    recalls = np.diag(cm) / cm.sum(axis=1)
    return float(np.mean(recalls))


def load_cm(slug: str) -> tuple[np.ndarray, list[str]] | tuple[None, None]:
    path = RESULTS / f"cm_{slug}.json"
    if not path.exists():
        logger.warning("Missing %s", path.name)
        return None, None
    with open(path) as f:
        payload = json.load(f)
    return np.array(payload["confusion_matrix"], dtype=float), payload["labels"]


def load_report(slug: str) -> dict | None:
    path = RESULTS / f"report_{slug}.json"
    if not path.exists():
        logger.warning("Missing %s", path.name)
        return None
    with open(path) as f:
        return json.load(f)


# table builders
def overall_table() -> pd.DataFrame:
    """Headline metrics per model on the full held-out test set."""
    rows = []
    for slug, name in MODELS:
        cm, _ = load_cm(slug)
        rep = load_report(slug)
        if cm is None or rep is None:
            continue
        rows.append({
            "Model": name,
            "Test size": int(cm.sum()),
            "Accuracy": round(rep["accuracy"], 4),
            "Balanced accuracy": round(balanced_accuracy(cm), 4),
            "Macro precision": round(rep["macro avg"]["precision"], 4),
            "Macro recall": round(rep["macro avg"]["recall"], 4),
            "Macro F1": round(rep["macro avg"]["f1-score"], 4),
            "Weighted precision": round(rep["weighted avg"]["precision"], 4),
            "Weighted recall": round(rep["weighted avg"]["recall"], 4),
            "Weighted F1": round(rep["weighted avg"]["f1-score"], 4),
        })
    return pd.DataFrame(rows)


def per_class_table() -> pd.DataFrame:
    """Precision, recall, F1 and support for every model and class."""
    rows = []
    for slug, name in MODELS:
        rep = load_report(slug)
        if rep is None:
            continue
        for cls in CLASSES:
            if cls not in rep:
                continue
            rows.append({
                "Model": name,
                "Class": cls.capitalize(),
                "Precision": round(rep[cls]["precision"], 4),
                "Recall": round(rep[cls]["recall"], 4),
                "F1": round(rep[cls]["f1-score"], 4),
                "Support": int(rep[cls]["support"]),
            })
    return pd.DataFrame(rows)


def confusion_table() -> pd.DataFrame:
    """Confusion matrices in long format, with row-normalised percentages."""
    rows = []
    for slug, name in MODELS:
        cm, labels = load_cm(slug)
        if cm is None:
            continue
        for i, true_lab in enumerate(labels):
            total = cm[i].sum()
            for j, pred_lab in enumerate(labels):
                rows.append({
                    "Model": name,
                    "True": true_lab.capitalize(),
                    "Predicted": pred_lab.capitalize(),
                    "Count": int(cm[i, j]),
                    "Row %": round(cm[i, j] / total * 100, 2) if total else 0.0,
                })
    return pd.DataFrame(rows)


def significance_table() -> pd.DataFrame:
    """McNemar's test between every pair of models: b/c are the counts where
    only one model is right, and an exact binomial test on b of b+c gives the
    p-value (concordant cases carry no info). Uses the diagnostic sample cached
    by evaluate_sentiment_diagnostics.py, so accuracies differ slightly from
    the full-test-set headline table."""
    path = RESULTS / "sentiment_diagnostics" / "predictions.csv"
    if not path.exists():
        logger.warning("No cached predictions — run evaluate_sentiment_diagnostics.py first. "
                       "Significance tests skipped.")
        return pd.DataFrame()

    preds = pd.read_csv(path)
    names = [c.split("::", 1)[1] for c in preds.columns if c.startswith("pred::")]
    truth = preds["true_label"]

    correct = {n: (preds[f"pred::{n}"] == truth).to_numpy() for n in names}

    rows = []
    for a, b in combinations(names, 2):
        ca, cb = correct[a], correct[b]
        only_a = int(np.sum(ca & ~cb))   # A right, B wrong
        only_b = int(np.sum(~ca & cb))   # B right, A wrong
        n_disc = only_a + only_b
        if n_disc == 0:
            p = 1.0
        else:
            p = binomtest(only_a, n_disc, 0.5).pvalue
        rows.append({
            "Model A": a,
            "Model B": b,
            "A accuracy": round(float(ca.mean()), 4),
            "B accuracy": round(float(cb.mean()), 4),
            "A right, B wrong": only_a,
            "B right, A wrong": only_b,
            "Discordant pairs": n_disc,
            "p-value": float(f"{p:.3e}"),
            "Significant (p<0.05)": "yes" if p < 0.05 else "no",
            "Better model": a if only_a > only_b else (b if only_b > only_a else "tie"),
        })
    return pd.DataFrame(rows).sort_values("p-value")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Reading saved results from %s", RESULTS)

    try:
        overall = overall_table()
        overall.to_csv(OUTPUT_DIR / "overall_metrics.csv", index=False)
        logger.info("overall_metrics.csv — %d models", len(overall))

        per_class = per_class_table()
        per_class.to_csv(OUTPUT_DIR / "per_class_metrics.csv", index=False)
        logger.info("per_class_metrics.csv — %d rows", len(per_class))

        confusion = confusion_table()
        confusion.to_csv(OUTPUT_DIR / "confusion_counts.csv", index=False)
        logger.info("confusion_counts.csv — %d rows", len(confusion))

        sig = significance_table()
        if not sig.empty:
            sig.to_csv(OUTPUT_DIR / "significance_tests.csv", index=False)
            logger.info("significance_tests.csv — %d pairs", len(sig))

        print("\n=== OVERALL METRICS (full held-out test set) ===")
        print(overall.to_string(index=False))
        if not sig.empty:
            print("\n=== McNEMAR PAIRWISE TESTS (balanced diagnostic sample) ===")
            print(sig[["Model A", "Model B", "Discordant pairs", "p-value",
                       "Significant (p<0.05)", "Better model"]].to_string(index=False))

        logger.info("Done. Written to %s", OUTPUT_DIR)

    except Exception as exc:
        logger.error("Metric generation failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
