"""
CSV import - feedbackiq.services.imports

What these protect, against a real PostgreSQL:

  * an upload produces one import batch whose counts explain what happened
  * valid rows become organisation-owned feedback, rejected rows do not
  * re-uploading is safe: the same file creates nothing, and a partly-overlapping file
    adds only what is new
  * analysis is queued, never run in the caller's thread
  * ownership is explicit and never inferred from the file
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from feedbackiq.core.exceptions import IngestionError
from feedbackiq.db.models import Feedback, ImportBatch, Job
from feedbackiq.db.persistence import create_organisation
from feedbackiq.services.imports import import_csv

pytestmark = pytest.mark.integration

CSV = b"""text,external_id,rating,created_at
Waited forty minutes for a table,R-1,1,2026-02-01
The food was cold,R-2,2,2026-02-02
,R-3,3,2026-02-03
Great service though,R-4,5,2026-02-04
"""


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- the batch


def test_an_upload_creates_one_batch_with_counts_that_explain_it(session, organisation):
    outcome = import_csv(session, raw=CSV, filename="february.csv", organisation_id=organisation.id)
    session.commit()

    batch = session.get(ImportBatch, outcome.batch.id)
    assert batch.organisation_id == organisation.id
    assert batch.original_filename == "february.csv"
    assert batch.status == "completed"
    assert (batch.row_count, batch.imported_count, batch.failed_count) == (4, 3, 1)
    assert batch.completed_at is not None
    assert batch.source_hash and len(batch.source_hash) == 64


def test_the_rejected_row_is_reported_with_its_line_number(session, organisation):
    outcome = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    summary = session.get(ImportBatch, outcome.batch.id).error_summary
    assert summary["rows"] == [{"row": 4, "field": "text", "message": "Feedback text is empty."}]
    assert summary["row_errors_total"] == 1


def test_valid_rows_become_organisation_owned_feedback(session, organisation):
    outcome = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    rows = session.scalars(select(Feedback).order_by(Feedback.external_id)).all()
    assert [row.text for row in rows] == [
        "Waited forty minutes for a table", "The food was cold", "Great service though",
    ]
    assert all(row.organisation_id == organisation.id for row in rows)
    assert all(row.import_batch_id == outcome.batch.id for row in rows)
    assert all(row.content_hash for row in rows)
    # The optional columns survived.
    assert float(rows[0].rating) == 1.0
    assert rows[0].feedback_at is not None
    assert rows[0].meta["source_row"] == 2


def test_a_file_with_nothing_usable_fails_the_batch_rather_than_pretending(session, organisation):
    outcome = import_csv(session, raw=b"text\n   \n", organisation_id=organisation.id)
    session.commit()

    assert outcome.stored == 0
    assert session.get(ImportBatch, outcome.batch.id).status == "failed"


def test_an_unusable_file_stores_no_batch_at_all(session, organisation):
    """A whole-file problem is not an import that failed; it is an import that never began."""
    with pytest.raises(IngestionError):
        import_csv(session, raw=b"name,rating\nAcme,5\n", organisation_id=organisation.id)

    assert count(session, ImportBatch) == 0
    assert count(session, Feedback) == 0


def test_an_oversized_upload_is_refused(session, organisation, monkeypatch):
    from feedbackiq.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 32)

    with pytest.raises(IngestionError, match="limit"):
        import_csv(session, raw=CSV, organisation_id=organisation.id)

    assert count(session, ImportBatch) == 0


# ---------------------------------------------------------------- analysis is queued


def test_analysis_is_queued_and_not_run(session, organisation):
    outcome = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    job = session.get(Job, outcome.job.id)
    assert job.kind == "analyse_import"
    assert job.status == "queued"          # the worker runs it, not the caller
    assert job.organisation_id == organisation.id
    assert job.payload["import_batch_id"] == str(outcome.batch.id)
    assert job.attempts == 0

    # Nothing was analysed during the import itself.
    from feedbackiq.db.models import AnalysisResult
    assert count(session, AnalysisResult) == 0


def test_no_job_is_queued_when_nothing_was_stored(session, organisation):
    outcome = import_csv(session, raw=b"text\n  \n", organisation_id=organisation.id)
    session.commit()

    assert outcome.job is None
    assert count(session, Job) == 0


def test_queueing_can_be_declined(session, organisation):
    outcome = import_csv(session, raw=CSV, organisation_id=organisation.id, queue_analysis=False)
    session.commit()

    assert outcome.job is None
    assert outcome.stored == 3


# ---------------------------------------------------------------- repeat safety


def test_the_same_file_twice_creates_nothing_the_second_time(session, organisation):
    """Batch-level idempotency, by SHA-256 of the uploaded bytes."""
    first = import_csv(session, raw=CSV, filename="february.csv", organisation_id=organisation.id)
    session.commit()

    second = import_csv(session, raw=CSV, filename="february-again.csv", organisation_id=organisation.id)
    session.commit()

    assert second.is_duplicate_upload is True
    assert second.batch.id == first.batch.id
    assert second.stored == 0
    assert second.job is None
    assert count(session, ImportBatch) == 1
    assert count(session, Feedback) == 3
    assert count(session, Job) == 1


def test_a_partly_overlapping_file_adds_only_the_new_rows(session, organisation):
    """Row-level identity: external_id first, then the content hash."""
    import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    extended = CSV + b"A brand new complaint,R-9,2,2026-02-06\n"
    second = import_csv(session, raw=extended, organisation_id=organisation.id)
    session.commit()

    assert second.is_duplicate_upload is False
    assert second.stored == 1
    assert second.duplicates_in_database == 3
    assert count(session, Feedback) == 4


def test_the_same_text_reformatted_is_still_a_duplicate(session, organisation):
    """
    No external ids in play, so the normalised content hash identifies the row: leading and
    trailing whitespace does not make a complaint new.

    Note what this does *not* claim: the hash is case-sensitive, so "The app crashed" and
    "the app crashed" are two rows. Recorded as a known limitation rather than hidden -
    lower-casing every hash would also merge genuinely distinct feedback.
    """
    import_csv(session, raw=b"text\nthe app keeps crashing\n", organisation_id=organisation.id)
    session.commit()

    second = import_csv(
        session, raw=b"text\n  the app keeps crashing  \n", organisation_id=organisation.id
    )
    session.commit()

    assert second.stored == 0
    assert second.duplicates_in_database == 1
    assert count(session, Feedback) == 1


def test_two_organisations_uploading_the_same_file_do_not_collide(
    session, organisation, other_organisation
):
    ours = import_csv(session, raw=CSV, organisation_id=organisation.id)
    theirs = import_csv(session, raw=CSV, organisation_id=other_organisation.id)
    session.commit()

    assert ours.stored == theirs.stored == 3
    assert ours.batch.id != theirs.batch.id
    assert count(session, Feedback) == 6

    # Each organisation sees only its own.
    for org in (organisation, other_organisation):
        rows = session.scalars(
            select(Feedback).where(Feedback.organisation_id == org.id)
        ).all()
        assert len(rows) == 3


# ---------------------------------------------------------------- ownership


def test_ownership_is_never_inferred_from_the_file(session, organisation):
    """The CSV cannot name its own owner: columns that look like ownership are ignored."""
    hostile = (
        b"text,organisation_id,organisation\n"
        b"a complaint,00000000-0000-0000-0000-000000000000,Someone Else\n"
    )

    import_csv(session, raw=hostile, organisation_id=organisation.id)
    session.commit()

    [row] = session.scalars(select(Feedback)).all()
    assert row.organisation_id == organisation.id


def test_an_unknown_organisation_is_refused(session):
    import uuid

    with pytest.raises(IngestionError, match="No organisation"):
        import_csv(session, raw=CSV, organisation_id=uuid.uuid4())


def test_without_an_explicit_organisation_the_seeded_development_one_is_used(session):
    """The documented stand-in until authentication exists."""
    from feedbackiq.core.config import settings

    dev = create_organisation(session, name="Development", slug=settings.DEV_ORGANISATION_SLUG)
    session.flush()

    outcome = import_csv(session, raw=CSV)
    session.commit()

    assert outcome.batch.organisation_id == dev.id


def test_a_missing_development_organisation_is_an_actionable_error(session):
    with pytest.raises(IngestionError, match="db.seed"):
        import_csv(session, raw=CSV)


# ---------------------------------------------------------------- file handling


@pytest.mark.parametrize(
    "given, stored",
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\feedback.csv", "feedback.csv"),
        ("report<script>.csv", "report_script_.csv"),
        ("", None),
    ],
)
def test_filenames_are_sanitised(session, organisation, given, stored):
    outcome = import_csv(
        session, raw=b"text\nsomething\n", filename=given, organisation_id=organisation.id
    )
    session.commit()

    assert session.get(ImportBatch, outcome.batch.id).original_filename == stored


def test_the_uploaded_file_itself_is_not_stored(session, organisation):
    """The database is the source of truth for imported feedback; the file is not kept."""
    outcome = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    assert session.get(ImportBatch, outcome.batch.id).storage_reference is None
