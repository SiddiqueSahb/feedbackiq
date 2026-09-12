import asyncio
import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        )
    ),
)

from fastapi import APIRouter, HTTPException

from backend.services.evaluation_service import (
    get_rag_evaluation,
    get_retrieval_evaluation,
    get_sentiment_evaluation,
)

from logger import get_logger

router = APIRouter(tags=["Evaluation"])

log = get_logger("api.evaluation")


@router.get(
    "/sentiment",
    summary="Sentiment classifier evaluation",
    description="Model comparison, confusion matrices and significance tests "
                "for the five sentiment classifiers.",
)
async def sentiment_evaluation() -> dict:

    log.info("GET /evaluation/sentiment")

    try:
        return await asyncio.to_thread(get_sentiment_evaluation)

    except Exception:
        log.exception("Unable to load sentiment evaluation results.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load sentiment evaluation results.",
        )


@router.get(
    "/retrieval",
    summary="Retrieval evaluation",
    description="TF-IDF vs semantic search (FAISS) retrieval quality and latency.",
)
async def retrieval_evaluation() -> dict:

    log.info("GET /evaluation/retrieval")

    try:
        return await asyncio.to_thread(get_retrieval_evaluation)

    except Exception:
        log.exception("Unable to load retrieval evaluation results.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load retrieval evaluation results.",
        )


@router.get(
    "/rag",
    summary="RAG / RAGAS evaluation",
    description="Faithfulness, answer relevancy and other RAG pipeline metrics "
                "from scripts/evaluate_rag.py. Returns available=false if that "
                "script hasn't been run yet.",
)
async def rag_evaluation() -> dict:

    log.info("GET /evaluation/rag")

    try:
        return await asyncio.to_thread(get_rag_evaluation)

    except Exception:
        log.exception("Unable to load RAG evaluation results.")

        raise HTTPException(
            status_code=500,
            detail="Failed to load RAG evaluation results.",
        )
