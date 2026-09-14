"""
Categorisation - feedbackiq.engine.categorisation

Protects the dissertation's two-stage design and the product change that matters:
**categories are passed in**, so a customer's taxonomy needs no code change and nothing
is read from a file at import time.

  * embedding similarity shortlists candidates; only those reach the NLI reranker
  * below CATEGORY_CONFIDENCE_THRESHOLD (0.35) the result is "Unclassified /
    Emerging Complaint", with the near-misses kept for a human to inspect
  * an empty taxonomy produces no opinion, not a forced guess

The embedder and classifier are fakes, so nothing is downloaded.
"""

import numpy as np

from feedbackiq.core.config import settings
from feedbackiq.engine import Category, ZeroShotCategoriser, categories_from_dicts
from feedbackiq.engine.categorisation import (
    UNCLASSIFIED_NAME,
    nli_hypothesis,
    shortlist_text,
)

CATEGORIES = (
    Category(id="battery", name="Battery & Charging",
             description="The product fails to charge or hold a charge.",
             exemplars=("battery drains fast", "won't charge")),
    Category(id="delivery", name="Delivery & Shipping",
             description="The order arrived late or damaged in transit."),
    Category(id="fees", name="Fees & Charges",
             description="Unexpected or undisclosed fees were charged to the customer."),
    Category(id="store", name="Store Hours",
             description="The store was closed during its posted business hours."),
)

# A tiny "embedding space": each keyword points along one axis.
DIRECTIONS = {
    "battery": [1, 0, 0, 0], "charge": [1, 0, 0, 0], "charg": [1, 0, 0, 0],
    "late": [0, 1, 0, 0], "damaged": [0, 1, 0, 0], "arrived": [0, 1, 0, 0],
    "fee": [0, 0, 1, 0], "closed": [0, 0, 0, 1], "hours": [0, 0, 0, 1], "store": [0, 0, 0, 1],
}


def fake_vector(text: str) -> np.ndarray:
    vector = np.zeros(4)
    for keyword, direction in DIRECTIONS.items():
        if keyword in text.lower():
            vector += np.array(direction, dtype=float)
    if not vector.any():
        vector = np.full(4, 0.01)
    return vector / np.linalg.norm(vector)


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, texts, normalize_embeddings=True):
        self.calls.append(list(texts))
        return np.stack([fake_vector(t) for t in texts])


class FakeClassifier:
    """Records the candidates it was shown; scores them, or replays fixed scores."""

    def __init__(self, fixed_scores=None):
        self.fixed_scores = fixed_scores
        self.seen = []

    def __call__(self, text, candidate_labels, hypothesis_template=None):
        self.seen.append(list(candidate_labels))
        if self.fixed_scores is not None:
            return {"labels": list(candidate_labels), "scores": self.fixed_scores[: len(candidate_labels)]}
        scored = sorted(
            ((label, float(fake_vector(text) @ fake_vector(label))) for label in candidate_labels),
            key=lambda pair: -pair[1],
        )
        return {"labels": [l for l, _ in scored], "scores": [max(s, 0.0) for _, s in scored]}


def build(classifier=None, **kwargs) -> tuple[ZeroShotCategoriser, FakeEmbedder, FakeClassifier]:
    embedder = FakeEmbedder()
    classifier = classifier or FakeClassifier()
    categoriser = ZeroShotCategoriser(embedder=embedder, classifier=classifier, **kwargs)
    return categoriser, embedder, classifier


# hypothesis construction

def test_the_shortlist_target_includes_exemplars_but_the_nli_hypothesis_does_not():
    battery = CATEGORIES[0]

    assert shortlist_text(battery).startswith(battery.description)
    assert "battery drains fast" in shortlist_text(battery)
    assert nli_hypothesis(battery) == battery.description


# the two stages

def test_the_matching_category_wins():
    categoriser, _, _ = build(shortlist_k=2)

    [outcome] = categoriser.categorise_batch(["The battery won't charge."], CATEGORIES)

    assert outcome.top.category_id == "battery"
    assert outcome.is_unclassified is False


def test_only_the_shortlist_reaches_the_classifier():
    categoriser, _, classifier = build(shortlist_k=2)

    categoriser.categorise_batch(["The battery won't charge."], CATEGORIES)

    assert len(classifier.seen[0]) == 2
    assert nli_hypothesis(CATEGORIES[0]) in classifier.seen[0]


def test_the_shortlist_size_cannot_exceed_the_taxonomy():
    categoriser, _, classifier = build(shortlist_k=10)

    categoriser.categorise_batch(["The battery won't charge."], CATEGORIES)

    assert len(classifier.seen[0]) == len(CATEGORIES)


