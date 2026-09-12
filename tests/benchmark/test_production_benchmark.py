"""
Production benchmark: does the engine still analyse as well as it did?

This is the guard against a future refactor quietly degrading the analytics. It runs
the **real** fine-tuned model through the **engine** over a fixed, deterministic sample
of labelled corpus reviews, and fails if quality drops below an explicit floor.

    measured 2026-09-12 on this benchmark (n=600, 200 per class, seed 42):
        accuracy 0.8133   macro F1 0.8137
    floor enforced here:
        accuracy 0.80     macro F1 0.80

The floor sits ~1.3 points below the measurement: tight enough to catch a real
regression (a broken preprocessing step, a mis-wired model, a batching bug), loose
enough not to fail on harmless variation.

(The separate preprocessing comparison in engine/preprocessing.py used a larger
n=1,200 sample and reported 0.8214 macro F1 for raw text; a different sample gives a
slightly different figure, which is why this file states its own measurement.)

This is NOT the dissertation benchmark. The dissertation evaluates `cleaned_text` via
`scripts/evaluate_models.py` and its numbers are untouched. This one measures what the
engine actually serves: the caller's text, through `analyse_batch`.

It needs the corpus and the model, so it skips automatically when they are absent -
which is the case in CI, where `data/` and `models/` are not present. Run it locally:

    python -m pytest tests/benchmark -v
"""

from __future__ import annotations

import pytest

from feedbackiq.core.config import settings

pytestmark = pytest.mark.benchmark

# Fixed sample: same seed, same rows, every run.
PER_CLASS = 200
SEED = 42

# The floor. Raise it only with a new measurement; lowering it needs a documented reason.
MINIMUM_ACCURACY = 0.80
MINIMUM_MACRO_F1 = 0.80


def _require_artefacts() -> None:
    if not settings.data_file.exists():
        pytest.skip(f"corpus not available at {settings.data_file}")
    if not settings.model_dir.exists():
        pytest.skip(f"fine-tuned model not available at {settings.model_dir}")


@pytest.fixture(scope="module")
def sample():
    """A stratified, deterministic slice of the labelled corpus."""
    _require_artefacts()
    import pandas as pd

    frame = pd.read_parquet(
        settings.data_file, columns=["review_id", "text", "sentiment_label"]
    )
    picked = (
        frame.groupby("sentiment_label", group_keys=False)
        .apply(lambda group: group.sample(PER_CLASS, random_state=SEED))
        .sort_values("review_id")
        .reset_index(drop=True)
    )
    return picked


@pytest.fixture(scope="module")
def engine():
    """The sentiment-only engine: no categoriser, no retrieval, no LLM."""
    _require_artefacts()
    from feedbackiq.engine import build_sentiment_only_engine

    return build_sentiment_only_engine()


def test_sentiment_quality_stays_above_the_floor(engine, sample):
    from sklearn.metrics import accuracy_score, f1_score

    from feedbackiq.engine import FeedbackItem

    items = [
        FeedbackItem(id=str(row.review_id), text=str(row.text))
        for row in sample.itertuples()
    ]

    batch = engine.analyse_batch(items)

    assert len(batch.results) == len(items), "every input must produce a result"
    assert not batch.failed, f"{len(batch.failed)} records failed: {batch.failed[:2]}"
    assert [r.feedback_id for r in batch.results] == [i.id for i in items], "order must be preserved"

    truth = sample["sentiment_label"].tolist()
    predicted = [r.sentiment.label for r in batch.results]

    accuracy = accuracy_score(truth, predicted)
    macro_f1 = f1_score(truth, predicted, average="macro")

    print(
        f"\nproduction benchmark | n={len(items)} "
        f"accuracy={accuracy:.4f} macro_f1={macro_f1:.4f} "
        f"(floor {MINIMUM_ACCURACY}/{MINIMUM_MACRO_F1})"
    )

    assert accuracy >= MINIMUM_ACCURACY, f"accuracy {accuracy:.4f} below floor {MINIMUM_ACCURACY}"
    assert macro_f1 >= MINIMUM_MACRO_F1, f"macro F1 {macro_f1:.4f} below floor {MINIMUM_MACRO_F1}"


def test_batched_scoring_agrees_with_one_at_a_time(engine, sample):
    """
    Batching is where the speed-up comes from, and padding is the thing that could
    silently change a prediction. It must not.
    """
    from feedbackiq.engine import FeedbackItem
    from feedbackiq.nlp.sentiment import get_finetuned

    texts = [str(t) for t in sample["text"].head(60).tolist()]
    items = [FeedbackItem(id=str(i), text=text) for i, text in enumerate(texts)]

    batched = [r.sentiment.label for r in engine.analyse_batch(items).results]

    classifier = get_finetuned()
    one_at_a_time = [classifier.predict(text)["label"] for text in texts]

    disagreements = [
        (text[:60], a, b) for text, a, b in zip(texts, batched, one_at_a_time) if a != b
    ]

    assert not disagreements, f"batched and single scoring disagree: {disagreements[:3]}"


def test_the_engine_records_which_models_produced_the_results(engine, sample):
    from feedbackiq.engine import FeedbackItem

    batch = engine.analyse_batch(
        [FeedbackItem(id="1", text=str(sample["text"].iloc[0]))]
    )

    assert batch.versions["engine"]
    assert settings.model_dir.name in str(batch.versions["sentiment_model"])
    assert batch.results[0].sentiment.model_version
