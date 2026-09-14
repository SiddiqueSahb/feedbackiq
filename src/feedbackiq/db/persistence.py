"""
Storing what the engine produced.

Plain functions taking a `Session`, not repository classes. A repository is worth its
weight when there are several implementations to swap or a domain model to keep separate
from the tables; here there is one database, and the models *are* the domain model, so a
class per table would be indirection with nothing behind it. When a second caller needs
the same query, it imports the function.

The direction of dependency is one-way and load-bearing:

    engine (typed results)  ──▶  persistence  ──▶  PostgreSQL

The engine knows nothing about this module. This module imports the engine's *types* to
read them, which is why the conversion lives here rather than in the engine.

    with session_scope() as session:
        rows = save_feedback(session, organisation_id=org.id, data_source_id=src.id,
                             texts=["the app keeps crashing"])
        analysis = engine.analyse_batch([FeedbackItem(id=str(rows[0].id), text=...)])
        run = save_batch_analysis(session, organisation_id=org.id, analysis=analysis)
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from feedbackiq.core.logging import get_logger
from feedbackiq.db.models import (
    AnalysisResult,
    AnalysisRun,
    Category,
    DataSource,
    Feedback,
    ImportBatch,
    Insight,
    Organisation,
)
from feedbackiq.engine.preprocessing import normalise_text
from feedbackiq.engine.types import BatchAnalysis, ItemAnalysis

log = get_logger("db.persistence")


# ---------------------------------------------------------------- organisations & sources


def create_organisation(session: Session, *, name: str, slug: str) -> Organisation:
    organisation = Organisation(name=name, slug=slug)
    session.add(organisation)
    session.flush()

    return organisation


def get_organisation_by_slug(session: Session, slug: str) -> Organisation | None:
    return session.scalar(select(Organisation).where(Organisation.slug == slug))


def create_data_source(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    name: str,
    kind: str = "csv_upload",
    settings: Mapping[str, object] | None = None,
) -> DataSource:
    source = DataSource(
        organisation_id=organisation_id,
        name=name,
        kind=kind,
        settings=dict(settings or {}),
    )
    session.add(source)
    session.flush()

    return source


# ---------------------------------------------------------------- import batches


def start_import_batch(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    data_source_id: uuid.UUID,
    original_filename: str | None = None,
    storage_reference: str | None = None,
    source_hash: str | None = None,
    row_count: int = 0,
) -> ImportBatch:
    """
    Record that an import began.

    `source_hash` is the SHA-256 of the uploaded bytes, so an identical re-upload can be
    recognised (see services/imports.py). `storage_reference` stays None while uploads are
    processed in memory and never kept: the database is the source of truth for imported
    feedback, and the original file is not customer data we need to hold.
    """
    batch = ImportBatch(
        organisation_id=organisation_id,
        data_source_id=data_source_id,
        original_filename=original_filename,
        storage_reference=storage_reference,
        source_hash=source_hash,
        status="importing",
        row_count=row_count,
    )
    session.add(batch)
    session.flush()

    return batch


def finish_import_batch(
    session: Session,
    batch: ImportBatch,
    *,
    imported_count: int,
    failed_count: int = 0,
    error_summary: Mapping[str, object] | None = None,
    status: str = "completed",
) -> ImportBatch:
    batch.status = status
    batch.imported_count = imported_count
    batch.failed_count = failed_count
    batch.error_summary = dict(error_summary) if error_summary else None
    batch.completed_at = datetime.now(timezone.utc)
    session.flush()

    return batch


# ---------------------------------------------------------------- feedback


def content_hash(text: str) -> str:
    """
    A stable hash of the feedback text, for spotting a re-uploaded row.

    Hashing the *normalised* text (the engine's own `normalise_text`) rather than the raw
    string, so trailing whitespace or a different newline convention does not look like
    new feedback. The engine's function is reused deliberately: two definitions of
    "the same text" would eventually disagree.
    """
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def save_feedback(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    data_source_id: uuid.UUID,
    texts: Sequence[str] | None = None,
    items: Sequence[Mapping[str, object]] | None = None,
    import_batch_id: uuid.UUID | None = None,
) -> list[Feedback]:
    """
    Store feedback as received.

    `texts` is the simple case - a list of strings. `items` takes dicts for the fields a
    real import has: `text` (required) plus any of `external_id`, `rating`, `language`,
    `feedback_at`, `metadata`.

    Nothing is de-duplicated here: `content_hash` is stored and indexed so a caller can
    decide what a duplicate means (the same text twice may be two genuine complaints).
    """
    if texts is not None and items is not None:
        raise ValueError("pass either texts or items, not both")

    records = [{"text": text} for text in texts] if texts is not None else list(items or [])

    rows: list[Feedback] = []
    for record in records:
        text = str(record["text"])
        rows.append(
            Feedback(
                organisation_id=organisation_id,
                data_source_id=data_source_id,
                import_batch_id=import_batch_id,
                external_id=record.get("external_id"),
                text=text,
                rating=record.get("rating"),
                language=record.get("language"),
                feedback_at=record.get("feedback_at"),
                meta=dict(record.get("metadata") or {}),
                content_hash=content_hash(text),
            )
        )

    session.add_all(rows)
    session.flush()

    return rows


# ---------------------------------------------------------------- reads for ingestion


def get_data_source_by_name(
    session: Session, *, organisation_id: uuid.UUID, name: str
) -> DataSource | None:
    return session.scalar(
        select(DataSource).where(
            DataSource.organisation_id == organisation_id, DataSource.name == name
        )
    )


def get_import_batch(
    session: Session, *, organisation_id: uuid.UUID, import_batch_id: uuid.UUID
) -> ImportBatch | None:
    """
    One import batch, scoped to its organisation.

    The `organisation_id` filter is not decoration: it is how a lookup by id stops being a
    way to read another tenant's import once this is reachable over HTTP.
    """
    return session.scalar(
        select(ImportBatch).where(
            ImportBatch.id == import_batch_id,
            ImportBatch.organisation_id == organisation_id,
        )
    )


def find_import_batch_by_source_hash(
    session: Session, *, organisation_id: uuid.UUID, source_hash: str
) -> ImportBatch | None:
    """The most recent completed import of this exact file, if there is one."""
    return session.scalar(
        select(ImportBatch)
        .where(
            ImportBatch.organisation_id == organisation_id,
            ImportBatch.source_hash == source_hash,
            ImportBatch.status == "completed",
        )
        .order_by(ImportBatch.created_at.desc())
        .limit(1)
    )


def existing_content_hashes(
    session: Session, *, organisation_id: uuid.UUID, hashes: Sequence[str]
) -> set[str]:
    """
    Which of these content hashes this organisation already has.

    One query for the whole batch rather than one per row - the difference between an
    import that scales and one that does not.
    """
    if not hashes:
        return set()

    return set(
        session.scalars(
            select(Feedback.content_hash).where(
                Feedback.organisation_id == organisation_id,
                Feedback.content_hash.in_(set(hashes)),
            )
        ).all()
    )


def existing_external_ids(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    data_source_id: uuid.UUID,
    external_ids: Sequence[str],
) -> set[str]:
    """Which of these source-provided identifiers are already stored for this source."""
    if not external_ids:
        return set()

    return set(
        session.scalars(
            select(Feedback.external_id).where(
                Feedback.organisation_id == organisation_id,
                Feedback.data_source_id == data_source_id,
                Feedback.external_id.in_(set(external_ids)),
            )
        ).all()
    )


def feedback_for_import_batch(
    session: Session, *, organisation_id: uuid.UUID, import_batch_id: uuid.UUID
) -> list[Feedback]:
    """Everything stored by one import, oldest first, for the worker to analyse."""
    return list(
        session.scalars(
            select(Feedback)
            .where(
                Feedback.organisation_id == organisation_id,
                Feedback.import_batch_id == import_batch_id,
            )
            .order_by(Feedback.created_at, Feedback.id)
        ).all()
    )


# ---------------------------------------------------------------- analysis runs


def save_batch_analysis(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    analysis: BatchAnalysis,
    import_batch_id: uuid.UUID | None = None,
    trigger: str = "manual",
) -> AnalysisRun:
    """
    Persist a whole `BatchAnalysis`: one run row, one result row per item, an insight row
    where the LLM produced one.

    `ItemAnalysis.feedback_id` must be the string form of a `feedback.id` - the engine
    echoes back whatever identifier the caller gave it, and this is the layer that knows
    those identifiers are database rows.

    Results supersede rather than overwrite: any previous `is_current` result for the same
    feedback is cleared, so re-analysing with a newer model keeps the history.
    """
    run = _create_run(
        session,
        organisation_id=organisation_id,
        analysis=analysis,
        import_batch_id=import_batch_id,
        trigger=trigger,
    )

    feedback_ids = [_feedback_uuid(item) for item in analysis.results]
    _clear_current_results(session, organisation_id=organisation_id, feedback_ids=feedback_ids)

    # One lookup per category name, reused across the batch.
    category_ids: dict[str, uuid.UUID | None] = {}

    for item, feedback_id in zip(analysis.results, feedback_ids):
        result = _result_row(
            session,
            run=run,
            organisation_id=organisation_id,
            feedback_id=feedback_id,
            item=item,
            category_ids=category_ids,
        )
        session.add(result)

        if item.insight is not None:
            session.add(
                _insight_row(organisation_id=organisation_id, result=result, item=item)
            )

    session.flush()

    return run


def _create_run(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    analysis: BatchAnalysis,
    import_batch_id: uuid.UUID | None,
    trigger: str,
) -> AnalysisRun:
    """The run row, with its versions taken straight from the engine's manifest."""
    versions = dict(analysis.versions)
    now = datetime.now(timezone.utc)

    run = AnalysisRun(
        organisation_id=organisation_id,
        import_batch_id=import_batch_id,
        trigger=trigger,
        status="completed",
        # `engine` is the manifest key; missing only if a caller built a BatchAnalysis by
        # hand, in which case "unknown" is more honest than crashing.
        engine_version=str(versions.get("engine", "unknown")),
        # `taxonomy_*` is stored alongside the model versions so a result can answer which
        # taxonomy produced it, and whether that taxonomy was the packaged default or one
        # the caller supplied.
        model_versions={
            key: versions[key]
            for key in (
                "sentiment_model",
                "categoriser_model",
                "embedding_model",
                "llm_model",
                "taxonomy_id",
                "taxonomy_version",
                "taxonomy_source",
            )
            if key in versions
        },
        prompt_versions=dict(versions.get("prompts") or {}),
        thresholds=dict(versions.get("thresholds") or {}),
        item_count=len(analysis.results),
        succeeded_count=len(analysis.succeeded),
        failed_count=len(analysis.failed),
        llm_calls=analysis.usage.llm_calls,
        input_tokens=analysis.usage.input_tokens,
        output_tokens=analysis.usage.output_tokens,
        llm_retries=analysis.usage.retries,
        started_at=now,
        completed_at=now,
    )
    session.add(run)
    session.flush()

    return run


def _result_row(
    session: Session,
    *,
    run: AnalysisRun,
    organisation_id: uuid.UUID,
    feedback_id: uuid.UUID,
    item: ItemAnalysis,
    category_ids: dict[str, uuid.UUID | None],
) -> AnalysisResult:
    sentiment = item.sentiment

    category_id = None
    category_confidence = None
    if item.category is not None and not item.is_unclassified:
        category_id = _resolve_category_id(
            session,
            organisation_id=organisation_id,
            match=item.category,
            cache=category_ids,
        )
        category_confidence = item.category.score

    return AnalysisResult(
        organisation_id=organisation_id,
        feedback_id=feedback_id,
        analysis_run_id=run.id,
        status=item.status,
        error=item.error,
        sentiment_label=sentiment.label if sentiment else None,
        sentiment_confidence=sentiment.confidence if sentiment else None,
        sentiment_scores=dict(sentiment.scores) if sentiment else None,
        sentiment_model_version=sentiment.model_version if sentiment else None,
        category_id=category_id,
        category_confidence=category_confidence,
        is_unclassified=item.is_unclassified,
        categorisation_skipped=item.categorisation_skipped,
        candidate_categories=[
            {"name": candidate.name, "score": candidate.score}
            for candidate in item.candidate_categories
        ] or None,
        is_current=True,
    )


def _insight_row(
    *, organisation_id: uuid.UUID, result: AnalysisResult, item: ItemAnalysis
) -> Insight:
    insight = item.insight
    assert insight is not None  # checked by the caller

    return Insight(
        organisation_id=organisation_id,
        # Set through the relationship rather than the column: `result` has not been
        # flushed yet, so its id does not exist until it is.
        result=result,
        summary=insight.summary,
        business_insight=insight.business_insight,
        executive_summary=insight.executive_summary,
        keywords=list(insight.keywords),
        severity=insight.severity or None,
        priority=insight.priority or None,
        department=insight.department or None,
        evidence_ids=list(insight.evidence_ids),
        prompt_version=insight.prompt_version or None,
        model_version=insight.model_version or None,
    )


def _clear_current_results(
    session: Session, *, organisation_id: uuid.UUID, feedback_ids: Iterable[uuid.UUID]
) -> None:
    """
    Un-mark previous results for this feedback.

    Required, not merely tidy: `uq_analysis_results_current_feedback` is a unique index
    on `feedback_id WHERE is_current`, so inserting a second current result without this
    would be rejected by the database.
    """
    ids = list(feedback_ids)
    if not ids:
        return

    session.execute(
        update(AnalysisResult)
        .where(
            AnalysisResult.organisation_id == organisation_id,
            AnalysisResult.feedback_id.in_(ids),
            AnalysisResult.is_current.is_(True),
        )
        .values(is_current=False)
    )


def _resolve_category_id(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    match,
    cache: dict[str, uuid.UUID | None],
) -> uuid.UUID | None:
    """
    Turn the engine's category into a `categories.id`.

    The engine's `Category.id` is whatever the caller used when passing the taxonomy in, so
    three things are tried in order of how much they can be trusted:

    1. **a database UUID** - the categories came straight from this table;
    2. **the stable key** - the canonical taxonomy (1.1.0 onward) identifies categories by
       key, which survives a display-name change;
    3. **the name** - a hand-built taxonomy, or a row written before keys existed.

    An unrecognised category stores NULL rather than inventing a row.
    """
    cache_key = f"{match.category_id}|{match.name}"
    if cache_key in cache:
        return cache[cache_key]

    resolved = _category_id_from_uuid(session, match.category_id)
    if resolved is None:
        resolved = _category_id_from_key(
            session, organisation_id=organisation_id, key=match.category_id
        )
    if resolved is None:
        resolved = _category_id_from_name(
            session, organisation_id=organisation_id, name=match.name
        )

    if resolved is None:
        log.warning("No category row matches %r; storing result without a category.", match.name)

    cache[cache_key] = resolved

    return resolved


def _category_id_from_uuid(session: Session, category_id: str | None) -> uuid.UUID | None:
    if not category_id:
        return None

    try:
        candidate = uuid.UUID(str(category_id))
    except ValueError:
        return None

    return session.scalar(select(Category.id).where(Category.id == candidate))


def _category_id_from_key(
    session: Session, *, organisation_id: uuid.UUID, key: str | None
) -> uuid.UUID | None:
    """The organisation's own category wins over a global default with the same key."""
    if not key:
        return None

    own = session.scalar(
        select(Category.id).where(
            Category.organisation_id == organisation_id, Category.key == key
        )
    )
    if own is not None:
        return own

    return session.scalar(
        select(Category.id).where(Category.organisation_id.is_(None), Category.key == key)
    )


def _category_id_from_name(
    session: Session, *, organisation_id: uuid.UUID, name: str
) -> uuid.UUID | None:
    """The organisation's own category wins over a global default of the same name."""
    own = session.scalar(
        select(Category.id).where(
            Category.organisation_id == organisation_id, Category.name == name
        )
    )
    if own is not None:
        return own

    return session.scalar(
        select(Category.id).where(Category.organisation_id.is_(None), Category.name == name)
    )


def _feedback_uuid(item: ItemAnalysis) -> uuid.UUID:
    try:
        return uuid.UUID(str(item.feedback_id))
    except ValueError as exc:
        raise ValueError(
            f"feedback_id {item.feedback_id!r} is not a feedback row id: analysis results can "
            "only be stored for feedback that was saved first"
        ) from exc
