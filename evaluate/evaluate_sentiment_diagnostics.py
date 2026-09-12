"""
Sentiment diagnostics - why the off-the-shelf models (RoBERTa 0.633, VADER
0.514 accuracy) lose to fine-tuned DistilBERT (0.824) and logistic
regression (0.803): both barely predict "neutral" (F1 0.225/0.086), so
they behave like binary classifiers on this three-class task.

Three experiments test that, sharing one cached prediction run:
  A. Per-platform breakdown - is RoBERTa's Twitter pretraining showing?
  B. Binary re-evaluation (neutral removed) - does the 3-class scheme
     explain the gap?
  C. VADER threshold sweep - is VADER's narrow default neutral band
     ([-0.05, 0.05]) the real culprit?

Outputs (data/results/sentiment_diagnostics/):
    predictions.csv                 cached per-review predictions (all models)
    per_platform_metrics.csv/.png   experiment A
    binary_comparison.csv/.png      experiment B
    vader_threshold_sweep.csv/.png  experiment C
    diagnostics_summary.csv         one-row roll-up

Usage:
    python evaluate/evaluate_sentiment_diagnostics.py
    python evaluate/evaluate_sentiment_diagnostics.py --refresh   # re-run inference
"""

from __future__ import annotations

import os
import json
import random
import logging
import argparse
from pathlib import Path

# Set before torch is imported (transformers pulls it in).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score

from feedbackiq.core.config import settings

# Settings
RANDOM_SEED = 42
# Per (platform, sentiment) cell - 200 -> 1,800 total, enough for stable
# per-platform F1 without blowing up CPU runtime for the transformer models.
SAMPLE_PER_CELL = 200
MIN_TEXT_LENGTH = 6

PLATFORMS = ["amazon", "yelp", "twitter_airline"]
CLASSES = ["negative", "neutral", "positive"]

OUTPUT_DIR = PROJECT_ROOT / "data" / "results" / "sentiment_diagnostics"
PREDICTIONS_PATH = OUTPUT_DIR / "predictions.csv"

# Colour-blind-friendly palette (Okabe & Ito, 2008)
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sentiment_diagnostics")


def pretty(label: str) -> str:
    """'twitter_airline' -> 'Twitter Airline'."""
    return str(label).replace("_", " ").title()


def load_sample() -> pd.DataFrame:
    """Rebuilds evaluate_models.py's held-out test split (20%, stratified,
    seed 42), then balances per platform-and-class cell - otherwise Twitter
    Airline (~2% of the corpus) is too thin for experiment A."""
    data_path = PROJECT_ROOT / settings.DATA_PATH
    logger.info("Loading %s", data_path)
    df = pd.read_parquet(data_path)

    # Same filtering/split as the main evaluation script - same held-out data.
    df = df.dropna(subset=["cleaned_text", "sentiment_label"])
    df = df[df["cleaned_text"].str.len() > MIN_TEXT_LENGTH]
    df = df[df["sentiment_label"].isin(CLASSES)]
    _, test_df = train_test_split(
        df, test_size=0.2, random_state=RANDOM_SEED, stratify=df["sentiment_label"]
    )
    logger.info("Held-out test set: %d reviews", len(test_df))

    cells = []
    for platform in PLATFORMS:
        for cls in CLASSES:
            cell = test_df[
                (test_df["platform"] == platform) & (test_df["sentiment_label"] == cls)
            ]
            if cell.empty:
                logger.warning("No %s / %s reviews in the test split", platform, cls)
                continue
            take = min(len(cell), SAMPLE_PER_CELL)
            cells.append(cell.sample(n=take, random_state=RANDOM_SEED))

    sample = pd.concat(cells).reset_index(drop=True)
    logger.info("Diagnostic sample: %d reviews", len(sample))
    logger.info("Per platform: %s", sample["platform"].value_counts().to_dict())
    return sample[["review_id", "platform", "sentiment_label", "cleaned_text"]]


