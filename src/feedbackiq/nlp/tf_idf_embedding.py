from functools import lru_cache

import pandas as pd

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import settings


@lru_cache(maxsize=1)
def load_tfidf():

    df = pd.read_parquet(settings.DATA_PATH)

    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_features=50000
    )

    matrix = vectorizer.fit_transform(
        df["cleaned_text"]
    )

    return df, vectorizer, matrix


def tfidf_search(
    query,
    top_k=10,
    platform_filter=None,
    sentiment_filter=None,
    min_rating=1
):

    df, vectorizer, matrix = load_tfidf()

    query_vector = vectorizer.transform([query])

    scores = cosine_similarity(
    query_vector,
    matrix
).flatten()

    import numpy as np

    # Find only the Top-K highest similarity scores
    top_indices = np.argpartition(scores, -top_k)[-top_k:]

    # Sort only the Top-K results in descending order
    top_indices = top_indices[
        np.argsort(scores[top_indices])[::-1]
    ]

    results = []

    for idx in top_indices:

        row = df.iloc[idx]

        if platform_filter and row["platform"] != platform_filter:
            continue

        if sentiment_filter and row["sentiment_label"] != sentiment_filter:
            continue

        if row["rating"] < min_rating:
            continue

        results.append({

            "review_id": row["review_id"],

            "text": row["text"],

            "platform": row["platform"],

            "rating": row["rating"],

            "sentiment_label": row["sentiment_label"],

            "similarity_score": round(float(scores[idx]),4)

        })

        if len(results) == top_k:
            break

    return results