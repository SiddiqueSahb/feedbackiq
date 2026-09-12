"""
Dynamic complaint category discovery: BERTopic per platform, then merges
near-duplicate topics ACROSS platforms into one consolidated taxonomy before
an LLM names each merged cluster. Raw per-platform BERTopic topics are a
clustering artifact, not a business taxonomy (lots of near-synonym topics,
plus platform-irrelevant categories leaking into other platforms' predictions
if used unmerged) -- see docs/methodology_review.md for the full rationale.

Usage:
    python scripts/discover_categories.py                       # discover -> merge -> name, all platforms
    python scripts/discover_categories.py --sentiment negative
    python scripts/discover_categories.py --target-categories 18
    python scripts/discover_categories.py --platform amazon     # audit mode: single platform, no merge
    python scripts/discover_categories.py --use-llm             # (audit mode) LLM labels instead of rule-based

Outputs (default multi-platform mode):
    data/processed/complaint_categories_all_<sentiment>.json   <- consolidated taxonomy, used by categoriser.py
    data/processed/taxonomy_merge_info_<sentiment>.json        <- merge diagnostics
    data/processed/bertopic_info_<platform>_<sentiment>.json   <- raw per-platform topics (audit trail)

Author: Mohammad Asim | MSc Dissertation 2026
"""

from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS
from bertopic.representation import KeyBERTInspired
from langdetect import detect, LangDetectException, DetectorFactory
DetectorFactory.seed = 0  # otherwise non-deterministic, which would invalidate cached embeddings
from groq import Groq

import json
import argparse
import numpy as np
import pandas as pd
from collections import Counter

from config import settings
from logger import get_logger

log = get_logger("discover_categories")
representation_model = KeyBERTInspired()

import spacy
nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])

PLATFORMS = ["amazon", "yelp", "twitter_airline"]
DEFAULT_TARGET_CATEGORIES = 24  # raised from 18 -- 18 produced an oversized catch-all cluster

# Fallback static categories (used if BERTopic fails entirely)
STATIC_FALLBACK: list[str] = [
    "product quality",
    "delivery and shipping",
    "customer service",
    "pricing and value",
    "user experience",
    "technical issues",
    "refund and returns",
]


# Stage 1 helpers — language filter, dynamic min topic size, noise filter

def filter_english(texts: list[str]) -> list[str]:
    """Removes non-English reviews using langdetect. Short texts (<4 words) kept as-is."""
    keep = []
    for text in texts:
        if len(text.split()) < 4:
            keep.append(text)
            continue
        try:
            if detect(text) == "en":
                keep.append(text)
        except LangDetectException:
            keep.append(text)

    removed = len(texts) - len(keep)
    if removed:
        print(f"  Language filter: removed {removed:,} non-English reviews "
              f"({removed / len(texts) * 100:.1f}%)")
    else:
        print(f"  Language filter: all reviews appear to be English")
    return keep


def dynamic_min_topic_size(n_texts: int) -> int:
    """Scale min topic size with corpus size: 0.15% of corpus, capped 30-300.
    Was 0.5% capped 50-500 -- that collapsed a 58k-review corpus (min_size=289)
    into just 2 HDBSCAN topics. Over-splitting here is fine; merge_topics()
    consolidates afterward."""
    size = int(n_texts * 0.0015)
    return max(30, min(300, size))