def load_models() -> dict[str, object]:
    """Instantiates the five classifiers; a failed load is skipped with a
    warning. FineTunedSentiment silently falls back to RoBERTa when its
    weights are missing, which would duplicate the RoBERTa column - so
    _loaded is checked here."""
    from feedbackiq.nlp.sentiment import VaderSentiment, RobertaSentiment, FineTunedSentiment
    from feedbackiq.nlp.classical_models import NaiveBayesSentiment, LogisticRegressionSentiment

    models: dict[str, object] = {}

    for name, factory in [
        ("VADER", VaderSentiment),
        ("Naive Bayes", NaiveBayesSentiment),
        ("Logistic Regression", LogisticRegressionSentiment),
        ("RoBERTa (pre-trained)", RobertaSentiment),
        ("DistilBERT (fine-tuned)", FineTunedSentiment),
    ]:
        try:
            model = factory()
        except Exception as exc:
            logger.warning("Could not load %s: %s", name, exc)
            continue

        # Classical models expose _loaded; so does FineTunedSentiment.
        if getattr(model, "_loaded", True) is False:
            logger.warning("%s is not available (untrained or weights missing) — skipped", name)
            continue

        models[name] = model
        logger.info("Loaded %s", name)

    return models


def run_predictions(sample: pd.DataFrame) -> pd.DataFrame:
    """Predicts every sampled review with every model, once - also caches
    per-class scores (for experiment B) and VADER's signed compound score
    (for experiment C; predict() reports abs(compound), losing the sign)."""
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    models = load_models()
    if not models:
        raise RuntimeError("No sentiment models could be loaded — nothing to evaluate.")

    vader_analyzer = SentimentIntensityAnalyzer()

    rows = []
    total = len(sample)
    for i, row in enumerate(sample.itertuples(index=False), start=1):
        text = str(row.cleaned_text)
        record = {
            "review_id": row.review_id,
            "platform": row.platform,
            "true_label": row.sentiment_label,
            "vader_compound": vader_analyzer.polarity_scores(text)["compound"],
        }

        for name, model in models.items():
            try:
                result = model.predict(text)
                record[f"pred::{name}"] = result.get("label", "error")
                # Raw scores let experiment B force a binary decision later.
                record[f"scores::{name}"] = json.dumps(result.get("scores", {}))
            except Exception as exc:
                logger.debug("%s failed on %s: %s", name, row.review_id, exc)
                record[f"pred::{name}"] = "error"
                record[f"scores::{name}"] = "{}"

        rows.append(record)
        if i % 200 == 0 or i == total:
            logger.info("Predicted %d / %d reviews", i, total)

    predictions = pd.DataFrame(rows)
    predictions.to_csv(PREDICTIONS_PATH, index=False)
    logger.info("Cached predictions -> %s", PREDICTIONS_PATH)
    return predictions


def model_names(predictions: pd.DataFrame) -> list[str]:
    """Model names recovered from the 'pred::<name>' columns."""
    return [c.split("::", 1)[1] for c in predictions.columns if c.startswith("pred::")]


# Experiment A: per-platform breakdown
def experiment_a_per_platform(predictions: pd.DataFrame) -> pd.DataFrame:
    """Macro F1 and accuracy per model per platform. Point of interest:
    RoBERTa was pretrained on Twitter, so domain mismatch predicts it
    should score higher there than on Amazon."""
    rows = []
    for name in model_names(predictions):
        for platform in PLATFORMS:
            subset = predictions[predictions["platform"] == platform]
            subset = subset[subset[f"pred::{name}"] != "error"]
            if subset.empty:
                continue
            y_true = subset["true_label"]
            y_pred = subset[f"pred::{name}"]
            rows.append({
                "Model": name,
                "Platform": pretty(platform),
                "Reviews": len(subset),
                "Accuracy": round(accuracy_score(y_true, y_pred), 4),
                "Macro F1": round(f1_score(y_true, y_pred, average="macro", zero_division=0), 4),
                "Neutral F1": round(
                    f1_score(y_true, y_pred, labels=["neutral"], average="macro", zero_division=0), 4
                ),
            })

    table = pd.DataFrame(rows)
    table.to_csv(OUTPUT_DIR / "per_platform_metrics.csv", index=False)

    # Grouped bar chart: one group per model, one bar per platform.
    pivot = table.pivot(index="Model", columns="Platform", values="Macro F1")
    ax = pivot.plot(kind="bar", figsize=(11, 6), color=OKABE_ITO[: len(pivot.columns)])
    ax.set_ylabel("Macro F1")
    ax.set_xlabel("")
    ax.set_title("Sentiment Performance by Platform")
    ax.set_ylim(0, 1)
    ax.legend(title="Platform")
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "per_platform_metrics.png", dpi=300)
    plt.close()

    logger.info("Experiment A done")
    return table


