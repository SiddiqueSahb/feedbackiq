"""
Per-review analysis pipeline - feedbackiq.services.sentiment_service

Since Milestone 3 this module is a compatibility adapter: the engine does the analysis,
the adapter preserves the response shape the API and Streamlit expect, together with the
graceful degradation they have always relied on:

  sentiment unavailable -> VADER
  categoriser failure   -> "General Feedback"
  retrieval failure     -> no similar reviews (the analysis still runs)
  LLM failure           -> empty analysis fields
  one bad batch row     -> that row is labelled "error", the healthy rows survive

It also carries the one deliberate behaviour change of Milestone 3: positive feedback is
no longer given a complaint category (the sentiment gate). That was a strict xfail in
Milestone 1 and is a passing test here - see docs/production/milestone-03.md.

Everything is faked: no models, no index, no LLM, no network.
"""

import pytest

import feedbackiq.services.sentiment_service as service
from feedbackiq.engine import (
    AnalyticsEngine,
    CallableRetriever,
    Category,
    CategoryMatch,
    Evidence,
    Insight,
    SentimentPrediction,
    UsageStats,
)
from feedbackiq.engine.categorisation import CategorisationOutcome

REVIEW = "The battery died after a week."
CATEGORIES = (Category(id="battery", name="Battery & Charging", description="Fails to charge."),)

ANALYSIS_FIELDS = ("summary", "keywords", "business_insight", "severity",
                   "priority", "department", "executive_summary")

EVIDENCE_ROW = Evidence(
    id="r1", text="My battery died too.", score=0.71,
    metadata={"platform": "amazon", "rating": 1.0, "sentiment_label": "negative"},
)


class FakeSentiment:
    """A batch sentiment model with a fixed label."""

    def __init__(self, label="negative", *, fail=False, model_version="fake-1"):
        self.label = label
        self.fail = fail
        self.model_version = model_version

    def predict_batch(self, texts):
        if self.fail:
            raise RuntimeError("model crashed")
        return [
            SentimentPrediction(label=self.label, confidence=0.9,
                                scores={self.label: 0.9}, model_version=self.model_version)
            for _ in texts
        ]


class FakeCategoriser:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.seen = []

    def categorise_batch(self, texts, categories):
        if self.fail:
            raise RuntimeError("NLI model unavailable")
        self.seen.append(list(texts))
        match = CategoryMatch(category_id="battery", name="Battery & Charging",
                              score=0.81, description="Fails to charge.")
        return [CategorisationOutcome(top=match, candidates=(match,)) for _ in texts]


class FakeInsightGenerator:
    class _LLM:
        usage = UsageStats()
        model_name = "fake-llm"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.llm = self._LLM()
        self.calls = []

    def generate(self, text, *, sentiment, category_name, evidence=()):
        if self.fail:
            raise RuntimeError("provider down")
        self.calls.append({"text": text, "category": category_name, "evidence": tuple(evidence)})
        return Insight(
            summary="Battery failed quickly.",
            keywords=("battery", "failure", "charging", "defect", "quality"),
            business_insight="Review battery supplier quality.",
            severity="High", priority="High", department="Quality Assurance",
            executive_summary="Early battery failures reported.",
            evidence_ids=tuple(item.id for item in evidence),
        )


@pytest.fixture
def engine(monkeypatch):
    """Install an engine built from fakes, and hand it back so tests can swap parts."""
    built = AnalyticsEngine(
        sentiment=FakeSentiment(),
        categoriser=FakeCategoriser(),
        retriever=CallableRetriever(lambda query, limit: [EVIDENCE_ROW]),
        insight_generator=FakeInsightGenerator(),
        default_categories=CATEGORIES,
    )
    monkeypatch.setattr(service, "get_engine", lambda: built)
    monkeypatch.setattr(service, "get_batch_engine", lambda: built)
    return built


# ---------------------------------------------------------------- the happy path


def test_the_pipeline_combines_all_four_stages(engine):
    result = service.analyse_review(REVIEW)

    assert result["review"] == REVIEW
    assert result["sentiment"]["label"] == "negative"
    assert result["sentiment"]["model"] == "fake-1"
    assert result["category"] == "Battery & Charging"
    assert result["categories"][0]["category"] == "Battery & Charging"
    assert result["similar_reviews"][0]["review_id"] == "r1"
    assert result["similar_reviews"][0]["platform"] == "amazon"
    assert result["similar_reviews"][0]["similarity_score"] == 0.71
    assert result["summary"] == "Battery failed quickly."
    assert result["severity"] == "High"


def test_the_response_keeps_every_field_the_api_and_ui_read(engine):
    result = service.analyse_review(REVIEW)

    assert set(result) == {
        "review", "sentiment", "categories", "category", "similar_reviews", *ANALYSIS_FIELDS,
    }
    assert set(result["sentiment"]) == {"label", "confidence", "scores", "model"}
    assert isinstance(result["keywords"], list)


def test_the_insight_receives_the_category_and_the_retrieved_evidence(engine):
    service.analyse_review(REVIEW)

    call = engine.insight_generator.calls[0]
    assert call["category"] == "Battery & Charging"
    assert [item.id for item in call["evidence"]] == ["r1"]


