"""
Which issue is this feedback about?

Same two-stage design the dissertation defends, with one change that matters for a
product: **the categories are passed in**. Nothing is loaded from a file at import
time, so a customer's own taxonomy is a function argument, not a deployment.

    embedding similarity  ->  shortlist of candidates  ->  zero-shot NLI rerank
                                                            |
                            below CATEGORY_CONFIDENCE_THRESHOLD (0.35)
                                                            |
                                            "Unclassified / Emerging Complaint"

Keeping the "unclassified" outcome is deliberate: forcing every item into the nearest
category would produce 100% coverage and a taxonomy that lies, and the unclassified
share is a useful signal in its own right (emerging issues).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import numpy as np

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.engine.types import Category, CategoryMatch

log = get_logger("engine.categorisation")

# The label used when nothing clears the confidence threshold. Same string the
# dissertation used, so stored results and the dashboard stay comparable.
UNCLASSIFIED_NAME = "Unclassified / Emerging Complaint"
UNCLASSIFIED_DESCRIPTION = "No candidate category exceeded the confidence threshold."


@dataclass(frozen=True)
class CategorisationOutcome:
    """What categorisation concluded for one item."""

    top: CategoryMatch | None
    candidates: tuple[CategoryMatch, ...] = ()
    is_unclassified: bool = False


class Categoriser(Protocol):
    def categorise_batch(
        self, texts: Sequence[str], categories: Sequence[Category]
    ) -> list[CategorisationOutcome]:
        ...


def shortlist_text(category: Category) -> str:
    """
    What the embedding stage compares against: the description, plus up to three
    exemplars. The description alone is a weaker target than description+examples.
    """
    text = category.description
    if category.exemplars:
        text = f"{text} For example: {'; '.join(category.exemplars[:3])}."

    return text


def nli_hypothesis(category: Category) -> str:
    """
    What the NLI stage tests: the description as-is.

    No template wrapper - the description is already a complete sentence, and wrapping
    it produced malformed compounds.
    """
    return category.description


class ZeroShotCategoriser:
    """
    Embedding shortlist followed by a zero-shot NLI rerank.

    `embedder` and `classifier` are injectable: tests pass tiny fakes, so no model is
    ever downloaded. By default they come from `feedbackiq.nlp.categoriser`, which
    already caches both models per process behind a lock, so the engine shares those
    instances rather than loading second copies.
    """

    def __init__(
        self,
        *,
        embedder: Any | None = None,
        classifier: Any | None = None,
        shortlist_k: int | None = None,
        threshold: float | None = None,
        max_chars: int | None = None,
        top_k: int = 3,
    ) -> None:
        self._embedder = embedder
        self._classifier = classifier
        self.shortlist_k = shortlist_k or settings.CATEGORY_SHORTLIST_K
        self.threshold = threshold if threshold is not None else settings.CATEGORY_CONFIDENCE_THRESHOLD
        self.max_chars = max_chars or settings.CATEGORY_MAX_CHARS
        self.top_k = top_k

    # ---------------------------------------------------------------- models

    def _get_embedder(self) -> Any:
        if self._embedder is None:
            from feedbackiq.nlp.categoriser import _get_embedder

            self._embedder = _get_embedder()
        return self._embedder

    def _get_classifier(self) -> Any:
        if self._classifier is None:
            from feedbackiq.nlp.categoriser import _get_classifier

            self._classifier = _get_classifier(settings.ZEROSHOT_MODEL)
        return self._classifier

    # ---------------------------------------------------------------- work

    def categorise_batch(
        self, texts: Sequence[str], categories: Sequence[Category]
    ) -> list[CategorisationOutcome]:
        if not texts:
            return []

        if not categories:
            # No taxonomy means no opinion - not a forced guess.
            return [CategorisationOutcome(top=None) for _ in texts]

        embedder = self._get_embedder()
        classifier = self._get_classifier()

        # One encode call for the taxonomy, reused for every item in the batch.
        category_vectors = np.asarray(
            embedder.encode([shortlist_text(c) for c in categories], normalize_embeddings=True)
        )
        clipped = [str(text)[: self.max_chars] for text in texts]
        text_vectors = np.asarray(embedder.encode(clipped, normalize_embeddings=True))

        hypothesis_to_category = {nli_hypothesis(c): c for c in categories}
        outcomes: list[CategorisationOutcome] = []

        for text, text_vector in zip(clipped, text_vectors):
            similarities = category_vectors @ text_vector
            take = min(self.shortlist_k, len(categories))
            shortlist = [categories[i] for i in np.argsort(-similarities)[:take]]

            result = classifier(
                text,
                candidate_labels=[nli_hypothesis(c) for c in shortlist],
                hypothesis_template="{}",  # the description is already a sentence
            )

            ranked: list[CategoryMatch] = []
            for label, score in zip(result["labels"], result["scores"]):
                category = hypothesis_to_category[label]
                ranked.append(
                    CategoryMatch(
                        category_id=category.id,
                        name=category.name,
                        score=round(float(score), 4),
                        description=category.description,
                    )
                )

            outcomes.append(self._apply_threshold(ranked))

        return outcomes

    def _apply_threshold(self, ranked: list[CategoryMatch]) -> CategorisationOutcome:
        if not ranked:
            return CategorisationOutcome(top=None)

        best = ranked[0]

        if best.score < self.threshold:
            unclassified = CategoryMatch(
                category_id=None,
                name=UNCLASSIFIED_NAME,
                score=best.score,
                description=UNCLASSIFIED_DESCRIPTION,
            )
            # The real candidates are kept, so a human can see what it nearly matched.
            return CategorisationOutcome(
                top=unclassified,
                candidates=tuple(ranked[: self.top_k]),
                is_unclassified=True,
            )

        return CategorisationOutcome(
            top=best,
            candidates=tuple(ranked[: self.top_k]),
            is_unclassified=False,
        )


def categories_from_dicts(rows: Sequence[dict], *, id_prefix: str = "") -> tuple[Category, ...]:
    """
    Build `Category` objects from the taxonomy's JSON shape
    (`{"key": ..., "category": ..., "description": ..., "exemplars": [...]}`).

    **`key` is the identity when present** (taxonomy 1.1.0 onward), falling back to the
    name when it is absent. That separation is the point: `key` is stable and
    machine-readable, `name` is a customer-facing label that may be reworded without
    changing which category a stored result refers to. Rows written before keys existed,
    and a caller passing a hand-built taxonomy, still work off the name.
    """
    categories: list[Category] = []

    for row in rows:
        name = str(row.get("category") or row.get("name") or "").strip()
        if not name:
            continue

        identity = str(row.get("key") or "").strip() or name

        categories.append(
            Category(
                id=f"{id_prefix}{identity}",
                name=name,
                description=str(row.get("description") or name),
                exemplars=tuple(str(e) for e in (row.get("exemplars") or [])),
            )
        )

    return tuple(categories)
