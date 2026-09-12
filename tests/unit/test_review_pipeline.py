"""
Per-review analysis pipeline - backend/services/sentiment_service.py

Protects how the four stages are combined, and how the pipeline degrades when one fails:
  sentiment error      -> VADER result
  categoriser error    -> "General Feedback"
  similar-review error -> no similar reviews (the analysis still runs)
  LLM error            -> empty analysis
and that one bad row in a batch doesn't fail the whole batch.

Every model and the LLM are replaced with fakes.
"""

import pytest

import backend.services.sentiment_service as service

REVIEW = "The battery died after a week."
CATEGORY = {"category": "Product Performance Failures", "score": 0.81, "description": "The product stops working."}
ANALYSIS = {
    "summary": "Battery failed quickly.",
    "keywords": ["battery", "failure", "charging", "defect", "quality"],
    "business_insight": "Review battery supplier quality.",
    "severity": "High",
    "priority": "High",
    "department": "Quality Assurance",
    "executive_summary": "Early battery failures reported.",
}


class FakeSentimentModel:
    """Stands in for any of the sentiment classes: same predict() result shape."""

    def __init__(self, label="negative", model="fake", fail_on=None):
        self.label = label
        self.model = model
        self.fail_on = fail_on

    def predict(self, text):
        if self.fail_on is not None and self.fail_on in text:
            raise RuntimeError("model crashed")
        return {
            "label": self.label,
            "confidence": 0.9,
            "scores": {"positive": 0.05, "neutral": 0.05, "negative": 0.9},
            "model": self.model,
        }


@pytest.fixture
def stages(monkeypatch):
    """Replace every stage with a fake and record how each one was called."""
    calls = {"categorise": [], "search": [], "llm": []}

    def fake_categorise(text, top_k=3):
        calls["categorise"].append(text)
        return [CATEGORY]

    def fake_search(**kwargs):
        calls["search"].append(kwargs)
        return []

    def fake_llm(**kwargs):
        calls["llm"].append(kwargs)
        return dict(ANALYSIS)

    monkeypatch.setattr(service, "get_sentiment_model", lambda model: FakeSentimentModel())
    monkeypatch.setattr(service, "categorise", fake_categorise)
    monkeypatch.setattr(service, "semantic_search", fake_search)
    monkeypatch.setattr(service, "analyse_review_with_llm", fake_llm)
    return calls


def test_pipeline_combines_all_four_stages(stages):
    result = service.analyse_review(REVIEW)

    assert result["review"] == REVIEW
    assert result["sentiment"]["label"] == "negative"
    assert result["category"] == CATEGORY["category"]
    assert result["categories"] == [CATEGORY]
    assert result["similar_reviews"] == []
    assert result["severity"] == ANALYSIS["severity"]
    assert stages["search"][0]["sentiment_filter"] == "negative"  # similar reviews share the predicted sentiment
    assert stages["llm"][0]["category"] == CATEGORY["category"]


def test_blank_review_is_rejected(stages):
    with pytest.raises(ValueError):
        service.analyse_review("   ")


def test_sentiment_failure_falls_back_to_vader(stages, monkeypatch):
    monkeypatch.setattr(service, "get_sentiment_model", lambda model: FakeSentimentModel(fail_on=""))
    monkeypatch.setattr(service, "get_vader", lambda: FakeSentimentModel(label="neutral", model="vader"))

    result = service.analyse_review(REVIEW)

    assert result["sentiment"]["model"] == "vader"


def test_categoriser_failure_falls_back_to_general_feedback(stages, monkeypatch):
    def broken_categoriser(text, top_k=3):
        raise RuntimeError("NLI model unavailable")

    monkeypatch.setattr(service, "categorise", broken_categoriser)

    assert service.analyse_review(REVIEW)["category"] == "General Feedback"


def test_similar_review_failure_still_produces_an_analysis(stages, monkeypatch):
    def missing_index(**kwargs):
        raise FileNotFoundError("FAISS index missing")

    monkeypatch.setattr(service, "semantic_search", missing_index)

    result = service.analyse_review(REVIEW)

    assert result["similar_reviews"] == []
    assert stages["llm"][0]["similar_reviews"] == []
    assert result["summary"] == ANALYSIS["summary"]


def test_llm_failure_returns_an_empty_analysis(stages, monkeypatch):
    def provider_down(**kwargs):
        raise RuntimeError("Groq unavailable")

    monkeypatch.setattr(service, "analyse_review_with_llm", provider_down)

    result = service.analyse_review(REVIEW)

    for field, empty_value in service.EMPTY_ANALYSIS.items():
        assert result[field] == empty_value


def test_one_failing_row_does_not_fail_the_batch(monkeypatch):
    monkeypatch.setattr(service, "get_sentiment_model", lambda model: FakeSentimentModel(fail_on="boom"))

    results = service.batch_predict(["Works great", "boom", "Terrible"])

    assert [row["label"] for row in results] == ["negative", "error", "negative"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known issue (stated in README): complaint categories are assigned even to "
        "positive feedback because there is no sentiment gate. Planned fix: Milestone 3."
    ),
)
def test_positive_feedback_is_not_given_a_complaint_category(stages, monkeypatch):
    monkeypatch.setattr(service, "get_sentiment_model", lambda model: FakeSentimentModel(label="positive"))

    service.analyse_review("Absolutely love it, works perfectly.")

    assert stages["categorise"] == []
