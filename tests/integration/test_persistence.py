"""
Storing engine output - feedbackiq.db.persistence

The `BatchAnalysis` objects here are built by hand from the engine's own types: that is
exactly what `AnalyticsEngine.analyse_batch` returns, and building them directly keeps
these tests about persistence, with no model loaded and no network call.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from feedbackiq.db.models import AnalysisResult, AnalysisRun, Category, Feedback, Insight
from feedbackiq.db.persistence import (
    content_hash,
    finish_import_batch,
    save_batch_analysis,
    save_feedback,
    start_import_batch,
)
from feedbackiq.db.session import reset_engine, session_scope
from feedbackiq.engine.types import (
    BatchAnalysis,
    CategoryMatch,
    Insight as EngineInsight,
    ItemAnalysis,
    SentimentPrediction,
    UsageStats,
)

pytestmark = pytest.mark.integration

# What the engine's manifest looks like (engine/versions.py), which is what a run row
# records.
VERSIONS = {
    "engine": "1.0.0",
    "sentiment_model": "distilbert-finetuned-final@abc123def456",
    "categoriser_model": "MoritzLaurer/deberta-v3-base-zeroshot-v2.0",
    "embedding_model": "all-MiniLM-L6-v2",
    "llm_model": "openai/gpt-oss-20b",
    "prompts": {"item_analysis": "item-analysis-2026-09"},
    "thresholds": {"category_confidence": 0.35},
}


def negative_result(feedback_id, *, category="Service and Wait Time Delays", score=0.81):
    return ItemAnalysis(
        feedback_id=str(feedback_id),
        sentiment=SentimentPrediction(
            label="negative",
            confidence=0.9712,
            scores={"negative": 0.9712, "neutral": 0.02, "positive": 0.0088},
            model_version="distilbert-finetuned-final@abc123def456",
        ),
        category=CategoryMatch(category_id=category, name=category, score=score),
        candidate_categories=(
            CategoryMatch(category_id=category, name=category, score=score),
            CategoryMatch(category_id="other", name="Product Quality Issues", score=0.11),
        ),
    )


def batch(*results, usage=UsageStats()) -> BatchAnalysis:
    return BatchAnalysis(results=tuple(results), usage=usage, versions=VERSIONS)


@pytest.fixture()
def feedback_rows(session, organisation, data_source):
    return save_feedback(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        texts=["waited forty minutes for a table", "the food was cold"],
    )


# ---------------------------------------------------------------- the run row


def test_a_run_records_how_it_was_produced(session, organisation, feedback_rows):
    analysis = batch(
        negative_result(feedback_rows[0].id),
        usage=UsageStats(llm_calls=2, input_tokens=340, output_tokens=90, retries=1),
    )

    run = save_batch_analysis(session, organisation_id=organisation.id, analysis=analysis)
    session.commit()

    stored = session.get(AnalysisRun, run.id)
    assert stored.engine_version == "1.0.0"
    assert stored.model_versions["sentiment_model"] == VERSIONS["sentiment_model"]
    assert stored.prompt_versions == {"item_analysis": "item-analysis-2026-09"}
    assert stored.thresholds == {"category_confidence": 0.35}
    # Usage is flattened into columns because metering will SUM these.
    assert (stored.llm_calls, stored.input_tokens, stored.output_tokens, stored.llm_retries) == (
        2, 340, 90, 1,
    )
    assert stored.status == "completed"


def test_a_run_counts_what_succeeded_and_what_failed(session, organisation, feedback_rows):
    analysis = batch(
        negative_result(feedback_rows[0].id),
        ItemAnalysis(feedback_id=str(feedback_rows[1].id)).with_error("sentiment failed: boom"),
    )

    run = save_batch_analysis(session, organisation_id=organisation.id, analysis=analysis)
    session.commit()

    assert (run.item_count, run.succeeded_count, run.failed_count) == (2, 1, 1)


def test_a_run_can_be_traced_back_to_its_import(session, organisation, data_source):
    import_batch = start_import_batch(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        original_filename="february-reviews.csv",
        row_count=2,
    )
    rows = save_feedback(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        texts=["slow service", "cold food"],
        import_batch_id=import_batch.id,
    )
    finish_import_batch(session, import_batch, imported_count=len(rows))

    run = save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(rows[0].id)),
        import_batch_id=import_batch.id,
        trigger="import",
    )
    session.commit()

    assert run.import_batch_id == import_batch.id
    assert run.trigger == "import"
    assert import_batch.status == "completed"
    assert import_batch.imported_count == 2
    assert session.get(Feedback, rows[0].id).import_batch_id == import_batch.id


# ---------------------------------------------------------------- result rows


def test_a_result_keeps_the_columns_a_dashboard_filters_on(session, organisation, feedback_rows):
    save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id)),
    )
    session.commit()

    result = session.scalar(select(AnalysisResult))
    assert result.sentiment_label == "negative"
    assert float(result.sentiment_confidence) == pytest.approx(0.9712, abs=1e-4)
    assert result.status == "ok"
    assert result.is_current is True
    # Read as a block, never filtered on - so JSONB rather than three more columns.
    assert result.sentiment_scores["negative"] == pytest.approx(0.9712)
    assert [c["name"] for c in result.candidate_categories] == [
        "Service and Wait Time Delays",
        "Product Quality Issues",
    ]


def test_a_category_is_resolved_by_name_against_the_taxonomy(
    session, organisation, feedback_rows, default_categories
):
    """The engine names a category; this layer turns that into a row id."""
    save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id)),
    )
    session.commit()

    result = session.scalar(select(AnalysisResult))
    category = session.get(Category, result.category_id)
    assert category.name == "Service and Wait Time Delays"
    assert category.organisation_id is None          # the shared default taxonomy
    assert float(result.category_confidence) == pytest.approx(0.81)


def test_an_organisations_own_category_wins_over_the_default_of_the_same_name(
    session, organisation, feedback_rows, default_categories
):
    own = Category(
        organisation_id=organisation.id,
        name="Service and Wait Time Delays",
        description="How this customer defines slow service.",
        source="custom",
    )
    session.add(own)
    session.flush()

    save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id)),
    )
    session.commit()

    assert session.scalar(select(AnalysisResult)).category_id == own.id


def test_an_unknown_category_name_stores_no_category_rather_than_inventing_one(
    session, organisation, feedback_rows
):
    save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id, category="Nothing Like This")),
    )
    session.commit()

    result = session.scalar(select(AnalysisResult))
    assert result.category_id is None
    assert session.scalar(select(Category).where(Category.name == "Nothing Like This")) is None


def test_an_unclassified_result_stores_no_category(session, organisation, feedback_rows):
    item = ItemAnalysis(
        feedback_id=str(feedback_rows[0].id),
        sentiment=SentimentPrediction(
            label="negative", confidence=0.88, scores={"negative": 0.88},
            model_version="distilbert-finetuned-final@abc123def456",
        ),
        category=CategoryMatch(
            category_id=None, name="Unclassified / Emerging Complaint", score=0.2
        ),
        is_unclassified=True,
    )

    save_batch_analysis(session, organisation_id=organisation.id, analysis=batch(item))
    session.commit()

    result = session.scalar(select(AnalysisResult))
    assert result.is_unclassified is True
    assert result.category_id is None


def test_a_skipped_categorisation_keeps_the_engines_reason(session, organisation, feedback_rows):
    """The sentiment gate from Milestone 3: positive feedback gets no complaint category,
    and the stored row says why rather than looking like a missing value."""
    item = ItemAnalysis(
        feedback_id=str(feedback_rows[0].id),
        sentiment=SentimentPrediction(
            label="positive", confidence=0.99, scores={"positive": 0.99},
            model_version="distilbert-finetuned-final@abc123def456",
        ),
        categorisation_skipped="sentiment 'positive' is not categorised (gate: negative, neutral)",
    )

    save_batch_analysis(session, organisation_id=organisation.id, analysis=batch(item))
    session.commit()

    result = session.scalar(select(AnalysisResult))
    assert result.category_id is None
    assert "positive" in result.categorisation_skipped


def test_a_failed_item_is_stored_with_its_error(session, organisation, feedback_rows):
    item = ItemAnalysis(feedback_id=str(feedback_rows[0].id)).with_error("categorisation failed: boom")

    save_batch_analysis(session, organisation_id=organisation.id, analysis=batch(item))
    session.commit()

    result = session.scalar(select(AnalysisResult))
    assert result.status == "failed"
    assert "categorisation failed" in result.error
    assert result.sentiment_label is None


def test_results_can_only_be_stored_for_saved_feedback(session, organisation):
    item = ItemAnalysis(feedback_id="row-7")   # a CSV line number, not a feedback row

    with pytest.raises(ValueError, match="not a feedback row id"):
        save_batch_analysis(session, organisation_id=organisation.id, analysis=batch(item))


# ---------------------------------------------------------------- re-analysis


def test_re_analysing_adds_a_result_and_supersedes_the_previous_one(
    session, organisation, feedback_rows
):
    """History is kept: a newer model produces a new row, and only the newest is current.
    Enforced by `uq_analysis_results_current_feedback`."""
    first = save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id)),
    )
    second = save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id, score=0.93)),
        trigger="reanalysis",
    )
    session.commit()

    results = session.scalars(
        select(AnalysisResult).order_by(AnalysisResult.created_at)
    ).all()
    assert len(results) == 2
    current = [r for r in results if r.is_current]
    assert len(current) == 1
    assert current[0].analysis_run_id == second.id
    assert first.id != second.id


# ---------------------------------------------------------------- insights


def test_an_insight_is_stored_alongside_its_result(session, organisation, feedback_rows):
    item = replace(
        negative_result(feedback_rows[0].id),
        insight=EngineInsight(
            summary="Customers wait too long to be seated.",
            keywords=("wait", "service", "queue"),
            business_insight="Add staff at peak times.",
            severity="High",
            priority="Urgent",
            department="Operations",
            executive_summary="Long waits are driving negative sentiment.",
            evidence_ids=("f-1", "f-2"),
            prompt_version="item-analysis-2026-09",
            model_version="openai/gpt-oss-20b",
        ),
    )

    save_batch_analysis(session, organisation_id=organisation.id, analysis=batch(item))
    session.commit()

    insight = session.scalar(select(Insight))
    result = session.scalar(select(AnalysisResult))
    assert insight.analysis_result_id == result.id
    assert insight.organisation_id == organisation.id
    assert insight.keywords == ["wait", "service", "queue"]
    assert insight.evidence_ids == ["f-1", "f-2"]
    assert insight.severity == "High"


def test_a_result_without_an_insight_stores_none(session, organisation, feedback_rows):
    """Most results have no insight: bulk analysis runs without an LLM, which is why the
    insight is a separate table rather than more columns."""
    save_batch_analysis(
        session,
        organisation_id=organisation.id,
        analysis=batch(negative_result(feedback_rows[0].id)),
    )
    session.commit()

    assert session.scalars(select(Insight)).all() == []


# ---------------------------------------------------------------- transactions


def test_content_hash_ignores_formatting_differences():
    assert content_hash("  slow  service ") == content_hash("slow service")
    assert content_hash("slow service") != content_hash("fast service")


def test_a_failed_unit_of_work_leaves_nothing_behind(
    session, database_url, db_engine, organisation, data_source
):
    """`session_scope` rolls back on any exception - a half-written import is worse than
    no import."""
    organisation_id, data_source_id = organisation.id, data_source.id
    # Committed first: session_scope opens its own connection, which cannot see rows that
    # another session has only flushed.
    session.commit()

    reset_engine()
    try:
        with pytest.raises(RuntimeError):
            with session_scope(database_url) as scoped:
                save_feedback(
                    scoped,
                    organisation_id=organisation_id,
                    data_source_id=data_source_id,
                    texts=["this should not survive"],
                )
                raise RuntimeError("the import failed halfway")
    finally:
        # The cached engine points at the test database; later tests must not inherit it.
        reset_engine()

    with Session(db_engine) as check:
        assert check.scalars(select(Feedback.text)).all() == []
