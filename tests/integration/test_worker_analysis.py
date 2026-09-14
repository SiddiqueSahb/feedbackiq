"""
Background analysis - feedbackiq.worker, feedbackiq.services.analysis

The end-to-end path this milestone exists to build:

    CSV ──▶ feedback ──▶ job ──▶ worker ──▶ engine.analyse_batch ──▶ analysis_results

The engine is a fake in every test here: the point is the plumbing, the job lifecycle and
what gets persisted, not the model. Real models are measured by the benchmark suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from feedbackiq import worker
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import AnalysisResult, AnalysisRun, Feedback, Job
from feedbackiq.db.session import reset_engine
from feedbackiq.engine.categorisation import CategorisationOutcome
from feedbackiq.engine.pipeline import AnalyticsEngine
from feedbackiq.engine.types import CategoryMatch, SentimentPrediction
from feedbackiq.services.analysis import analyse_import_batch
from feedbackiq.services.imports import import_csv

pytestmark = pytest.mark.integration

CSV = b"""text,external_id
Waited forty minutes for a table,R-1
The food was cold,R-2
Cold coffee as well,R-3
"""


class FakeSentiment:
    """Scores everything negative, and records what it was asked to score."""

    def __init__(self, *, fail_texts: set[str] | None = None):
        self.fail_texts = fail_texts or set()
        self.seen: list[list[str]] = []

    def predict_batch(self, texts):
        self.seen.append(list(texts))

        if any(text in self.fail_texts for text in texts):
            raise RuntimeError("model unavailable for this chunk")

        return [
            SentimentPrediction(
                label="negative", confidence=0.91,
                scores={"negative": 0.91, "neutral": 0.06, "positive": 0.03},
                model_version="fake-sentiment-1",
            )
            for _ in texts
        ]


class FakeCategoriser:
    def categorise_batch(self, texts, categories):
        match = CategoryMatch(
            category_id=categories[0].id, name=categories[0].name, score=0.77
        )
        return [CategorisationOutcome(top=match, candidates=(match,)) for _ in texts]


def fake_engine(**kwargs) -> AnalyticsEngine:
    from feedbackiq.engine.defaults import default_categories

    return AnalyticsEngine(
        sentiment=kwargs.pop("sentiment", FakeSentiment()),
        categoriser=FakeCategoriser(),
        default_categories=default_categories(),
        **kwargs,
    )


@pytest.fixture()
def imported(session, organisation, default_categories):
    """An import whose rows are committed and whose job is waiting."""
    outcome = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    return outcome


@pytest.fixture()
def worker_engine(monkeypatch):
    """Point the worker's default engine at a fake, so no model is ever loaded."""
    engine = fake_engine()
    monkeypatch.setattr("feedbackiq.services.analysis.default_engine", lambda: engine)
    reset_engine()

    yield engine

    reset_engine()


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- the analysis service


def test_stored_feedback_reaches_the_engine_and_results_are_persisted(
    session, organisation, imported
):
    engine = fake_engine()

    summary = analyse_import_batch(
        session,
        organisation_id=organisation.id,
        import_batch_id=imported.batch.id,
        engine=engine,
    )
    session.commit()

    assert (summary.analysed, summary.succeeded, summary.failed) == (3, 3, 0)
    assert count(session, AnalysisResult) == 3

    # The engine saw the stored text, not the file.
    assert sorted(engine.sentiment.seen[0]) == sorted(
        ["Waited forty minutes for a table", "The food was cold", "Cold coffee as well"]
    )

    result = session.scalars(select(AnalysisResult)).first()
    assert result.organisation_id == organisation.id
    assert result.sentiment_label == "negative"
    assert result.category_id is not None        # resolved by stable key
    assert result.is_current is True


def test_the_run_records_how_it_was_produced(session, organisation, imported):
    analyse_import_batch(
        session, organisation_id=organisation.id,
        import_batch_id=imported.batch.id, engine=fake_engine(),
    )
    session.commit()

    run = session.scalars(select(AnalysisRun)).first()
    assert run.import_batch_id == imported.batch.id
    assert run.trigger == "import"
    assert run.status == "completed"
    assert run.model_versions["taxonomy_source"] == "default"
    assert run.model_versions["taxonomy_version"]


def test_analysis_is_chunked_so_memory_does_not_scale_with_the_upload(
    session, organisation, imported
):
    engine = fake_engine()

    summary = analyse_import_batch(
        session, organisation_id=organisation.id,
        import_batch_id=imported.batch.id, engine=engine, batch_size=2,
    )
    session.commit()

    assert summary.runs == 2                      # 3 rows, 2 per chunk
    assert [len(chunk) for chunk in engine.sentiment.seen] == [2, 1]
    assert count(session, AnalysisRun) == 2
    assert count(session, AnalysisResult) == 3


def test_an_import_with_nothing_to_analyse_is_not_an_error(session, organisation):
    import uuid

    summary = analyse_import_batch(
        session, organisation_id=organisation.id,
        import_batch_id=uuid.uuid4(), engine=fake_engine(),
    )

    assert (summary.analysed, summary.runs) == (0, 0)


def test_only_this_organisations_feedback_is_analysed(
    session, organisation, other_organisation, default_categories
):
    ours = import_csv(session, raw=CSV, organisation_id=organisation.id)
    import_csv(session, raw=CSV, organisation_id=other_organisation.id)
    session.commit()

    analyse_import_batch(
        session, organisation_id=organisation.id,
        import_batch_id=ours.batch.id, engine=fake_engine(),
    )
    session.commit()

    results = session.scalars(select(AnalysisResult)).all()
    assert len(results) == 3
    assert {r.organisation_id for r in results} == {organisation.id}


