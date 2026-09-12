"""
The batch pipeline - feedbackiq.engine.pipeline

Protects the promises the rest of the product relies on:
  * one result per input, in input order, tagged with the caller's id
  * a failure in one record does not lose the batch
  * the SENTIMENT GATE: positive feedback is not given a complaint category
    (the defect recorded as a strict xfail in Milestone 1)
  * retrieval and LLM insight are opt-in, so a bulk import pays for neither
  * every batch carries the manifest of models, prompts and thresholds used

Every collaborator is a fake: no models, no network, no data files.
"""

import pytest

from feedbackiq.engine import (
    AnalyticsEngine,
    CallableRetriever,
    Category,
    CategoryMatch,
    Evidence,
    FeedbackItem,
    Insight,
    NullRetriever,
    SentimentPrediction,
    UsageStats,
    gated_sentiments,
)
from feedbackiq.engine.categorisation import CategorisationOutcome
from feedbackiq.engine.errors import EngineError

CATEGORIES = (
    Category(id="battery", name="Battery & Charging", description="The product fails to charge."),
)


class FakeSentiment:
    """Returns the scripted labels, in order, and records what it was asked to score."""

    def __init__(self, labels, *, fail=False):
        self.labels = list(labels)
        self.fail = fail
        self.seen: list[list[str]] = []

    def predict_batch(self, texts):
        if self.fail:
            raise RuntimeError("model weights unavailable")
        self.seen.append(list(texts))
        return [
            SentimentPrediction(label=label, confidence=0.9, scores={label: 0.9},
                                model_version="fake-sentiment-1")
            for label in self.labels[: len(texts)]
        ]


class FakeCategoriser:
    def __init__(self, *, fail=False, unclassified=False):
        self.fail = fail
        self.unclassified = unclassified
        self.seen: list[list[str]] = []

    def categorise_batch(self, texts, categories):
        if self.fail:
            raise RuntimeError("NLI model unavailable")
        self.seen.append(list(texts))

        match = CategoryMatch(
            category_id=None if self.unclassified else "battery",
            name="Unclassified / Emerging Complaint" if self.unclassified else "Battery & Charging",
            score=0.2 if self.unclassified else 0.81,
        )
        return [
            CategorisationOutcome(top=match, candidates=(match,), is_unclassified=self.unclassified)
            for _ in texts
        ]


class FakeInsightGenerator:
    """Mimics InsightGenerator, including its `llm.usage` for accounting."""

    class _LLM:
        def __init__(self):
            self.usage = UsageStats(llm_calls=2, input_tokens=300, output_tokens=60)

    def __init__(self, *, fail=False):
        self.fail = fail
        self.llm = self._LLM()
        self.calls: list[dict] = []

    def generate(self, text, *, sentiment, category_name, evidence=()):
        if self.fail:
            raise RuntimeError("provider down")
        self.calls.append({"text": text, "category": category_name, "evidence": tuple(evidence)})
        return Insight(
            summary="Battery failed early.",
            keywords=("battery", "charging", "failure", "support", "quality"),
            business_insight="Audit the battery supplier.",
            severity="High", priority="Urgent", department="Quality Assurance",
            executive_summary="Early battery failures risk churn.",
            evidence_ids=tuple(item.id for item in evidence),
            prompt_version="item-analysis-test", model_version="fake-llm",
        )


def build_engine(labels, **kwargs) -> AnalyticsEngine:
    defaults = dict(
        sentiment=FakeSentiment(labels),
        categoriser=FakeCategoriser(),
        default_categories=CATEGORIES,
    )
    defaults.update(kwargs)
    return AnalyticsEngine(**defaults)


def items(*texts) -> list[FeedbackItem]:
    return [FeedbackItem(id=f"item-{i}", text=text) for i, text in enumerate(texts, start=1)]


# ---------------------------------------------------------------- batch contract


def test_one_result_per_input_in_input_order():
    engine = build_engine(["negative", "neutral", "negative"])

    batch = engine.analyse_batch(items("battery died", "it is okay", "arrived broken"))

    assert len(batch) == 3
    assert [r.feedback_id for r in batch.results] == ["item-1", "item-2", "item-3"]
    assert [r.sentiment.label for r in batch.results] == ["negative", "neutral", "negative"]


