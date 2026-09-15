"""
Reading feedback: /api/v1/feedback

Every filter is applied in PostgreSQL by `services/feedback.py`. The route's job is to
validate what arrived, hand it over with the signed-in user's organisation, and map an
invalid filter to 422 rather than a 500.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from feedbackiq.api.deps import get_current_organisation
from feedbackiq.api.v1.schemas import FeedbackDetail, FeedbackItem, FeedbackPage
from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.db.session import session_scope
from feedbackiq.services.auth import AuthContext
from feedbackiq.services.feedback import (
    FeedbackFilters,
    InvalidFilter,
    get_feedback,
    list_feedback,
)

router = APIRouter(tags=["Feedback (v1)"])

log = get_logger("api.v1.feedback")


@router.get(
    "/feedback",
    response_model=FeedbackPage,
    summary="List feedback",
    description=(
        "One organisation's feedback, newest first, with its current analysis. Every filter "
        "is applied in the database. Items not yet analysed are included with null analysis "
        "fields; use `analysed=false` to find them."
    ),
)
async def list_items(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(
        None, ge=1, le=settings.FEEDBACK_PAGE_SIZE_MAX,
        description=f"defaults to {settings.FEEDBACK_PAGE_SIZE}",
    ),
    sentiment: Literal["positive", "neutral", "negative"] | None = None,
    category_key: str | None = Query(None, description="stable category key, not its name"),
    unclassified: bool | None = Query(None, description="only items the categoriser could not place"),
    analysed: bool | None = Query(None, description="filter on whether analysis has run"),
    platform: str | None = Query(None, description="matches metadata.platform from the upload"),
    search: str | None = Query(None, min_length=2, max_length=200, description="substring of the text"),
    date_from: datetime | None = Query(None, description="feedback_at >= this"),
    date_to: datetime | None = Query(None, description="feedback_at <= this"),
    min_rating: float | None = Query(None, ge=1, le=5),
    max_rating: float | None = Query(None, ge=1, le=5),
    sort: Literal["feedback_at", "created_at", "rating"] = "feedback_at",
    order: Literal["desc", "asc"] = "desc",
    context: AuthContext = Depends(get_current_organisation),
) -> FeedbackPage:

    filters = FeedbackFilters(
        sentiment=sentiment,
        category_key=category_key,
        unclassified=unclassified,
        analysed=analysed,
        platform=platform,
        search=search,
        date_from=date_from,
        date_to=date_to,
        min_rating=min_rating,
        max_rating=max_rating,
    )

    log.info("GET /v1/feedback | page=%d | sentiment=%s | category=%s", page, sentiment, category_key)

    try:
        return await asyncio.to_thread(
            _list, context.organisation_id, filters, page, page_size, sort, order == "desc"
        )

    except InvalidFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except HTTPException:
        raise

    except Exception:
        log.exception("Listing feedback failed.")
        raise HTTPException(status_code=500, detail="Could not list feedback.")


@router.get(
    "/feedback/{feedback_id}",
    response_model=FeedbackDetail,
    summary="One piece of feedback",
)
async def read_item(
    feedback_id: str,
    context: AuthContext = Depends(get_current_organisation),
) -> FeedbackDetail:

    try:
        identifier = uuid.UUID(feedback_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{feedback_id}' is not a valid feedback id.")

    try:
        return await asyncio.to_thread(_detail, context.organisation_id, identifier)

    except HTTPException:
        raise

    except Exception:
        log.exception("Reading feedback %s failed.", feedback_id)
        raise HTTPException(status_code=500, detail="Could not read the feedback.")


# ---------------------------------------------------------------- blocking work


def _list(
    organisation_id: uuid.UUID,
    filters: FeedbackFilters,
    page: int,
    page_size: int | None,
    sort: str,
    descending: bool,
) -> FeedbackPage:
    with session_scope() as session:
        result = list_feedback(
            session,
            organisation_id=organisation_id,
            filters=filters,
            page=page,
            page_size=page_size,
            sort=sort,
            descending=descending,
        )

        return FeedbackPage(
            items=[FeedbackItem(**item) for item in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            pages=result.pages,
            has_next=result.has_next,
        )


def _detail(organisation_id: uuid.UUID, feedback_id: uuid.UUID) -> FeedbackDetail:
    with session_scope() as session:
        item = get_feedback(
            session, organisation_id=organisation_id, feedback_id=feedback_id
        )

        if item is None:
            # 404 rather than 403: another organisation's id must be indistinguishable
            # from one that does not exist.
            raise HTTPException(status_code=404, detail="No such feedback.")

        return FeedbackDetail(**item)
