"""
Imports and job visibility: /api/v1/imports, /api/v1/imports/{id}, /api/v1/jobs/{id}

    POST /api/v1/imports          upload a CSV for the signed-in user's organisation
    GET  /api/v1/imports          recent imports
    GET  /api/v1/imports/{id}     one import
    GET  /api/v1/jobs/{id}        one background job

The upload is the same operation as `POST /api/imports` (Milestone 5B), sharing its code
(`api/routes/imports.py::accept_upload`), so the two cannot drift apart. It exists here so a
frontend can use one versioned API for everything.

The organisation is always the signed-in user's. There is no parameter, header or form field
that names one, and a CSV column called `organisation_id` is ignored as data like any other
unknown column.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from feedbackiq.api.deps import get_current_organisation
from feedbackiq.api.routes.imports import (
    UPLOAD_DESCRIPTION,
    _import_summary,
    _job_summary,
    accept_upload,
)
from feedbackiq.api.schemas import ImportAccepted, ImportSummary, JobSummary
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.persistence import get_import_batch, list_import_batches
from feedbackiq.db.session import session_scope
from feedbackiq.services.auth import AuthContext

router = APIRouter(tags=["Imports (v1)"])

log = get_logger("api.v1.imports")


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
    "/imports",
    response_model=list[ImportSummary],
    summary="Recent imports",
    description="This organisation's imports, newest first, with the counts each produced.",
)
async def list_imports(
    limit: int = Query(50, ge=1, le=200),
    context: AuthContext = Depends(get_current_organisation),
) -> list[ImportSummary]:

    log.info("GET /v1/imports | limit=%d", limit)

    try:
        return await asyncio.to_thread(_list, context.organisation_id, limit)

    except HTTPException:
        raise

    except Exception:
        log.exception("Listing imports failed.")
        raise HTTPException(status_code=500, detail="Could not list imports.")


@router.get(
    "/imports/{import_id}",
    response_model=ImportSummary,
    summary="One import",
)
async def read_import(
    import_id: str,
    context: AuthContext = Depends(get_current_organisation),
) -> ImportSummary:

    identifier = _uuid(import_id, "import")

    try:
        return await asyncio.to_thread(_import, context.organisation_id, identifier)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading import %s failed.", import_id)
        raise HTTPException(status_code=500, detail="Could not read the import.")


@router.get(
    "/jobs/{job_id}",
    response_model=JobSummary,
    summary="One background job",
    description=(
        "Status, attempts and the analysis result summary. `error` is a short message, never "
        "internal detail."
    ),
)
async def read_job(
    job_id: str,
    context: AuthContext = Depends(get_current_organisation),
) -> JobSummary:

    identifier = _uuid(job_id, "job")

    try:
        return await asyncio.to_thread(_job, context.organisation_id, identifier)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading job %s failed.", job_id)
        raise HTTPException(status_code=500, detail="Could not read the job.")


# ---------------------------------------------------------------- blocking work


def _list(organisation_id: uuid.UUID, limit: int) -> list[ImportSummary]:
    with session_scope() as session:
        batches = list_import_batches(
            session, organisation_id=organisation_id, limit=limit
        )

        return [_import_summary(batch) for batch in batches]


def _import(organisation_id: uuid.UUID, import_id: uuid.UUID) -> ImportSummary:
    with session_scope() as session:
        batch = get_import_batch(
            session, organisation_id=organisation_id, import_batch_id=import_id
        )

        if batch is None:
            raise HTTPException(status_code=404, detail="No such import.")

        return _import_summary(batch)


def _job(organisation_id: uuid.UUID, job_id: uuid.UUID) -> JobSummary:
    with session_scope() as session:
        job = job_queue.get_job(session, job_id, organisation_id=organisation_id)

        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")

        return _job_summary(job)


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{value}' is not a valid {label} id.")
