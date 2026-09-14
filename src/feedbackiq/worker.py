"""
The background worker.

    python -m feedbackiq.worker              run until stopped
    python -m feedbackiq.worker --once       claim at most one job, then exit
    python -m feedbackiq.worker --drain      keep going until the queue is empty

One process, one job at a time, in a loop:

    claim a queued job  ──▶  run it  ──▶  mark it succeeded or failed  ──▶  repeat
    (FOR UPDATE SKIP LOCKED)

Running several of these is safe: `claim_next_job` locks the row it takes and other workers
skip it. That is the entire concurrency design, and it is deliberately the smallest thing
that works - no broker, no scheduler, no distributed coordination. `run_job` is the seam to
replace if a real queue is ever justified; nothing above it would change.

Each job runs in its own transaction. A crash mid-job leaves the row `running`; the
`attempts` counter and `max_attempts` bound how often a poisonous job is retried. Re-running
`analyse_import_batch` is safe because results supersede rather than duplicate (the
`is_current` flag from Milestone 4).
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import time
import uuid
from types import FrameType

from sqlalchemy.orm import Session

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import Job
from feedbackiq.db.session import session_scope
from feedbackiq.services.analysis import analyse_import_batch

log = get_logger("worker")

_stopping = False


def worker_id() -> str:
    """Who holds a job: host and pid, enough to find the process that stalled."""
    return f"{socket.gethostname()}:{os.getpid()}"


def run_job(session: Session, job: Job) -> dict:
    """
    Do the work one job describes, and return a small result for its audit trail.

    Raises on failure; the caller decides what that means for the job's status. Unknown job
    kinds raise too - silently ignoring one would leave it queued forever.
    """
    if job.kind == job_queue.ANALYSE_IMPORT:
        payload = dict(job.payload or {})
        import_batch_id = payload.get("import_batch_id")

        if not import_batch_id:
            raise ValueError("payload has no import_batch_id")

        summary = analyse_import_batch(
            session,
            organisation_id=job.organisation_id,
            import_batch_id=uuid.UUID(str(import_batch_id)),
        )

        if summary.all_failed:
            # Every item failed: that is a failed job, not a quiet success.
            raise RuntimeError(
                f"all {summary.analysed} feedback items failed analysis"
            )

        return summary.as_dict()

    raise ValueError(f"unknown job kind '{job.kind}'")


def process_one(*, database_url: str | None = None) -> bool:
    """
    Claim and run at most one job. Returns False when the queue is empty.

    **Three transactions, not one, and the reason matters.** The obvious version claims the
    job and does the work in a single transaction - but then rolling back a failure also
    rolls back the claim, so the job returns to `queued` with `attempts` never incremented
    and a poisonous job retries forever. So:

        1. claim and commit    the job is now `running`, attempts incremented
        2. do the work         its own transaction; results commit or roll back alone
        3. record the outcome  succeeded, or failed (re-queued while attempts remain)

    Once step 1 commits, no other worker can take the job: `claim_next_job` only considers
    `queued` rows. The cost is that a worker killed between steps leaves a row `running`
    forever - see the known limitations in milestone-05b.md.
    """
    claimed = _claim_one(database_url)
    if claimed is None:
        return False

    job_id, kind = claimed

    try:
        with session_scope(database_url) as session:
            job = job_queue.get_job(session, job_id)
            if job is None:  # deleted under us; nothing to do
                return True

            result = run_job(session, job)
            job_queue.mark_succeeded(session, job, result=result)

        log.info("Job %s (%s) succeeded: %s", job_id, kind, result)

    except Exception as exc:
        # The stored message is what an operator and (later) a customer will read, so it is
        # a summary. The traceback goes to the log and nowhere else.
        log.exception("Job %s (%s) failed.", job_id, kind)

        with session_scope(database_url) as recovery:
            failed = job_queue.get_job(recovery, job_id)
            if failed is not None:
                job_queue.mark_failed(recovery, failed, error=f"{type(exc).__name__}: {exc}")

    return True


def _claim_one(database_url: str | None) -> tuple[uuid.UUID, str] | None:
    """Claim a job and commit the claim, returning its id and kind."""
    with session_scope(database_url) as session:
        job = job_queue.claim_next_job(session, worker_id=worker_id())

        if job is None:
            return None

        return job.id, job.kind


def run_forever(*, poll_seconds: float | None = None, database_url: str | None = None) -> None:
    """Process jobs until the queue is empty, then wait and look again."""
    interval = poll_seconds if poll_seconds is not None else settings.WORKER_POLL_SECONDS

    log.info("Worker %s started; polling every %.1fs", worker_id(), interval)

    while not _stopping:
        try:
            did_work = process_one(database_url=database_url)
        except Exception:
            # A failure *outside* a job - the database is down, say. Log and keep going;
            # exiting would need a supervisor to restart the process.
            log.exception("Worker loop error; retrying after %.1fs", interval)
            did_work = False

        if not did_work:
            time.sleep(interval)

    log.info("Worker %s stopped.", worker_id())


def drain(*, database_url: str | None = None, limit: int = 1000) -> int:
    """Run jobs until the queue is empty. Used by tests and the Docker smoke check."""
    processed = 0

    while processed < limit and process_one(database_url=database_url):
        processed += 1

    return processed


def _handle_stop(signum: int, frame: FrameType | None) -> None:
    """Finish the job in hand, then exit - SIGTERM is how a container asks to stop."""
    global _stopping
    _stopping = True
    log.info("Signal %s received; stopping after the current job.", signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FeedbackIQ background worker.")
    parser.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    parser.add_argument("--drain", action="store_true", help="run until the queue is empty")
    parser.add_argument("--poll-seconds", type=float, default=None)
    parser.add_argument("--database-url", default=None, help="override settings.DATABASE_URL")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if args.once:
        ran = process_one(database_url=args.database_url)
        print("processed 1 job" if ran else "no jobs waiting")
        return 0

    if args.drain:
        print(f"processed {drain(database_url=args.database_url)} job(s)")
        return 0

    run_forever(poll_seconds=args.poll_seconds, database_url=args.database_url)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
