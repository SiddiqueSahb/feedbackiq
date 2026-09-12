"""
Complaint categorisation - feedbackiq.nlp.categoriser

Protects the two-stage design the dissertation defends:
  1. embedding similarity picks a shortlist of candidate categories
  2. zero-shot NLI ranks only that shortlist
  3. if the best score is below CATEGORY_CONFIDENCE_THRESHOLD (0.35), the review is
     reported as "Unclassified / Emerging Complaint" instead of being forced into a category

Ported from tests/test_categoriser_shortlist.py (a manual check script, still runnable).
The embedding model and the NLI classifier are replaced with small fakes: no downloads.
"""

import numpy as np
import pytest

import feedbackiq.nlp.categoriser as categoriser
from feedbackiq.core.config import settings

# A deliberately mixed taxonomy (product, delivery, airline, fees, store), as in the original check.
TAXONOMY = [
    {"category": "Battery & Charging Issues",
     "description": "The product fails to charge or hold a charge.",
     "exemplars": ["battery drains fast", "won't charge"]},
    {"category": "Delivery & Shipping Issues",
     "description": "The order arrived late or damaged in transit.",
     "exemplars": ["package late", "arrived damaged"]},
    {"category": "Flight Delay & Rescheduling Issues",
     "description": "Flights were delayed, cancelled, or rescheduled without notice.",
     "exemplars": ["flight delayed", "flight cancelled"]},
    {"category": "Fees & Charge Problems",
     "description": "Unexpected or undisclosed fees were charged to the customer.",
     "exemplars": ["hidden fee", "charged extra"]},
    {"category": "Store Operational Hours Failures",
     "description": "The store was closed during its posted business hours.",
     "exemplars": ["closed early", "hours wrong"]},
]

BATTERY_REVIEW = "The battery stopped charging after one week."

# Keyword -> direction in a tiny 5-dimensional "embedding space".
# "charg" points partly at batteries and partly at fees, on purpose.
KEYWORD_DIRECTIONS = {
    "battery": [1.0, 0.0, 0.0, 0.0, 0.0],
    "charg": [0.6, 0.0, 0.0, 0.4, 0.0],
    "hold": [0.5, 0.0, 0.0, 0.0, 0.0],
    "package": [0.0, 1.0, 0.0, 0.0, 0.0],
    "damaged": [0.0, 1.0, 0.0, 0.0, 0.0],
    "late": [0.0, 1.0, 0.0, 0.0, 0.0],
    "flight": [0.0, 0.0, 1.0, 0.0, 0.0],
    "delay": [0.0, 0.0, 1.0, 0.0, 0.0],
    "fee": [0.0, 0.0, 0.0, 1.0, 0.0],
    "store": [0.0, 0.0, 0.0, 0.0, 1.0],
    "hours": [0.0, 0.0, 0.0, 0.0, 1.0],
    "closed": [0.0, 0.0, 0.0, 0.0, 1.0],
}


def fake_embedding(text: str) -> np.ndarray:
    vector = np.zeros(5)
    for keyword, direction in KEYWORD_DIRECTIONS.items():
        if keyword in text.lower():
            vector += np.array(direction)
    if not vector.any():
        vector = np.full(5, 0.01)
    return vector / np.linalg.norm(vector)


class FakeEmbedder:
    """Stands in for SentenceTransformer: same encode() call, keyword-based vectors."""

    def encode(self, texts, normalize_embeddings=True):
        return np.stack([fake_embedding(text) for text in texts])