def test_blank_review_is_rejected(engine):
    with pytest.raises(ValueError):
        service.analyse_review("   ")


# ---------------------------------------------------------------- the sentiment gate


def test_positive_feedback_is_not_given_a_complaint_category(engine):
    """Milestone 3 fix (previously a strict xfail): the taxonomy was discovered from
    negative reviews, so a positive item has no complaint to categorise."""
    engine.sentiment = FakeSentiment("positive")

    result = service.analyse_review("Absolutely love it, works perfectly.")

    assert result["category"] == service.GENERAL_FEEDBACK
    assert result["categories"] == [{"category": service.GENERAL_FEEDBACK, "confidence": 0.0}]
    assert engine.categoriser.seen == []                 # the model was never asked
    assert result["sentiment"]["label"] == "positive"     # everything else still works


@pytest.mark.parametrize("label", ["negative", "neutral"])
def test_negative_and_neutral_feedback_are_still_categorised(engine, label):
    engine.sentiment = FakeSentiment(label)

    result = service.analyse_review(REVIEW)

    assert result["category"] == "Battery & Charging"
    assert engine.categoriser.seen == [[REVIEW]]


# ---------------------------------------------------------------- graceful degradation


def test_sentiment_failure_falls_back_to_vader(engine, monkeypatch):
    engine.sentiment = FakeSentiment(fail=True)

    class FakeVader:
        def predict(self, text):
            return {"label": "neutral", "confidence": 0.1, "scores": {}, "model": "vader"}

    monkeypatch.setattr(service, "get_vader", lambda: FakeVader())

    result = service.analyse_review(REVIEW)

    assert result["sentiment"]["model"] == "vader"


def test_categoriser_failure_falls_back_to_general_feedback(engine):
    engine.categoriser = FakeCategoriser(fail=True)

    result = service.analyse_review(REVIEW)

    assert result["category"] == service.GENERAL_FEEDBACK
    assert result["sentiment"]["label"] == "negative"     # sentiment is unaffected


def test_retrieval_failure_still_produces_an_analysis(engine):
    def broken(query, limit):
        raise FileNotFoundError("FAISS index missing")

    engine.retriever = CallableRetriever(broken)

    result = service.analyse_review(REVIEW)

    assert result["similar_reviews"] == []
    assert result["summary"] == "Battery failed quickly."         # the LLM still ran
    assert engine.insight_generator.calls[0]["evidence"] == ()


def test_llm_failure_returns_an_empty_analysis(engine):
    engine.insight_generator = FakeInsightGenerator(fail=True)

    result = service.analyse_review(REVIEW)

    for field, empty_value in service.EMPTY_ANALYSIS.items():
        assert result[field] == empty_value
    assert result["category"] == "Battery & Charging"             # the rest survives


def test_without_an_llm_the_analysis_fields_are_empty(engine):
    engine.insight_generator = None

    result = service.analyse_review(REVIEW)

    assert result["summary"] == ""
    assert result["similar_reviews"][0]["review_id"] == "r1"


# ---------------------------------------------------------------- batch scoring


def test_batch_scores_every_row_in_order(engine):
    rows = service.batch_predict(["Works great", "Terrible", "It is fine"])

    assert [row["text"] for row in rows] == ["Works great", "Terrible", "It is fine"]
    assert all(row["label"] == "negative" for row in rows)        # the fake's fixed label
    assert all("confidence" in row for row in rows)


def test_one_failing_row_does_not_fail_the_batch(engine):
    class OneBadRow:
        """Fails any batch containing "boom"; scores everything else."""

        def predict_batch(self, texts):
            if any("boom" in text for text in texts):
                raise RuntimeError("cannot score that row")
            return [
                SentimentPrediction(label="negative", confidence=0.9,
                                    scores={}, model_version="fake-1")
                for _ in texts
            ]

    engine.sentiment = OneBadRow()

    rows = service.batch_predict(["Works great", "boom", "Terrible"])

    assert rows[1]["label"] == "error"
    assert rows[1]["confidence"] == 0.0
    assert [row["text"] for row in rows] == ["Works great", "boom", "Terrible"]


def test_an_empty_batch_returns_nothing(engine):
    assert service.batch_predict([]) == []


# ---------------------------------------------------------------- model selection


def test_a_non_default_model_is_injected_into_the_engine(engine, monkeypatch):
    class FakeVader:
        def predict(self, text):
            return {"label": "neutral", "confidence": 0.2, "scores": {}, "model": "vader"}

    monkeypatch.setattr(service, "get_sentiment_model", lambda model: FakeVader())

    built = service.engine_for_model("vader")

    assert built is not engine                        # a different engine ...
    assert built.categoriser is engine.categoriser    # ... reusing the loaded components
    assert built.retriever is engine.retriever


@pytest.mark.parametrize("model", ["distilbert", "finetuned"])
def test_the_default_model_uses_the_shared_engine(engine, model):
    assert service.engine_for_model(model) is engine
