"""
Zero-shot complaint categorisation: embedding retrieval narrows to a
shortlist, then NLI reranks it (see categorise()).

The default categories come from the canonical packaged taxonomy
(`feedbackiq.core.taxonomy`), which is the same source the analytics engine and the
database seed use. They are NOT read from data/processed/: that is gitignored research
output, absent in a fresh clone, in CI and in the container image, and the loader used to
substitute 7 static categories when it was missing - silently categorising against the
wrong taxonomy (see docs/production/milestone-05a.md).

A taxonomy discovered by `scripts/discover_categories.py` is still loadable explicitly,
for research, through `load_research_taxonomy()` / `reload_categories()`. Those raise when
the file is absent rather than falling back. Concatenating per-platform files caused
cross-platform leakage (e.g. an airline category showing up for an Amazon review) - see
the dissertation's methodology chapter.
"""

from __future__ import annotations
import os
import json
import threading
import numpy as np
from functools import lru_cache

from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import TaxonomyError
from feedbackiq.core.logging import get_logger
from feedbackiq.core.taxonomy import load_default_taxonomy

log = get_logger("nlp.categoriser")

_embedder_lock = threading.Lock()
_classifier_lock = threading.Lock()

UNCLASSIFIED = "Unclassified / Emerging Complaint"

_PROCESSED_DIR = str(settings.taxonomy_dir)


def _research_taxonomy_path(sentiment: str = "negative") -> str:
    """Where scripts/discover_categories.py writes a taxonomy it has just discovered."""
    return os.path.join(_PROCESSED_DIR, f"complaint_categories_all_{sentiment}.json")


def _normalise(raw: list) -> list[dict]:
    """Accept the current shape and the legacy flat list of strings."""
    cats: list[dict] = []

    for c in raw:
        if isinstance(c, str):
            cats.append({"category": c, "description": c, "exemplars": []})
        else:
            cats.append({
                "category": str(c["category"]),
                "description": str(c.get("description") or c["category"]),
                "exemplars": [str(e) for e in c.get("exemplars", [])],
            })

    return cats


def load_research_taxonomy(sentiment: str = "negative") -> list[dict]:
    """
    A taxonomy produced by the discovery script, for RESEARCH use.

    Raises rather than falling back. A missing file used to mean "quietly categorise
    against 7 static categories", which produced wrong results that looked fine - the
    defect CI caught in Milestone 4 and this milestone removed.
    """
    path = _research_taxonomy_path(sentiment)

    if not os.path.exists(path):
        raise TaxonomyError(
            f"No discovered taxonomy at '{path}'. This is research output, not part of "
            f"the product. Run: python scripts/discover_categories.py --sentiment {sentiment}"
        )

    with open(path) as f:
        cats = _normalise(json.load(f))

    log.info("Loaded %d %s categories from %s", len(cats), sentiment, os.path.basename(path))

    return cats


# The canonical production taxonomy, packaged with the code. One source, shared with the
# engine and the database seed, so no environment can categorise against a different set.
COMPLAINT_CATEGORIES: list[dict] = load_default_taxonomy()


def _hypothesis_text(cat: dict) -> str:
    """Embedding target for shortlist retrieval — desc+exemplars beats the bare label."""
    text = cat["description"]
    if cat["exemplars"]:
        text = f"{text} For example: {'; '.join(cat['exemplars'][:3])}."
    return text


def _nli_hypothesis(cat: dict) -> str:
    """NLI hypothesis sentence — the description as-is, no template wrapper
    (NLI needs a clean single sentence, not a malformed compound)."""
    return cat["description"]


@lru_cache(maxsize=1)
def _get_embedder_uncached():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.EMBEDDING_MODEL)


def _get_embedder():
    # Closes the race where two threads both miss lru_cache and load twice
    with _embedder_lock:
        return _get_embedder_uncached()


def _category_embeddings(categories: tuple) -> np.ndarray:
    """Embeds each category's hypothesis text; needs hashable input to memoise."""
    embedder = _get_embedder()
    texts = [c[1] for c in categories]  # (name, hypothesis_text) pairs
    return embedder.encode(texts, normalize_embeddings=True)


def _categories_key(categories: list[dict]) -> tuple:
    return tuple((c["category"], _hypothesis_text(c)) for c in categories)


@lru_cache(maxsize=4)
def _get_classifier_uncached(model_name: str):
    from transformers import pipeline
    return pipeline("zero-shot-classification", model=model_name, device=-1)


def _get_classifier(model_name: str):
    # One lock, not per-model — only ever 1-2 distinct models in practice
    with _classifier_lock:
        return _get_classifier_uncached(model_name)


