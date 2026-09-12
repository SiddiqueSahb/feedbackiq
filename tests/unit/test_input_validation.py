"""
Request validation - backend/models/schemas.py

Protects the limits and clean-up applied to every request before any model runs:
length limits, control-character stripping, allowed model/platform values,
batch and history size caps.
"""

import pytest
from pydantic import ValidationError

from backend.models.schemas import (
    BatchPredictRequest,
    ChatRequest,
    ReviewRequest,
    SearchRequest,
)


# ReviewRequest (used by /predict and /analyse)

def test_review_text_has_control_characters_and_outer_spaces_removed():
    request = ReviewRequest(text="  Great\x00 product\x07  ")
    assert request.text == "Great product"


def test_review_defaults_to_the_fine_tuned_model():
    assert ReviewRequest(text="The battery died.").model == "distilbert"


@pytest.mark.parametrize("text", ["", "ab", "x" * 5001], ids=["empty", "too-short", "too-long"])
def test_review_text_outside_length_limits_is_rejected(text):
    with pytest.raises(ValidationError):
        ReviewRequest(text=text)


def test_review_made_only_of_control_characters_and_spaces_is_rejected():
    with pytest.raises(ValidationError):
        ReviewRequest(text="   \x00\x01   ")


def test_unknown_model_is_rejected():
    with pytest.raises(ValidationError):
        ReviewRequest(text="The battery died.", model="gpt-4")


def test_unknown_platform_is_rejected():
    with pytest.raises(ValidationError):
        ReviewRequest(text="The battery died.", platform="facebook")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known issue: min_length=3 is checked before spaces and control characters "
        "are stripped, so '  a  ' is accepted and becomes 'a'."
    ),
)
def test_length_limit_applies_after_cleaning():
    with pytest.raises(ValidationError):
        ReviewRequest(text="  a  ")


# SearchRequest

def test_search_query_needs_at_least_two_characters():
    with pytest.raises(ValidationError):
        SearchRequest(query="a")


@pytest.mark.parametrize("top_k", [0, 51])
def test_search_result_count_is_limited_to_1_to_50(top_k):
    with pytest.raises(ValidationError):
        SearchRequest(query="battery", top_k=top_k)


@pytest.mark.parametrize("min_rating", [0.5, 5.5])
def test_search_minimum_rating_is_limited_to_1_to_5(min_rating):
    with pytest.raises(ValidationError):
        SearchRequest(query="battery", min_rating=min_rating)


# BatchPredictRequest (used by the CSV upload page)

def test_batch_drops_blank_rows():
    request = BatchPredictRequest(texts=["Works great", "   ", "\x00", "Terrible"])
    assert request.texts == ["Works great", "Terrible"]


def test_batch_of_only_blank_rows_is_rejected():
    with pytest.raises(ValidationError):
        BatchPredictRequest(texts=["   ", "\x00"])


def test_batch_is_capped_at_200_rows():
    assert len(BatchPredictRequest(texts=["ok"] * 200).texts) == 200
    with pytest.raises(ValidationError):
        BatchPredictRequest(texts=["ok"] * 201)


# ChatRequest (used by the RAG assistant)

@pytest.mark.parametrize("question", ["hi", "x" * 501], ids=["too-short", "too-long"])
def test_chat_question_outside_length_limits_is_rejected(question):
    with pytest.raises(ValidationError):
        ChatRequest(question=question)


def test_chat_history_is_capped_at_20_messages():
    message = {"role": "user", "content": "What are the top complaints?"}
    assert len(ChatRequest(question="And delivery?", chat_history=[message] * 20).chat_history) == 20
    with pytest.raises(ValidationError):
        ChatRequest(question="And delivery?", chat_history=[message] * 21)
