"""
Evaluates the complaint category-discovery pipeline (BERTopic + zero-shot
labelling) for the dissertation's evaluation chapter. Unsupervised task,
no ground truth - reports descriptive/cluster-quality evidence (category
sizes, platform/sentiment spread, embedding coherence) instead of
accuracy/precision/recall.

Doesn't repeat BERTopic discovery (slow, not deterministic) - loads the
taxonomy scripts/discover_categories.py already produced and classifies a
fresh review sample through nlp/categoriser.py, so results test the
taxonomy, not training data.

Sections (all written to results/category_discovery/):
    1. Category frequency               category_frequency.csv/.png
    2. Platform-wise category spread    platform_category_distribution.csv/.png
    3. Sentiment spread per category    sentiment_by_category.csv/.png
    4. Category coherence (embeddings)  category_coherence.csv/.png
    5. Representative reviews           representative_reviews.csv
    6. Manual validation scaffold       manual_validation.csv
    7. Evaluation summary               evaluation_summary.csv
    8. Discovery pipeline stats         discovery_platform_topics.csv,
                                         discovery_pipeline_summary.csv,
                                         discovery_topic_reduction.png
"""

from __future__ import annotations

import os
import sys
import json
import random
import logging
from pathlib import Path

# Must be set before torch is imported (sentence-transformers/transformers
# pull it in). Same OpenMP segfault fix as rag/pipeline.py.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Let this script be run from anywhere, e.g. `python evaluate/evaluate_category_discovery.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from config import settings
from nlp.categoriser import categorise, COMPLAINT_CATEGORIES

# Settings you can tweak without reading the rest of the file
RANDOM_SEED = 42                 # keeps sampling + results reproducible
REVIEWS_PER_PLATFORM = 150       # sample size per platform (450 total by default)
MANUAL_VALIDATION_SIZE = 100     # size of the manual-checking scaffold (Section 6)
REPRESENTATIVE_REVIEWS_PER_CATEGORY = 5
MIN_TEXT_LENGTH = 15             # skip near-empty reviews before classifying
TEXT_COLUMN = "text"             # raw review text (more readable than cleaned_text)

DISCOVERY_SENTIMENT = "negative"                              # matches the taxonomy categoriser.py loads by default
PLATFORMS = ["amazon", "yelp", "twitter_airline"]              # matches discover_categories.py's per-platform runs
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

OUTPUT_DIR = PROJECT_ROOT / "results" / "category_discovery"

# Colour-blind-friendly palette (Okabe & Ito, 2008) — used for every chart.
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evaluate_category_discovery")


def pretty(label: str) -> str:
    """Turn 'twitter_airline' / 'negative' into 'Twitter Airline' / 'Negative'."""
    return str(label).replace("_", " ").title()


def load_review_sample() -> pd.DataFrame:
    """Fixed-seed sample, split evenly across platforms (a plain random
    sample would be ~70% Yelp, barely touch Twitter)."""
    data_path = PROJECT_ROOT / settings.DATA_PATH
    logger.info("Loading reviews from %s", data_path)
    df = pd.read_parquet(data_path)

    df = df.dropna(subset=[TEXT_COLUMN])
    df = df[df[TEXT_COLUMN].str.len() >= MIN_TEXT_LENGTH]

    sampled = pd.concat([
        group.sample(n=min(len(group), REVIEWS_PER_PLATFORM), random_state=RANDOM_SEED)
        for _, group in df.groupby("platform")
    ]).reset_index(drop=True)
    logger.info("Sampled %d reviews across %d platforms", len(sampled), sampled["platform"].nunique())
    return sampled[["review_id", "platform", "sentiment_label", TEXT_COLUMN]]


def classify_reviews(df: pd.DataFrame) -> pd.DataFrame:
    """Runs each review through categorise(); wrapped in its own try/except
    so one bad input skips that row instead of killing the whole run."""
    records = []
    total = len(df)
    for i, row in enumerate(df.itertuples(index=False), start=1):
        text = getattr(row, TEXT_COLUMN)
        try:
            result = categorise(str(text), top_k=1)
            category = result[0]["category"] if result else "Unclassified / Emerging Complaint"
            score = result[0]["score"] if result else 0.0
        except Exception as exc:
            logger.warning("Skipped review %s — classification failed: %s", row.review_id, exc)
            continue

        records.append({
            "review_id": row.review_id,
            "platform": row.platform,
            "sentiment_label": row.sentiment_label,
            "text": text,
            "category": category,
            "category_score": score,
        })

        if i % 50 == 0 or i == total:
            logger.info("Classified %d / %d reviews", i, total)

    return pd.DataFrame.from_records(records)