def filter_noise_topics(topic_details: list[dict]) -> list[dict]:
    """
    Three-stage dynamic noise filter — fully data-driven, no hardcoded word lists.

    Stage 1 — Cross-topic noise: words appearing in >50% of all topics carry no
              discriminative signal.
    Stage 2 — Keyword dominance: if one word dominates >=70% of a topic's own
              keywords, the topic is degenerate.
    Stage 3 — Noun presence: topics with no nouns/proper nouns are pure
              sentiment expressions, not complaint categories.
    """
    if len(topic_details) < 3:
        return topic_details

    word_count = Counter(kw.lower() for d in topic_details for kw in d["keywords"])
    noise = {w for w, c in word_count.items() if c >= len(topic_details) * 0.5}
    if noise:
        print(f"  Global noise words: {', '.join(sorted(noise)[:8])}")

    after_s1 = []
    for detail in topic_details:
        kws = [kw.lower() for kw in detail["keywords"]]
        noise_ratio = sum(1 for kw in kws if any(n in kw for n in noise)) / max(len(kws), 1)
        if noise_ratio >= 0.6:
            print(f"  Dropped (cross-topic): '{detail['label']}' ({detail['count']:,} reviews)")
        else:
            after_s1.append(detail)

    # Dominance alone isn't noise -- a coherent single-concept topic (e.g. "flight
    # cancelled") naturally has one dominant word. Only flag it if that word is
    # also a cross-topic noise word from Stage 1.
    after_s2 = []
    for detail in after_s1:
        word_freq = Counter(
            w for kw in detail["keywords"]
            for w in kw.lower().split() if len(w) > 2
        )
        top_word, top_count = word_freq.most_common(1)[0] if word_freq else ("", 0)
        dominance = top_count / max(len(detail["keywords"]), 1)
        if dominance >= 0.7 and top_word in noise:
            print(f"  Dropped (word dominance + cross-topic noise): '{detail['label']}' — '{top_word}' in {top_count}/{len(detail['keywords'])} keywords")
        else:
            after_s2.append(detail)

    result = []
    for detail in after_s2:
        all_text = " ".join(detail["keywords"][:5])
        doc = nlp(all_text)
        has_noun = any(token.pos_ in {"NOUN", "PROPN"} for token in doc)
        if not has_noun:
            print(f"  Dropped (no nouns): '{detail['label']}' ({detail['count']:,} reviews)")
        else:
            result.append(detail)

    removed = len(topic_details) - len(result)
    if removed:
        print(f"  Noise filter total: {removed} removed, {len(result)} remain")
    return result


