"""
Preprocess real review datasets → unified Parquet file.

Datasets:
  Amazon  — All_Beauty.jsonl.gz, Grocery.jsonl.gz, Electronics.jsonl.gz
             (Amazon Reviews 2023 JSONL format, from McAuley Lab / UCSD)
  Yelp    — yelp_academic_dataset_review.json (JSON lines)
  Twitter — Tweets.csv.zip (US Airline Sentiment, Kaggle)

Output:
  data/processed/reviews_unified.parquet

Usage:
    python scripts/preprocess.py

Place your raw files following this layout:
    data/raw/amazon/All_Beauty.jsonl.gz
    data/raw/amazon/Grocery.jsonl.gz
    data/raw/amazon/Electronics.jsonl.gz
    data/raw/Yelp/yelp_academic_dataset_review.json
    data/raw/Twitter/Tweets.csv.zip
"""
from __future__ import annotations

import os
import re
import sys
import json
import gzip
from typing import Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AMAZON_BEAUTY_PATH      = "data/raw/amazon/All_Beauty.jsonl.gz"
AMAZON_GROCERY_PATH     = "data/raw/amazon/Grocery.jsonl.gz"
AMAZON_ELECTRONICS_PATH = "data/raw/amazon/Electronics.jsonl.gz"
YELP_PATH               = "data/raw/Yelp/yelp_academic_dataset_review.json"
TWITTER_PATH = "data/raw/Twitter/Tweets.csv.zip"



AMAZON_PER_CLASS = 20000

YELP_PER_CLASS = 150000

OUTPUT_PATH = "data/processed/reviews_unified.parquet"

os.makedirs("data/processed", exist_ok=True)
os.makedirs("data/raw/amazon", exist_ok=True)
os.makedirs("data/raw/Yelp",   exist_ok=True)
os.makedirs("data/raw/Twitter", exist_ok=True)

def clean_text(text:str) -> str:
    """
    Clean and normalise review text.
    Steps: URL removal → @mentions → hashtags → HTML → emoji→text
           → non-ASCII removal → lowercase.
    """
    text = str(text)
    text = re.sub(r"http\S+|www\S+", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"#(\w+)", r"\1", text)
    text = re.sub(r"<.*?>", " ", text)
    try:
        import emoji
        text = emoji.demojize(text, delimiters=(" ", " "))
    except ImportError:
        pass   # skip emoji conversion if package not installed
    text = text.replace("_", " ")  # from emoji names like :thumbs_up:
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()

def rating_to_sentiment(rating:int) -> str:
    """ Map rating to sentiment class.
    Rating 1-2: Negative
    3: Neutral
    4-5: Positive
    """
    if rating <= 2:
        return "negative"
    elif rating == 3:
        return "neutral"
    else:
        return "positive"

def balanced_sample(df: pd.DataFrame, per_class: int, seed: int = 42) -> pd.DataFrame:
    """
    Sample per_class reviews from each sentiment class.
    If a class has fewer rows than per_class, takes all rows from that class.
    """
    sampled = []
    for _, group in df.groupby("sentiment_label"):
        sampled.append(group.sample(n=min(len(group), per_class), random_state=seed))
    return pd.concat(sampled, ignore_index=True)


def standardise_amazon(df:pd.DataFrame, category:str) -> pd.DataFrame:
    """Standardise a raw Amazon JSONL DataFrame into the unified schema."""
    output = pd.DataFrame({
        "platform": "amazon",
        "product_category": category,
        "text": (df["title"].fillna("") + ". " + df["text"].fillna("")).str.strip(". ").str.strip(),
        "rating": df["rating"].astype(float),
        "date": pd.to_datetime(
            df["timestamp"],
            unit="ms",
            errors="coerce"
        ).dt.strftime("%Y-%m-%d"),
        "review_id": df["asin"].astype(str) + "_" + df.index.astype(str),
    })

    output["sentiment_label"] = output["rating"].apply(rating_to_sentiment)

    return output

