"""
Aggregates over one organisation's feedback, computed in PostgreSQL.

    summary(session, organisation_id=...)            totals, sentiment split, average rating
    sentiment_trend(session, organisation_id=...)    volume over time, by day/week/month
    category_breakdown(session, organisation_id=...) counts, share, sentiment per category

Every figure here is a `GROUP BY` in the database. That is the whole design note: the
previous generation (`services/analytics_service.py`) loaded a 427 MB parquet file into
pandas per request and grouped in Python, which cannot be scoped to a tenant, cannot use an
index, and grows with the corpus rather than with the answer.

Aggregates read the **current** analysis result only (`is_current`), so re-analysing an
import updates the dashboard instead of double-counting it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from feedbackiq.core.logging import get_logger
from feedbackiq.db.models import AnalysisResult, Category, Feedback
from feedbackiq.services.feedback import (
    SENTIMENTS,
    FeedbackFilters,
    InvalidFilter,
    _apply_filters,
)

log = get_logger("service.analytics")

# PostgreSQL date_trunc units a caller may group a trend by.
TREND_INTERVALS = ("day", "week", "month")

# A trend is capped so one request cannot ask for a row per day since the epoch.
MAX_TREND_POINTS = 400


def summary(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    filters: FeedbackFilters | None = None,
) -> dict:
    """
    Headline numbers for one organisation: volume, sentiment split, average rating.

    One query. The sentiment counts are conditional aggregates (`count(...) FILTER`), which
    is why three numbers do not cost three round trips.
    """
    filters = filters or FeedbackFilters()

    row = session.execute(_summary_query(organisation_id, filters)).one()

    total = row.total or 0
    analysed = row.analysed or 0

    return {
        "total_feedback": total,
        "analysed": analysed,
        "not_analysed": total - analysed,
        "sentiment_counts": {sentiment: getattr(row, sentiment) or 0 for sentiment in SENTIMENTS},
        "sentiment_percentages": {
            sentiment: _percentage(getattr(row, sentiment) or 0, analysed)
            for sentiment in SENTIMENTS
        },
        "unclassified": row.unclassified or 0,
        "unclassified_percentage": _percentage(row.unclassified or 0, analysed),
        "average_rating": round(float(row.average_rating), 2) if row.average_rating else None,
        "earliest_feedback_at": row.earliest,
        "latest_feedback_at": row.latest,
    }


def sentiment_trend(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    interval: str = "day",
    filters: FeedbackFilters | None = None,
) -> list[dict]:
    """
    Feedback volume and sentiment split over time.

    Buckets come from `date_trunc` in PostgreSQL, so the database does the grouping and the
    API returns one row per period. Periods with no feedback are simply absent - filling
    gaps is presentation, and belongs to whatever draws the chart.
    """
    if interval not in TREND_INTERVALS:
        raise InvalidFilter(
            f"Unknown interval '{interval}'. Choose one of: {', '.join(TREND_INTERVALS)}."
        )

    filters = filters or FeedbackFilters()

    # Feedback with no date of its own is grouped by when it was ingested: a trend that
    # silently dropped those rows would not add up to the summary.
    period = func.date_trunc(interval, func.coalesce(Feedback.feedback_at, Feedback.created_at))

    query = (
        _base_query(organisation_id, filters)
        .with_only_columns(
            period.label("period"),
            func.count(Feedback.id).label("feedback_count"),
            *[
                func.count(AnalysisResult.id)
                .filter(AnalysisResult.sentiment_label == sentiment)
                .label(sentiment)
                for sentiment in SENTIMENTS
            ],
            func.avg(Feedback.rating).label("average_rating"),
        )
        .group_by(period)
        .order_by(period)
        .limit(MAX_TREND_POINTS)
    )

    return [
        {
            "period": row.period,
            "feedback_count": row.feedback_count,
            "positive": row.positive or 0,
            "neutral": row.neutral or 0,
            "negative": row.negative or 0,
            "average_rating": round(float(row.average_rating), 2) if row.average_rating else None,
        }
        for row in session.execute(query).all()
    ]


def category_breakdown(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    filters: FeedbackFilters | None = None,
) -> list[dict]:
    """
    Counts, share and sentiment split per category, plus the unclassified bucket.

    Grouped by the stable key, so a renamed category keeps its history in one row. The
    unclassified count is reported alongside as its own entry with a null key, because
    "what could the categoriser not place?" is the emerging-issue signal and hiding it in a
    zero would waste it.
    """
    filters = filters or FeedbackFilters()

    query = (
        _base_query(organisation_id, filters)
        .with_only_columns(
            Category.key.label("category_key"),
            Category.name.label("category_name"),
            func.count(AnalysisResult.id).label("count"),
            *[
                func.count(AnalysisResult.id)
                .filter(AnalysisResult.sentiment_label == sentiment)
                .label(sentiment)
                for sentiment in SENTIMENTS
            ],
            func.avg(AnalysisResult.category_confidence).label("average_confidence"),
        )
        .where(AnalysisResult.id.isnot(None))
        .group_by(Category.key, Category.name)
        .order_by(func.count(AnalysisResult.id).desc())
    )

    rows = session.execute(query).all()
    analysed = sum(row.count for row in rows) or 0

    breakdown = []
    for row in rows:
        breakdown.append(
            {
                # A row with no category is the unclassified/skipped bucket.
                "category_key": row.category_key,
                "category": row.category_name or "Unclassified / Not categorised",
                "count": row.count,
                "percentage": _percentage(row.count, analysed),
                "sentiment_counts": {
                    sentiment: getattr(row, sentiment) or 0 for sentiment in SENTIMENTS
                },
                "average_confidence": (
                    round(float(row.average_confidence), 3) if row.average_confidence else None
                ),
            }
        )

    return breakdown


def available_categories(session: Session, *, organisation_id: uuid.UUID) -> list[dict]:
    """
    The categories this organisation's analysis may use: its own, plus the active defaults.

    Retired research categories (`is_active = false`) are excluded from what is *offered*,
    while `category_breakdown` still reports them if historical results reference them -
    which is the whole reason Milestone 6 retired them instead of deleting them.
    """
    rows = session.execute(
        select(Category)
        .where(
            Category.is_active.is_(True),
            (Category.organisation_id == organisation_id)
            | (Category.organisation_id.is_(None)),
        )
        .order_by(Category.organisation_id.isnot(None).desc(), Category.name)
    ).scalars().all()

    # An organisation's own category shadows a default with the same key.
    seen: dict[str, dict] = {}
    for category in rows:
        seen.setdefault(
            category.key,
            {
                "key": category.key,
                "name": category.name,
                "description": category.description,
                "source": category.source,
                "organisation_specific": category.organisation_id is not None,
            },
        )

    return list(seen.values())


# ---------------------------------------------------------------- query building


def _base_query(organisation_id: uuid.UUID, filters: FeedbackFilters) -> Select:
    """
    Feedback joined to its current analysis result and that result's category, scoped to one
    organisation, with the caller's filters applied.

    The same join and the same `_apply_filters` the listing endpoint uses, so a dashboard
    number and the list behind it can never disagree about what was counted.
    """
    query = (
        select(Feedback.id)
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


def _summary_query(organisation_id: uuid.UUID, filters: FeedbackFilters) -> Select:
    return _base_query(organisation_id, filters).with_only_columns(
        func.count(Feedback.id).label("total"),
        func.count(AnalysisResult.id).label("analysed"),
        *[
            func.count(AnalysisResult.id)
            .filter(AnalysisResult.sentiment_label == sentiment)
            .label(sentiment)
            for sentiment in SENTIMENTS
        ],
        func.count(AnalysisResult.id)
        .filter(AnalysisResult.is_unclassified.is_(True))
        .label("unclassified"),
        func.avg(Feedback.rating).label("average_rating"),
        func.min(Feedback.feedback_at).label("earliest"),
        func.max(Feedback.feedback_at).label("latest"),
    )


def _percentage(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


__all__ = [
    "available_categories",
    "category_breakdown",
    "sentiment_trend",
    "summary",
    "TREND_INTERVALS",
]
