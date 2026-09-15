"""
Recovering jobs a dead worker left behind.

Milestone 5B's worker commits the job claim before doing the work, so a worker killed
mid-job leaves its row `running` with nobody working on it. Nothing reclaims it, because
`claim_next_job` only ever looks at `queued` rows - recorded as limitation 2 of that
milestone, and fixed here.

    running  +  locked longer than the threshold  ──┬──▶ queued   (attempts remain)
                                                    └──▶ failed   (attempts spent)

    reclaim_stale_jobs(session)

The threshold is a plain timestamp comparison against `jobs.locked_at`, which the worker
already sets when it claims. No heartbeat table, no lease renewal, no Redis, no scheduler:
a stale job is one that has been held longer than any real job takes, and the recovery is
the same transition the worker itself would make.

**Not run automatically.** A worker calls it on startup (so restarting a crashed worker
recovers its own abandoned work) and it can be run by hand:

    python -m feedbackiq.worker --reclaim

Deliberately *not* on a timer inside the worker loop: a periodic sweep in every process is
how two workers end up reclaiming the same job, and there is no scheduler here to own it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import Job

log = get_logger("service.maintenance")


@dataclass(frozen=True)
class ReclaimOutcome:
    """What a sweep did. `requeued + abandoned == len(job_ids)`."""

    requeued: int = 0
    abandoned: int = 0
    job_ids: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.requeued + self.abandoned

    def as_dict(self) -> dict:
        return {
            "reclaimed": self.total,
            "requeued": self.requeued,
            "abandoned": self.abandoned,
        }


def find_stale_jobs(
    session: Session, *, older_than_minutes: int | None = None
) -> list[Job]:
    """
    Jobs still marked `running` whose claim is older than the threshold.

    Locked with `FOR UPDATE SKIP LOCKED` for the same reason the worker uses it: if another
    process is already reclaiming a row, skip it rather than fight over it.
    """
    minutes = older_than_minutes or settings.STALE_JOB_MINUTES
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    return list(
        session.scalars(
            select(Job)
            .where(Job.status == "running", Job.locked_at < cutoff)
            .order_by(Job.locked_at)
            .with_for_update(skip_locked=True)
        ).all()
    )


def reclaim_stale_jobs(
    session: Session, *, older_than_minutes: int | None = None
) -> ReclaimOutcome:
    """
    Requeue (or abandon) every job a dead worker left running.

    A reclaimed job goes through `mark_failed`, so it follows exactly the same rules as a
    job that failed honestly: re-queued while `attempts < max_attempts`, and left `failed`
    once they are spent. That is what stops a job that kills its worker every time from
    being retried forever.
    """
    minutes = older_than_minutes or settings.STALE_JOB_MINUTES
    stale = find_stale_jobs(session, older_than_minutes=minutes)

    requeued = abandoned = 0
    reclaimed: list[str] = []

    for job in stale:
        held_by = job.locked_by or "an unknown worker"
        job_queue.mark_failed(
            session,
            job,
            error=(
                f"Reclaimed after being held by {held_by} for more than {minutes} "
                "minutes without finishing; the worker is presumed to have stopped."
            ),
        )

        if job.status == "queued":
            requeued += 1
        else:
            abandoned += 1

        reclaimed.append(str(job.id))

    if reclaimed:
        log.warning(
            "Reclaimed %d stale job(s): %d requeued, %d abandoned",
            len(reclaimed), requeued, abandoned,
        )

    return ReclaimOutcome(requeued=requeued, abandoned=abandoned, job_ids=tuple(reclaimed))
