"""
Request and response shapes for /api/v1.

Kept separate from `api/schemas.py`, which serves the dissertation-era routes and the
Streamlit tool. Those shapes exist to keep an internal tool working; these are a contract a
frontend will be written against, and the two should be free to move independently.

Field names are the ones a dashboard actually needs. Internal bookkeeping - content hashes,
data source ids, run ids - is deliberately absent.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackItem(BaseModel):
    """One piece of feedback with its current analysis, as a list row."""

    id: str
    text: str
    external_id: str | None = None
    rating: float | None = None
    language: str | None = None
    feedback_at: datetime | None = None
    created_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)

    # Analysis. All null when the item has not been analysed yet, which is a real state:
    # ingestion stores feedback immediately and the worker catches up.
    analysed: bool = False
    sentiment: str | None = None
    sentiment_confidence: float | None = None
    category_key: str | None = None
    category: str | None = None
    category_confidence: float | None = None
    is_unclassified: bool | None = None


class FeedbackDetail(FeedbackItem):
    """One item, with the provenance a support conversation needs."""

    import_batch_id: str | None = None
    analysed_at: datetime | None = None
    sentiment_model_version: str | None = None
    # Why no category was assigned, when the sentiment gate skipped it.
    categorisation_skipped: str | None = None


class FeedbackPage(BaseModel):
    """A page plus enough to render a pager without a second request."""

    items: list[FeedbackItem]
    total: int
    page: int
    page_size: int
    pages: int
    has_next: bool


class SentimentCounts(BaseModel):
    positive: int = 0
    neutral: int = 0
    negative: int = 0


class SentimentPercentages(BaseModel):
    positive: float = 0.0
    neutral: float = 0.0
    negative: float = 0.0


class AnalyticsSummary(BaseModel):
    """Headline numbers. Percentages are of *analysed* feedback, not of everything."""

    total_feedback: int
    analysed: int
    not_analysed: int
    sentiment_counts: SentimentCounts
    sentiment_percentages: SentimentPercentages
    unclassified: int
    unclassified_percentage: float
    average_rating: float | None = None
    earliest_feedback_at: datetime | None = None
    latest_feedback_at: datetime | None = None


class TrendPoint(BaseModel):
    """One bucket of a time series. Periods with no feedback are absent, not zero-filled."""

    period: datetime
    feedback_count: int
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    average_rating: float | None = None


class CategoryStat(BaseModel):
    """
    One row of the category breakdown.

    `category_key` is null for the bucket holding results with no category: unclassified
    items and those the sentiment gate skipped. That bucket is the emerging-issue signal, so
    it is reported rather than dropped.
    """

    category_key: str | None = None
    category: str
    count: int
    percentage: float
    sentiment_counts: SentimentCounts
    average_confidence: float | None = None


class Category(BaseModel):
    """A category available to this organisation's analysis."""

    key: str
    name: str
    description: str
    source: str
    organisation_specific: bool = False
