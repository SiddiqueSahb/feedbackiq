"""
Analytics service for the FeedbackIQ dashboard.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from functools import lru_cache

import pandas as pd
import spacy

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger

log = get_logger("service.analytics")

_nlp_lock = threading.Lock()
_df_lock = threading.Lock()

# Precomputed by scripts/precompute_keywords.py — see get_top_keywords().
KEYWORDS_FILE = str(settings.keywords_file)

# Fallback size when the precomputed file is absent; rankings are stable
# well below the full corpus. Results are labelled "sample" either way.
KEYWORD_SAMPLE_SIZE = 25_000
KEYWORD_SAMPLE_SEED = 42

# Keyword filtering: drop pronoun/determiner-headed chunks, strip leading
# determiners, drop stopwords/short words. Without this, "they", "you",
# "that" top the "business keywords" list.
#
# Bump KEYWORD_FILTER_VERSION on rule changes — _load_precomputed_keywords()
# rejects files built with an older version.
KEYWORD_FILTER_VERSION = 2

MIN_PHRASE_CHARS = 3

_LEADING_DETERMINER = re.compile(
    r"^(?:the|a|an|my|your|our|their|his|her|its|this|that|these|those|"
    r"some|any|no|every|another|each)\s+",
    re.IGNORECASE,
)

KEYWORD_STOPWORDS = frozenset({
    # pronouns and pro-forms that survive the POS check as multi-word chunks
    "they", "you", "that", "this", "she", "he", "it", "we", "i", "me", "him",
    "her", "them", "us", "who", "what", "which", "there", "these", "those",
    "all", "some", "any", "none", "one", "ones", "another", "others",
    "something", "anything", "nothing", "everything", "someone", "anyone",
    "everyone", "nobody", "somebody", "anybody", "everybody",
    "myself", "yourself", "himself", "herself", "itself", "ourselves",
    "themselves", "yourselves",
    # contentless nouns; "time" deliberately excluded, "wait time" etc. are
    # informative on their own.
    "thing", "things", "way", "ways", "lot", "lots", "bit", "kind", "sort",
    "part", "parts", "case", "cases", "point", "points",
    # bare determiners; catches mis-tagged single-word chunks rule 2's
    # regex would miss.
    "the", "a", "an", "this", "that", "these", "those", "my", "your", "our",
    "their", "his", "its", "every", "each",
})


def normalise_phrase(chunk) -> str | None:
    """
    Turn one spaCy noun chunk into a keyword, or None to drop it.

    Shared with scripts/precompute_keywords.py so the two paths can't
    drift apart.
    """
    # 1. pronoun- or determiner-headed chunks carry no topical content
    if chunk.root.pos_ in ("PRON", "DET"):
        return None

    text = chunk.text.lower().strip()

    # 2. "the food" and "food" should be the same keyword
    text = _LEADING_DETERMINER.sub("", text).strip()

    # 3. what's left has to be a real word
    if len(text) < MIN_PHRASE_CHARS:
        return None
    if text in KEYWORD_STOPWORDS:
        return None
    if not any(ch.isalpha() for ch in text):
        return None

    return text


@lru_cache(maxsize=1)
def _get_nlp_uncached():
    """
    Lazy-loaded so importing this module doesn't delay app startup.
    ner/lemmatizer disabled — noun_chunks only needs tagger + parser.
    """
    return spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])


def _get_nlp():
    with _nlp_lock:
        return _get_nlp_uncached()


@lru_cache(maxsize=1)
def _load_df_uncached() -> pd.DataFrame:
    """Load the processed dataset once."""

    if not settings.data_file.exists():
        log.warning("Dataset not found: %s", settings.data_file)
        return pd.DataFrame()

    log.info("Loading dataset...")
    return pd.read_parquet(settings.data_file)


def _load_df() -> pd.DataFrame:
    # Prevents two concurrent first requests both reading the 407MB parquet
    # at once — lru_cache alone doesn't close that race.
    with _df_lock:
        return _load_df_uncached()


def get_summary_stats() -> dict:
    """Return dashboard summary statistics."""

    df = _load_df()

    if df.empty:
        return {}

    return {
        "total_reviews": len(df),
        "platforms": df["platform"].nunique(),
        "avg_rating": round(df["rating"].mean(), 2),
        "positive_pct": round(
            (df["sentiment_label"] == "positive").mean() * 100,
            1,
        ),
        "negative_pct": round(
            (df["sentiment_label"] == "negative").mean() * 100,
            1,
        ),
        "neutral_pct": round(
            (df["sentiment_label"] == "neutral").mean() * 100,
            1,
        ),
    }

def get_sentiment_by_platform() -> list[dict]:
    """Return sentiment counts grouped by platform."""

    df = _load_df()

    if df.empty:
        return []

    result = (
        df.groupby(["platform", "sentiment_label"])
        .size()
        .reset_index(name="count")
        .sort_values("platform")
    )

    return result.to_dict("records")


def get_rating_distribution() -> list[dict]:
    """Return the number of reviews for each rating."""

    df = _load_df()

    if df.empty:
        return []

    ratings = (
        df["rating"]
        .value_counts()
        .sort_index()
        .rename_axis("rating")
        .reset_index(name="count")
    )

    return ratings.to_dict("records")

@lru_cache(maxsize=1)
def _load_precomputed_keywords() -> dict | None:
    """
    Read the precomputed keyword counts, or None if they haven't been
    generated yet. Cached because the file doesn't change while the process
    is running; restart the backend after regenerating it.
    """

    if not os.path.exists(KEYWORDS_FILE):
        log.warning(
            "No precomputed keywords at %s — falling back to a %s-review "
            "sample. Run scripts/precompute_keywords.py for exact counts.",
            KEYWORDS_FILE,
            f"{KEYWORD_SAMPLE_SIZE:,}",
        )
        return None

    try:
        with open(KEYWORDS_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)

    except (OSError, json.JSONDecodeError):
        log.exception("Precomputed keyword file is unreadable: %s", KEYWORDS_FILE)
        return None

    if not isinstance(payload.get("keywords"), dict):
        log.error("Precomputed keyword file has no 'keywords' object: %s", KEYWORDS_FILE)
        return None

    # Refuse a file built by an older filter version — otherwise it keeps
    # silently serving stale counts (e.g. pronoun-dominated results).
    file_version = payload.get("filter_version", 1)
    if file_version != KEYWORD_FILTER_VERSION:
        log.warning(
            "Precomputed keywords were built with filter version %s but the "
            "current filter is version %s. Ignoring the file and sampling "
            "instead. Re-run scripts/precompute_keywords.py to refresh it.",
            file_version, KEYWORD_FILTER_VERSION,
        )
        return None

    log.info(
        "Loaded precomputed keywords generated %s (filter v%s)",
        payload.get("generated_at", "at an unknown time"), file_version,
    )

    return payload


@lru_cache(maxsize=8)
def get_top_keywords(
    sentiment: str | None = None,
    n: int = 30,
) -> list[dict]:
    """
    Return the most common business-related phrases.

    Prefers precomputed counts (scripts/precompute_keywords.py) — a live
    spaCy pass over the full corpus takes tens of minutes, too slow for a
    request. Falls back to sampling if the precomputed file is missing.
    Each record carries `basis` ("corpus"/"sample") and `n_reviews`.
    """

    precomputed = _load_precomputed_keywords()

    if precomputed is not None:

        key = sentiment or "all"
        entries = precomputed["keywords"].get(key)

        if entries is None:
            log.warning(
                "Precomputed keywords have no '%s' bucket; sampling instead.", key
            )

        else:
            reviewed = precomputed.get("review_counts", {}).get(key, 0)

            return [
                {
                    "word": entry["word"],
                    "count": entry["count"],
                    "basis": "corpus",
                    "n_reviews": reviewed,
                }
                for entry in entries[:n]
            ]

    # Fallback: sample, and say so.
    df = _load_df()

    if df.empty:
        return []

    if sentiment:
        df = df[df["sentiment_label"] == sentiment]

    texts = df["cleaned_text"].dropna()

    if texts.empty:
        return []

    sampled = len(texts) > KEYWORD_SAMPLE_SIZE

    if sampled:
        # Fixed seed so results are stable across calls/restarts.
        texts = texts.sample(KEYWORD_SAMPLE_SIZE, random_state=KEYWORD_SAMPLE_SEED)

    log.info(
        "Computing keywords live over %d reviews (sampled=%s).", len(texts), sampled
    )

    counter = Counter()

    # nlp.pipe() batches internally — faster than per-row calls.
    # normalise_phrase() matches precompute_keywords.py so sample and
    # precomputed results use identical rules.
    for doc in _get_nlp().pipe(texts.tolist(), batch_size=200):

        counter.update(
            phrase
            for phrase in (normalise_phrase(chunk) for chunk in doc.noun_chunks)
            if phrase is not None
        )

    return [
        {
            "word": word,
            "count": count,
            "basis": "sample" if sampled else "corpus",
            "n_reviews": len(texts),
        }
        for word, count in counter.most_common(n)
    ]


def warm_cache() -> None:
    """
    Load the parquet and precomputed keywords ahead of the first request.

    Called from main.py's startup hook. Swallows errors deliberately — a
    failed warm-up should slow the first request, not block startup.
    """

    try:
        rows = len(_load_df())
        _load_precomputed_keywords()
        log.info("Warm-up complete: %d rows resident.", rows)

    except Exception:
        log.exception("Warm-up failed; data will be loaded on first request.")

def get_platform_list() -> list[str]:
    """Return all available review platforms."""

    df = _load_df()

    if df.empty:
        return []

    return sorted(df["platform"].dropna().unique().tolist())

def get_trend_data() -> list[dict]:
    """Return monthly sentiment trends."""

    df = _load_df()

    if df.empty or "date" not in df.columns:
        return []

    try:

        trend_df = df.copy()

        trend_df["date"] = pd.to_datetime(
            trend_df["date"],
            errors="coerce",
        )

        trend_df = trend_df.dropna(subset=["date"])

        trend_df["month"] = (
            trend_df["date"]
            .dt.to_period("M")
            .astype(str)
        )

        trends = (
            trend_df.groupby(
                ["month", "sentiment_label"]
            )
            .size()
            .reset_index(name="count")
            .sort_values("month")
        )

        return trends.to_dict("records")

    except Exception:
        log.exception("Unable to generate trend data.")
        return []