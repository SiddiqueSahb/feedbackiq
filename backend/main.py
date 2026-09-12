"""
FeedbackIQ backend — FastAPI application entrypoint.

Run locally:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Routers wrap the existing nlp/, rag/, evaluate/ modules unchanged. Models
and the FAISS index load lazily on first request, not at startup.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.deps import require_api_key
from backend.api.routes import analytics, evaluation, rag, search, sentiment
from backend.models.schemas import HealthResponse
from backend.services.analytics_service import warm_cache
from config import settings
from logger import get_logger

log = get_logger("api.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Warm the analytics cache in the background so the 407MB parquet load
    doesn't block startup or delay the health check.
    """

    task = asyncio.create_task(asyncio.to_thread(warm_cache))

    yield

    if not task.done():
        task.cancel()


app = FastAPI(
    lifespan=lifespan,
    title="FeedbackIQ API",
    description=(
        "Backend for FeedbackIQ — sentiment analysis, complaint categorisation, "
        "semantic search and retrieval-augmented question answering over "
        "customer feedback."
    ),
    version="1.0.0",
)

# Streamlit frontend calls this API over HTTP, so CORS is required.
# ALLOWED_ORIGINS is comma-separated, defaults to "*" for local dev.
#
# allow_credentials intentionally omitted — invalid together with
# allow_origins=["*"] per the CORS spec, and nothing here uses cookies.
allowed_origins = [
    origin.strip()
    for origin in settings.ALLOWED_ORIGINS.split(",")
    if origin.strip()
] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with its status code and how long it took."""

    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 1)

    log.info(
        "%s %s -> %d (%sms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )

    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch anything that slips past route-level error handling."""

    log.exception("Unhandled error on %s %s", request.method, request.url.path)

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


# Prefix here builds the frontend's "/api/<area>/<path>" URLs.
#
# API key dependency attached at router level so new endpoints are
# protected by default. "/" and "/api/health" stay open — see deps.py.
protected = [Depends(require_api_key)]

app.include_router(sentiment.router, prefix="/api/sentiment", dependencies=protected)
app.include_router(search.router, prefix="/api/search", dependencies=protected)
app.include_router(rag.router, prefix="/api/rag", dependencies=protected)
app.include_router(analytics.router, prefix="/api/analytics", dependencies=protected)
app.include_router(evaluation.router, prefix="/api/evaluation", dependencies=protected)


@app.get("/", tags=["Health"], summary="API root")
async def root() -> dict:
    return {"service": "FeedbackIQ API", "status": "running", "docs": "/docs"}


@app.get("/api/health", response_model=HealthResponse, tags=["Health"], summary="Health check")
async def health() -> HealthResponse:
    """Reports whether data/index/LLM are actually available, not just that the process is alive."""

    data_loaded = os.path.exists(settings.DATA_PATH)
    index_ready = os.path.exists(settings.INDEX_PATH) or os.path.exists(settings.LANGCHAIN_INDEX_PATH)
    llm_ready = bool(settings.GROQ_API_KEY)

    return HealthResponse(
        status="ok",
        environment=os.getenv("ENVIRONMENT", "development"),
        data_loaded=data_loaded,
        index_ready=index_ready,
        llm_ready=llm_ready,
    )