# Section 1: Category Frequency
def section1_category_frequency(df: pd.DataFrame) -> pd.DataFrame:
    """Count how many reviews landed in each category; save CSV + bar chart."""
    freq = (
        df["category"].value_counts().rename_axis("Category")
        .reset_index(name="Number of Reviews")
        .sort_values("Number of Reviews", ascending=False)
    )
    freq.to_csv(OUTPUT_DIR / "category_frequency.csv", index=False)

    plot_data = freq.sort_values("Number of Reviews")  # ascending so the biggest bar is on top
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.barh(plot_data["Category"], plot_data["Number of Reviews"], color=OKABE_ITO[0])
    ax.set_xlabel("Number of Reviews")
    ax.set_ylabel("Complaint Category")
    ax.set_title("Category Frequency")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "category_frequency.png", dpi=300)
    plt.close(fig)

    logger.info("Section 1 done: %d categories found", len(freq))
    return freq


# Section 2: Platform-wise Category Distribution
def section2_platform_distribution(df: pd.DataFrame) -> None:
    """Cross-tab category x platform; save CSV + stacked bar chart."""
    table = pd.crosstab(df["category"], df["platform"])
    platform_order = [p for p in ["amazon", "yelp", "twitter_airline"] if p in table.columns]
    table = table[platform_order]
    table.columns = [pretty(c) for c in table.columns]
    table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]  # biggest category first
    table.to_csv(OUTPUT_DIR / "platform_category_distribution.csv")

    ax = table.plot(kind="bar", stacked=True, figsize=(14, 8), color=OKABE_ITO[: len(table.columns)])
    ax.set_xlabel("Complaint Category")
    ax.set_ylabel("Number of Reviews")
    ax.set_title("Platform-wise Category Distribution")
    ax.legend(title="Platform")
    plt.xticks(rotation=75, ha="right")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "platform_category_distribution.png", dpi=300)
    plt.close()

    logger.info("Section 2 done")


# Section 3: Sentiment Distribution by Category
def section3_sentiment_distribution(df: pd.DataFrame) -> None:
    """Cross-tab category x sentiment; save CSV + stacked bar chart."""
    table = pd.crosstab(df["category"], df["sentiment_label"])
    sentiment_order = [s for s in ["negative", "neutral", "positive"] if s in table.columns]
    table = table[sentiment_order]
    table.columns = [pretty(c) for c in table.columns]
    table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
    table.to_csv(OUTPUT_DIR / "sentiment_by_category.csv")

    # Fixed colours so "Negative" is always the same colour across runs.
    sentiment_colors = {"Negative": OKABE_ITO[3], "Neutral": OKABE_ITO[6], "Positive": OKABE_ITO[2]}
    colors = [sentiment_colors.get(c, OKABE_ITO[0]) for c in table.columns]

    ax = table.plot(kind="bar", stacked=True, figsize=(14, 8), color=colors)
    ax.set_xlabel("Complaint Category")
    ax.set_ylabel("Number of Reviews")
    ax.set_title("Sentiment Distribution by Category")
    ax.legend(title="Sentiment")
    plt.xticks(rotation=75, ha="right")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "sentiment_by_category.png", dpi=300)
    plt.close()

    logger.info("Section 3 done")