def load_amazon_jsonl(path: str, category: str, per_class: int,
                      chunk_size: int = 100000) -> Optional[pd.DataFrame]:
    """
    Load an Amazon Reviews 2023 JSONL.gz file in chunks, standardise,
    and return a class-balanced sample.
    """
    if not os.path.exists(path):
        print(f" Not found: {path}  (skipping)")
        return None

    print(f"   Loading {category} from {path}...")

    chunks: list[pd.DataFrame] = []
    total = 0

    opener = gzip.open if path.endswith(".gz") else open

    buffer: list[dict] = []

    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                buffer.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(buffer) >= chunk_size:
                chunk_df = pd.DataFrame(buffer)
                for col in ["title", "text", "rating", "timestamp", "asin"]:
                    if col not in chunk_df.columns:
                        chunk_df[col] = None
                chunk_df = chunk_df[
                    chunk_df["text"].notna() &
                    (chunk_df["text"].astype(str).str.len() > 10)
                ]
                if not chunk_df.empty:
                    chunks.append(standardise_amazon(chunk_df, category))
                total += len(buffer)
                buffer = []
                print(f"      ...{total:,} rows read", end="\r")
    if buffer:  # flush the last partial chunk
        chunk_df = pd.DataFrame(buffer)
        for col in ["title", "text", "rating", "timestamp", "asin"]:
            if col not in chunk_df.columns:
                chunk_df[col] = None
        chunk_df = chunk_df[
            chunk_df["text"].notna() &
            (chunk_df["text"].astype(str).str.len() > 10)
        ]
        if not chunk_df.empty:
            chunks.append(standardise_amazon(chunk_df, category))

        total += len(buffer)

    if not chunks:
        print(f"   ⚠  No valid rows in {path}")
        return None

    raw_df = pd.concat(chunks, ignore_index=True)

    print(f"   {category}: {len(raw_df):,} reviews loaded → balancing to {per_class}/class")

    balanced = balanced_sample(raw_df, per_class)

    print(f"   {category}: {len(balanced):,} reviews after balancing")
    print(f"   {category}: class dist: {balanced['sentiment_label'].value_counts().to_dict()}")

    return balanced


def load_yelp(path: str, per_class: int) -> Optional[pd.DataFrame]:
    """
    Load Yelp academic dataset (JSON lines format).
    stars→rating, date[:10], review_id, balanced_sample.
    """
    if not os.path.exists(path):
        print(f"   ⚠  Not found: {path}  (skipping)")
        return None

    print(f"   Loading Yelp from {path}...")

    rows: list[dict] = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                d = json.loads(line)
                text = d.get("text", "")
                if len(str(text)) < 10:
                    continue

                rows.append({
                    "platform":         "yelp",
                    "product_category": "",
                    "text":             str(text),
                    "rating":           float(d.get("stars", 3)),
                    "date":             str(d.get("date", ""))[:10],
                    "review_id":        str(d.get("review_id", f"YLP_{len(rows)}")),
                })

            except Exception:  # skip malformed records
                continue

    if not rows:
        print("  No valid Yelp rows")
        return None

    yelp_std = pd.DataFrame(rows)

    yelp_std["sentiment_label"] = yelp_std["rating"].apply(rating_to_sentiment)

    print(f"   Yelp: {len(yelp_std):,} reviews → balancing to {per_class}/class")

    yelp_balanced = balanced_sample(yelp_std, per_class)

    print(f"   Yelp: {len(yelp_balanced):,} reviews after balancing")
    print(f"   Yelp: class dist: {yelp_balanced['sentiment_label'].value_counts().to_dict()}")

    return yelp_balanced


