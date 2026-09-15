"""
Aggregates: /api/v1/analytics/*

Every number comes from a `GROUP BY` in PostgreSQL (`services/analytics.py`). The filters
are the same ones the listing endpoint accepts, and they go through the same code, so a
headline figure and the list behind it always agree about what was counted.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from feedbackiq.api.deps import get_current_organisation
from feedbackiq.api.v1.schemas import AnalyticsSummary, Category, CategoryStat, TrendPoint
from feedbackiq.core.logging import get_logger
from feedbackiq.db.session import session_scope
from feedbackiq.services.auth import AuthContext
from feedbackiq.services.analytics import (
    available_categories,
    category_breakdown,
    sentiment_trend,
    summary,
)
from feedbackiq.services.feedback import FeedbackFilters, InvalidFilter

router = APIRouter(tags=["Analytics (v1)"])

log = get_logger("api.v1.analytics")


def _filters(
    sentiment: str | None,
    category_key: str | None,
    platform: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> FeedbackFilters:
    return FeedbackFilters(
        sentiment=sentiment,
        category_key=category_key,
        platform=platform,
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/analytics/summary",
    response_model=AnalyticsSummary,
    summary="Headline metrics",
    description=(
        "Volume, sentiment split, unclassified share and average rating for one "
        "organisation. Percentages are of *analysed* feedback, so they are not diluted by "
        "items the worker has not reached yet."
    ),
)
async def read_summary(
    sentiment: Literal["positive", "neutral", "negative"] | None = None,
    category_key: str | None = None,
    platform: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    context: AuthContext = Depends(get_current_organisation),
) -> AnalyticsSummary:

    log.info("GET /v1/analytics/summary")

    return await _run(
        context.organisation_id,
        summary,
        _filters(sentiment, category_key, platform, date_from, date_to),
        AnalyticsSummary,
        "Could not load the summary.",
    )


@router.get(
    "/analytics/trend",
    response_model=list[TrendPoint],
    summary="Feedback volume over time",
    description=(
        "Counts per day, week or month, bucketed with date_trunc in PostgreSQL. Feedback "
        "with no date of its own is grouped by when it was ingested, so the trend adds up to "
        "the summary. Periods with no feedback are absent rather than zero-filled."
    ),
)
async def read_trend(
    interval: Literal["day", "week", "month"] = "day",
    sentiment: Literal["positive", "neutral", "negative"] | None = None,
    category_key: str | None = None,
    platform: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    context: AuthContext = Depends(get_current_organisation),
) -> list[TrendPoint]:

    log.info("GET /v1/analytics/trend | interval=%s", interval)

    filters = _filters(sentiment, category_key, platform, date_from, date_to)

    try:
        return await asyncio.to_thread(_trend, context.organisation_id, interval, filters)

    except InvalidFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except HTTPException:
        raise

    except Exception:
        log.exception("Loading the trend failed.")
        raise HTTPException(status_code=500, detail="Could not load the trend.")


@router.get(
    "/analytics/categories",
    response_model=list[CategoryStat],
    summary="Category breakdown",
    description=(
        "Counts, share and sentiment split per category, largest first. Grouped by the "
        "stable category key, so a renamed category keeps one row. The entry with a null "
        "`category_key` holds results with no category - unclassified items and those the "
        "sentiment gate skipped."
    ),
)
async def read_category_breakdown(
    sentiment: Literal["positive", "neutral", "negative"] | None = None,
    platform: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    context: AuthContext = Depends(get_current_organisation),
) -> list[CategoryStat]:

    log.info("GET /v1/analytics/categories")

    filters = _filters(sentiment, None, platform, date_from, date_to)

    try:
        return await asyncio.to_thread(_categories, context.organisation_id, filters)

    except InvalidFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except HTTPException:
        raise

    except Exception:
        log.exception("Loading the category breakdown failed.")
        raise HTTPException(status_code=500, detail="Could not load the category breakdown.")


@router.get(
    "/categories",
    response_model=list[Category],
    summary="Categories available to this organisation",
    description=(
        "The active taxonomy: this organisation's own categories plus the shared defaults. "
        "Retired research categories are excluded here, while the breakdown above still "
        "reports them if historical results reference them."
    ),
)
async def read_categories(
    context: AuthContext = Depends(get_current_organisation),
) -> list[Category]:

    log.info("GET /v1/categories")

    try:
        return await asyncio.to_thread(_available, context.organisation_id)

    except HTTPException:
        raise

    except Exception:
        log.exception("Loading categories failed.")
        raise HTTPException(status_code=500, detail="Could not load categories.")


# ---------------------------------------------------------------- blocking work


async def _run(organisation_id: uuid.UUID, function, filters: FeedbackFilters, model, error: str):
    try:
        return await asyncio.to_thread(_scoped, organisation_id, function, filters, model)

    except InvalidFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except HTTPException:
        raise

    except Exception:
        log.exception("%s", error)
        raise HTTPException(status_code=500, detail=error)


def _scoped(organisation_id: uuid.UUID, function, filters: FeedbackFilters, model):
    with session_scope() as session:
        return model(**function(session, organisation_id=organisation_id, filters=filters))


def _trend(organisation_id: uuid.UUID, interval: str, filters: FeedbackFilters) -> list[TrendPoint]:
    with session_scope() as session:
        rows = sentiment_trend(
            session, organisation_id=organisation_id, interval=interval, filters=filters
        )

        return [TrendPoint(**row) for row in rows]


def _categories(organisation_id: uuid.UUID, filters: FeedbackFilters) -> list[CategoryStat]:
    with session_scope() as session:
        rows = category_breakdown(session, organisation_id=organisation_id, filters=filters)

        return [CategoryStat(**row) for row in rows]


def _available(organisation_id: uuid.UUID) -> list[Category]:
    with session_scope() as session:
        return [
            Category(**row)
            for row in available_categories(session, organisation_id=organisation_id)
        ]
