"""
RAG chat endpoint — natural-language questions about customer complaints,
answered from retrieved reviews rather than the model's own knowledge.
"""

import asyncio

from fastapi import APIRouter, HTTPException

from feedbackiq.api.schemas import ChatRequest, ChatResponse
from feedbackiq.services.rag_service import ask_question
from feedbackiq.core.logging import get_logger

router = APIRouter(tags=["RAG"])

log = get_logger("api.rag")


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask a question about customer complaints",
)
async def chat(request: ChatRequest) -> ChatResponse:

    log.info("POST /rag/chat | question='%s'", request.question[:60])

    try:
        result = await asyncio.to_thread(ask_question, request.question, request.chat_history)

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception:
        log.exception("RAG chat failed.")
        raise HTTPException(status_code=500, detail="Failed to answer the question.")

    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        retrieval_count=result.get("retrieval_count", 0),
        grounded=result.get("grounded", True),
    )