def _word_root(word: str) -> str:
    w = word.lower().replace(" ", "")
    for suffix in ("ing", "tion", "ed", "er", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    return w


def clean_topic_label(keywords: list[tuple[str, float]]) -> str:
    """Rule-based fallback: converts raw BERTopic keywords into a readable label."""
    clean = []
    for word, _ in keywords:
        word = word.replace("_", " ").strip()
        parts = word.lower().split()

        if len(parts) == 2 and parts[0] == parts[1]:
            continue
        if word.lower() in ENGLISH_STOP_WORDS or len(word) <= 2:
            continue
        doc = nlp(word)
        if not any(token.pos_ in {"NOUN", "PROPN"} for token in doc):
            continue
        clean.append(word)

    if not clean:
        clean = [word for word, _ in keywords[:4]]

    seen_roots = set()
    deduped = []
    for word in clean:
        root = _word_root(word)
        w_lower = word.lower()
        already_covered = (
            root in seen_roots or
            any(w_lower in e.lower() or e.lower() in w_lower for e in deduped)
        )
        if not already_covered:
            seen_roots.add(root)
            deduped.append(word)

    return ", ".join(deduped[:4]).title()


def llm_convert_to_category(keywords: list[tuple[str, float]]) -> str:
    """Single-topic LLM naming — used only in single-platform audit mode (--platform)."""
    keyword_str = ", ".join(word for word, _ in keywords[:10])

    prompt = (
        "You are an expert business analyst specialising in customer complaint analysis.\n\n"
        f"Keywords extracted from similar customer reviews:\n{keyword_str}\n\n"
        "Your task is to generate ONE general business complaint category.\n\n"
        "Rules:\n"
        "- Use 2 to 5 words.\n"
        "- The category should represent the underlying complaint, not the product.\n"
        "- Create a reusable business category that can classify many future customer reviews.\n"
        "- Do not mention brand names or specific products.\n"
        "- Use consistent names ending with 'Issues', 'Problems', 'Failures', or 'Delays' where appropriate.\n"
        "- Return ONLY the category name.\n\n"
        "Examples:\n"
        "battery, charging, power, drain → Battery & Charging Issues\n"
        "screen, cracked, display → Screen & Display Issues\n"
        "wifi, router, internet → Network Connectivity Issues\n"
        "late, package, shipping, courier → Delivery & Shipping Issues\n"
        "refund, exchange, money back → Refund & Return Issues\n"
        "staff, rude, support, response → Customer Service Issues\n"
        "taste, flavour, stale, smell → Product Quality Issues\n"
    )

    if settings.GROQ_API_KEY:
        try:
            client = Groq(api_key=settings.GROQ_API_KEY)
            response = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [LLM] Groq error: {e} — falling back to rule-based")

    print("  [LLM] No API key found — using rule-based label")
    return clean_topic_label(keywords)


# Stage 2 — BERTopic per platform; also returns topic centroids + example docs for the merge stage

def run_bertopic(
    texts: list[str],
    n_topics: int | str = "auto",
    min_topic_size: int = 30,
    embed_cache: str = "data/processed/embeddings_cache.npy",
    use_llm: bool = False,
):
    """
    Run BERTopic on the provided texts.

    Returns:
        categories    — list of discovered category label strings (rule-based or LLM, per-topic)
        topic_details — list of dicts with: topic_id, label, keywords, count,
                         centroid (list[float] — mean embedding of member docs),
                         example_docs (list[str] — up to 3 texts closest to centroid)
    """
    from bertopic import BERTopic
    from sentence_transformers import SentenceTransformer
    from umap import UMAP
    from hdbscan import HDBSCAN

    print(f"  Loading embedding model: {settings.EMBEDDING_MODEL}")
    embed_model = SentenceTransformer(settings.EMBEDDING_MODEL)

    embeddings = None
    if os.path.exists(embed_cache):
        cached = np.load(embed_cache)
        if cached.shape[0] == len(texts):
            print(f"  Loading cached embeddings from {embed_cache}")
            embeddings = cached
        else:
            print(f"  Cached embeddings at {embed_cache} have {cached.shape[0]:,} rows but "
                  f"{len(texts):,} texts are being encoded now — cache is stale "
                  f"(likely upstream data/filtering changed since it was built). Regenerating.")

    if embeddings is None:
        print(f"  Generating embeddings (will be cached)...")
        embeddings = embed_model.encode(texts, show_progress_bar=True)
        os.makedirs("data/processed", exist_ok=True)
        np.save(embed_cache, embeddings)
        print(f"  Embeddings saved to {embed_cache}")

    umap_model = UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=42, low_memory=True)
    hdbscan_model = HDBSCAN(min_cluster_size=min_topic_size, min_samples=10, metric="euclidean", cluster_selection_method="eom", prediction_data=True)
    # min_df/max_df count TOPICS here, not reviews (fits on per-topic aggregated
    # text). A stricter min_df can crash sklearn once nr_topics="auto" shrinks the
    # topic count, so keep it permissive.
    vectorizer_model = CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1, max_df=1.0)

    print(f"Creating BERTopic model with {n_topics} topics")
    topic_model = BERTopic(
        representation_model=representation_model,
        embedding_model=embed_model,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        vectorizer_model=vectorizer_model,
        nr_topics=n_topics,
        top_n_words=10,
        verbose=True,
        calculate_probabilities=False,
    )

    topics, _ = topic_model.fit_transform(texts, embeddings)
    topics = np.array(topics)

    topic_info_df = topic_model.get_topic_info()
    print(f"Total topics (including outlier): {len(topic_info_df)}")
    print(f"Actual topics: {len(topic_info_df[topic_info_df['Topic'] != -1])}")

    n_outliers = int((topics == -1).sum())
    print(f"Outlier (unclustered, topic=-1) documents: {n_outliers:,} / {len(texts):,} "
          f"({n_outliers / max(len(texts), 1) * 100:.1f}%)")
    if len(topic_info_df[topic_info_df['Topic'] != -1]) <= 3:
        print(f"  WARNING: only {len(topic_info_df[topic_info_df['Topic'] != -1])} topics found — "
              f"HDBSCAN likely collapsed the corpus into too few clusters at "
              f"min_topic_size={min_topic_size}. Consider a smaller --min-topic-size.")

    categories: list[str] = []
    topic_details: list[dict] = []

    for _, row in topic_info_df.iterrows():
        topic_id = int(row["Topic"])
        if topic_id == -1:
            continue

        top_words_raw = topic_model.get_topic(topic_id)
        if not top_words_raw:
            continue

        if use_llm:
            label = llm_convert_to_category(top_words_raw)
            print(f"  [LLM] {[w for w, _ in top_words_raw[:5]]} → '{label}'")
        else:
            label = clean_topic_label(top_words_raw)

        keywords = [word for word, _ in top_words_raw[:10]]
        count = int(row.get("Count", 0))

        # True mean of member doc embeddings (not a keyword-string embedding) -- this is what merge_topics clusters on
        member_idx = np.where(topics == topic_id)[0]
        if len(member_idx) > 0:
            centroid = embeddings[member_idx].mean(axis=0)
            # Representative example docs — the 3 member texts closest to the centroid
            member_vecs = embeddings[member_idx]
            sims = member_vecs @ centroid / (
                np.linalg.norm(member_vecs, axis=1) * np.linalg.norm(centroid) + 1e-9
            )
            top_local = np.argsort(-sims)[:3]
            example_docs = [texts[member_idx[i]] for i in top_local]
        else:
            centroid = np.zeros(embeddings.shape[1])
            example_docs = []

        categories.append(label)
        topic_details.append({
            "topic_id": topic_id,
            "label": label,
            "keywords": keywords,
            "count": count,
            "centroid": centroid.tolist(),
            "example_docs": example_docs,
        })

    return categories, topic_details