# Section 4: Category Coherence
def section4_category_coherence(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Embeds each category's reviews, takes the centroid, and scores cosine
    similarity of each review to it (near 1.0 = tight cluster, low = scattered
    catch-all like Unclassified).

    Returns: (df + "coherence_to_centroid" column for Section 5, per-category
    avg/min/max/std table)."""
    from sentence_transformers import SentenceTransformer

    logger.info("Embedding %d reviews for coherence scoring", len(df))
    embedder = SentenceTransformer(settings.EMBEDDING_MODEL)
    embeddings = embedder.encode(df[TEXT_COLUMN].tolist(), normalize_embeddings=True, show_progress_bar=False)

    df = df.reset_index(drop=True).copy()
    coherence_scores = np.zeros(len(df))
    rows = []

    for category, group in df.groupby("category"):
        idx = group.index.to_numpy()
        vectors = embeddings[idx]

        centroid = vectors.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        similarities = vectors @ centroid
        coherence_scores[idx] = similarities

        rows.append({
            "category": category,
            "num_reviews": len(group),
            "avg_coherence": float(np.mean(similarities)),
            "min_coherence": float(np.min(similarities)),
            "max_coherence": float(np.max(similarities)),
            "std_coherence": float(np.std(similarities)) if len(similarities) > 1 else 0.0,
        })

    df["coherence_to_centroid"] = coherence_scores
    coherence_table = pd.DataFrame(rows).sort_values("avg_coherence", ascending=False)
    coherence_table.to_csv(OUTPUT_DIR / "category_coherence.csv", index=False)

    fig, ax = plt.subplots(figsize=(10, 8))
    plot_data = coherence_table.sort_values("avg_coherence")
    ax.barh(plot_data["category"], plot_data["avg_coherence"], color=OKABE_ITO[2])
    ax.set_xlabel("Average Coherence (cosine similarity to centroid)")
    ax.set_ylabel("Complaint Category")
    ax.set_title("Category Coherence")
    ax.set_xlim(0, 1)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "category_coherence.png", dpi=300)
    plt.close(fig)

    logger.info("Section 4 done")
    return df, coherence_table


# Section 5: Representative Reviews
def section5_representative_reviews(df_with_coherence: pd.DataFrame) -> None:
    """Per category, save the N reviews closest to that category's centroid."""
    top = (
        df_with_coherence.sort_values("coherence_to_centroid", ascending=False)
        .groupby("category", group_keys=False)
        .head(REPRESENTATIVE_REVIEWS_PER_CATEGORY)
    )
    out = pd.DataFrame({
        "Category": top["category"],
        "Platform": top["platform"].map(pretty),
        "Review ID": top["review_id"],
        "Sentiment": top["sentiment_label"].map(pretty),
        "Review Text": top["text"],
    })
    out.to_csv(OUTPUT_DIR / "representative_reviews.csv", index=False)
    logger.info("Section 5 done: %d representative reviews saved", len(out))


# Section 6: Manual Validation Dataset
def section6_manual_validation(df: pd.DataFrame) -> None:
    """Random sample for manual checking; Human Category / Correct columns
    are left blank on purpose - filled in by hand, not by this script."""
    n = min(MANUAL_VALIDATION_SIZE, len(df))
    sample = df.sample(n=n, random_state=RANDOM_SEED)
    out = pd.DataFrame({
        "Review Text": sample["text"],
        "Platform": sample["platform"].map(pretty),
        "Predicted Category": sample["category"],
        "Sentiment": sample["sentiment_label"].map(pretty),
        "Human Category": "",
        "Correct (Yes/No)": "",
    })
    out.to_csv(OUTPUT_DIR / "manual_validation.csv", index=False)
    logger.info("Section 6 done: %d reviews sampled for manual validation", n)


# Section 7: Evaluation Summary
def section7_evaluation_summary(freq_table: pd.DataFrame, coherence_table: pd.DataFrame) -> None:
    """One-row roll-up of the whole evaluation, for a quick dissertation table."""
    largest = freq_table.iloc[0]
    smallest = freq_table.iloc[-1]

    summary = pd.DataFrame([{
        "Total Reviews": int(freq_table["Number of Reviews"].sum()),
        "Number of Categories": len(freq_table),
        "Largest Category": f"{largest['Category']} ({int(largest['Number of Reviews'])})",
        "Smallest Category": f"{smallest['Category']} ({int(smallest['Number of Reviews'])})",
        "Average Category Size": round(freq_table["Number of Reviews"].mean(), 2),
        "Average Category Coherence": round(coherence_table["avg_coherence"].mean(), 4),
    }])
    summary.to_csv(OUTPUT_DIR / "evaluation_summary.csv", index=False)
    logger.info("Section 7 done")


# Section 8: Discovery Pipeline Stats
def section8_discovery_pipeline_stats() -> None:
    """Reports on the discovery step itself from discover_categories.py's
    output files - no BERTopic re-run needed.

    Requires (run scripts/discover_categories.py first if missing):
        data/processed/taxonomy_merge_info_<sentiment>.json
        data/processed/bertopic_info_<platform>_<sentiment>.json
    """
    merge_path = PROCESSED_DIR / f"taxonomy_merge_info_{DISCOVERY_SENTIMENT}.json"
    if not merge_path.exists():
        logger.warning("Section 8 skipped — missing %s. Run scripts/discover_categories.py first.", merge_path.name)
        return

    with open(merge_path) as f:
        merge_info = json.load(f)

    platform_rows = []
    for platform in PLATFORMS:
        bertopic_path = PROCESSED_DIR / f"bertopic_info_{platform}_{DISCOVERY_SENTIMENT}.json"
        if not bertopic_path.exists():
            logger.warning("Section 8: missing %s, skipping that platform.", bertopic_path.name)
            continue
        with open(bertopic_path) as f:
            info = json.load(f)
        platform_rows.append({
            "Platform": pretty(info.get("platform", platform)),
            "Sample Size": info.get("sample_size"),
            "Min Topic Size": info.get("min_topic_size"),
            "Raw Topics Discovered": info.get("n_topics"),
        })

    if not platform_rows:
        logger.warning("Section 8 skipped — no per-platform bertopic_info files found.")
        return

    platform_table = pd.DataFrame(platform_rows)
    platform_table.to_csv(OUTPUT_DIR / "discovery_platform_topics.csv", index=False)

    summary = pd.DataFrame([{
        "Sentiment": pretty(merge_info.get("sentiment", DISCOVERY_SENTIMENT)),
        "Total Raw Topics (all platforms)": merge_info.get("n_raw_topics"),
        "Final Merged Categories": merge_info.get("n_categories"),
        "Target Categories": merge_info.get("target_categories"),
        "Taxonomy Coherence (mean pairwise cosine similarity)":
            round(merge_info.get("coherence_mean_pairwise_cosine_sim", 0.0), 4),
    }])
    summary.to_csv(OUTPUT_DIR / "discovery_pipeline_summary.csv", index=False)

    # Raw topics per platform + final merged count as a reference bar.
    chart_labels = list(platform_table["Platform"]) + ["Merged Taxonomy"]
    chart_values = list(platform_table["Raw Topics Discovered"]) + [merge_info.get("n_categories", 0)]
    chart_colors = [OKABE_ITO[0]] * len(platform_table) + [OKABE_ITO[3]]

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.bar(chart_labels, chart_values, color=chart_colors)
    ax.set_ylabel("Number of Topics / Categories")
    ax.set_title("Raw BERTopic Topics per Platform vs. Final Merged Taxonomy")
    for i, v in enumerate(chart_values):
        ax.text(i, v, str(v), ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "discovery_topic_reduction.png", dpi=300)
    plt.close(fig)

    logger.info("Section 8 done: %d raw topics merged into %d categories",
                merge_info.get("n_raw_topics", 0), merge_info.get("n_categories", 0))


def main() -> None:
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Output folder: %s", OUTPUT_DIR)

    if len(COMPLAINT_CATEGORIES) <= 7:
        # 7 = categoriser's static fallback list; likely means the real
        # taxonomy wasn't found and discover_categories.py hasn't run yet.
        logger.warning(
            "Only %d categories loaded — this looks like the static fallback list. "
            "Run scripts/discover_categories.py first for real results.",
            len(COMPLAINT_CATEGORIES),
        )

    try:
        sample = load_review_sample()
        classified = classify_reviews(sample)

        if classified.empty:
            logger.error("No reviews were classified successfully — nothing to evaluate.")
            return

        freq_table = section1_category_frequency(classified)
        section2_platform_distribution(classified)
        section3_sentiment_distribution(classified)
        classified_with_coherence, coherence_table = section4_category_coherence(classified)
        section5_representative_reviews(classified_with_coherence)
        section6_manual_validation(classified_with_coherence)
        section7_evaluation_summary(freq_table, coherence_table)
        section8_discovery_pipeline_stats()

        logger.info("All sections complete. Results saved to %s", OUTPUT_DIR)

    except FileNotFoundError as exc:
        logger.error("Required file not found: %s", exc)
    except Exception as exc:
        logger.error("Evaluation failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()