# Experiment B: binary re-evaluation
def _forced_binary(scores_json: str, fallback_label: str) -> str:
    """Forces a negative/positive choice from the model's own class scores
    (ignoring neutral); falls back to the original label if scores are
    missing."""
    try:
        scores = json.loads(scores_json) or {}
    except Exception:
        scores = {}

    neg, pos = scores.get("negative"), scores.get("positive")
    if neg is None or pos is None:
        return fallback_label
    return "negative" if neg >= pos else "positive"


def experiment_b_binary(predictions: pd.DataFrame) -> pd.DataFrame:
    """Re-scores every model with neutral removed, two ways: strict (true-
    neutral rows dropped; a neutral prediction still counts wrong) and
    forced binary (same rows, model must pick negative/positive). Forced
    binary isolates the label-scheme effect; strict penalises models that
    use neutral well."""
    non_neutral = predictions[predictions["true_label"] != "neutral"]

    rows = []
    for name in model_names(predictions):
        subset = non_neutral[non_neutral[f"pred::{name}"] != "error"]
        if subset.empty:
            continue
        y_true = subset["true_label"]

        strict_f1 = f1_score(y_true, subset[f"pred::{name}"], average="macro", zero_division=0)

        forced = [
            _forced_binary(s, lbl)
            for s, lbl in zip(subset[f"scores::{name}"], subset[f"pred::{name}"])
        ]
        forced_f1 = f1_score(y_true, forced, average="macro", zero_division=0)

        # Three-class macro F1 on the same sample, for a like-for-like gap.
        full = predictions[predictions[f"pred::{name}"] != "error"]
        three_class_f1 = f1_score(
            full["true_label"], full[f"pred::{name}"], average="macro", zero_division=0
        )

        rows.append({
            "Model": name,
            "Three-class Macro F1": round(three_class_f1, 4),
            "Binary Macro F1 (strict)": round(strict_f1, 4),
            "Binary Macro F1 (forced)": round(forced_f1, 4),
            "Gain from dropping neutral": round(forced_f1 - three_class_f1, 4),
        })

    table = pd.DataFrame(rows).sort_values("Gain from dropping neutral", ascending=False)
    table.to_csv(OUTPUT_DIR / "binary_comparison.csv", index=False)

    plot_data = table.set_index("Model")[["Three-class Macro F1", "Binary Macro F1 (forced)"]]
    ax = plot_data.plot(kind="bar", figsize=(11, 6), color=[OKABE_ITO[0], OKABE_ITO[2]])
    ax.set_ylabel("Macro F1")
    ax.set_xlabel("")
    ax.set_title("Effect of Removing the Neutral Class")
    ax.set_ylim(0, 1)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "binary_comparison.png", dpi=300)
    plt.close()

    logger.info("Experiment B done")
    return table


