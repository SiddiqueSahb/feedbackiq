import asyncio

from fastapi import APIRouter, HTTPException, Query

from feedbackiq.engine.types import SentimentLabel

from feedbackiq.services.analytics_service import (
    get_platform_list,
    get_rating_distribution,
    get_sentiment_by_platform,
    get_summary_stats,
    get_top_keywords,
    get_trend_data,
)

from feedbackiq.core.logging import get_logger

router = APIRouter(tags=["Analytics"])

log = get_logger("api.analytics")


@router.get(
    "/summary",
    summary="Dashboard summary",
    description="Return overall review statistics."
)
async def summary() -> dict:

    log.info("GET /analytics/summary")

    try:
        return await asyncio.to_thread(get_summary_stats)

    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="Processed dataset not found."
        )

    except Exception:
        log.exception("Unable to load dashboard summary.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load dashboard summary."
        )

@router.get(
    "/sentiment-by-platform",
    summary="Platform sentiment"
)
async def sentiment_by_platform() -> list[dict]:

    log.info("GET /analytics/sentiment-by-platform")

    try:
        return await asyncio.to_thread(get_sentiment_by_platform)

    except Exception:

        log.exception("Unable to load platform sentiment.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load platform sentiment."
        )


@router.get(
    "/rating-distribution",
    summary="Rating distribution"
)
async def rating_distribution() -> list[dict]:

    log.info("GET /analytics/rating-distribution")

    try:
        return await asyncio.to_thread(get_rating_distribution)

    except Exception:

        log.exception("Unable to load rating distribution.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load rating distribution."
        )

@router.get(
    "/keywords",
    summary="Top keywords",
    description="Return the most frequent business keywords."
)
async def keywords(
    # The engine's one definition, not a local Literal: two Literals with the same values in a
    # different order are cached as one object by `typing`, so whichever was created first
    # decided the enum order in the OpenAPI document (tests/unit/test_literal_ordering.py).
    sentiment: SentimentLabel | None = None,
    n: int = Query(30, ge=1, le=100),
    top_n: int | None = Query(None, ge=1, le=100),
) -> list[dict]:

    count = top_n or n

    log.info(
        "GET /analytics/keywords | sentiment=%s | n=%d",
        sentiment,
        count,
    )

    try:
        return await asyncio.to_thread(
            get_top_keywords,
            sentiment=sentiment,
            n=count,
        )

    except Exception:

        log.exception("Unable to load keywords.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load keywords.",
        )

@router.get(
    "/platforms",
    summary="Available platforms"
)
async def platforms() -> list[str]:

    log.info("GET /analytics/platforms")

    try:
        return await asyncio.to_thread(get_platform_list)

    except Exception:

        log.exception("Unable to load platforms.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load platform list.",
        )

@router.get(
    "/trends",
    summary="Monthly sentiment trends",
    description="Return monthly sentiment trends for dashboard visualisation."
)
async def trends() -> list[dict]:

    log.info("GET /analytics/trends")

    try:
        return await asyncio.to_thread(get_trend_data)

    except Exception:

        log.exception("Unable to load trend data.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load trend data.",
        )