# Stage 3 — cross-platform merge: cluster topic centroids into a target number of categories

def merge_topics(all_topics: list[dict], target_categories: int) -> list[dict]:
    """
    Merge near-duplicate topics (possibly from different platforms) into a
    target number of business-category clusters, using agglomerative
    clustering on topic-centroid cosine distance.

    Each raw topic in `all_topics` must have: keywords, count, centroid,
    example_docs, label (original per-topic label, kept for audit), platform.

    Returns a list of merged-cluster dicts:
        {
          "keywords":        top keywords across all member topics, ranked by
                              cross-topic frequency then original rank,
          "example_docs":     up to 6 representative example reviews,
          "count":            summed review count across member topics,
          "platforms":        sorted list of platforms this cluster draws from,
          "source_labels":    original per-topic labels (audit trail),
          "source_topic_ids": list of (platform, topic_id) pairs,
          "centroid":         mean of member centroids,
        }
    """
    from sklearn.cluster import AgglomerativeClustering

    n = len(all_topics)
    if n == 0:
        return []
    if n <= target_categories:
        print(f"  {n} raw topics <= target {target_categories} — no merging needed, treating each as its own category")
        cluster_labels = list(range(n))
    else:
        X = np.stack([np.array(t["centroid"]) for t in all_topics])
        clustering = AgglomerativeClustering(
            n_clusters=target_categories, metric="cosine", linkage="average"
        )
        cluster_labels = clustering.fit_predict(X)
        print(f"  Merged {n} raw topics → {target_categories} clusters (agglomerative, cosine, average linkage)")

    clusters: dict[int, list[dict]] = {}
    for topic, cl in zip(all_topics, cluster_labels):
        clusters.setdefault(int(cl), []).append(topic)

    merged: list[dict] = []
    for cl_id, members in clusters.items():
        kw_counter: Counter = Counter()
        for m in members:
            for rank, kw in enumerate(m["keywords"]):
                kw_counter[kw.lower()] += (len(m["keywords"]) - rank) * (m["count"] or 1)
        top_keywords = [kw for kw, _ in kw_counter.most_common(12)]

        example_docs: list[str] = []
        for m in sorted(members, key=lambda x: -x["count"]):
            for doc in m["example_docs"]:
                if doc not in example_docs:
                    example_docs.append(doc)
            if len(example_docs) >= 6:
                break
        example_docs = example_docs[:6]

        centroids = np.stack([np.array(m["centroid"]) for m in members])
        merged_centroid = centroids.mean(axis=0)

        merged.append({
            "keywords": top_keywords,
            "example_docs": example_docs,
            "count": sum(m["count"] for m in members),
            "platforms": sorted({m["platform"] for m in members}),
            "source_labels": [m["label"] for m in members],
            "source_topic_ids": [{"platform": m["platform"], "topic_id": m["topic_id"]} for m in members],
            "centroid": merged_centroid.tolist(),
        })

    # Largest clusters first, so the LLM's "already used" list is seeded by the most important categories
    merged.sort(key=lambda c: -c["count"])
    return merged


def taxonomy_coherence(merged: list[dict]) -> float:
    """
    Mean pairwise cosine similarity between merged-category centroids.
    Lower = better separated categories (report this to justify
    --target-categories in the dissertation methodology chapter).
    """
    if len(merged) < 2:
        return 0.0
    X = np.stack([np.array(c["centroid"]) for c in merged])
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    sims = X @ X.T
    n = len(merged)
    upper = sims[np.triu_indices(n, k=1)]
    return float(upper.mean())


# Stage 4 — name each merged cluster with a context-aware LLM call

