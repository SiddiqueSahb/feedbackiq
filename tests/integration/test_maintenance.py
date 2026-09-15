"""
Stale job recovery - feedbackiq.services.maintenance

The Milestone 5B limitation this closes: the worker commits its claim before doing the work,
so a worker killed mid-job leaves the row `running` and nothing ever reclaims it -
`claim_next_job` only looks at `queued`.

    running + locked longer than the threshold ──┬──▶ queued   (attempts remain)
                                                └──▶ failed   (attempts spent)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import Job
from feedbackiq.services.maintenance import find_stale_jobs, reclaim_stale_jobs

pytestmark = pytest.mark.integration


def claimed_job(session, organisation, *, held_minutes: int = 0, attempts_used: int = 1) -> Job:
    """A job in the state a crashed worker leaves behind."""
    job = job_queue.create_job(
        session, organisation_id=organisation.id, payload={"import_batch_id": "x"}
    )
    session.flush()

    job.status = "running"
    job.locked_by = "worker-that-died:123"
    job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=held_minutes)
    job.attempts = attempts_used
    session.commit()

    return job


# ---------------------------------------------------------------- detection


def test_a_job_held_longer_than_the_threshold_is_stale(session, organisation):
    claimed_job(session, organisation, held_minutes=90)

    assert len(find_stale_jobs(session, older_than_minutes=30)) == 1


def test_a_job_claimed_recently_is_left_alone(session, organisation):
    """The threshold protects work in progress: reclaiming a running job would analyse the
    same import twice."""
    claimed_job(session, organisation, held_minutes=2)

    assert find_stale_jobs(session, older_than_minutes=30) == []


@pytest.mark.parametrize("status", ["queued", "succeeded", "failed"])
def test_only_running_jobs_are_candidates(session, organisation, status):
    job = claimed_job(session, organisation, held_minutes=90)
    job.status = status
    session.commit()

    assert find_stale_jobs(session, older_than_minutes=30) == []


# ---------------------------------------------------------------- recovery


def test_a_stale_job_is_requeued_while_attempts_remain(session, organisation):
    job = claimed_job(session, organisation, held_minutes=90, attempts_used=1)

    outcome = reclaim_stale_jobs(session, older_than_minutes=30)
    session.commit()

    session.expire_all()
    recovered = session.get(Job, job.id)
    assert outcome.requeued == 1
    assert outcome.abandoned == 0
    assert recovered.status == "queued"
    assert recovered.locked_by is None
    assert "presumed to have stopped" in recovered.last_error
    # The attempt the dead worker used is not given back, which is what bounds retries.
    assert recovered.attempts == 1


def test_a_requeued_job_can_be_claimed_again(session, organisation):
    claimed_job(session, organisation, held_minutes=90)

    reclaim_stale_jobs(session, older_than_minutes=30)
    session.commit()

    claimed = job_queue.claim_next_job(session, worker_id="worker-2")
    session.commit()

    assert claimed is not None
    assert claimed.attempts == 2


def test_a_stale_job_with_no_attempts_left_is_abandoned(session, organisation):
    """A job that kills its worker every time must stop, or it recycles forever."""
    job = claimed_job(session, organisation, held_minutes=90, attempts_used=3)

    outcome = reclaim_stale_jobs(session, older_than_minutes=30)
    session.commit()

    session.expire_all()
    recovered = session.get(Job, job.id)
    assert (outcome.requeued, outcome.abandoned) == (0, 1)
    assert recovered.status == "failed"
    assert job_queue.claim_next_job(session, worker_id="worker-3") is None


def test_reclaiming_an_empty_queue_does_nothing(session, organisation):
    outcome = reclaim_stale_jobs(session, older_than_minutes=30)

    assert outcome.total == 0
    assert outcome.as_dict() == {"reclaimed": 0, "requeued": 0, "abandoned": 0}


def test_several_stale_jobs_are_all_recovered(session, organisation):
    for _ in range(3):
        claimed_job(session, organisation, held_minutes=90)

    outcome = reclaim_stale_jobs(session, older_than_minutes=30)
    session.commit()

    assert outcome.total == 3
    assert len(outcome.job_ids) == 3


def test_the_recorded_error_names_the_worker_and_the_threshold(session, organisation):
    """An operator reading this needs to know which process stopped and why it was taken."""
    job = claimed_job(session, organisation, held_minutes=90)

    reclaim_stale_jobs(session, older_than_minutes=30)
    session.commit()

    session.expire_all()
    message = session.get(Job, job.id).last_error
    assert "worker-that-died:123" in message
    assert "30" in message
    assert "Traceback" not in message


# ---------------------------------------------------------------- the worker entry point


def test_the_worker_reclaims_on_demand(session, organisation, database_url, monkeypatch):
    from feedbackiq import worker
    from feedbackiq.core.config import settings
    from feedbackiq.db.session import reset_engine

    monkeypatch.setattr(settings, "STALE_JOB_MINUTES", 30)
    job = claimed_job(session, organisation, held_minutes=90)

    reset_engine()
    try:
        result = worker.reclaim(database_url=database_url)
    finally:
        reset_engine()

    session.expire_all()
    assert result == {"reclaimed": 1, "requeued": 1, "abandoned": 0}
    assert session.get(Job, job.id).status == "queued"


def test_tenant_scoping_is_not_needed_to_reclaim_but_ownership_is_preserved(
    session, organisation, other_organisation
):
    """The sweep is an operator action across organisations, but it must not move a job
    between them."""
    mine = claimed_job(session, organisation, held_minutes=90)
    theirs = claimed_job(session, other_organisation, held_minutes=90)

    reclaim_stale_jobs(session, older_than_minutes=30)
    session.commit()

    session.expire_all()
    assert session.get(Job, mine.id).organisation_id == organisation.id
    assert session.get(Job, theirs.id).organisation_id == other_organisation.id
