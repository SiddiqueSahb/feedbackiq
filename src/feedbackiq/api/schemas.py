"""
Pydantic request and response models.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from feedbackiq.engine.types import SentimentLabel


class ReviewRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=5000)

    platform: Literal[
        "amazon",
        "yelp",
        "twitter_airline",
    ] | None = None

    model: Literal[
        "vader",
        "roberta",
        "distilbert",
        "finetuned",
        "naive_bayes",
        "logistic_regression",
    ] = "distilbert"

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:

        value = re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
            "",
            value,
        ).strip()

        if not value:
            raise ValueError("Review text cannot be empty.")

        return value

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)

    top_k: int = Field(
        10,
        ge=1,
        le=50,
    )

    platform_filter: Literal[
        "amazon",
        "yelp",
        "twitter_airline",
    ] | None = None

    # The engine's one definition rather than a local Literal in another order: `typing` caches
    # equal Literals as one object, so the order in the OpenAPI document depended on import order
    # (tests/unit/test_literal_ordering.py).
    sentiment_filter: SentimentLabel | None = None

    min_rating: float = Field(
        1.0,
        ge=1,
        le=5,
    )

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:

        return re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
            "",
            value,
        ).strip()


class TextRequest(BaseModel):
    """Plain text input — used by endpoints that don't need platform/model choice."""

    text: str = Field(..., min_length=3, max_length=5000)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:

        value = re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
            "",
            value,
        ).strip()

        if not value:
            raise ValueError("Text cannot be empty.")

        return value


class BatchPredictRequest(BaseModel):
    """A batch of review texts to score with a single sentiment model."""

    texts: list[str] = Field(..., min_length=1, max_length=200)

    model: Literal[
        "vader",
        "roberta",
        "distilbert",
        "finetuned",
        "naive_bayes",
        "logistic_regression",
    ] = "distilbert"

    @field_validator("texts")
    @classmethod
    def clean_texts(cls, value: list[str]) -> list[str]:

        cleaned = [
            re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(v)).strip()
            for v in value
        ]

        cleaned = [v for v in cleaned if v]

        if not cleaned:
            raise ValueError("At least one non-empty review text is required.")

        return cleaned


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)

    chat_history: list[dict] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:

        return re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
            "",
            value,
        ).strip()


class SentimentScore(BaseModel):
    positive: float
    neutral: float
    negative: float


class SentimentResult(BaseModel):
    label: str
    confidence: float
    scores: SentimentScore
    model: str

class SimilarReview(BaseModel):
    review_id: str
    text: str
    platform: str
    rating: float
    sentiment_label: str
    similarity_score: float

    @field_validator("review_id", mode="before")
    @classmethod
    def coerce_review_id(cls, value: object) -> str:
        # pandas may infer review_id as int64, not str — normalise here.
        return str(value)

class ComplaintCategory(BaseModel):
    category: str
    confidence: float
    description: str = ""

class ReviewAnalysis(BaseModel):
    summary: str
    keywords: list[str]
    business_insight: str
    severity: str
    priority: str
    department: str
    executive_summary: str

class AnalyseResponse(BaseModel):
    review: str
    sentiment: SentimentResult
    category: str
    categories: list[ComplaintCategory]
    similar_reviews: list[SimilarReview]
    analysis: ReviewAnalysis

class SearchResult(SimilarReview):
    pass


class BatchPredictItem(BaseModel):
    text: str
    label: str
    confidence: float


class RagSource(BaseModel):
    text: str
    full_text: str
    platform: str
    sentiment_label: str
    rating: float | int
    review_id: str
    similarity_score: float | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[RagSource]
    retrieval_count: int = 0
    # False when the pipeline refused for lack of relevant evidence — lets
    # the frontend show a refusal distinctly from a grounded answer.
    grounded: bool = True


class HealthResponse(BaseModel):
    status: str
    environment: str
    data_loaded: bool
    index_ready: bool
    llm_ready: bool


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None


# ---------------------------------------------------------------- ingestion (Milestone 5B)


class ImportRowError(BaseModel):
    """One rejected row, identified by the line number in the customer's own file."""

    row: int
    field: str
    message: str


class ImportSummary(BaseModel):
    """An import batch as a customer sees it."""

    import_id: str
    organisation_id: str
    filename: str | None = None
    status: str
    rows_received: int
    rows_imported: int
    rows_rejected: int
    created_at: datetime | None = None
    completed_at: datetime | None = None

    # The analysis behind this import (Milestone 8): its latest `analyse_import` job and that job's
    # status, so an imports page can say "queued", "analysing", "done" or "failed" after a reload,
    # not only straight after the upload. Both are null when no analysis was queued - a file in
    # which no row could be stored.
    job_id: str | None = None
    analysis_status: Literal["queued", "running", "succeeded", "failed"] | None = None


class ImportAccepted(ImportSummary):
    """
    The response to an upload: what was stored, what was not, and what happens next.

    `job_id` and `analysis_status` describe the analysis this upload queued. For an identical
    re-upload they are null - nothing new was queued - and `duplicate_upload` is true.
    """

    duplicates_in_file: int = 0
    duplicates_in_database: int = 0
    # True when this exact file had already been imported: nothing new was created and
    # `import_id` refers to the original.
    duplicate_upload: bool = False
    row_errors: list[ImportRowError] = Field(default_factory=list)


class JobSummary(BaseModel):
    """Progress of background work. `error` is a summary - never internal detail."""

    job_id: str
    organisation_id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    created_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict | None = None