class FakeClassifier:
    """Stands in for the zero-shot NLI pipeline and records the candidates it was shown.

    With `fixed_scores`, it returns the candidates in the order received with those scores.
    Otherwise it scores each candidate by embedding similarity to the review.
    """

    def __init__(self, fixed_scores=None):
        self.fixed_scores = fixed_scores
        self.seen_candidates = []

    def __call__(self, text, candidate_labels, hypothesis_template=None):
        self.seen_candidates.append(list(candidate_labels))
        if self.fixed_scores is not None:
            return {"labels": list(candidate_labels), "scores": self.fixed_scores[: len(candidate_labels)]}
        scored = sorted(
            ((label, float(fake_embedding(text) @ fake_embedding(label))) for label in candidate_labels),
            key=lambda pair: -pair[1],
        )
        return {"labels": [label for label, _ in scored], "scores": [max(score, 0.0) for _, score in scored]}


@pytest.fixture
def use_fakes(monkeypatch):
    """Install the test taxonomy and fake embedder; call the result to choose a classifier."""
    monkeypatch.setattr(categoriser, "COMPLAINT_CATEGORIES", TAXONOMY)
    monkeypatch.setattr(categoriser, "_get_embedder", lambda: FakeEmbedder())

    def install(classifier):
        monkeypatch.setattr(categoriser, "_get_classifier", lambda model_name: classifier)
        return classifier

    return install


def test_settings_match_the_dissertation():
    assert settings.CATEGORY_CONFIDENCE_THRESHOLD == 0.35
    assert settings.CATEGORY_SHORTLIST_K == 6


@pytest.mark.parametrize(
    "review, expected",
    [
        (BATTERY_REVIEW, "Battery & Charging Issues"),
        ("My package arrived damaged and three weeks late.", "Delivery & Shipping Issues"),
    ],
)
def test_review_is_assigned_the_matching_category(use_fakes, review, expected):
    use_fakes(FakeClassifier())
    result = categoriser.categorise(review, top_k=3, shortlist_k=3)
    assert result[0]["category"] == expected


def test_only_the_shortlist_reaches_the_classifier(use_fakes):
    classifier = use_fakes(FakeClassifier())

    categoriser.categorise(BATTERY_REVIEW, top_k=3, shortlist_k=3)

    shown = classifier.seen_candidates[-1]
    assert len(shown) == 3
    assert categoriser._nli_hypothesis(TAXONOMY[0]) in shown  # battery
    assert categoriser._nli_hypothesis(TAXONOMY[3]) in shown  # fees: shares the word "charge"


def test_default_shortlist_is_capped_by_the_taxonomy_size(use_fakes):
    classifier = use_fakes(FakeClassifier())
    categoriser.categorise(BATTERY_REVIEW)
    assert len(classifier.seen_candidates[-1]) == len(TAXONOMY)  # default is 6, only 5 exist


def test_best_score_below_threshold_is_reported_as_unclassified(use_fakes):
    just_below = settings.CATEGORY_CONFIDENCE_THRESHOLD - 0.01
    use_fakes(FakeClassifier(fixed_scores=[just_below, 0.20, 0.10]))

    result = categoriser.categorise(BATTERY_REVIEW, top_k=3, shortlist_k=3)

    assert result[0]["category"] == categoriser.UNCLASSIFIED
    assert result[0]["score"] == round(just_below, 4)
    assert result[1]["category"] != categoriser.UNCLASSIFIED  # only the top pick is replaced


def test_score_exactly_at_threshold_is_classified(use_fakes):
    use_fakes(FakeClassifier(fixed_scores=[settings.CATEGORY_CONFIDENCE_THRESHOLD, 0.20, 0.10]))
    result = categoriser.categorise(BATTERY_REVIEW, top_k=3, shortlist_k=3)
    assert result[0]["category"] == "Battery & Charging Issues"


def test_top_k_limits_the_number_of_results(use_fakes):
    use_fakes(FakeClassifier())
    assert len(categoriser.categorise(BATTERY_REVIEW, top_k=2, shortlist_k=5)) == 2


def test_empty_taxonomy_returns_no_categories(monkeypatch):
    monkeypatch.setattr(categoriser, "COMPLAINT_CATEGORIES", [])
    assert categoriser.categorise("Anything at all.") == []
