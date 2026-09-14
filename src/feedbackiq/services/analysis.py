"""
Analysing stored feedback.

    analyse_import_batch(session, organisation_id=..., import_batch_id=...)

        feedback rows ──▶ FeedbackItem ──▶ engine.analyse_batch ──▶ BatchAnalysis
                                                                        │
                                             db.persistence.save_batch_analysis

This is an adapter, not a second analytics implementation. It turns persisted rows into the
engine's typed input and hands the typed output to the persistence layer; every sentiment,
categorisation and threshold decision stays in `feedbackiq.engine`.

The default engine for bulk analysis is **sentiment + categorisation only** - no retrieval,
no LLM. Both are per-item and expensive, and an import of ten thousand rows does not need an
LLM summary of each one. Evidence and insights arrive when a customer asks for them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.db.persistence import feedback_for_import_batch, save_batch_analysis
from feedbackiq.engine.pipeline import AnalyticsEngine
from feedbackiq.engine.types import FeedbackItem

log = get_logger("service.analysis")


@dataclass(frozen=True)
class AnalysisSummary:
    """What an analysis run over one import achieved."""

    import_batch_id: uuid.UUID
    analysed: int
    succeeded: int
    failed: int
    runs: int

    @property
    def all_failed(self) -> bool:
        return self.analysed > 0 and self.succeeded == 0

    def as_dict(self) -> dict:
        return {
            "import_batch_id": str(self.import_batch_id),
            "analysed": self.analysed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "runs": self.runs,
        }


@lru_cache(maxsize=1)
def default_engine() -> AnalyticsEngine:
    """
    The engine bulk analysis uses: sentiment and categorisation, nothing else.

    Cached per process, because constructing it is cheap but the models it lazily loads are
    not - a worker should load DistilBERT once, not once per job.
    """
    from feedbackiq.engine.defaults import build_default_engine
    from feedbackiq.engine.retrieval import NullRetriever

    return build_default_engine(retriever=NullRetriever(), with_insights=False)


def analyse_import_batch(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    import_batch_id: uuid.UUID,
    engine: AnalyticsEngine | None = None,
    batch_size: int | None = None,
) -> AnalysisSummary:
    """
    Analyse everything one import stored, in chunks, and persist the results.

    **Partial failures are kept, not discarded.** The engine returns one result per item and
    marks the ones that failed, so a single unanalysable row is stored as a failed result
    beside its successful neighbours. The batch is only a failure if nothing in it succeeded
    (see `AnalysisSummary.all_failed`, which is what the worker turns into a job status).

    Each chunk is one analysis run, committed by the caller's transaction. Chunking bounds
    memory and means a crash loses one chunk's work, not the whole import.
    """
    engine = engine or default_engine()
    size = batch_size or settings.ANALYSIS_BATCH_SIZE

    rows = feedback_for_import_batch(
        session, organisation_id=organisation_id, import_batch_id=import_batch_id
    )
    if not rows:
        log.info("Import %s has no feedback to analyse.", import_batch_id)
        return AnalysisSummary(import_batch_id, analysed=0, succeeded=0, failed=0, runs=0)

    succeeded = failed = runs = 0

    for start in range(0, len(rows), size):
        chunk = rows[start : start + size]

        # The engine is told the database id and echoes it back; that is the only thing it
        # ever knows about persistence.
        items = [FeedbackItem(id=str(row.id), text=row.text) for row in chunk]

        analysis = engine.analyse_batch(items)

        save_batch_analysis(
            session,
            organisation_id=organisation_id,
            analysis=analysis,
            import_batch_id=import_batch_id,
            trigger="import",
        )

        succeeded += len(analysis.succeeded)
        failed += len(analysis.failed)
        runs += 1

        log.info(
            "Import %s chunk %d: %d analysed, %d failed",
            import_batch_id, runs, len(analysis.succeeded), len(analysis.failed),
        )

    return AnalysisSummary(
        import_batch_id=import_batch_id,
        analysed=len(rows),
        succeeded=succeeded,
        failed=failed,
        runs=runs,
    )