def load_twitter(path: str) -> Optional[pd.DataFrame]:
    """
    Load Twitter US Airline Sentiment CSV (Kaggle, optionally zipped).
    Matches notebook: airline_sentiment, tweet_created→date, tweet_id→review_id.
    """

    if not os.path.exists(path):
        print(f"   ⚠  Not found: {path}  (skipping)")
        return None

    print(f"   Loading Twitter from {path}...")

    compression = "zip" if path.endswith(".zip") else "infer"

    tweets = pd.read_csv(path, compression=compression)

    twt_std = pd.DataFrame({
        "platform":         "twitter_airline",
        "product_category": "",
        "text":             tweets["text"].astype(str),
        "sentiment_label":  tweets["airline_sentiment"],
        "rating":           tweets["airline_sentiment"].map({
                                "positive": 5,
                                "neutral": 3,
                                "negative": 1
                            }),
        "date": pd.to_datetime(
            tweets["tweet_created"],
            errors="coerce"
        ).dt.strftime("%Y-%m-%d"),
        "review_id": tweets["tweet_id"].astype(str),
    })

    print(f"   Twitter: {len(twt_std):,} reviews")
    print(f"   Twitter: class dist: {twt_std['sentiment_label'].value_counts().to_dict()}")

    return twt_std


def main() -> None:
    """Runs the full pipeline: load each platform, merge, clean, filter, save."""
    dfs : list[pd.Dataframe] = []

    print("\n Amazon Reviews 2023 (3 categories)...")

    amazon_paths = {
        "All_Beauty": AMAZON_BEAUTY_PATH,
        "Grocery" : AMAZON_GROCERY_PATH,
        "Electronics": AMAZON_ELECTRONICS_PATH,
    }
    for category,path in amazon_paths.items():
        amz_cat = load_amazon_jsonl(path,category,AMAZON_PER_CLASS)
        if amz_cat is not None:
            dfs.append(amz_cat)
    
    yelp_df = load_yelp(YELP_PATH,YELP_PER_CLASS)
    if yelp_df is not None:
        dfs.append(yelp_df)
    
    twt_df = load_twitter(TWITTER_PATH)
    if twt_df is not None:
        dfs.append(twt_df)
    
    if not dfs:
        print("\nNo datasets loaded. Check file paths at the top of this script.")
        print("Expected locations:")
        for p in [AMAZON_BEAUTY_PATH, AMAZON_GROCERY_PATH, AMAZON_ELECTRONICS_PATH,
                  YELP_PATH, TWITTER_PATH]:
            print(f"  {p}")
        sys.exit(1)


    print("\n Merging all datasets...")
    df = pd.concat(dfs,ignore_index=True)

    print("Cleaning text...")
    df["cleaned_text"] = df["text"].apply(clean_text)

    # Filtering steps match the original notebook exactly
    df["product_category"] = df["product_category"].fillna("")
    df = df.dropna(subset=["text", "sentiment_label"])
    df = df[df["cleaned_text"].str.len() > 15]
    df = df.drop_duplicates(subset="cleaned_text")
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(3)
    df = df.reset_index(drop=True)

    required_cols = ["review_id", "platform", "product_category","text", "cleaned_text", "rating", "sentiment_label", "date"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
    df = df[required_cols]

    df.to_parquet(OUTPUT_PATH, index=False)

    print(f" Preprocessing complete!")
    print(f"  Output    : {OUTPUT_PATH}")
    print(f"  Total rows: {len(df):,}")

    print(f"\n  Platform distribution:")
    for plat, cnt in df["platform"].value_counts().items():
        print(f"    {plat:<25} {cnt:>8,}")

    print(f"\n  Sentiment distribution:")
    for sent, cnt in df["sentiment_label"].value_counts().items():
        print(f"    {sent:<25} {cnt:>8,}")

    if "product_category" in df.columns:
        cats = df[df["product_category"] != ""]["product_category"].value_counts()
        if not cats.empty:
            print(f"\n  Amazon categories:")
            for cat, cnt in cats.items():
                print(f"    {cat:<25} {cnt:>8,}")


if __name__ == "__main__":
    main()