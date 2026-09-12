"""
Sentiment analysis endpoints.

/predict        — one review, one model, sentiment only (fast)
/analyse         — one review, full pipeline (sentiment + category + similar
                    reviews + LLM business analysis)
/compare         — one review, all five models side by side
/batch-predict   — many reviews, one model, sentiment only (used by the
                    Upload page for CSV scoring)
/models          — static list of models the frontend can offer in a dropdown
"""

import asyncio

from fastapi import APIRouter, HTTPException

from feedbackiq.api.schemas import (
    AnalyseResponse,
    BatchPredictRequest,
    ReviewRequest,
    SentimentResult,
    TextRequest,
)
from feedbackiq.services.sentiment_service import (
    analyse_review,
    batch_predict,
    compare_models,
    predict_only,
)
from feedbackiq.core.logging import get_logger

router = APIRouter(tags=["Sentiment"])

log = get_logger("api.sentiment")

# Shown in the frontend model picker. Keep in sync with ReviewRequest.model
# in backend/models/schemas.py.
AVAILABLE_MODELS = [
    {"id": "distilbert", "name": "DistilBERT (fine-tuned)", "tier": "Fine-tuned transformer"},
    {"id": "roberta", "name": "RoBERTa (pre-trained)", "tier": "Pre-trained transformer"},
    {"id": "logistic_regression", "name": "Logistic Regression", "tier": "Classical ML"},
    {"id": "naive_bayes", "name": "Naive Bayes", "tier": "Classical ML"},
    {"id": "vader", "name": "VADER", "tier": "Lexicon baseline"},
]


@router.get("/models", summary="List available sentiment models")
async def models() -> list[dict]:
    return AVAILABLE_MODELS


@router.post(
    "/predict",
    response_model=SentimentResult,
    summary="Sentiment only, one model",
)
async def predict(request: ReviewRequest) -> SentimentResult:

    log.info("POST /sentiment/predict | model=%s", request.model)

    try:
        return await asyncio.to_thread(predict_only, request.text, request.model)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception:
        log.exception("Prediction failed.")
        raise HTTPException(status_code=500, detail="Sentiment prediction failed.")


@router.post(
    "/analyse",
    response_model=AnalyseResponse,
    summary="Full review analysis pipeline",
    description=(
        "Sentiment, complaint category, similar reviews and an LLM-generated "
        "business summary — everything the Analyse page shows."
    ),
)
async def analyse(request: ReviewRequest) -> AnalyseResponse:

    log.info("POST /sentiment/analyse | model=%s", request.model)

    try:
        result = await asyncio.to_thread(analyse_review, request.text, request.model)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception:
        log.exception("Review analysis failed.")
        raise HTTPException(status_code=500, detail="Review analysis failed.")

    # Reshape analyse_review()'s flat dict into AnalyseResponse's nested
    # "analysis" field. Confidence is "score" normally but "confidence" in
    # the error fallback path — accept either.
    categories = [
        {
            "category": cat.get("category", "Unknown"),
            "confidence": cat.get("confidence", cat.get("score", 0.0)),
            "description": cat.get("description", ""),
        }
        for cat in result["categories"]
    ]

    return AnalyseResponse(
        review=result["review"],
        sentiment=result["sentiment"],
        category=result["category"],
        categories=categories,
        similar_reviews=result["similar_reviews"],
        analysis={
            "summary": result.get("summary", ""),
            "keywords": result.get("keywords", []),
            "business_insight": result.get("business_insight", ""),
            "severity": result.get("severity", ""),
            "priority": result.get("priority", ""),
            "department": result.get("department", ""),
            "executive_summary": result.get("executive_summary", ""),
        },
    )


@router.post(
    "/compare",
    summary="Compare all five sentiment models on one review",
)
async def compare(request: TextRequest) -> dict:

    log.info("POST /sentiment/compare")

    try:
        return await asyncio.to_thread(compare_models, request.text)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception:
        log.exception("Model comparison failed.")
        raise HTTPException(status_code=500, detail="Model comparison failed.")


@router.post(
    "/batch-predict",
    summary="Sentiment for many reviews at once",
    description="Used by the Upload page to score an uploaded CSV. Capped at 200 rows per call.",
)
async def batch(request: BatchPredictRequest) -> list[dict]:

    log.info("POST /sentiment/batch-predict | n=%d | model=%s", len(request.texts), request.model)

    try:
        return await asyncio.to_thread(batch_predict, request.texts, request.model)

    except Exception:
        log.exception("Batch prediction failed.")
        raise HTTPException(status_code=500, detail="Batch prediction failed.")
