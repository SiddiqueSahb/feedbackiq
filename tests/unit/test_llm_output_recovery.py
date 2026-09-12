"""
Structured LLM output and its error recovery - nlp/summariser.py

Protects:
  * the business analysis must match the ReviewAnalysisLLM schema (5 keywords, fixed enums)
  * when a model writes JSON as text instead of a tool call, Groq rejects it with
    `tool_use_failed`; the valid JSON is recovered from the error payload
  * anything that can't be validated falls back to an empty analysis, never a crash
  * similar reviews below ANALYSE_SIMILARITY_THRESHOLD are never shown to the LLM as evidence

The evidence cases are ported from evaluate/verify_analyse_fixes.py (which needs a live Groq key).
"""

import json

import pytest

import nlp.summariser as summariser

VALID_ANALYSIS = {
    "summary": "The battery failed within a week and support did not reply.",
    "keywords": ["battery failure", "charging", "customer support", "response time", "product defect"],
    "business_insight": "Audit battery supplier quality and set a 48-hour support reply target.",
    "severity": "High",
    "priority": "Urgent",
    "department": "Quality Assurance",
    "executive_summary": "Early battery failures plus unanswered support requests put retention at risk.",
}

REVIEW = "The battery stopped charging after one week and support never replied."


class ToolUseFailedError(Exception):
    """Shaped like the Groq client error: the response payload is available on `.body`."""

    def __init__(self, failed_generation, code="tool_use_failed"):
        super().__init__("Error code: 400")
        self.body = {"error": {"code": code, "failed_generation": failed_generation}}


class FakeChain:
    """Stands in for `prompt | llm.with_structured_output(...)` and records its inputs."""

    def __init__(self, respond):
        self.respond = respond
        self.inputs = []

    def invoke(self, inputs):
        self.inputs.append(inputs)
        return self.respond()


def use_chain(monkeypatch, respond):
    chain = FakeChain(respond)
    monkeypatch.setattr(summariser, "_get_chain", lambda: chain)
    return chain


def valid_output():
    return summariser.ReviewAnalysisLLM(**VALID_ANALYSIS)


def analyse(similar_reviews=()):
    return summariser.analyse_review_with_llm(
        text=REVIEW,
        sentiment="negative",
        category="Product Performance Failures",
        similar_reviews=list(similar_reviews),
    )


# Recovering JSON from a failed tool call

def test_recovers_json_from_a_failed_tool_call():
    error = ToolUseFailedError(json.dumps(VALID_ANALYSIS))
    assert summariser._recover_from_failed_tool_call(error) == VALID_ANALYSIS


def test_recovers_json_wrapped_in_a_markdown_fence():
    text = "Here is the analysis:\n```json\n" + json.dumps(VALID_ANALYSIS) + "\n```\nHope this helps."
    assert summariser._recover_from_failed_tool_call(ToolUseFailedError(text)) == VALID_ANALYSIS


def test_recovers_json_surrounded_by_prose():
    text = "Sure! " + json.dumps(VALID_ANALYSIS) + " Let me know if you need more."
    assert summariser._recover_from_failed_tool_call(ToolUseFailedError(text)) == VALID_ANALYSIS


def test_recovers_from_an_error_that_only_has_a_message():
    # Older client versions put the payload only in the exception message.
    message = (
        "Error code: 400 - {'error': {'message': 'Failed to call a function.', "
        "'type': 'invalid_request_error', 'code': 'tool_use_failed', "
        "'failed_generation': '" + json.dumps(VALID_ANALYSIS) + "'}}"
    )
    assert summariser._recover_from_failed_tool_call(Exception(message)) == VALID_ANALYSIS


def test_other_provider_errors_are_not_treated_as_recoverable():
    error = ToolUseFailedError(json.dumps(VALID_ANALYSIS), code="rate_limit_exceeded")
    assert summariser._recover_from_failed_tool_call(error) is None


@pytest.mark.parametrize(
    "change",
    [
        {"keywords": ["battery", "charging", "support", "delay"]},
        {"severity": "Extreme"},
        {"department": "Legal"},
    ],
    ids=["four-keywords", "unknown-severity", "unknown-department"],
)
def test_recovered_json_must_still_match_the_schema(change):
    payload = json.dumps({**VALID_ANALYSIS, **change})
    assert summariser._recover_from_failed_tool_call(ToolUseFailedError(payload)) is None


def test_error_without_any_json_is_not_recoverable():
    assert summariser._recover_from_failed_tool_call(RuntimeError("connection reset")) is None


# analyse_review_with_llm end to end (LLM chain faked)

def test_valid_structured_output_is_returned_as_a_dict(monkeypatch):
    use_chain(monkeypatch, valid_output)
    assert analyse() == VALID_ANALYSIS


def test_failed_tool_call_is_recovered_end_to_end(monkeypatch):
    def model_wrote_json_as_text():
        raise ToolUseFailedError(json.dumps(VALID_ANALYSIS))

    use_chain(monkeypatch, model_wrote_json_as_text)
    assert analyse() == VALID_ANALYSIS


def test_unrecoverable_llm_failure_returns_an_empty_analysis(monkeypatch):
    def provider_down():
        raise RuntimeError("connection reset")

    use_chain(monkeypatch, provider_down)
    assert analyse() == summariser.EMPTY_ANALYSIS


def test_schema_violation_returns_an_empty_analysis(monkeypatch):
    def incomplete_output():
        return summariser.ReviewAnalysisLLM.model_validate({"summary": "incomplete"})

    use_chain(monkeypatch, incomplete_output)
    assert analyse() == summariser.EMPTY_ANALYSIS


# Evidence threshold

def test_evidence_threshold_matches_the_dissertation():
    assert summariser.ANALYSE_SIMILARITY_THRESHOLD == 0.35


@pytest.mark.parametrize(
    "similar_reviews",
    [
        [],
        [
            {"platform": "amazon", "rating": 3, "sentiment_label": "neutral",
             "similarity_score": 0.10, "text": "Completely unrelated review about a toaster."},
            {"platform": "yelp", "rating": 2, "sentiment_label": "negative",
             "similarity_score": 0.22, "text": "Also unrelated, about parking validation."},
        ],
    ],
    ids=["no-similar-reviews", "weak-matches-only"],
)
def test_weak_or_missing_evidence_is_declared_as_none(monkeypatch, similar_reviews):
    chain = use_chain(monkeypatch, valid_output)

    analyse(similar_reviews)

    rag_context = chain.inputs[-1]["rag_context"]
    assert rag_context == summariser.NO_SIMILAR_REVIEWS_MARKER
    assert "toaster" not in rag_context


def test_only_evidence_at_or_above_threshold_reaches_the_prompt(monkeypatch):
    chain = use_chain(monkeypatch, valid_output)

    analyse([
        {"platform": "amazon", "rating": 1, "sentiment_label": "negative", "similarity_score": 0.71,
         "text": "My phone battery died after 5 days and nobody from support responded."},
        {"platform": "amazon", "rating": 2, "sentiment_label": "negative",
         "similarity_score": summariser.ANALYSE_SIMILARITY_THRESHOLD,
         "text": "Charging port stopped working within the first two weeks."},
        {"platform": "yelp", "rating": 2, "sentiment_label": "negative", "similarity_score": 0.20,
         "text": "Also unrelated, about parking validation."},
    ])

    rag_context = chain.inputs[-1]["rag_context"]
    assert "battery died after 5 days" in rag_context
    assert "Charging port stopped working" in rag_context
    assert "parking validation" not in rag_context
