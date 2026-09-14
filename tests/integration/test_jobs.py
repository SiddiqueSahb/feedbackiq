"""
The job queue - feedbackiq.db.jobs

    queued ──▶ running ──┬──▶ succeeded
                         └──▶ failed ──▶ queued   (while attempts remain)

Two things only a real database can demonstrate, and both are tested here: that
`FOR UPDATE SKIP LOCKED` lets two workers share a queue without ever running one job twice,
and that an invalid transition raises instead of quietly correcting itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from feedbackiq.core.exceptions import JobStateError
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import Job

pytestmark = pytest.mark.integration


@pytest.fixture()
def job(session, organisation):
    created = job_queue.create_job(
        session, organisation_id=organisation.id, payload={"import_batch_id": "x"}
    )
    session.commit()

    return created


# ---------------------------------------------------------------- creation


def test_a_new_job_is_queued_and_unclaimed(job):
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.locked_by is None
    assert job.finished_at is None
    assert job.max_attempts == 3


def test_the_payload_carries_identifiers_not_feedback_text(session, organisation):
    created = job_queue.create_job(
        session, organisation_id=organisation.id, payload={"import_batch_id": "abc"}
    )
    session.commit()

    assert set(created.payload) == {"import_batch_id"}


# ---------------------------------------------------------------- the lifecycle


def test_claiming_moves_a_job_to_running_and_counts_the_attempt(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="worker-1")
    session.commit()

    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.locked_by == "worker-1"
    assert claimed.locked_at is not None


def test_a_running_job_can_succeed(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="w")
    job_queue.mark_succeeded(session, claimed, result={"analysed": 3})
    session.commit()

    stored = session.get(Job, job.id)
    assert stored.status == "succeeded"
    assert stored.finished_at is not None
    assert stored.last_error is None
    assert stored.payload["result"] == {"analysed": 3}


def test_a_running_job_can_fail_and_is_requeued_while_attempts_remain(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="w")
    job_queue.mark_failed(session, claimed, error="RuntimeError: model unavailable")
    session.commit()

    stored = session.get(Job, job.id)
    assert stored.status == "queued"            # retried, not abandoned
    assert stored.attempts == 1
    assert stored.last_error == "RuntimeError: model unavailable"
    assert stored.finished_at is None


def test_a_job_stops_being_retried_once_its_attempts_are_spent(session, job):
    for expected_attempt in (1, 2, 3):
        claimed = job_queue.claim_next_job(session, worker_id="w")
        assert claimed is not None, f"attempt {expected_attempt} was not claimable"
        job_queue.mark_failed(session, claimed, error="boom")
        session.commit()

    stored = session.get(Job, job.id)
    assert stored.status == "failed"
    assert stored.attempts == 3
    assert job_queue.claim_next_job(session, worker_id="w") is None


def test_retrying_can_be_declined(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="w")
    job_queue.mark_failed(session, claimed, error="unknown job kind", retry=False)
    session.commit()

    assert session.get(Job, job.id).status == "failed"


def test_a_stored_error_is_a_summary_not_a_traceback(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="w")
    job_queue.mark_failed(session, claimed, error="x" * 5000)
    session.commit()

    assert len(session.get(Job, job.id).last_error) <= 2000


# ---------------------------------------------------------------- invalid transitions


@pytest.mark.parametrize(
    "action",
    ["succeed", "fail"],
)
def test_finishing_a_job_nobody_claimed_is_rejected(session, job, action):
    """A bug in the caller, so it raises rather than being silently corrected."""
    with pytest.raises(JobStateError) as error:
        if action == "succeed":
            job_queue.mark_succeeded(session, job)
        else:
            job_queue.mark_failed(session, job, error="never ran")

    assert "queued" in str(error.value)


def test_a_finished_job_cannot_be_finished_again(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="w")
    job_queue.mark_succeeded(session, claimed)
    session.commit()

    with pytest.raises(JobStateError):
        job_queue.mark_succeeded(session, claimed)


def test_a_succeeded_job_is_never_reclaimed(session, job):
    claimed = job_queue.claim_next_job(session, worker_id="w")
    job_queue.mark_succeeded(session, claimed)
    session.commit()

    assert job_queue.claim_next_job(session, worker_id="w") is None


def test_a_running_job_is_not_claimable_by_anyone_else(session, job):
    job_queue.claim_next_job(session, worker_id="worker-1")
    session.commit()

    assert job_queue.claim_next_job(session, worker_id="worker-2") is None


# ---------------------------------------------------------------- concurrency


def test_two_workers_never_claim_the_same_job(db_engine, session, organisation):
    """
    `FOR UPDATE SKIP LOCKED` in action: the second worker skips the locked row instead of
    blocking on it or taking it twice. This is the whole reason the queue can live in
    PostgreSQL rather than in a broker.
    """
    job_queue.create_job(session, organisation_id=organisation.id, payload={"n": 1})
    session.commit()

    factory = sessionmaker(bind=db_engine, expire_on_commit=False, future=True)
    first, second = factory(), factory()

    try:
        claimed_by_first = job_queue.claim_next_job(first, worker_id="worker-1")
        # Still inside worker-1's open transaction, so the row is locked.
        claimed_by_second = job_queue.claim_next_job(second, worker_id="worker-2")

        assert claimed_by_first is not None
        assert claimed_by_second is None

        first.commit()
    finally:
        first.rollback(); first.close()
        second.rollback(); second.close()


def test_two_workers_share_a_queue_of_two_jobs(db_engine, session, organisation):
    for n in (1, 2):
        job_queue.create_job(session, organisation_id=organisation.id, payload={"n": n})
    session.commit()

    factory = sessionmaker(bind=db_engine, expire_on_commit=False, future=True)
    first, second = factory(), factory()

    try:
        one = job_queue.claim_next_job(first, worker_id="worker-1")
        two = job_queue.claim_next_job(second, worker_id="worker-2")

        assert one is not None and two is not None
        assert one.id != two.id          # a job each, no overlap

        first.commit(); second.commit()
    finally:
        first.close(); second.close()

    assert session.scalar(
        select(func.count()).select_from(Job).where(Job.status == "running")
    ) == 2


# ---------------------------------------------------------------- tenant scoping


def test_reading_a_job_can_be_scoped_to_its_organisation(session, organisation, other_organisation, job):
    """Without the filter, a job id would be a way to read another tenant's work."""
    assert job_queue.get_job(session, job.id, organisation_id=organisation.id) is not None
    assert job_queue.get_job(session, job.id, organisation_id=other_organisation.id) is None