# Experiment C: VADER threshold sweep
def experiment_c_vader_thresholds(predictions: pd.DataFrame) -> pd.DataFrame:
    """Re-labels VADER's cached compound scores over widening neutral bands
    (default +/-0.05 up to +/-0.60) - if F1 improves a lot, the threshold
    is the problem, not the lexicon."""
    bands = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60]
    y_true = predictions["true_label"]

    rows = []
    for band in bands:
        relabelled = np.where(
            predictions["vader_compound"] >= band, "positive",
            np.where(predictions["vader_compound"] <= -band, "negative", "neutral"),
        )
        rows.append({
            "Neutral band": band,
            "Accuracy": round(accuracy_score(y_true, relabelled), 4),
            "Macro F1": round(f1_score(y_true, relabelled, average="macro", zero_division=0), 4),
            "Neutral F1": round(
                f1_score(y_true, relabelled, labels=["neutral"], average="macro", zero_division=0), 4
            ),
            "Predicted neutral (%)": round(float((relabelled == "neutral").mean()) * 100, 1),
        })

    table = pd.DataFrame(rows)
    table.to_csv(OUTPUT_DIR / "vader_threshold_sweep.csv", index=False)

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.plot(table["Neutral band"], table["Macro F1"], marker="o", color=OKABE_ITO[0], label="Macro F1")
    ax.plot(table["Neutral band"], table["Neutral F1"], marker="s", color=OKABE_ITO[1], label="Neutral F1")
    ax.plot(table["Neutral band"], table["Accuracy"], marker="^", color=OKABE_ITO[2], label="Accuracy")
    ax.axvline(0.05, linestyle="--", color="grey", linewidth=1)
    ax.annotate("VADER default", xy=(0.05, 0.05), xytext=(0.08, 0.05), fontsize=9, color="grey")
    ax.set_xlabel("Neutral band half-width (|compound| below this = neutral)")
    ax.set_ylabel("Score")
    ax.set_title("VADER Sensitivity to the Neutral Decision Threshold")
    ax.set_ylim(0, 1)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "vader_threshold_sweep.png", dpi=300)
    plt.close(fig)

    logger.info("Experiment C done")
    return table


def write_summary(
    per_platform: pd.DataFrame, binary: pd.DataFrame, vader: pd.DataFrame
) -> None:
    """Pull the few numbers worth quoting directly in the results chapter."""
    summary: dict[str, object] = {}

    # The domain-mismatch check: RoBERTa on Twitter vs on Amazon.
    rob = per_platform[per_platform["Model"].str.startswith("RoBERTa")]
    for platform in ["Twitter Airline", "Amazon", "Yelp"]:
        cell = rob[rob["Platform"] == platform]
        if not cell.empty:
            summary[f"RoBERTa Macro F1 ({platform})"] = float(cell.iloc[0]["Macro F1"])

    if not binary.empty:
        top = binary.iloc[0]
        summary["Largest gain from dropping neutral (model)"] = top["Model"]
        summary["Largest gain from dropping neutral (value)"] = float(top["Gain from dropping neutral"])

    if not vader.empty:
        best = vader.loc[vader["Macro F1"].idxmax()]
        summary["VADER default Macro F1"] = float(vader.iloc[0]["Macro F1"])
        summary["VADER best Macro F1"] = float(best["Macro F1"])
        summary["VADER best neutral band"] = float(best["Neutral band"])

    pd.DataFrame([summary]).to_csv(OUTPUT_DIR / "diagnostics_summary.csv", index=False)
    logger.info("Summary written")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentiment diagnostics for RQ1.")
    parser.add_argument(
        "--refresh", action="store_true",
        help="re-run model inference even if cached predictions exist",
    )
    args = parser.parse_args()

    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if PREDICTIONS_PATH.exists() and not args.refresh:
            logger.info("Reusing cached predictions (%s). Pass --refresh to redo inference.",
                        PREDICTIONS_PATH.name)
            predictions = pd.read_csv(PREDICTIONS_PATH)
        else:
            predictions = run_predictions(load_sample())

        found = model_names(predictions)
        logger.info("Models in predictions: %s", ", ".join(found))

        per_platform = experiment_a_per_platform(predictions)
        binary = experiment_b_binary(predictions)
        vader = experiment_c_vader_thresholds(predictions)
        write_summary(per_platform, binary, vader)

        logger.info("All experiments complete. Results in %s", OUTPUT_DIR)

    except FileNotFoundError as exc:
        logger.error("Required file not found: %s", exc)
    except Exception as exc:
        logger.error("Diagnostics failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
