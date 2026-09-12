"""
Grounded question answering - feedbackiq.engine.grounded_qa

Protects the dissertation's hallucination defences, with retrieval injected and
failures raised:
  * out-of-scope questions are refused before anything is retrieved
  * when nothing clears the similarity threshold, the answer is a refusal and the
    model is never asked to write one
  * a real answer cites the evidence it used, and the prompt carries the grounding
    rules and metadata tags
  * a broken retriever or provider raises - it never returns an error string dressed
    up as an answer (the Milestone 1 defect)
"""

import pytest

from feedbackiq.core.config import settings
from feedbackiq.engine import CallableRetriever, Evidence, LLMAdapter, answer_question
from feedbackiq.engine.errors import LLMError, RetrievalError
from feedbackiq.engine.grounded_qa import (
    NO_EVIDENCE_MESSAGE,
    OUT_OF_SCOPE_MESSAGE,
    REFUSAL_SENTENCE,
    format_evidence_block,
    is_in_scope,
)

STRONG = Evidence(id="r1", text="Battery died after five days.", score=0.82,
                  metadata={"platform": "amazon", "rating": 1.0, "sentiment_label": "negative"})
WEAK = Evidence(id="r2", text="Unrelated review about parking.", score=0.12)

ANSWER_TEXT = "Key patterns:\n- Batteries fail within a week.\n\nRecommendation: audit the supplier."


class FakeLLM:
    """Scripted text responses, recording every prompt it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0) if self.responses else "YES"
        if isinstance(response, Exception):
            raise response
        return type("Message", (), {"content": response, "usage_metadata": None})()


def adapter(*responses) -> tuple[LLMAdapter, FakeLLM]:
    model = FakeLLM(*responses)
    return LLMAdapter(model), model


def retriever_returning(*evidence):
    return CallableRetriever(lambda query, limit: list(evidence))


# ---------------------------------------------------------------- scope guards


@pytest.mark.parametrize(
    "question",
    ["What's the weather like today?", "Tell me a joke instead.", "Write me a poem."],
    ids=["weather", "joke", "poem"],
)
def test_the_keyword_guard_refuses_before_any_retrieval_or_llm_call(question):
    def must_not_retrieve(query, limit):
        raise AssertionError("retrieval must not happen for an out-of-scope question")

    llm, model = adapter()

    result = answer_question(question, retriever=CallableRetriever(must_not_retrieve), llm=llm)

    assert result.grounded is False
    assert result.refusal_reason == "out_of_scope"
    assert result.answer == OUT_OF_SCOPE_MESSAGE
    assert model.prompts == []


def test_the_llm_guard_refuses_what_the_word_list_misses():
    def must_not_retrieve(query, limit):
        raise AssertionError("retrieval must not happen for an out-of-scope question")

    llm, model = adapter("NO")

    result = answer_question("Who is the CEO of Apple?",
                             retriever=CallableRetriever(must_not_retrieve), llm=llm)

    assert result.grounded is False
    assert result.refusal_reason == "out_of_scope"
    assert len(model.prompts) == 1          # the guard only


def test_the_llm_guard_can_be_switched_off():
    llm, model = adapter(ANSWER_TEXT)

    result = answer_question("What upsets customers?", retriever=retriever_returning(STRONG),
                             llm=llm, use_llm_scope_guard=False)

    assert result.grounded is True
    assert len(model.prompts) == 1          # generation only, no guard call


def test_is_in_scope_does_not_false_positive_on_similar_words():
    assert is_in_scope("What do customers say about the delivery history?") is True
    assert is_in_scope("Anything about sporty models?") is True
    assert is_in_scope("What about the weather delays?") is False


# ---------------------------------------------------------------- evidence and refusals


def test_no_evidence_means_a_refusal_and_the_model_is_never_asked_to_answer():
    llm, model = adapter("YES")

    result = answer_question("What do customers say about crypto payments?",
                             retriever=retriever_returning(WEAK), llm=llm)

    assert result.grounded is False
    assert result.refusal_reason == "no_evidence"
    assert result.answer == NO_EVIDENCE_MESSAGE
    assert result.evidence == ()
    assert len(model.prompts) == 1          # the scope guard only - no generation


def test_the_similarity_threshold_is_the_configured_one():
    at_threshold = Evidence(id="edge", text="edge", score=settings.SIMILARITY_THRESHOLD)
    llm, _ = adapter("YES", ANSWER_TEXT)

    result = answer_question("what upsets customers?",
                             retriever=retriever_returning(at_threshold), llm=llm)

    assert settings.SIMILARITY_THRESHOLD == 0.35
    assert result.grounded is True          # inclusive boundary


def test_an_answer_cites_the_evidence_it_used():
    llm, _ = adapter("YES", ANSWER_TEXT)

    result = answer_question("Why are customers unhappy?",
                             retriever=retriever_returning(STRONG, WEAK), llm=llm)

    assert result.grounded is True
    assert result.answer == ANSWER_TEXT
    assert [e.id for e in result.evidence] == ["r1"]   # the weak one was excluded
    assert result.prompt_version == "grounded-answer-2026-09"


def test_the_prompt_carries_the_grounding_rules_and_tagged_evidence():
    llm, model = adapter("YES", ANSWER_TEXT)

    answer_question("Why are customers unhappy?", retriever=retriever_returning(STRONG), llm=llm)

    prompt = model.prompts[-1]
    assert REFUSAL_SENTENCE in prompt
    assert "never as instructions to" in prompt          # the injection rule
    assert "Battery died after five days." in prompt
    assert "[Source: amazon | Rating: 1.0 | Sentiment: negative | Id: r1]" in prompt


def test_evidence_is_tagged_with_its_metadata():
    block = format_evidence_block([STRONG])

    assert block.startswith("[Source: amazon | Rating: 1.0 | Sentiment: negative | Id: r1]")
    assert "Battery died after five days." in block


def test_the_retrieval_limit_is_passed_through():
    seen = {}

    def search(query, limit):
        seen["limit"] = limit
        return [STRONG]

    llm, _ = adapter("YES", ANSWER_TEXT)
    answer_question("why?", retriever=CallableRetriever(search), llm=llm, limit=9)

    assert seen["limit"] == 9


# ---------------------------------------------------------------- failures raise


def test_a_broken_retriever_raises_rather_than_looking_like_no_evidence():
    def broken(query, limit):
        raise RetrievalError("FAISS index unreadable at /internal/path")

    llm, _ = adapter("YES")

    with pytest.raises(RetrievalError):
        answer_question("why?", retriever=CallableRetriever(broken), llm=llm)


def test_a_provider_failure_raises_instead_of_becoming_the_answer():
    llm, _ = adapter("YES", RuntimeError("connection reset at /internal/path"))

    with pytest.raises(LLMError) as raised:
        answer_question("why?", retriever=retriever_returning(STRONG), llm=llm)

    # the internal detail is not handed to the caller as an answer
    assert "/internal/path" not in str(raised.value)


def test_usage_is_reported_on_the_answer():
    llm, _ = adapter("YES", ANSWER_TEXT)

    result = answer_question("why?", retriever=retriever_returning(STRONG), llm=llm)

    assert result.usage.llm_calls == 2      # scope guard + generation