# ---------------------------------------------------------------- partial failures


def test_a_failing_item_is_stored_as_failed_beside_its_successful_neighbours(
    session, organisation, imported
):
    """The engine isolates failures per chunk; successful work is kept, not discarded."""
    engine = fake_engine(sentiment=FakeSentiment(fail_texts={"The food was cold"}))

    summary = analyse_import_batch(
        session, organisation_id=organisation.id,
        import_batch_id=imported.batch.id, engine=engine, batch_size=1,
    )
    session.commit()

    assert (summary.analysed, summary.succeeded, summary.failed) == (3, 2, 1)
    assert not summary.all_failed

    statuses = sorted(r.status for r in session.scalars(select(AnalysisResult)).all())
    assert statuses == ["failed", "ok", "ok"]

    failed = session.scalar(select(AnalysisResult).where(AnalysisResult.status == "failed"))
    assert "sentiment failed" in failed.error
    assert failed.sentiment_label is None


def test_a_batch_where_everything_fails_is_reported_as_such(session, organisation, imported):
    engine = fake_engine(sentiment=FakeSentiment(fail_texts={"The food was cold"}))

    summary = analyse_import_batch(
        session, organisation_id=organisation.id,
        import_batch_id=imported.batch.id, engine=engine,   # one chunk, so all three fail
    )
    session.commit()

    assert summary.all_failed is True
    assert summary.succeeded == 0
    assert count(session, AnalysisResult) == 3          # the failures are still recorded


# ---------------------------------------------------------------- the worker


def test_the_worker_runs_a_queued_job_end_to_end(
    session, organisation, imported, worker_engine, database_url
):
    assert worker.process_one(database_url=database_url) is True

    session.expire_all()
    job = session.get(Job, imported.job.id)
    assert job.status == "succeeded"
    assert job.attempts == 1
    assert job.finished_at is not None
    assert job.payload["result"]["succeeded"] == 3

    assert count(session, AnalysisResult) == 3


def test_the_worker_reports_an_empty_queue(worker_engine, database_url):
    assert worker.process_one(database_url=database_url) is False


def test_draining_processes_every_waiting_job(
    session, organisation, default_categories, worker_engine, database_url
):
    first = import_csv(session, raw=CSV, organisation_id=organisation.id)
    second = import_csv(
        session, raw=b"text\nsomething else entirely\n", organisation_id=organisation.id
    )
    session.commit()

    assert worker.drain(database_url=database_url) == 2

    session.expire_all()
    assert session.get(Job, first.job.id).status == "succeeded"
    assert session.get(Job, second.job.id).status == "succeeded"
    assert count(session, AnalysisResult) == 4


def test_a_failing_job_keeps_its_attempt_count_and_is_retried(
    session, organisation, imported, database_url, monkeypatch
):
    """
    The bug this guards against: claiming and working in one transaction meant a rollback
    also undid the claim, so `attempts` never advanced and a poisonous job retried forever.
    """
    engine = fake_engine(sentiment=FakeSentiment(fail_texts={"The food was cold"}))
    monkeypatch.setattr("feedbackiq.services.analysis.default_engine", lambda: engine)
    reset_engine()

    try:
        assert worker.process_one(database_url=database_url) is True

        session.expire_all()
        job = session.get(Job, imported.job.id)
        assert job.attempts == 1                  # counted, not lost
        assert job.status == "queued"             # retried while attempts remain
        assert "all 3 feedback items failed" in job.last_error
    finally:
        reset_engine()


def test_a_job_that_keeps_failing_ends_as_failed_with_a_safe_message(
    session, organisation, imported, database_url, monkeypatch
):
    engine = fake_engine(sentiment=FakeSentiment(fail_texts={"The food was cold"}))
    monkeypatch.setattr("feedbackiq.services.analysis.default_engine", lambda: engine)
    reset_engine()

    try:
        for _ in range(3):
            worker.process_one(database_url=database_url)

        session.expire_all()
        job = session.get(Job, imported.job.id)
        assert job.status == "failed"
        assert job.attempts == 3
        # A summary, not a traceback, and no file paths.
        assert "Traceback" not in job.last_error
        assert "/Users/" not in job.last_error
    finally:
        reset_engine()


def test_an_unknown_job_kind_fails_loudly(session, organisation, database_url, worker_engine):
    created = job_queue.create_job(
        session, organisation_id=organisation.id, kind="not_a_real_kind", payload={}
    )
    session.commit()

    worker.process_one(database_url=database_url)

    session.expire_all()
    job = session.get(Job, created.id)
    assert "unknown job kind" in job.last_error


def test_re_running_an_analysis_supersedes_rather_than_duplicates(
    session, organisation, imported, worker_engine, database_url
):
    """Re-running a job must be safe: results supersede, so a retry cannot double-count."""
    worker.process_one(database_url=database_url)

    job_queue.create_job(
        session,
        organisation_id=organisation.id,
        payload={"import_batch_id": str(imported.batch.id)},
    )
    session.commit()

    worker.process_one(database_url=database_url)
    session.expire_all()

    results = session.scalars(select(AnalysisResult)).all()
    assert len(results) == 6                        # history kept
    assert sum(1 for r in results if r.is_current) == 3    # exactly one current per item
    assert count(session, Feedback) == 3            # and no duplicated feedback
