"""
Importing a customer's CSV.

    import_csv(session, raw=..., filename=...)
        ├─ resolve the owning organisation        (explicit, never inferred from the file)
        ├─ recognise an identical re-upload       (SHA-256 of the bytes)
        ├─ validate rows                          (ingestion/csv_reader.py)
        ├─ drop rows this organisation already has
        ├─ store the survivors as `feedback`
        ├─ close the import batch with its counts
        └─ queue an analysis job

Two things this module deliberately does not do:

* **Analyse anything.** The engine runs in the worker, not in the request. A 10,000-row
  upload would otherwise hold an HTTP connection open for minutes.
* **Keep the uploaded file.** It is parsed in memory and discarded; only its name and hash
  are recorded. The database is the source of truth for imported feedback, so there is no
  second copy of customer data to secure, back up or delete on request.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import IngestionError
from feedbackiq.core.logging import get_logger
from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import ImportBatch, Job, Organisation
from feedbackiq.db.persistence import (
    content_hash,
    create_data_source,
    existing_content_hashes,
    existing_external_ids,
    finish_import_batch,
    find_import_batch_by_source_hash,
    get_data_source_by_name,
    get_organisation_by_slug,
    save_feedback,
    start_import_batch,
)
from feedbackiq.ingestion.csv_reader import CsvFormatError, ParseResult, parse_csv

log = get_logger("service.imports")

# Keep a recognisable filename without letting one become a path or a log-injection vector.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]")
MAX_FILENAME_CHARS = 200

# How many row errors are stored on the batch. The full list of a 50,000-row disaster
# belongs in neither a database column nor an HTTP response.
MAX_STORED_ROW_ERRORS = 100


@dataclass(frozen=True)
class ImportOutcome:
    """What one upload did."""

    batch: ImportBatch
    job: Job | None
    received: int
    stored: int
    rejected: int
    duplicates_in_file: int
    duplicates_in_database: int
    row_errors: list[dict]
    # Set when this exact file was already imported; nothing new was created.
    duplicate_of: ImportBatch | None = None

    @property
    def is_duplicate_upload(self) -> bool:
        return self.duplicate_of is not None


def import_csv(
    session: Session,
    *,
    raw: bytes,
    filename: str | None = None,
    organisation_id: uuid.UUID | None = None,
    queue_analysis: bool = True,
) -> ImportOutcome:
    """
    Validate and store a CSV, then queue its analysis.

    Raises `IngestionError` when the file as a whole is unusable (too large, unreadable, no
    text column). Individual bad rows are *not* errors: they are counted, reported on the
    batch, and the rest of the file is imported.
    """
    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise IngestionError(
            f"The file is {len(raw) / 1_048_576:.1f} MB; the limit is "
            f"{settings.MAX_UPLOAD_BYTES / 1_048_576:.0f} MB."
        )

    organisation = _resolve_organisation(session, organisation_id)
    safe_name = _safe_filename(filename)
    source_hash = hashlib.sha256(raw).hexdigest()

    # Batch-level idempotency: the same bytes, already imported by this organisation.
    already = find_import_batch_by_source_hash(
        session, organisation_id=organisation.id, source_hash=source_hash
    )
    if already is not None:
        log.info(
            "Organisation %s re-uploaded an identical file; returning import %s",
            organisation.id, already.id,
        )
        return ImportOutcome(
            batch=already,
            job=None,
            received=already.row_count,
            stored=0,
            rejected=0,
            duplicates_in_file=0,
            duplicates_in_database=already.imported_count,
            row_errors=[],
            duplicate_of=already,
        )

    try:
        parsed = parse_csv(raw, max_rows=settings.MAX_IMPORT_ROWS)
    except CsvFormatError as exc:
        # A whole-file problem: nothing is stored, and no empty batch is left behind.
        raise IngestionError(str(exc)) from exc

    data_source = _resolve_data_source(session, organisation.id)

    batch = start_import_batch(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        original_filename=safe_name,
        source_hash=source_hash,
        row_count=parsed.received,
    )

    keep, duplicates_in_database = _drop_known_rows(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        parsed=parsed,
    )

    stored = save_feedback(
        session,
        organisation_id=organisation.id,
        data_source_id=data_source.id,
        items=[row.as_record() for row in keep],
        import_batch_id=batch.id,
    )

    row_errors = [error.as_dict() for error in parsed.errors[:MAX_STORED_ROW_ERRORS]]
    rejected = parsed.rejected + duplicates_in_database

    finish_import_batch(
        session,
        batch,
        imported_count=len(stored),
        failed_count=rejected,
        error_summary={
            "rows": row_errors,
            "row_errors_total": len(parsed.errors),
            "duplicates_in_file": parsed.duplicates_in_file,
            "duplicates_in_database": duplicates_in_database,
        },
        # "completed" even with rejected rows: the import ran and its counts say what
        # happened. Only a file that yielded nothing at all is a failed import.
        status="completed" if stored else "failed",
    )

    job = None
    if stored and queue_analysis:
        job = job_queue.create_job(
            session,
            organisation_id=organisation.id,
            kind=job_queue.ANALYSE_IMPORT,
            payload={"import_batch_id": str(batch.id), "organisation_id": str(organisation.id)},
        )

    log.info(
        "Import %s: received=%d stored=%d rejected=%d (file duplicates=%d, known=%d)",
        batch.id, parsed.received, len(stored), rejected,
        parsed.duplicates_in_file, duplicates_in_database,
    )

    return ImportOutcome(
        batch=batch,
        job=job,
        received=parsed.received,
        stored=len(stored),
        rejected=rejected,
        duplicates_in_file=parsed.duplicates_in_file,
        duplicates_in_database=duplicates_in_database,
        row_errors=row_errors,
    )


# ---------------------------------------------------------------- ownership


def _resolve_organisation(session: Session, organisation_id: uuid.UUID | None) -> Organisation:
    """
    Whose data this is.

    **Temporary mechanism.** With no authentication yet, the application layer names the
    organisation: an explicit id, or the seeded development organisation. It is never
    inferred from the filename, the CSV contents or anything else a caller controls.
    Milestone 7/8 replace this with the authenticated user's organisation, and this function
    is the single place that has to change.
    """
    if organisation_id is not None:
        organisation = session.get(Organisation, organisation_id)
        if organisation is None:
            raise IngestionError(f"No organisation {organisation_id}.")
        return organisation

    organisation = get_organisation_by_slug(session, settings.DEV_ORGANISATION_SLUG)
    if organisation is None:
        raise IngestionError(
            f"No organisation '{settings.DEV_ORGANISATION_SLUG}' exists. "
            "Run: python -m feedbackiq.db.seed"
        )

    return organisation


def _resolve_data_source(session: Session, organisation_id: uuid.UUID):
    """The organisation's CSV-upload source, created on first use."""
    existing = get_data_source_by_name(
        session, organisation_id=organisation_id, name=settings.IMPORT_SOURCE_NAME
    )
    if existing is not None:
        return existing

    return create_data_source(
        session,
        organisation_id=organisation_id,
        name=settings.IMPORT_SOURCE_NAME,
        kind="csv_upload",
    )


