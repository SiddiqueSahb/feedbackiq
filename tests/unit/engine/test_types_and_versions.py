"""
Engine types and the version manifest - feedbackiq.engine.types / .versions

Protects:
  * results are typed, ordered and always paired with their input id
  * a failed record is still a result, not a lost one
  * usage adds up across calls
  * every batch can say which engine, models and prompts produced it
"""

import pytest

from feedbackiq.core.config import settings
from feedbackiq.engine import (
    ENGINE_VERSION,
    PROMPT_VERSIONS,
    BatchAnalysis,
    Category,
    FeedbackItem,
    ItemAnalysis,
    SentimentPrediction,
    UsageStats,
    manifest,
)


# FeedbackItem

def test_feedback_item_keeps_the_callers_id_and_text():
    item = FeedbackItem(id="row-7", text="The battery died.")
    assert (item.id, item.text) == ("row-7", "The battery died.")


@pytest.mark.parametrize("text", ["", "   ", "\n\t"], ids=["empty", "spaces", "whitespace"])
def test_feedback_item_rejects_text_with_nothing_in_it(text):
    with pytest.raises(ValueError):
        FeedbackItem(id="row-7", text=text)


def test_category_requires_a_description_because_that_is_the_nli_hypothesis():
    category = Category(id="c1", name="Battery", description="The product fails to charge.")
    assert category.description == "The product fails to charge."
    assert category.exemplars == ()


# ItemAnalysis / BatchAnalysis

def test_item_analysis_defaults_to_ok_and_carries_no_error():
    result = ItemAnalysis(feedback_id="a")
    assert result.ok is True
    assert result.status == "ok"
    assert result.error is None


def test_with_error_marks_a_record_failed_without_losing_its_id():
    failed = ItemAnalysis(feedback_id="a").with_error("sentiment failed: boom")

    assert failed.feedback_id == "a"
    assert failed.ok is False
    assert failed.status == "failed"
    assert "boom" in failed.error


def test_batch_separates_succeeded_from_failed_and_keeps_order():
    batch = BatchAnalysis(
        results=(
            ItemAnalysis(feedback_id="a"),
            ItemAnalysis(feedback_id="b").with_error("nope"),
            ItemAnalysis(feedback_id="c"),
        )
    )

    assert len(batch) == 3
    assert [r.feedback_id for r in batch.results] == ["a", "b", "c"]
    assert [r.feedback_id for r in batch.succeeded] == ["a", "c"]
    assert [r.feedback_id for r in batch.failed] == ["b"]


def test_sentiment_prediction_records_which_model_produced_it():
    prediction = SentimentPrediction(
        label="negative", confidence=0.97,
        scores={"negative": 0.97, "neutral": 0.02, "positive": 0.01},
        model_version="distilbert-finetuned-final@abc123",
    )
    assert prediction.model_version == "distilbert-finetuned-final@abc123"


# UsageStats

def test_usage_starts_at_zero():
    usage = UsageStats()
    assert (usage.llm_calls, usage.input_tokens, usage.output_tokens, usage.retries) == (0, 0, 0, 0)
    assert usage.total_tokens == 0


def test_usage_adds_up_across_calls():
    first = UsageStats(llm_calls=1, input_tokens=100, output_tokens=20, retries=1)
    second = UsageStats(llm_calls=2, input_tokens=50, output_tokens=10)

    total = first.plus(second)

    assert total.llm_calls == 3
    assert total.input_tokens == 150
    assert total.output_tokens == 30
    assert total.retries == 1
    assert total.total_tokens == 180


# Manifest

def test_manifest_names_the_engine_models_and_prompts():
    entry = manifest()

    assert entry["engine"] == ENGINE_VERSION
    assert entry["categoriser_model"] == settings.ZEROSHOT_MODEL
    assert entry["embedding_model"] == settings.EMBEDDING_MODEL
    assert entry["llm_model"] == settings.GROQ_MODEL
    assert entry["prompts"] == PROMPT_VERSIONS
    assert str(entry["sentiment_model"]).startswith(settings.model_dir.name)


def test_manifest_records_the_thresholds_a_result_depended_on():
    thresholds = manifest()["thresholds"]

    assert thresholds["category_confidence"] == settings.CATEGORY_CONFIDENCE_THRESHOLD
    assert thresholds["retrieval_similarity"] == settings.SIMILARITY_THRESHOLD
    assert thresholds["evidence_similarity"] == settings.ANALYSE_SIMILARITY_THRESHOLD
    # The dissertation's values, unchanged by Milestone 3.
    assert thresholds == {
        "category_confidence": 0.35,
        "retrieval_similarity": 0.35,
        "evidence_similarity": 0.35,
    }


def test_prompt_versions_exist_for_every_prompt_the_engine_sends():
    assert set(PROMPT_VERSIONS) == {
        "item_analysis", "grounded_answer", "condense_question", "scope_guard",
    }
    assert all(version for version in PROMPT_VERSIONS.values())