def reload_categories(sentiment: str = "negative") -> list[dict]:
    """
    Replace this process's taxonomy with a DISCOVERED one, for research.

    Called by scripts/discover_categories.py after it writes a taxonomy, so one command
    shows both the taxonomy and how it classifies. Raises `TaxonomyError` if that research
    file does not exist - the product default is `load_default_taxonomy()` and is never
    reached by accident from here.
    """
    global COMPLAINT_CATEGORIES
    COMPLAINT_CATEGORIES = load_research_taxonomy(sentiment)

    return COMPLAINT_CATEGORIES


def categorise(
    text: str,
    top_k: int = 3,
    shortlist_k: int | None = None,
    model_name: str | None = None,
) -> list[dict[str, object]]:
    """
    Embedding retrieval narrows to a shortlist (bounds what NLI sees
    regardless of taxonomy size), then NLI reranks it. Below the confidence
    threshold, top result becomes "Unclassified / Emerging Complaint".

    Returns up to `top_k` {"category", "score", "description"} dicts.
    """
    categories = COMPLAINT_CATEGORIES
    if not categories:
        return []

    shortlist_k = shortlist_k or settings.CATEGORY_SHORTLIST_K
    model_name = model_name or settings.ZEROSHOT_MODEL
    text = str(text)[: settings.CATEGORY_MAX_CHARS]

    try:
        cat_key = _categories_key(categories)
        cat_embeddings = _category_embeddings(cat_key)

        embedder = _get_embedder()
        text_embedding = embedder.encode([text], normalize_embeddings=True)[0]

        sims = cat_embeddings @ text_embedding
        shortlist_idx = np.argsort(-sims)[: min(shortlist_k, len(categories))]
        shortlist = [categories[i] for i in shortlist_idx]

        candidate_labels = [_nli_hypothesis(c) for c in shortlist]
        label_to_category = {_nli_hypothesis(c): c for c in shortlist}

        classifier = _get_classifier(model_name)
        result = classifier(
            text,
            candidate_labels=candidate_labels,
            hypothesis_template="{}",  # description is already a complete sentence — no wrapper
        )

        ranked: list[dict[str, object]] = []
        for lbl, sc in zip(result["labels"], result["scores"]):
            cat = label_to_category[lbl]
            ranked.append({
                "category": cat["category"],
                "score": round(float(sc), 4),
                "description": cat["description"],
            })

        if ranked and ranked[0]["score"] < settings.CATEGORY_CONFIDENCE_THRESHOLD:
            ranked[0] = {
                "category": UNCLASSIFIED,
                "score": ranked[0]["score"],
                "description": "No candidate category exceeded the confidence threshold.",
            }

        return ranked[:top_k]

    except Exception as e:
        raise RuntimeError(f"Zero-shot categorisation failed: {e}")


if __name__ == "__main__":

    print("Loaded Categories:")
    for i, cat in enumerate(COMPLAINT_CATEGORIES, 1):
        print(f"  {i}. {cat['category']} — {cat['description']}")

    print()
    tests = [
    "The battery stopped charging after only one week. I tried different chargers but the device still won't charge.",
    "My package arrived three weeks late and the box was badly damaged. The product inside was also broken.",
    "The staff were extremely rude and refused to help me when I explained the problem. Nobody followed up with my complaint.",
    "The food arrived completely cold and tasted terrible. The meal was not fresh and I would not order from this restaurant again.",
    "My flight was cancelled two hours before departure and the airline provided no explanation or alternative flight.",
    "The router keeps disconnecting from the internet several times a day. I have restarted it multiple times but the connection remains unstable.",
    "I returned the product two weeks ago but I still haven't received my refund. Customer service has not responded to my emails.",
    "The product looked good when it arrived, but after just a few days the material started breaking and the item became unusable.",
    "The headphones sound distorted and the audio quality is very poor, especially when listening at higher volume.",
    "The screen developed lines after a few days and the display now flickers whenever I turn the device on.",
    "The product is far too expensive for the quality provided and I don't think it is worth the money.",
    "I tried to book my flight several times but the website kept rejecting the payment and would not complete the reservation.",
    "I contacted technical support several times but they could not resolve the software problem.",
    "The package arrived with damaged packaging and several items were missing from the box.",
    "I tried to make an appointment but the clinic kept cancelling and could not provide me with another available date.",
    "The car started making a strange noise after a few days and the engine performance has become noticeably worse.",
    "The store closed earlier than the advertised opening hours and I was unable to make my purchase.",
    "I was charged twice for the same order and customer support has not corrected the duplicate payment.",
    "The headphones are too tight and uncomfortable to wear, even after adjusting the headband.",
    "The journey was extremely uncomfortable and poorly organised, and the staff provided very little information throughout the trip."
]

    for review in tests:
        print(f"Review: {review}")
        print(f"Predicted: {categorise(review)}")
        print()
