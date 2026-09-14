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
* **Sessions open inside the handlers**, not as a FastAPI dependency, so an unauthenticated
  request never touches the database - and the API test suite can import this module with no
  PostgreSQL running.
* **Ownership is explicit.** Every row is written against an organisation resolved by the
  application layer, never inferred from the file. See services/imports.py.

**No authentication yet**: these routes are protected by the single shared API key like every
other route, and they act for the development organisation. Milestone 7/8 replace that with a
real user and tenant scope.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from feedbackiq.api.schemas import ImportAccepted, ImportSummary, JobSummary
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import IngestionError
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import ImportBatch, Job
from feedbackiq.db.persistence import get_import_batch, get_organisation_by_slug
from feedbackiq.db.session import session_scope
from feedbackiq.services.imports import ImportOutcome, import_csv

router = APIRouter(tags=["Imports"])

log = get_logger("api.imports")


@router.post(
    "/imports",
    response_model=ImportAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV of customer feedback",
    description=(
        "Validates the file, stores the valid rows and queues analysis. Requires a `text` "
        "column; `external_id`, `created_at`, `rating` and `platform` are optional. Rejected "
        "rows are reported per row and do not stop the import. Re-uploading an identical "
        "file creates nothing and returns the original import."
    ),
)
async def create_import(file: UploadFile = File(...)) -> ImportAccepted:

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

    log.info("POST /imports | filename=%s | bytes=%d", file.filename, len(raw))

    try:
        return await asyncio.to_thread(_store_import, raw, file.filename)

    except IngestionError as exc:
        # The file cannot be used at all: a message the customer can act on.
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception:
        log.exception("Import failed.")
        raise HTTPException(status_code=500, detail="Import failed.")


@router.get(
    "/imports/{import_id}",
    response_model=ImportSummary,
    summary="Status and counts for one import",
)
async def read_import(import_id: str) -> ImportSummary:

    try:
        return await asyncio.to_thread(_read_import, import_id)

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
async def read_job(job_id: str) -> JobSummary:

    try:
        return await asyncio.to_thread(_read_job, job_id)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading job %s failed.", job_id)
        raise HTTPException(status_code=500, detail="Could not read the job.")


# ---------------------------------------------------------------- blocking work


def _store_import(raw: bytes, filename: str | None) -> ImportAccepted:
    with session_scope() as session:
        outcome = import_csv(session, raw=raw, filename=filename)

        return _import_accepted(outcome)


def _read_import(import_id: str) -> ImportSummary:
    identifier = _uuid(import_id, "import")

    with session_scope() as session:
        organisation_id = _organisation_id(session)
        batch = get_import_batch(
            session, organisation_id=organisation_id, import_batch_id=identifier
        )

        if batch is None:
            # 404 rather than 403: an id belonging to another organisation must not be
            # distinguishable from one that does not exist.
            raise HTTPException(status_code=404, detail="No such import.")

        return _import_summary(batch)


def _read_job(job_id: str) -> JobSummary:
    identifier = _uuid(job_id, "job")

    with session_scope() as session:
        organisation_id = _organisation_id(session)
        job = job_queue.get_job(session, identifier, organisation_id=organisation_id)

        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")

        return _job_summary(job)


# ---------------------------------------------------------------- mapping


def _import_accepted(outcome: ImportOutcome) -> ImportAccepted:
    summary = _import_summary(outcome.batch)

    return ImportAccepted(
        **summary.model_dump(),
        job_id=str(outcome.job.id) if outcome.job else None,
        duplicates_in_file=outcome.duplicates_in_file,
        duplicates_in_database=outcome.duplicates_in_database,
        duplicate_upload=outcome.is_duplicate_upload,
        row_errors=outcome.row_errors,
    )


def _import_summary(batch: ImportBatch) -> ImportSummary:
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


def _organisation_id(session):
    """
    Which organisation this request acts for.

    **Temporary.** Until authentication exists, reads are scoped to the seeded development
    organisation. This is the one function to replace when a request carries a real user.
    """
    organisation = get_organisation_by_slug(session, settings.DEV_ORGANISATION_SLUG)

    if organisation is None:
        raise HTTPException(
            status_code=503,
            detail="No organisation is configured. Seed the database first.",
        )

    return organisation.id


def _uuid(value: str, label: str):
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{value}' is not a valid {label} id.")