# ---------------------------------------------------------------- duplicates


def _drop_known_rows(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    data_source_id: uuid.UUID,
    parsed: ParseResult,
) -> tuple[list, int]:
    """
    Remove rows this organisation already has, so a partial re-upload does not duplicate.

    Two signals, both already indexed by the schema:

    * `external_id` - the customer's own identifier, unique per organisation and source.
      The database enforces it; checking here turns a failed insert into a counted
      duplicate.
    * `content_hash` - the same normalised text, for the common case of a file with no ids.

    Both are looked up in **one query each** for the whole batch.
    """
    external_ids = [row.external_id for row in parsed.rows if row.external_id]
    known_ids = existing_external_ids(
        session,
        organisation_id=organisation_id,
        data_source_id=data_source_id,
        external_ids=external_ids,
    )

    hashes = {row.text: content_hash(row.text) for row in parsed.rows}
    known_hashes = existing_content_hashes(
        session, organisation_id=organisation_id, hashes=list(hashes.values())
    )

    keep = []
    duplicates = 0
    seen_hashes: set[str] = set()

    for row in parsed.rows:
        row_hash = hashes[row.text]

        if row.external_id and row.external_id in known_ids:
            duplicates += 1
            continue
        if row_hash in known_hashes or row_hash in seen_hashes:
            duplicates += 1
            continue

        seen_hashes.add(row_hash)
        keep.append(row)

    return keep, duplicates


def _safe_filename(filename: str | None) -> str | None:
    """A recognisable name with no path, no control characters, bounded length."""
    if not filename:
        return None

    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_FILENAME.sub("_", base).strip()

    return cleaned[:MAX_FILENAME_CHARS] or None
