"""
Semantic search endpoint — finds reviews similar in meaning to a query,
using the FAISS index built by scripts/build_index.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException

from feedbackiq.api.schemas import SearchRequest, SearchResult
from feedbackiq.services.search_service import search_reviews
from feedbackiq.core.logging import get_logger

router = APIRouter(tags=["Search"])

log = get_logger("api.search")


@router.post(
    "",
    response_model=list[SearchResult],
    summary="Semantic search over customer reviews",
)
async def search(request: SearchRequest) -> list[SearchResult]:

    log.info("POST /search | query='%s'", request.query[:60])

    try:
        return await asyncio.to_thread(
            search_reviews,
            query=request.query,
            top_k=request.top_k,
            platform_filter=request.platform_filter,
            sentiment_filter=request.sentiment_filter,
            min_rating=request.min_rating,
        )

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="Search index not found. Run scripts/build_index.py first.",
        )

    except Exception:
        log.exception("Semantic search failed.")
        raise HTTPException(status_code=500, detail="Semantic search failed.")
