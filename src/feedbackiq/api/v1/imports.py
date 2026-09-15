"""
Import and job visibility: /api/v1/imports, /api/v1/jobs/{id}

Read-only. Uploading stays at `POST /api/imports` (Milestone 5B) - moving it would break a
working endpoint for no gain, and a frontend can perfectly well post to one path and read
from another.

These exist so a future dashboard can answer "did my upload work, and has it been analysed
yet?" without inventing its own polling protocol: an import carries its counts, and its job
carries the analysis outcome.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Query

from feedbackiq.api.deps import resolve_organisation_id
from feedbackiq.api.routes.imports import _import_summary, _job_summary
from feedbackiq.api.schemas import ImportSummary, JobSummary
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.persistence import get_import_batch, list_import_batches
from feedbackiq.db.session import session_scope

router = APIRouter(tags=["Imports (v1)"])

log = get_logger("api.v1.imports")


@router.get(
    "/imports",
    response_model=list[ImportSummary],
    summary="Recent imports",
    description="This organisation's imports, newest first, with the counts each produced.",
)
async def list_imports(limit: int = Query(50, ge=1, le=200)) -> list[ImportSummary]:

    log.info("GET /v1/imports | limit=%d", limit)

    try:
        return await asyncio.to_thread(_list, limit)

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
async def read_import(import_id: str) -> ImportSummary:

    identifier = _uuid(import_id, "import")

    try:
        return await asyncio.to_thread(_import, identifier)

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
async def read_job(job_id: str) -> JobSummary:

    identifier = _uuid(job_id, "job")

    try:
        return await asyncio.to_thread(_job, identifier)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading job %s failed.", job_id)
        raise HTTPException(status_code=500, detail="Could not read the job.")


# ---------------------------------------------------------------- blocking work


def _list(limit: int) -> list[ImportSummary]:
    with session_scope() as session:
        organisation_id = resolve_organisation_id(session)
        batches = list_import_batches(
            session, organisation_id=organisation_id, limit=limit
        )

        return [_import_summary(batch) for batch in batches]


def _import(import_id: uuid.UUID) -> ImportSummary:
    with session_scope() as session:
        organisation_id = resolve_organisation_id(session)
        batch = get_import_batch(
            session, organisation_id=organisation_id, import_batch_id=import_id
        )

        if batch is None:
            raise HTTPException(status_code=404, detail="No such import.")

        return _import_summary(batch)


def _job(job_id: uuid.UUID) -> JobSummary:
    with session_scope() as session:
        organisation_id = resolve_organisation_id(session)
        job = job_queue.get_job(session, job_id, organisation_id=organisation_id)

        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")

        return _job_summary(job)


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{value}' is not a valid {label} id.")
