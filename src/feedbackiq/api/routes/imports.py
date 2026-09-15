"""
Feedback ingestion endpoints.

    POST /api/imports              upload a CSV: validate, store, queue analysis
    GET  /api/imports/{import_id}  what that import did
    GET  /api/jobs/{job_id}        how its analysis is progressing

The upload returns as soon as the rows are stored. Analysis happens in the worker
(`python -m feedbackiq.worker`), so a ten-thousand-row file does not hold an HTTP connection
open while transformers run.

Three deliberate properties:

* **No analysis in the request.** The route creates a job; the worker runs the engine.
* **Sessions open inside the handlers**, not as a FastAPI dependency, so a request refused at
  authentication never touches the database - and the API test suite can import this module
  with no PostgreSQL running.
* **Ownership comes from the signed-in user.** Every row is written against the organisation
  of the user's session and membership (api/deps.py::get_current_organisation), never inferred
  from the file and never defaulted.

**Authentication (Milestone 7):** these routes need a signed-in user, not the shared API key -
they read and write customer data. `POST /api/v1/imports` is the same upload, sharing
`accept_upload` below.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from feedbackiq.api.deps import get_current_organisation
from feedbackiq.api.schemas import ImportAccepted, ImportSummary, JobSummary
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import IngestionError
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import ImportBatch, Job
from feedbackiq.db.persistence import get_import_batch
from feedbackiq.db.session import session_scope
from feedbackiq.services.auth import AuthContext
from feedbackiq.services.imports import ImportOutcome, import_csv

router = APIRouter(tags=["Imports"])

log = get_logger("api.imports")

UPLOAD_DESCRIPTION = (
    "Validates the file, stores the valid rows for the signed-in user's organisation and "
    "queues analysis. Requires a `text` column; `external_id`, `created_at`, `rating` and "
    "`platform` are optional. Rejected rows are reported per row and do not stop the import. "
    "Re-uploading an identical file creates nothing and returns the original import."
)


@router.post(
    "/imports",
    response_model=ImportAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV of customer feedback",
    description=UPLOAD_DESCRIPTION,
)
async def create_import(
    file: UploadFile = File(...),
    context: AuthContext = Depends(get_current_organisation),
) -> ImportAccepted:

    return await accept_upload(file, context.organisation_id)


@router.get(
    "/imports/{import_id}",
    response_model=ImportSummary,
    summary="Status and counts for one import",
)
async def read_import(
    import_id: str,
    context: AuthContext = Depends(get_current_organisation),
) -> ImportSummary:

    try:
        return await asyncio.to_thread(_read_import, context.organisation_id, import_id)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading import %s failed.", import_id)
        raise HTTPException(status_code=500, detail="Could not read the import.")


@router.get(
    "/jobs/{job_id}",
    response_model=JobSummary,
    summary="Status of a background job",
)
async def read_job(
    job_id: str,
    context: AuthContext = Depends(get_current_organisation),
) -> JobSummary:

    try:
        return await asyncio.to_thread(_read_job, context.organisation_id, job_id)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading job %s failed.", job_id)
        raise HTTPException(status_code=500, detail="Could not read the job.")


# ---------------------------------------------------------------- the upload, shared with /api/v1


async def accept_upload(file: UploadFile, organisation_id: uuid.UUID) -> ImportAccepted:
    """Read the upload within the size limit, store it for `organisation_id`, map errors."""

    # Read at most one byte beyond the limit: an oversized upload is refused without being
    # held in memory in full.
    raw = await file.read(settings.MAX_UPLOAD_BYTES + 1)

    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"The file is larger than the "
                f"{settings.MAX_UPLOAD_BYTES // 1_048_576} MB limit."
            ),
        )

    log.info("POST imports | filename=%s | bytes=%d", file.filename, len(raw))

    try:
        return await asyncio.to_thread(_store_import, organisation_id, raw, file.filename)

    except IngestionError as exc:
        # The file cannot be used at all: a message the customer can act on. `exc.message`, not
        # str(exc) - FeedBackError's str() prefixes the class name ("[IngestionError] ..."), which
        # is internal detail a customer should never read.
        raise HTTPException(status_code=422, detail=exc.message)

    except Exception:
        log.exception("Import failed.")
        raise HTTPException(status_code=500, detail="Import failed.")


# ---------------------------------------------------------------- blocking work


def _store_import(organisation_id: uuid.UUID, raw: bytes, filename: str | None) -> ImportAccepted:
    with session_scope() as session:
        outcome = import_csv(
            session, organisation_id=organisation_id, raw=raw, filename=filename
        )

        return _import_accepted(outcome)


def _read_import(organisation_id: uuid.UUID, import_id: str) -> ImportSummary:
    identifier = _uuid(import_id, "import")

    with session_scope() as session:
        batch = get_import_batch(
            session, organisation_id=organisation_id, import_batch_id=identifier
        )

        if batch is None:
            # 404 rather than 403: an id belonging to another organisation must not be
            # distinguishable from one that does not exist.
            raise HTTPException(status_code=404, detail="No such import.")

        return _summaries(session, organisation_id, [batch])[0]


def _read_job(organisation_id: uuid.UUID, job_id: str) -> JobSummary:
    identifier = _uuid(job_id, "job")

    with session_scope() as session:
        job = job_queue.get_job(session, identifier, organisation_id=organisation_id)

        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")

        return _job_summary(job)


# ---------------------------------------------------------------- mapping


def _import_accepted(outcome: ImportOutcome) -> ImportAccepted:
    # The job this upload queued - None for an identical re-upload, which queued nothing new.
    summary = _import_summary(outcome.batch, outcome.job)

    return ImportAccepted(
        **summary.model_dump(),
        duplicates_in_file=outcome.duplicates_in_file,
        duplicates_in_database=outcome.duplicates_in_database,
        duplicate_upload=outcome.is_duplicate_upload,
        row_errors=outcome.row_errors,
    )


def _summaries(session, organisation_id: uuid.UUID, batches: list[ImportBatch]) -> list[ImportSummary]:
    """
    Import summaries with their analysis status: one query for the jobs however many imports there
    are, scoped to the organisation (db/jobs.py::latest_import_jobs).
    """
    jobs = job_queue.latest_import_jobs(
        session, organisation_id=organisation_id, import_batch_ids=[batch.id for batch in batches]
    )

    return [_import_summary(batch, jobs.get(batch.id)) for batch in batches]


def _import_summary(batch: ImportBatch, job: Job | None = None) -> ImportSummary:
    return ImportSummary(
        import_id=str(batch.id),
        organisation_id=str(batch.organisation_id),
        filename=batch.original_filename,
        status=batch.status,
        rows_received=batch.row_count,
        rows_imported=batch.imported_count,
        rows_rejected=batch.failed_count,
        created_at=batch.created_at,
        completed_at=batch.completed_at,
        job_id=str(job.id) if job else None,
        analysis_status=job.status if job else None,
    )


def _job_summary(job: Job) -> JobSummary:
    payload = dict(job.payload or {})
    result = payload.get("result")

    return JobSummary(
        job_id=str(job.id),
        organisation_id=str(job.organisation_id),
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=job.last_error,
        result=result if isinstance(result, dict) else None,
    )


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{value}' is not a valid {label} id.")
