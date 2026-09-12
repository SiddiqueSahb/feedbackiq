"""
Precompute the dashboard's keyword frequencies.

get_top_keywords() used to run spaCy over the full corpus (~26M words) on the
first API request -- tens of minutes, hanging the page. This script does that
work once, offline, and writes the counts to JSON so the endpoint just reads
the file.

Run after any change to data/processed/reviews_unified.parquet:

    python scripts/precompute_keywords.py

Takes a while (full 26M words). If the output file is missing, the endpoint
falls back to a sample.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import spacy

from feedbackiq.services.analytics_service import (  # noqa: E402
    KEYWORD_FILTER_VERSION,
    normalise_phrase,
)
from feedbackiq.core.config import settings  # noqa: E402

# Keep more than the dashboard asks for (it requests 30, the API allows up to
# 100) so the same file serves every reasonable `n` without recomputing.
TOP_N = 200

# One bucket per sentiment filter value, plus unfiltered (None)
BUCKETS = [None, "positive", "negative", "neutral"]

OUTPUT_DIR = os.path.join("data", "results", "keywords")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "top_keywords.json")

BATCH_SIZE = 200


def count_phrases(nlp, texts: list[str]) -> Counter:
    """
    Noun-chunk frequencies over a list of texts.

    Phrase filtering comes from normalise_phrase() in analytics_service, not
    from a copy here. That is deliberate: the API falls back to computing
    these live from a sample when this file is missing, and if the two used
    different rules the dashboard would report different numbers for the same
    corpus depending on which path served the request.
    """

    counter: Counter = Counter()
    started = time.time()

    for i, doc in enumerate(nlp.pipe(texts, batch_size=BATCH_SIZE), start=1):

        counter.update(
            phrase
            for phrase in (normalise_phrase(chunk) for chunk in doc.noun_chunks)
            if phrase is not None
        )

        if i % 25_000 == 0:
            rate = i / max(time.time() - started, 1e-9)
            remaining = (len(texts) - i) / max(rate, 1e-9)
            print(
                f"    {i:,}/{len(texts):,} reviews "
                f"({rate:,.0f}/s, ~{remaining / 60:.1f} min left)",
                flush=True,
            )

    return counter


def main() -> int:

    if not os.path.exists(settings.DATA_PATH):
        print(f"Dataset not found: {settings.DATA_PATH}", file=sys.stderr)
        return 1

    print(f"Reading {settings.DATA_PATH} ...", flush=True)
    df = pd.read_parquet(
        settings.DATA_PATH,
        columns=["cleaned_text", "sentiment_label"],
    )
    print(f"  {len(df):,} rows", flush=True)

    # Same pipeline config as the service, so counts match what it would produce
    print("Loading spaCy (en_core_web_sm, ner + lemmatizer disabled) ...", flush=True)
    nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])

    results: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    overall_start = time.time()

    for bucket in BUCKETS:

        key = bucket or "all"
        subset = df if bucket is None else df[df["sentiment_label"] == bucket]
        texts = subset["cleaned_text"].dropna().tolist()
        counts[key] = len(texts)

        print(f"\n[{key}] {len(texts):,} reviews", flush=True)

        if not texts:
            results[key] = []
            continue

        counter = count_phrases(nlp, texts)

        results[key] = [
            {"word": word, "count": count}
            for word, count in counter.most_common(TOP_N)
        ]

        print(f"  {len(counter):,} distinct phrases, keeping top {TOP_N}", flush=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": settings.DATA_PATH,
        "basis": "corpus",
        "spacy_model": "en_core_web_sm",
        # Lets the API refuse a file built by an older filter version
        "filter_version": KEYWORD_FILTER_VERSION,
        "top_n": TOP_N,
        "review_counts": counts,
        "keywords": results,
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    elapsed = (time.time() - overall_start) / 60
    print(f"\nWrote {OUTPUT_FILE} in {elapsed:.1f} min", flush=True)
    print("The dashboard will now serve exact corpus counts instantly.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
