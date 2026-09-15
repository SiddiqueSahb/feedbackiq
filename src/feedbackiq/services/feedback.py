"""
Reading one organisation's feedback.

    list_feedback(session, organisation_id=..., filters=..., page=...)
    get_feedback(session, organisation_id=..., feedback_id=...)

Every function takes `organisation_id` as a required keyword argument and puts it in the
WHERE clause. That is the point: a query that *can* be written without a tenant scope
eventually is, so the scope is part of the signature rather than a convention.

**Filtering and paging happen in PostgreSQL.** Nothing here loads rows into Python to sift
them - the previous generation of this code (`services/analytics_service.py`) read a 427 MB
parquet file into pandas on every request, which is exactly what this replaces.

Feedback is joined to its **current** analysis result (`is_current`), so a re-analysed item
shows its latest sentiment and category without duplicating the row. The join is a LEFT
join: feedback that has not been analysed yet is still feedback, and a customer should see
it rather than wonder where it went.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.db.models import AnalysisResult, Category, Feedback

log = get_logger("service.feedback")

SENTIMENTS = ("positive", "neutral", "negative")

# What a caller may sort by, mapped to columns. A whitelist, not a passed-through column
# name: accepting arbitrary text here is how ORDER BY becomes an injection point.
SORT_FIELDS = {
    "feedback_at": Feedback.feedback_at,
    "created_at": Feedback.created_at,
    "rating": Feedback.rating,
}


@dataclass(frozen=True)
class FeedbackFilters:
    """
    Everything a caller may narrow the list by. All optional, all applied in SQL.

    `unclassified` and `category_key` are separate questions: "show me what the categoriser
    could not place" is the emerging-issue view, and it is not the same as filtering by a
    category that happens to be called Unclassified.
    """

    sentiment: str | None = None
    category_key: str | None = None
    unclassified: bool | None = None
    platform: str | None = None
    search: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    analysed: bool | None = None


@dataclass(frozen=True)
class Page:
    """One page of results, with enough to render a pager without a second request."""

    items: list[dict] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 0

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


class InvalidFilter(ValueError):
    """A filter value the API should reject with 422 rather than silently ignore."""


def list_feedback(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    filters: FeedbackFilters | None = None,
    page: int = 1,
    page_size: int | None = None,
    sort: str = "feedback_at",
    descending: bool = True,
) -> Page:
    """
    One page of this organisation's feedback, newest first by default.

    Two queries: one COUNT for the total, one SELECT for the page. Counting in SQL rather
    than measuring the length of a fetched list is the difference between a pager that works
    at ten million rows and one that does not.
    """
    filters = filters or FeedbackFilters()
    size = _page_size(page_size)

    if page < 1:
        raise InvalidFilter("page must be 1 or greater.")
    if sort not in SORT_FIELDS:
        raise InvalidFilter(
            f"Cannot sort by '{sort}'. Choose one of: {', '.join(sorted(SORT_FIELDS))}."
        )

    base = _scoped_query(organisation_id, filters)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    column = SORT_FIELDS[sort]
    order = column.desc() if descending else column.asc()

    rows = session.execute(
        base.order_by(order.nullslast(), Feedback.id)   # stable: id breaks ties
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    return Page(
        items=[_row_to_dict(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


def get_feedback(
    session: Session, *, organisation_id: uuid.UUID, feedback_id: uuid.UUID
) -> dict | None:
    """
    One piece of feedback with its current analysis, or None.

    Scoped by organisation, so an id belonging to another tenant is indistinguishable from
    one that does not exist - the caller turns None into a 404 either way.
    """
    row = session.execute(
        _scoped_query(organisation_id, FeedbackFilters()).where(Feedback.id == feedback_id)
    ).first()

    return _row_to_dict(row, detail=True) if row else None


# ---------------------------------------------------------------- query building


def _scoped_query(organisation_id: uuid.UUID, filters: FeedbackFilters) -> Select:
    """
    The shared SELECT: this organisation's feedback, left-joined to its current result.

    Built once and reused by the list, the count and the detail lookup, so all three apply
    exactly the same scope and filters.
    """
    query = (
        select(
            Feedback,
            AnalysisResult.sentiment_label,
            AnalysisResult.sentiment_confidence,
            AnalysisResult.is_unclassified,
            AnalysisResult.categorisation_skipped,
            AnalysisResult.created_at.label("analysed_at"),
            AnalysisResult.sentiment_model_version,
            Category.key.label("category_key"),
            Category.name.label("category_name"),
            AnalysisResult.category_confidence,
        )
        .outerjoin(
            AnalysisResult,
            and_(
                AnalysisResult.feedback_id == Feedback.id,
                AnalysisResult.organisation_id == Feedback.organisation_id,
                AnalysisResult.is_current.is_(True),
            ),
        )
        .outerjoin(Category, Category.id == AnalysisResult.category_id)
        .where(Feedback.organisation_id == organisation_id)
    )

    return _apply_filters(query, filters)


def _apply_filters(query: Select, filters: FeedbackFilters) -> Select:
    if filters.sentiment:
        sentiment = filters.sentiment.lower()
        if sentiment not in SENTIMENTS:
            raise InvalidFilter(
                f"Unknown sentiment '{filters.sentiment}'. Choose one of: "
                f"{', '.join(SENTIMENTS)}."
            )
        query = query.where(AnalysisResult.sentiment_label == sentiment)

    if filters.category_key:
        # Filtered by the stable key, never the display name: a reworded label must not
        # change what a saved filter or a dashboard link returns.
        query = query.where(Category.key == filters.category_key)

    if filters.unclassified is not None:
        query = query.where(AnalysisResult.is_unclassified.is_(filters.unclassified))

    if filters.platform:
        # platform arrives from the customer's CSV and lives in the metadata document.
        query = query.where(Feedback.meta["platform"].astext == filters.platform)

    if filters.search:
        term = filters.search.strip()
        if len(term) < 2:
            raise InvalidFilter("search needs at least two characters.")
        # ILIKE with escaped wildcards. Adequate at this size and honest about what it is:
        # substring matching, not ranked full-text search. See milestone-06.md.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Feedback.text.ilike(f"%{escaped}%", escape="\\"))

    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise InvalidFilter("date_from is after date_to.")

    if filters.date_from:
        query = query.where(Feedback.feedback_at >= filters.date_from)
    if filters.date_to:
        query = query.where(Feedback.feedback_at <= filters.date_to)

    if filters.min_rating is not None:
        _check_rating(filters.min_rating)
        query = query.where(Feedback.rating >= filters.min_rating)
    if filters.max_rating is not None:
        _check_rating(filters.max_rating)
        query = query.where(Feedback.rating <= filters.max_rating)

    if filters.analysed is not None:
        if filters.analysed:
            query = query.where(AnalysisResult.id.isnot(None))
        else:
            query = query.where(AnalysisResult.id.is_(None))

    return query


def _check_rating(value: float) -> None:
    if not 1 <= value <= 5:
        raise InvalidFilter(f"rating {value:g} is outside 1-5.")


def _page_size(requested: int | None) -> int:
    size = requested or settings.FEEDBACK_PAGE_SIZE

    if size < 1:
        raise InvalidFilter("page_size must be 1 or greater.")
    if size > settings.FEEDBACK_PAGE_SIZE_MAX:
        raise InvalidFilter(
            f"page_size {size} exceeds the maximum of {settings.FEEDBACK_PAGE_SIZE_MAX}."
        )

    return size


def _row_to_dict(row, *, detail: bool = False) -> dict:
    """
    One result row as the API's shape.

    Deliberately not the ORM object: `content_hash`, `data_source_id` and `import_batch_id`
    are internal bookkeeping, and the analysis is flattened into the fields a dashboard
    actually renders.
    """
    feedback: Feedback = row[0]

    payload = {
        "id": str(feedback.id),
        "text": feedback.text,
        "external_id": feedback.external_id,
        "rating": float(feedback.rating) if feedback.rating is not None else None,
        "language": feedback.language,
        "feedback_at": feedback.feedback_at,
        "created_at": feedback.created_at,
        "metadata": dict(feedback.meta or {}),
        "sentiment": row.sentiment_label,
        "sentiment_confidence": (
            float(row.sentiment_confidence) if row.sentiment_confidence is not None else None
        ),
        "category_key": row.category_key,
        "category": row.category_name,
        "category_confidence": (
            float(row.category_confidence) if row.category_confidence is not None else None
        ),
        "is_unclassified": bool(row.is_unclassified) if row.is_unclassified is not None else None,
        "analysed": row.sentiment_label is not None or row.is_unclassified is not None,
    }

    if detail:
        payload["import_batch_id"] = (
            str(feedback.import_batch_id) if feedback.import_batch_id else None
        )
        payload["analysed_at"] = row.analysed_at
        payload["sentiment_model_version"] = row.sentiment_model_version
        payload["categorisation_skipped"] = row.categorisation_skipped

    return payload