def test_the_taxonomy_is_encoded_once_for_the_whole_batch():
    categoriser, embedder, _ = build()

    categoriser.categorise_batch(["battery", "late delivery", "hidden fee"], CATEGORIES)

    # first call: the four category texts; second: the three items
    assert len(embedder.calls) == 2
    assert len(embedder.calls[0]) == len(CATEGORIES)
    assert len(embedder.calls[1]) == 3


def test_results_come_back_in_input_order():
    categoriser, _, _ = build()

    outcomes = categoriser.categorise_batch(
        ["The battery won't charge.", "It arrived damaged and late.", "hidden fee charged"],
        CATEGORIES,
    )

    assert [o.top.category_id for o in outcomes] == ["battery", "delivery", "fees"]


# the confidence threshold

def test_a_weak_match_is_reported_as_unclassified_but_keeps_its_near_misses():
    below = settings.CATEGORY_CONFIDENCE_THRESHOLD - 0.01
    categoriser, _, _ = build(FakeClassifier(fixed_scores=[below, 0.2, 0.1]), shortlist_k=3)

    [outcome] = categoriser.categorise_batch(["something vague"], CATEGORIES)

    assert outcome.is_unclassified is True
    assert outcome.top.name == UNCLASSIFIED_NAME
    assert outcome.top.category_id is None
    assert outcome.top.score == round(below, 4)
    assert len(outcome.candidates) == 3          # what it nearly matched is preserved
    assert outcome.candidates[0].category_id is not None


def test_a_score_exactly_at_the_threshold_is_classified():
    at = settings.CATEGORY_CONFIDENCE_THRESHOLD
    categoriser, _, _ = build(FakeClassifier(fixed_scores=[at, 0.1, 0.1]), shortlist_k=3)

    [outcome] = categoriser.categorise_batch(["borderline"], CATEGORIES)

    assert outcome.is_unclassified is False
    assert outcome.top.category_id is not None


def test_top_k_limits_the_candidates_returned():
    categoriser, _, _ = build(shortlist_k=4, top_k=2)

    [outcome] = categoriser.categorise_batch(["The battery won't charge."], CATEGORIES)

    assert len(outcome.candidates) == 2


# degenerate input

def test_no_taxonomy_means_no_opinion():
    categoriser, _, classifier = build()

    outcomes = categoriser.categorise_batch(["battery died"], [])

    assert [o.top for o in outcomes] == [None]
    assert classifier.seen == []  # the model was never asked


def test_an_empty_batch_returns_nothing():
    categoriser, _, _ = build()

    assert categoriser.categorise_batch([], CATEGORIES) == []


# the dissertation taxonomy shape

def test_categories_can_be_built_from_the_taxonomy_json_shape():
    rows = [
        {"category": "Service Delays", "description": "Customers waited too long.",
         "exemplars": ["long wait", "slow service"], "count": 73284},
        {"category": "", "description": "ignored because it has no name"},
    ]

    categories = categories_from_dicts(rows)

    assert len(categories) == 1
    # No "key" in these rows, so the name is the fallback identity (see below for the
    # taxonomy 1.1.0 shape, where the stable key takes over).
    assert categories[0].id == "Service Delays"
    assert categories[0].name == "Service Delays"
    assert categories[0].exemplars == ("long wait", "slow service")


def test_a_stable_key_becomes_the_identity_and_the_name_stays_a_label():
    """Taxonomy 1.1.0 onward: `key` identifies the category, `name` is what a customer
    reads. Renaming must not change which category a stored result refers to."""
    before = categories_from_dicts(
        [{"key": "delivery", "category": "Delivery Issues", "description": "Arrived late."}]
    )
    after = categories_from_dicts(
        [{"key": "delivery", "category": "Shipping & Delivery", "description": "Arrived late."}]
    )

    assert before[0].id == after[0].id == "delivery"
    assert (before[0].name, after[0].name) == ("Delivery Issues", "Shipping & Delivery")


def test_the_canonical_taxonomy_supplies_a_key_for_every_category():
    from feedbackiq.core.taxonomy import load_default_taxonomy

    categories = categories_from_dicts(load_default_taxonomy())

    assert len(categories) == 24
    # Identities are keys, not display names: lower_snake_case and unique.
    assert all(category.id.islower() and " " not in category.id for category in categories)
    assert len({category.id for category in categories}) == 24


def test_a_category_without_a_description_falls_back_to_its_name():
    [category] = categories_from_dicts([{"category": "Billing"}])

    assert category.description == "Billing"