def test_results_are_typed():
    engine = build_engine(["negative"])

    [result] = engine.analyse_batch(items("battery died")).results

    assert isinstance(result.sentiment, SentimentPrediction)
    assert isinstance(result.category, CategoryMatch)
    assert result.sentiment.model_version == "fake-sentiment-1"
    assert result.ok is True


def test_an_empty_batch_is_not_an_error():
    batch = build_engine([]).analyse_batch([])

    assert batch.results == ()
    assert batch.usage == UsageStats()
    assert batch.versions["engine"]


def test_text_is_normalised_before_the_model_sees_it():
    sentiment = FakeSentiment(["negative"])
    engine = build_engine([], sentiment=sentiment)

    engine.analyse_batch([FeedbackItem(id="a", text="  battery\x00  died  ")])

    assert sentiment.seen == [["battery died"]]


def test_every_batch_carries_its_manifest():
    batch = build_engine(["negative"]).analyse_batch(items("battery died"))

    assert set(batch.versions) >= {"engine", "sentiment_model", "categoriser_model", "prompts", "thresholds"}


# ---------------------------------------------------------------- the sentiment gate


def test_positive_feedback_is_not_given_a_complaint_category():
    """The Milestone 1 defect, fixed: the taxonomy was discovered from negative
    reviews, so a positive item has no complaint to categorise."""
    categoriser = FakeCategoriser()
    engine = build_engine(["positive"], categoriser=categoriser)

    [result] = engine.analyse_batch(items("Absolutely love it, works perfectly.")).results

    assert result.category is None
    assert result.candidate_categories == ()
    assert result.categorisation_skipped is not None
    assert "positive" in result.categorisation_skipped
    assert categoriser.seen == []          # the model was never asked
    assert result.ok is True               # skipping is not a failure


@pytest.mark.parametrize("label", ["negative", "neutral"])
def test_negative_and_neutral_feedback_are_categorised(label):
    engine = build_engine([label])

    [result] = engine.analyse_batch(items("the battery died")).results

    assert result.category is not None
    assert result.categorisation_skipped is None


def test_the_gate_only_sends_the_gated_items_to_the_categoriser():
    categoriser = FakeCategoriser()
    engine = build_engine(["negative", "positive", "neutral"], categoriser=categoriser)

    engine.analyse_batch(items("battery died", "love it", "it is okay"))

    assert categoriser.seen == [["battery died", "it is okay"]]


def test_the_gate_is_configurable(monkeypatch):
    monkeypatch.setattr(
        type(__import__("feedbackiq.core.config", fromlist=["settings"]).settings),
        "categorise_sentiments",
        property(lambda self: frozenset({"negative"})),
    )
    assert gated_sentiments() == frozenset({"negative"})

    categoriser = FakeCategoriser()
    engine = build_engine(["neutral"], categoriser=categoriser)

    [result] = engine.analyse_batch(items("it is okay")).results

    assert result.category is None
    assert categoriser.seen == []


def test_an_unclassified_result_is_marked_as_such():
    engine = build_engine(["negative"], categoriser=FakeCategoriser(unclassified=True))

    [result] = engine.analyse_batch(items("something vague")).results

    assert result.is_unclassified is True
    assert result.category.category_id is None


def test_without_a_categoriser_nothing_is_categorised():
    engine = build_engine(["negative"], categoriser=None)

    [result] = engine.analyse_batch(items("battery died")).results

    assert result.category is None
    assert result.ok is True


def test_without_categories_nothing_is_categorised():
    categoriser = FakeCategoriser()
    engine = build_engine(["negative"], categoriser=categoriser, default_categories=())

    [result] = engine.analyse_batch(items("battery died")).results

    assert result.category is None
    assert categoriser.seen == []


def test_categories_can_be_supplied_per_call():
    categoriser = FakeCategoriser()
    engine = build_engine(["negative"], categoriser=categoriser, default_categories=())
    customer_taxonomy = [Category(id="billing", name="Billing", description="Charged incorrectly.")]

    [result] = engine.analyse_batch(items("charged twice"), categories=customer_taxonomy).results

    assert result.category is not None
    assert categoriser.seen == [["charged twice"]]


# ---------------------------------------------------------------- failure isolation