def llm_name_merged_cluster(
    keywords: list[str],
    example_docs: list[str],
    existing_names: list[str],
) -> dict:
    """Names ONE merged cluster, given already-used names so it avoids near-synonyms
    (e.g. "Travel Experience Failures" vs "Travel Experience Issues").

    Returns {"name": str, "description": str, "exemplars": [str, ...]}
    """
    keyword_str = ", ".join(keywords[:12])
    examples_str = "\n".join(f"  - \"{d[:160]}\"" for d in example_docs[:4])
    existing_str = "\n".join(f"  - {n}" for n in existing_names) or "  (none yet)"

    prompt = (
        "You are an expert business analyst building a customer-complaint taxonomy.\n\n"
        f"Keywords from a cluster of similar customer complaints:\n{keyword_str}\n\n"
        f"Example reviews from this cluster:\n{examples_str}\n\n"
        f"Category names ALREADY used for other clusters (do NOT duplicate or\n"
        f"produce a close synonym of any of these — if this cluster is the same\n"
        f"underlying complaint as one of these, reuse that exact name instead):\n{existing_str}\n\n"
        "Respond with ONLY a JSON object, no other text, in this exact shape:\n"
        '{"name": "2-5 word category name ending in Issues/Problems/Failures/Delays where natural", '
        '"description": "one sentence describing the underlying complaint, written as a general '
        'statement usable to classify future unseen reviews, no brand or product names", '
        '"exemplars": ["short phrase 1", "short phrase 2", "short phrase 3"]}\n\n'
        "Example:\n"
        '{"name": "Battery & Charging Issues", "description": "The product fails to charge, '
        'does not hold a charge, or the battery degrades quickly after purchase.", '
        '"exemplars": ["battery drains fast", "won\'t charge", "stopped holding a charge"]}'
    )

    if settings.GROQ_API_KEY:
        try:
            client = Groq(api_key=settings.GROQ_API_KEY)
            response = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=settings.LLM_TEMPERATURE,
            )
            raw = response.choices[0].message.content.strip()
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            start, end = raw.find("{"), raw.rfind("}")
            parsed = json.loads(raw[start:end + 1])
            name = str(parsed["name"]).strip()
            description = str(parsed.get("description", name)).strip()
            exemplars = [str(e).strip() for e in parsed.get("exemplars", [])][:5]
            if name and not any(name.lower() == e.lower() for e in existing_names):
                return {"name": name, "description": description, "exemplars": exemplars}
            print(f"  [LLM] Duplicate or empty name '{name}' — falling back to rule-based")
        except Exception as e:
            print(f"  [LLM] Groq error naming merged cluster: {e} — falling back to rule-based")

    # Fallback: rule-based label + auto description from keywords
    label = clean_topic_label([(kw, 1.0) for kw in keywords])
    if not label or any(label.lower() == e.lower() for e in existing_names):
        label = f"{label} (Cluster {len(existing_names) + 1})" if label else f"Unnamed Cluster {len(existing_names) + 1}"
    description = f"Complaints related to: {', '.join(keywords[:6])}."
    return {"name": label, "description": description, "exemplars": keywords[:3]}


def name_all_clusters(merged: list[dict]) -> list[dict]:
    """Names every merged cluster sequentially, feeding forward already-used names."""
    named: list[dict] = []
    existing_names: list[str] = []

    for i, cluster in enumerate(merged, start=1):
        print(f"  Naming cluster {i}/{len(merged)}  ({cluster['count']:,} reviews, "
              f"platforms: {', '.join(cluster['platforms'])})")
        result = llm_name_merged_cluster(cluster["keywords"], cluster["example_docs"], existing_names)
        existing_names.append(result["name"])
        print(f"    → '{result['name']}'")

        named.append({
            "category": result["name"],
            "description": result["description"],
            "exemplars": result["exemplars"],
            "keywords": cluster["keywords"][:10],
            "platforms": cluster["platforms"],
            "count": cluster["count"],
            "source_labels": cluster["source_labels"],
            "source_topic_ids": cluster["source_topic_ids"],
        })

    # Safety net: merge exact-name collisions in case the LLM ignores "existing names"
    seen: dict[str, dict] = {}
    for cat in named:
        key = cat["category"].strip().lower()
        if key in seen:
            existing = seen[key]
            existing["count"] += cat["count"]
            existing["keywords"] = list(dict.fromkeys(existing["keywords"] + cat["keywords"]))[:10]
            existing["platforms"] = sorted(set(existing["platforms"]) | set(cat["platforms"]))
            existing["exemplars"] = list(dict.fromkeys(existing["exemplars"] + cat["exemplars"]))[:5]
        else:
            seen[key] = cat
    return list(seen.values())


