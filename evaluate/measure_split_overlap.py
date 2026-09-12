"""
Measures exact overlap between the shared sentiment test set and DistilBERT's
fine-tuning partition, to quantify test-set contamination (Ch3 3.5.3).

Both splits are deterministic (seed 42) from the same parquet, so the
overlap is computed exactly via pandas index intersection, not estimated.

Usage: python evaluate/measure_split_overlap.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd
from sklearn.model_selection import train_test_split

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

LABEL_MAP = {"negative": 0, "neutral": 1, "positive": 2}


def build_shared_test_set(df: pd.DataFrame) -> pd.Index:
    """80/20 test set, mirrors scripts/evaluate_models.py L64-73 (incl. the
    length filter, which the fine-tuning notebook skips)."""
    d = df.dropna(subset=["cleaned_text", "sentiment_label"])
    d = d[d["cleaned_text"].str.len() > 5]
    d = d.assign(label_id=d["sentiment_label"].map(LABEL_MAP))
    d = d.dropna(subset=["label_id"])
    d["label_id"] = d["label_id"].astype(int)

    _, test_df = train_test_split(
        d, test_size=0.2, random_state=42, stratify=d["label_id"]
    )
    return test_df.index


def build_finetune_partitions(df: pd.DataFrame) -> tuple[pd.Index, pd.Index, pd.Index]:
    """70/15/15 DistilBERT fine-tune split, mirrors
    notebooks/3.DistillBERTFineTuning.ipynb cells 8-15 (no length filter;
    nested split: 0.15 out, then 0.176 of the rest)."""
    d = df[["cleaned_text", "sentiment_label", "platform"]].dropna(
        subset=["cleaned_text", "sentiment_label"]
    )
    d = d.assign(label=d["sentiment_label"].map(LABEL_MAP))
    d = d.dropna(subset=["label"])
    d["label"] = d["label"].astype(int)

    train_val, test = train_test_split(
        d, test_size=0.15, random_state=42, stratify=d["label"]
    )
    train, val = train_test_split(
        train_val, test_size=0.176, random_state=42, stratify=train_val["label"]
    )
    return train.index, val.index, test.index


def main() -> None:
    if not os.path.exists(settings.DATA_PATH):
        sys.exit(f"Dataset not found at {settings.DATA_PATH}")

    print("Loading dataset...")
    df = pd.read_parquet(settings.DATA_PATH)
    print(f"  {len(df):,} rows\n")

    shared_test = build_shared_test_set(df)
    ft_train, ft_val, ft_test = build_finetune_partitions(df)

    print(f"Shared 80/20 test set        : {len(shared_test):,} reviews")
    print(f"DistilBERT train partition   : {len(ft_train):,} reviews")
    print(f"DistilBERT validation        : {len(ft_val):,} reviews")
    print(f"DistilBERT test partition    : {len(ft_test):,} reviews\n")

    seen_in_training = shared_test.intersection(ft_train)
    seen_in_validation = shared_test.intersection(ft_val)
    seen_either = shared_test.intersection(ft_train.union(ft_val))

    n = len(shared_test)
    print("Overlap with the shared test set")
    print("-" * 46)
    print(
        f"  Seen during training     : {len(seen_in_training):>8,}"
        f"  ({len(seen_in_training) / n:.1%})"
    )
    print(
        f"  Seen during validation   : {len(seen_in_validation):>8,}"
        f"  ({len(seen_in_validation) / n:.1%})"
    )
    print(
        f"  Seen in either           : {len(seen_either):>8,}"
        f"  ({len(seen_either) / n:.1%})"
    )
    print(
        f"  Genuinely held out       : {n - len(seen_either):>8,}"
        f"  ({(n - len(seen_either)) / n:.1%})\n"
    )

    print(
        "The 'seen in either' percentage is the contamination figure to quote "
        "in Chapter 3 §3.5.3.\nThe fine-tuned model's clean result remains the "
        "one from its own held-out\npartition, which is disjoint from its training data by "
        "construction."
    )


if __name__ == "__main__":
    main()
