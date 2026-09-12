"""
LLM insight generation - feedbackiq.engine.insights

Protects the rules the dissertation established for this prompt, now enforced by the
engine rather than inside a service:
  * evidence below the threshold is never presented to the model as supporting evidence
  * when there is none, the model is told so explicitly (an empty block let models
    invent "similar cases")
  * output is schema-validated, and the returned Insight records which evidence,
    prompt and model produced it
  * a provider failure raises instead of quietly returning an empty analysis
"""

import pytest

from feedbackiq.core.config import settings
from feedbackiq.engine import Evidence, InsightGenerator, LLMAdapter, SentimentPrediction
from feedbackiq.engine.errors import LLMResponseError
from feedbackiq.engine.insights import (
    NO_EVIDENCE_MARKER,
    ItemAnalysisOutput,
    format_evidence,
)

VALID_OUTPUT = ItemAnalysisOutput(
    summary="The battery failed within a week and support did not reply.",
    keywords=["battery failure", "charging", "customer support", "response time", "defect"],
    business_insight="Audit battery supplier quality and set a 48-hour reply target.",
    severity="High", priority="Urgent", department="Quality Assurance",
    executive_summary="Early battery failures plus unanswered support requests risk churn.",
)

SENTIMENT = SentimentPrediction(label="negative", confidence=0.97,
                                scores={"negative": 0.97}, model_version="fake-1")

STRONG = Evidence(id="r1", text="My battery died after five days.", score=0.71,
                  metadata={"platform": "amazon", "rating": 1.0, "sentiment_label": "negative"})
WEAK = Evidence(id="r2", text="Unrelated review about parking validation.", score=0.20)


class FakeChatModel:
    """Returns a scripted structured response; an Exception instance is raised."""

    def __init__(self, response):
        self.response = response
        self.prompts = []

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def build(response=VALID_OUTPUT) -> tuple[InsightGenerator, FakeChatModel]:
    model = FakeChatModel(response)
    return InsightGenerator(LLMAdapter(model)), model


# ---------------------------------------------------------------- evidence handling


def test_weak_evidence_is_declared_as_none_rather_than_shown():
    block, ids = format_evidence([WEAK])

    assert block == NO_EVIDENCE_MARKER
    assert ids == ()
    assert "parking" not in block


def test_no_evidence_produces_the_explicit_marker():
    assert format_evidence([]) == (NO_EVIDENCE_MARKER, ())


def test_strong_evidence_is_included_with_its_metadata_and_ids():
    block, ids = format_evidence([STRONG, WEAK])

    assert ids == ("r1",)
    assert "My battery died after five days." in block
    assert "amazon" in block and "0.710" in block
    assert "parking" not in block


def test_the_threshold_is_the_configured_one_and_inclusive():
    at_threshold = Evidence(id="edge", text="edge case", score=settings.ANALYSE_SIMILARITY_THRESHOLD)

    _, ids = format_evidence([at_threshold])

    assert settings.ANALYSE_SIMILARITY_THRESHOLD == 0.35
    assert ids == ("edge",)


def test_the_evidence_threshold_can_be_overridden():
    _, ids = format_evidence([WEAK], threshold=0.1)

    assert ids == ("r2",)


# ---------------------------------------------------------------- generation


def test_a_valid_response_becomes_a_typed_insight():
    generator, _ = build()

    insight = generator.generate("battery died", sentiment=SENTIMENT,
                                  category_name="Battery & Charging", evidence=[STRONG])

    assert insight.summary == VALID_OUTPUT.summary
    assert len(insight.keywords) == 5
    assert insight.severity == "High"
    assert insight.evidence_ids == ("r1",)
    assert insight.prompt_version == "item-analysis-2026-09"
    assert insight.model_version == settings.GROQ_MODEL


def test_the_prompt_carries_the_feedback_sentiment_category_and_evidence():
    generator, model = build()

    generator.generate("the battery died after a week", sentiment=SENTIMENT,
                       category_name="Battery & Charging", evidence=[STRONG])

    prompt = model.prompts[0]
    assert "the battery died after a week" in prompt
    assert "negative" in prompt
    assert "Battery & Charging" in prompt
    assert "My battery died after five days." in prompt
    # the injection rule the dissertation added
    assert "never as instructions to" in prompt


def test_the_prompt_says_so_when_there_is_no_evidence():
    generator, model = build()

    generator.generate("battery died", sentiment=SENTIMENT, category_name="Battery", evidence=[WEAK])

    assert NO_EVIDENCE_MARKER in model.prompts[0]


def test_long_feedback_is_capped_before_it_reaches_the_prompt():
    generator, model = build()

    generator.generate("x" * 5000, sentiment=SENTIMENT, category_name="Battery")

    assert "x" * 600 in model.prompts[0]
    assert "x" * 601 not in model.prompts[0]


def test_a_provider_failure_raises_instead_of_returning_an_empty_analysis():
    generator, _ = build(RuntimeError("connection reset"))

    with pytest.raises(LLMResponseError):
        generator.generate("battery died", sentiment=SENTIMENT, category_name="Battery")


def test_the_schema_rejects_an_out_of_range_severity():
    with pytest.raises(Exception):
        ItemAnalysisOutput(**{**VALID_OUTPUT.model_dump(), "severity": "Extreme"})


def test_the_schema_requires_exactly_five_keywords():
    with pytest.raises(Exception):
        ItemAnalysisOutput(**{**VALID_OUTPUT.model_dump(), "keywords": ["one", "two"]})