# Orchestration

def discover_platform_topics(df: pd.DataFrame, platform: str, sentiment_tag: str, args) -> list[dict]:
    """Runs the single-platform BERTopic discovery pipeline and returns raw topic dicts
    tagged with platform (each dict includes 'centroid' and 'example_docs')."""
    pdf = df[df["platform"] == platform]
    if pdf.empty:
        print(f"  No reviews for platform '{platform}' — skipping")
        return []

    print(f"\n{'=' * 60}\n  Platform: {platform}  ({len(pdf):,} reviews)\n{'=' * 60}")
    texts = pdf["cleaned_text"].fillna("").tolist()
    texts = filter_english(texts)
    if not texts:
        print(f"  No reviews remain after language filter for '{platform}'.")
        return []

    if args.min_topic_size:
        min_size = args.min_topic_size
    else:
        min_size = dynamic_min_topic_size(len(texts))

    n_topics = args.topics if args.topics else "auto"
    cache_path = f"data/processed/embeddings_cache_{platform}_{sentiment_tag}.npy"

    # Auto-retry with a shrinking min_topic_size if HDBSCAN collapses into too few
    # topics (min_size=289 once collapsed 58k amazon reviews into 2). Cheap retry --
    # embeddings are cached, only UMAP+HDBSCAN rerun.
    MIN_TOPIC_FLOOR = 15
    MIN_RAW_TOPICS_ACCEPTABLE = 5
    attempt = 0
    topic_details = []
    while True:
        attempt += 1
        print(f"  min_topic_size: {min_size}  (attempt {attempt})")
        try:
            _, topic_details = run_bertopic(
                texts, n_topics=n_topics, min_topic_size=min_size,
                embed_cache=cache_path, use_llm=False,  # rule-based per-topic label; LLM only used at merge-naming stage
            )
        except Exception as e:
            print(f"  BERTopic failed for '{platform}': {e}")
            log.error("BERTopic failed for %s: %s", platform, e, exc_info=True)
            return []

        if len(topic_details) >= MIN_RAW_TOPICS_ACCEPTABLE or min_size <= MIN_TOPIC_FLOOR:
            if len(topic_details) < MIN_RAW_TOPICS_ACCEPTABLE:
                print(f"  WARNING: only {len(topic_details)} topics even at the min_topic_size floor "
                      f"({MIN_TOPIC_FLOOR}) — accepting as-is. Consider --sample or manual review.")
            break

        new_min_size = max(MIN_TOPIC_FLOOR, min_size // 2)
        print(f"  Only {len(topic_details)} topics found (< {MIN_RAW_TOPICS_ACCEPTABLE}) — "
              f"retrying with min_topic_size={new_min_size} instead of {min_size}")
        min_size = new_min_size

    topic_details = filter_noise_topics(topic_details)
    for d in topic_details:
        d["platform"] = platform

    # Save raw per-platform dump for dissertation audit trail
    info_path = f"data/processed/bertopic_info_{platform}_{sentiment_tag}.json"
    os.makedirs("data/processed", exist_ok=True)
    with open(info_path, "w") as f:
        json.dump({
            "platform": platform, "sentiment": sentiment_tag,
            "sample_size": len(texts), "min_topic_size": min_size,
            "n_topics": len(topic_details),
            "topics": [{k: v for k, v in d.items() if k != "centroid"} for d in topic_details],  # omit raw vectors from the human-readable audit file
        }, f, indent=2)
    print(f"  Raw topics saved → {info_path}")

    return topic_details


def run_multi_platform_pipeline(df: pd.DataFrame, sentiment_tag: str, args) -> None:
    """Full pipeline: discover per platform -> merge across platforms -> name merged clusters -> save consolidated taxonomy."""
    all_topics: list[dict] = []
    for platform in PLATFORMS:
        all_topics.extend(discover_platform_topics(df, platform, sentiment_tag, args))

    if not all_topics:
        print("\n  No topics discovered on any platform — saving static fallback.")
        categories = [{"category": c.title(), "description": c, "exemplars": [], "keywords": c.split(),
                        "platforms": PLATFORMS, "count": 0, "source_labels": [c], "source_topic_ids": []}
                       for c in STATIC_FALLBACK]
        coherence = 0.0
    else:
        print(f"\n{'=' * 60}\n  Merging {len(all_topics)} raw topics across {len(PLATFORMS)} platforms\n{'=' * 60}")
        target = args.target_categories or DEFAULT_TARGET_CATEGORIES
        merged = merge_topics(all_topics, target_categories=target)
        coherence = taxonomy_coherence(merged)
        print(f"  Taxonomy coherence (mean pairwise centroid cosine similarity): {coherence:.4f}  (lower = better separated)")

        print(f"\n{'=' * 60}\n  Naming {len(merged)} merged clusters (context-aware, sequential)\n{'=' * 60}")
        categories = name_all_clusters(merged)

    print(f"\n  Final consolidated taxonomy: {len(categories)} categories\n")
    for i, cat in enumerate(categories, start=1):
        print(f"{i:2d}. {cat['category']}  ({cat['count']:,} reviews, platforms: {', '.join(cat['platforms'])})")
        print(f"     {cat['description']}")

    output_cats = f"data/processed/complaint_categories_all_{sentiment_tag}.json"
    output_info = f"data/processed/taxonomy_merge_info_{sentiment_tag}.json"
    os.makedirs("data/processed", exist_ok=True)

    with open(output_cats, "w") as f:
        json.dump(categories, f, indent=2)
    print(f"\n  Consolidated taxonomy saved → {output_cats}")

    with open(output_info, "w") as f:
        json.dump({
            "sentiment": sentiment_tag,
            "n_raw_topics": len(all_topics),
            "n_categories": len(categories),
            "target_categories": args.target_categories or DEFAULT_TARGET_CATEGORIES,
            "coherence_mean_pairwise_cosine_sim": coherence,
            "categories": categories,
        }, f, indent=2)
    print(f"  Merge diagnostics saved → {output_info}")

    _run_classification_smoke_test(sentiment_tag)


TEST_REVIEWS = [
    "The battery stopped charging after one week.",
    "My package arrived damaged and three weeks late.",
    "The staff were rude and refused to help me.",
    "Food was cold and tasteless, never coming back.",
    "Flight was cancelled with no explanation given.",
]


def _run_classification_smoke_test(sentiment_tag: str) -> None:
    """Loads the taxonomy just written and classifies TEST_REVIEWS in the same run,
    so one command shows both the taxonomy and whether classification looks right."""
    print(f"\n{'=' * 60}")
    print(f"  Smoke-testing the new taxonomy against fixed test reviews")
    print(f"{'=' * 60}")
    try:
        import nlp.categoriser as categoriser
        categoriser.reload_categories(sentiment_tag)
        for review in TEST_REVIEWS:
            result = categoriser.categorise(review)
            top = result[0] if result else None
            print(f"\n  Review: {review}")
            if top:
                print(f"  -> {top['category']}  (score={top['score']})")
                if len(result) > 1:
                    print(f"     runner-up: {result[1]['category']} (score={result[1]['score']})")
            else:
                print("  -> no prediction returned")
    except Exception as e:
        print(f"  Smoke test could not run: {e}")
        print(f"  (Run `python nlp/categoriser.py` manually to check classification.)")
        log.error("Classification smoke test failed: %s", e, exc_info=True)


def run_single_platform_audit(df: pd.DataFrame, platform: str, sentiment_tag: str, args) -> None:
    """Legacy single-platform mode: raw discovery only, no merge. Useful for
    exploring one platform's topics in isolation (dissertation appendix /
    debugging), NOT used to produce the taxonomy categoriser.py loads."""
    pdf = df[df["platform"] == platform]
    print(f"  Filtered to platform '{platform}': {len(pdf):,} reviews")
    if pdf.empty:
        print(" No reviews after filtering.")
        sys.exit(1)

    if args.sample and len(pdf) > args.sample:
        pdf = pdf.sample(n=args.sample, random_state=42)
        print(f"  Sampled to {len(pdf):,} reviews (--sample {args.sample})")

    texts = pdf["cleaned_text"].fillna("").tolist()
    texts = filter_english(texts)
    if not texts:
        print("  No reviews remain after language filter.")
        sys.exit(1)

    min_size = args.min_topic_size or dynamic_min_topic_size(len(texts))
    n_topics = args.topics if args.topics else "auto"
    cache_path = f"data/processed/embeddings_cache_{platform}_{sentiment_tag}.npy"

    categories, topic_details = run_bertopic(
        texts, n_topics=n_topics, min_topic_size=min_size,
        embed_cache=cache_path, use_llm=args.use_llm,
    )
    topic_details = filter_noise_topics(topic_details)

    seen, unique = set(), []
    for d in topic_details:
        if d["label"] not in seen:
            seen.add(d["label"])
            unique.append(d["label"])
    categories = unique

    print(f"\n  [AUDIT MODE — single platform, NOT merged, NOT used by categoriser.py]")
    print(f"  Found {len(categories)} raw topics for '{platform}':\n")
    for i, cat in enumerate(categories, start=1):
        print(f"  {i}. {cat}")

    output_cats = f"data/processed/complaint_categories_{platform}_{sentiment_tag}.json"
    output_info = f"data/processed/bertopic_info_{platform}_{sentiment_tag}.json"
    os.makedirs("data/processed", exist_ok=True)
    with open(output_cats, "w") as f:
        json.dump(categories, f, indent=2)
    with open(output_info, "w") as f:
        json.dump({
            "n_categories": len(categories), "sample_size": len(texts), "platform": platform,
            "sentiment": sentiment_tag, "min_topic_size": min_size, "categories": categories,
            "topic_details": [{k: v for k, v in d.items() if k != "centroid"} for d in topic_details],
        }, f, indent=2)
    print(f"\n  Raw audit files saved → {output_cats}, {output_info}")
    print(f"  NOTE: run WITHOUT --platform to build the merged taxonomy categoriser.py uses.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover complaint categories using BERTopic + cross-platform taxonomy merge.")
    parser.add_argument("--sample", type=int, default=None, help="Limit reviews per platform (single-platform audit mode only)")
    parser.add_argument("--topics", type=int, default=None, help="Number of raw BERTopic topics per platform (default: auto)")
    parser.add_argument("--min-topic-size", type=int, default=None, help="Minimum reviews per raw topic (default: auto — 0.5%% of corpus)")
    parser.add_argument("--target-categories", type=int, default=None, help=f"Final number of merged business categories (default: {DEFAULT_TARGET_CATEGORIES})")
    parser.add_argument("--platform", type=str, default=None, choices=PLATFORMS, help="AUDIT MODE: discover raw topics for one platform only, no merge (not used by categoriser.py)")
    parser.add_argument("--sentiment", type=str, default="negative", choices=["negative", "positive", "neutral"], help="Sentiment class to build a taxonomy for (default: negative)")
    parser.add_argument("--use-llm", action="store_true", help="AUDIT MODE only: use LLM for raw per-topic labels instead of rule-based")
    args = parser.parse_args()

    sentiment_tag = args.sentiment
    print("  FeedbackIQ — Category Discovery")
    print(f"  Sentiment : {sentiment_tag}")
    print(f"  Mode      : {'single-platform audit (' + args.platform + ')' if args.platform else 'multi-platform merge (recommended)'}")

    if not os.path.exists(settings.DATA_PATH):
        print(f"\n Data not found at {settings.DATA_PATH}")
        print(f"Run first: python scripts/preprocess.py")
        sys.exit(1)

    print(f"Loading data from {settings.DATA_PATH}")
    df = pd.read_parquet(settings.DATA_PATH)
    print(f"  Loaded {len(df):,} reviews from {settings.DATA_PATH}")
    print(f"  Platforms : {df['platform'].value_counts().to_dict()}")
    print(f"  Sentiments: {df['sentiment_label'].value_counts().to_dict()}")

    df = df[df["sentiment_label"] == sentiment_tag]
    print(f"  Filtered to '{sentiment_tag}' sentiment: {len(df):,} reviews")
    if df.empty:
        print(" No reviews after filtering.")
        sys.exit(1)

    try:
        if args.platform:
            run_single_platform_audit(df, args.platform, sentiment_tag, args)
        else:
            run_multi_platform_pipeline(df, sentiment_tag, args)
    except ImportError as e:
        print(f"\n Missing package: {e}")
        print(f"   Run: pip install bertopic")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f" Category discovery complete!")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
