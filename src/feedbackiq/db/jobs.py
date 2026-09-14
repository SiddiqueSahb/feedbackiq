"""
The job queue, in PostgreSQL.

    create_job(...)            a caller records work to be done
    claim_next_job(...)        a worker takes one, atomically
    mark_succeeded / mark_failed

**Why PostgreSQL and not a broker.** The queue is transactional with the data it
describes: a job is created in the same transaction as the import it refers to, so there is
no window where an import exists and its analysis was never queued. Redis or RabbitMQ would
add a second system to run, back up and reason about, for work measured in jobs per minute.
`SELECT ... FOR UPDATE SKIP LOCKED` is what makes it safe for several workers at once, and
it is one line.

The lifecycle is deliberately small:

    queued ──▶ running ──┬──▶ succeeded
                         └──▶ failed ──▶ queued   (retry, while attempts remain)

Invalid transitions raise `JobStateError` rather than being quietly corrected: finishing a
job nobody claimed means the caller has a bug, and hiding it would make a stuck queue
impossible to diagnose.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from feedbackiq.core.exceptions import JobStateError
from feedbackiq.core.logging import get_logger
from feedbackiq.db.models import Job

log = get_logger("db.jobs")

# The only job kind that exists today: analyse everything one import brought in.
ANALYSE_IMPORT = "analyse_import"

# status -> the statuses it may become.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running"}),
    "running": frozenset({"succeeded", "failed"}),
    # A failed job may be re-queued while it has attempts left; nothing leaves "succeeded".
    "failed": frozenset({"queued"}),
    "succeeded": frozenset(),
}

TERMINAL_STATUSES = frozenset({"succeeded"})


def create_job(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    kind: str = ANALYSE_IMPORT,
    payload: Mapping[str, object] | None = None,
    max_attempts: int = 3,
) -> Job:
    """
    Queue work.

    `payload` carries identifiers only - never feedback text, which would copy customer
    data into a second place for no reason.
    """
    job = Job(
        organisation_id=organisation_id,
        kind=kind,
        payload=dict(payload or {}),
        status="queued",
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()

    log.info("Queued %s job %s for organisation %s", kind, job.id, organisation_id)

    return job


def get_job(session: Session, job_id: uuid.UUID, *, organisation_id: uuid.UUID | None = None) -> Job | None:
    """
    One job. Pass `organisation_id` whenever the caller acts for a tenant.

    It is optional only because the worker legitimately looks across organisations; every
    request-driven caller must supply it, or a job id becomes a way to read another
    tenant's work.
    """
    query = select(Job).where(Job.id == job_id)
    if organisation_id is not None:
        query = query.where(Job.organisation_id == organisation_id)

    return session.scalar(query)


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    kinds: Sequence[str] | None = None,
) -> Job | None:
    """
    Take the next due job and mark it running, or return None if there is nothing to do.

    `FOR UPDATE SKIP LOCKED` is the whole concurrency story: the row is locked for this
    transaction, and any other worker running the same query skips it instead of blocking
    or claiming it twice. The caller must commit (or roll back) to release the lock.
    """
    query = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= _now())
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if kinds:
        query = query.where(Job.kind.in_(tuple(kinds)))

    job = session.scalar(query)
    if job is None:
        return None

    _transition(job, "running")
    job.locked_by = worker_id
    job.locked_at = _now()
    job.attempts += 1
    session.flush()

    log.info("Worker %s claimed job %s (%s), attempt %d", worker_id, job.id, job.kind, job.attempts)

    return job


def mark_succeeded(session: Session, job: Job, *, result: Mapping[str, object] | None = None) -> Job:
    """Finish a job. `result` is merged into the payload as a small audit trail."""
    _transition(job, "succeeded")
    job.finished_at = _now()
    job.last_error = None
    job.locked_by = None

    if result:
        # Replaced rather than mutated: SQLAlchemy only notices a new object for JSONB.
        job.payload = {**dict(job.payload or {}), "result": dict(result)}

    session.flush()

    return job


def mark_failed(session: Session, job: Job, *, error: str, retry: bool = True) -> Job:
    """
    Record a failure, and re-queue it if attempts remain.

    The stored message is what a customer or an operator will read, so callers pass a
    summary - never a traceback (see services/analysis.py).
    """
    _transition(job, "failed")
    job.finished_at = _now()
    job.last_error = error[:2000]
    job.locked_by = None
    session.flush()

    if retry and job.attempts < job.max_attempts:
        _transition(job, "queued")
        job.finished_at = None
        session.flush()
        log.warning(
            "Job %s failed (attempt %d/%d), re-queued: %s",
            job.id, job.attempts, job.max_attempts, error,
        )
    else:
        log.error("Job %s failed permanently after %d attempt(s): %s", job.id, job.attempts, error)

    return job


def _transition(job: Job, to_status: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(job.status, frozenset())

    if to_status not in allowed:
        raise JobStateError(
            f"Job {job.id} cannot go from '{job.status}' to '{to_status}'. "
            f"Allowed from '{job.status}': {', '.join(sorted(allowed)) or 'nothing'}."
        )

    job.status = to_status


def _now() -> datetime:
    return datetime.now(timezone.utc)
