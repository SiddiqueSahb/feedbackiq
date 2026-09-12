"""
Generates the two dissertation figures not produced elsewhere:
Fig 4.1 - review length distribution by platform (evidence for the
cross-domain register claim), Fig 4.2 - confusion matrices for four
models side by side (VADER/RoBERTa behaving as binary classifiers,
per Table 4.7).

Saved to data/results/figures/ at 300 dpi, colour-blind friendly palette.

Usage: python evaluate/generate_figures.py
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from feedbackiq.core.config import settings

OUTPUT_DIR = PROJECT_ROOT / "data" / "results" / "figures"
RESULTS_DIR = PROJECT_ROOT / "data" / "results"

PLATFORMS = ["amazon", "yelp", "twitter_airline"]
CLASSES = ["negative", "neutral", "positive"]

# Okabe & Ito colour-blind friendly palette
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("generate_figures")


def pretty(label: str) -> str:
    """'twitter_airline' -> 'Twitter Airline'."""
    return str(label).replace("_", " ").title()


def figure_review_length() -> None:
    """Fig 4.1: review length by platform. Log-scale box plot (Amazon's long
    tail would flatten Twitter otherwise) + median word count bars (the
    number the text quotes)."""
    data_path = PROJECT_ROOT / settings.DATA_PATH
    logger.info("Loading %s", data_path)
    df = pd.read_parquet(data_path, columns=["platform", "text"])
    df = df.dropna(subset=["text"])

    # Sample instead of counting all 642,692 reviews - same shape, much
    # faster (seed 42, up to 40k per platform).
    sample = pd.concat([
        group.sample(n=min(len(group), 40_000), random_state=42)
        for _, group in df.groupby("platform")
    ])
    logger.info("Sampled %d reviews for length analysis", len(sample))

    df = sample.copy()
    df["word_count"] = df["text"].str.count(r"\S+")
    df = df[df["word_count"] > 0]

    groups, labels, medians = [], [], []
    for platform in PLATFORMS:
        counts = df.loc[df["platform"] == platform, "word_count"]
        if counts.empty:
            continue
        groups.append(counts.values)
        labels.append(pretty(platform))
        medians.append(float(counts.median()))
        logger.info("%-16s n=%7d  median=%5.1f  mean=%5.1f  p95=%6.1f",
                    pretty(platform), len(counts), counts.median(),
                    counts.mean(), counts.quantile(0.95))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    bp = ax1.boxplot(groups, labels=labels, patch_artist=True, showfliers=False,
                     medianprops={"color": "black", "linewidth": 1.5})
    for patch, colour in zip(bp["boxes"], OKABE_ITO):
        patch.set_facecolor(colour)
        patch.set_alpha(0.75)
    ax1.set_yscale("log")
    ax1.set_ylabel("Words per review (log scale)")
    ax1.set_title("Review length distribution")
    ax1.grid(axis="y", alpha=0.3)

    ax2.bar(labels, medians, color=OKABE_ITO[: len(labels)])
    ax2.set_ylabel("Median words per review")
    ax2.set_title("Median review length")
    for i, value in enumerate(medians):
        ax2.text(i, value, f"{value:.0f}", ha="center", va="bottom")
    ax2.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    out = OUTPUT_DIR / "fig_4_1_review_length_by_platform.png"
    fig.savefig(out, dpi=300)
    plt.close(fig)
    logger.info("Saved %s", out.name)


def figure_confusion_matrices() -> None:
    """Fig 4.2: confusion matrices, row-normalised %. Top row = off-the-shelf
    models, bottom row = models trained on this corpus."""
    # All five models shown; sixth panel left free for the colour bar.
    models = [
        ("vader", "VADER (lexicon)"),
        ("roberta_(pre-trained)", "RoBERTa (pretrained)"),
        (None, None),  # spacer — colour bar goes here
        ("naive_bayes", "Naive Bayes"),
        ("logistic_regression", "Logistic Regression"),
        ("distilbert_(fine-tuned)", "DistilBERT (fine-tuned)"),
    ]

    # Single-hue sequential map, readable in greyscale and colour-blind safe.
    cmap = LinearSegmentedColormap.from_list("okabe_blue", ["#FFFFFF", "#0072B2"])

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    # Extra horizontal spacing - otherwise y-axis labels collide with neighbours.
    fig.subplots_adjust(wspace=0.35, hspace=0.35, top=0.86)
    spacer_ax = axes[0, 2]

    for idx, (ax, (slug, title)) in enumerate(zip(axes.flat, models)):
        if slug is None:
            ax.axis("off")
            continue
        path = RESULTS_DIR / f"cm_{slug}.json"
        if not path.exists():
            logger.warning("Missing %s — panel skipped", path.name)
            ax.axis("off")
            continue

        with open(path) as f:
            payload = json.load(f)

        matrix = np.array(payload["confusion_matrix"], dtype=float)
        labels = [pretty(l) for l in payload["labels"]]
        pct = matrix / matrix.sum(axis=1, keepdims=True) * 100

        im = ax.imshow(pct, cmap=cmap, vmin=0, vmax=100)

        ax.set_xticks(range(len(labels)), labels)
        ax.set_yticks(range(len(labels)), labels)
        ax.set_xlabel("Predicted")
        # Only leftmost panel per row gets the y-label (avoids overlap).
        if idx % 3 == 0:
            ax.set_ylabel("True")
        ax.set_title(title, fontsize=11, pad=8)

        for i in range(len(labels)):
            for j in range(len(labels)):
                # White text on dark cells, black on light ones.
                ax.text(j, i, f"{pct[i, j]:.1f}%", ha="center", va="center",
                        fontsize=10,
                        color="white" if pct[i, j] > 55 else "black")

    fig.suptitle("Confusion matrices for all five models, row-normalised\n"
                 "Top row: models that learned nothing from this corpus. "
                 "Bottom row: models trained on it.",
                 fontsize=13)

    # Colour bar in the lower part of the free slot; reading note sits above it.
    cbar = fig.colorbar(im, ax=spacer_ax, fraction=0.28, pad=0.02,
                        anchor=(0.0, 0.0), shrink=0.75,
                        label="% of true class")
    cbar.ax.tick_params(labelsize=9)

    spacer_ax.text(0.5, 0.72,
                   "Reading the figure\n\n"
                   "The neutral column is near-empty\n"
                   "for both top-row models and\n"
                   "populated for all three below.\n"
                   "Both off-the-shelf models are\n"
                   "behaving as binary classifiers.",
                   transform=spacer_ax.transAxes, ha="center", va="top",
                   fontsize=10, style="italic", linespacing=1.4)

    out = OUTPUT_DIR / "fig_4_2_confusion_matrices.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved %s", out.name)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Output folder: %s", OUTPUT_DIR)
    try:
        figure_review_length()
        figure_confusion_matrices()
        logger.info("Done.")
    except FileNotFoundError as exc:
        logger.error("Required file not found: %s", exc)
    except Exception as exc:
        logger.error("Figure generation failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