def test_a_categoriser_failure_marks_only_the_gated_records():
    engine = build_engine(["negative", "positive"], categoriser=FakeCategoriser(fail=True))

    batch = engine.analyse_batch(items("battery died", "love it"))

    failed, skipped = batch.results
    assert failed.status == "failed"
    assert "categorisation failed" in failed.error
    assert skipped.status == "ok"          # positive never went to the categoriser
    assert len(batch.failed) == 1 and len(batch.succeeded) == 1


def test_a_whole_batch_sentiment_failure_marks_every_record_not_raises():
    engine = build_engine([], sentiment=FakeSentiment([], fail=True))

    batch = engine.analyse_batch(items("a", "b"))

    assert [r.status for r in batch.results] == ["failed", "failed"]
    assert all("sentiment failed" in r.error for r in batch.results)
    assert [r.feedback_id for r in batch.results] == ["item-1", "item-2"]


def test_a_model_returning_the_wrong_number_of_predictions_is_a_programming_error():
    engine = build_engine(["negative"])  # one label for two items

    with pytest.raises(EngineError):
        engine.analyse_batch(items("a", "b"))


def test_a_retrieval_failure_isolates_that_record():
    def broken(query, limit):
        raise RuntimeError("index gone")

    engine = build_engine(["negative"], retriever=CallableRetriever(broken))

    [result] = engine.analyse_batch(items("battery died"), with_evidence=True).results

    assert result.status == "failed"
    assert "retrieval failed" in result.error


def test_an_insight_failure_isolates_that_record():
    engine = build_engine(["negative"], insight_generator=FakeInsightGenerator(fail=True))

    [result] = engine.analyse_batch(items("battery died"), with_insights=True).results

    assert result.status == "failed"
    assert "insight failed" in result.error


# ---------------------------------------------------------------- optional stages


def test_evidence_and_insights_are_off_by_default():
    retriever = CallableRetriever(lambda q, n: [Evidence(id="e1", text="same", score=0.9)])
    generator = FakeInsightGenerator()
    engine = build_engine(["negative"], retriever=retriever, insight_generator=generator)

    [result] = engine.analyse_batch(items("battery died")).results

    assert result.evidence == ()
    assert result.insight is None
    assert generator.calls == []


def test_evidence_is_retrieved_when_asked_for():
    retriever = CallableRetriever(
        lambda q, n: [Evidence(id="e1", text="battery died too", score=0.72)]
    )
    engine = build_engine(["negative"], retriever=retriever)

    [result] = engine.analyse_batch(items("battery died"), with_evidence=True).results

    assert [(e.id, e.score) for e in result.evidence] == [("e1", 0.72)]


def test_the_insight_receives_the_retrieved_evidence_and_the_category():
    retriever = CallableRetriever(lambda q, n: [Evidence(id="e1", text="same", score=0.8)])
    generator = FakeInsightGenerator()
    engine = build_engine(["negative"], retriever=retriever, insight_generator=generator)

    [result] = engine.analyse_batch(
        items("battery died"), with_evidence=True, with_insights=True
    ).results

    assert result.insight.evidence_ids == ("e1",)
    assert generator.calls[0]["category"] == "Battery & Charging"
    assert generator.calls[0]["evidence"][0].id == "e1"


def test_llm_usage_is_reported_on_the_batch():
    engine = build_engine(["negative"], insight_generator=FakeInsightGenerator())

    batch = engine.analyse_batch(items("battery died"), with_insights=True)

    assert batch.usage.llm_calls == 2
    assert batch.usage.total_tokens == 360


def test_usage_is_zero_when_no_llm_is_involved():
    batch = build_engine(["negative"]).analyse_batch(items("battery died"))

    assert batch.usage == UsageStats()


def test_analyse_one_returns_a_single_result_with_evidence_and_insight():
    retriever = CallableRetriever(lambda q, n: [Evidence(id="e1", text="same", score=0.8)])
    engine = build_engine(["negative"], retriever=retriever, insight_generator=FakeInsightGenerator())

    result = engine.analyse_one(FeedbackItem(id="solo", text="battery died"))

    assert result.feedback_id == "solo"
    assert result.evidence and result.insight is not None


def test_the_default_retriever_finds_nothing():
    engine = build_engine(["negative"], retriever=None)

    assert isinstance(engine.retriever, NullRetriever)
    [result] = engine.analyse_batch(items("battery died"), with_evidence=True).results
    assert result.evidence == ()
