"""
The /api/v1 HTTP contract - feedbackiq.api.v1

No database. The blocking helpers each route delegates to are replaced with fakes, which
also proves the property they were written for: **an invalid or unauthenticated request
never reaches the database**, and this suite can import the app with no PostgreSQL running.

What is checked here is the contract a frontend depends on: status codes, validation, and
that nothing internal leaks out of an error.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from feedbackiq.api.main import app
from feedbackiq.api.v1 import analytics as analytics_routes
from feedbackiq.api.v1 import feedback as feedback_routes
from feedbackiq.api.v1 import imports as import_routes
from feedbackiq.api.v1.schemas import (
    AnalyticsSummary,
    Category,
    CategoryStat,
    FeedbackDetail,
    FeedbackItem,
    FeedbackPage,
    TrendPoint,
)
from feedbackiq.core.config import settings
from feedbackiq.services.feedback import InvalidFilter

VALID_KEY = {"x-api-key": settings.API_KEY}

FEEDBACK_ID = "11111111-1111-1111-1111-111111111111"
IMPORT_ID = "22222222-2222-2222-2222-222222222222"
JOB_ID = "33333333-3333-3333-3333-333333333333"
ORG_ID = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def fail_if_called(*args, **kwargs):
    raise AssertionError("a request that should have been rejected reached the database")


def item(**overrides) -> FeedbackItem:
    fields = {
        "id": FEEDBACK_ID,
        "text": "waited forty minutes for a table",
        "rating": 1.0,
        "analysed": True,
        "sentiment": "negative",
        "sentiment_confidence": 0.97,
        "category_key": "wait_times_and_delays",
        "category": "Wait Times & Delays",
        "category_confidence": 0.81,
        "is_unclassified": False,
    }
    fields.update(overrides)

    return FeedbackItem(**fields)


def page(**overrides) -> FeedbackPage:
    fields = {
        "items": [item()],
        "total": 1,
        "page": 1,
        "page_size": 50,
        "pages": 1,
        "has_next": False,
    }
    fields.update(overrides)

    return FeedbackPage(**fields)


# ---------------------------------------------------------------- authentication


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/feedback",
        f"/api/v1/feedback/{FEEDBACK_ID}",
        "/api/v1/analytics/summary",
        "/api/v1/analytics/trend",
        "/api/v1/analytics/categories",
        "/api/v1/categories",
        "/api/v1/imports",
        f"/api/v1/imports/{IMPORT_ID}",
        f"/api/v1/jobs/{JOB_ID}",
    ],
)
def test_every_v1_route_requires_the_api_key(client, monkeypatch, path):
    for module, name in [
        (feedback_routes, "_list"), (feedback_routes, "_detail"),
        (analytics_routes, "_scoped"), (analytics_routes, "_trend"),
        (analytics_routes, "_categories"), (analytics_routes, "_available"),
        (import_routes, "_list"), (import_routes, "_import"), (import_routes, "_job"),
    ]:
        monkeypatch.setattr(module, name, fail_if_called)

    assert client.get(path).status_code == 401


# ---------------------------------------------------------------- feedback


def test_listing_returns_a_page(client, monkeypatch):
    monkeypatch.setattr(feedback_routes, "_list", lambda *a: page())

    response = client.get("/api/v1/feedback", headers=VALID_KEY)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["category_key"] == "wait_times_and_delays"


def test_filters_reach_the_service(client, monkeypatch):
    seen = {}

    def record(filters, page_number, page_size, sort, descending):
        seen.update(
            sentiment=filters.sentiment, category_key=filters.category_key,
            platform=filters.platform, search=filters.search, analysed=filters.analysed,
            page=page_number, page_size=page_size, sort=sort, descending=descending,
        )
        return page()

    monkeypatch.setattr(feedback_routes, "_list", record)

    client.get(
        "/api/v1/feedback?sentiment=negative&category_key=billing_and_payments"
        "&platform=app+store&search=refund&analysed=true&page=2&page_size=10"
        "&sort=rating&order=asc",
        headers=VALID_KEY,
    )

    assert seen == {
        "sentiment": "negative", "category_key": "billing_and_payments",
        "platform": "app store", "search": "refund", "analysed": True,
        "page": 2, "page_size": 10, "sort": "rating", "descending": False,
    }


@pytest.mark.parametrize(
    "query",
    [
        "page=0",                       # below the minimum
        "page_size=100000",             # above the cap
        "sentiment=grumpy",             # not one of the three
        "sort=text",                    # not a sortable column
        "order=sideways",
        "min_rating=9",
        "search=x",                     # too short
    ],
)
def test_invalid_query_parameters_are_rejected_before_any_query(client, monkeypatch, query):
    monkeypatch.setattr(feedback_routes, "_list", fail_if_called)

    assert client.get(f"/api/v1/feedback?{query}", headers=VALID_KEY).status_code == 422


def test_a_filter_the_service_rejects_becomes_422(client, monkeypatch):
    """Validation the route cannot do itself - a date range that is inverted, say."""
    def reject(*args):
        raise InvalidFilter("date_from is after date_to.")

    monkeypatch.setattr(feedback_routes, "_list", reject)

    response = client.get("/api/v1/feedback", headers=VALID_KEY)

    assert response.status_code == 422
    assert "date_from" in response.json()["detail"]


def test_feedback_detail_returns_provenance(client, monkeypatch):
    detail = FeedbackDetail(
        **item().model_dump(), sentiment_model_version="distilbert@abc123",
        import_batch_id=IMPORT_ID,
    )
    monkeypatch.setattr(feedback_routes, "_detail", lambda identifier: detail)

    body = client.get(f"/api/v1/feedback/{FEEDBACK_ID}", headers=VALID_KEY).json()

    assert body["sentiment_model_version"] == "distilbert@abc123"
    assert body["import_batch_id"] == IMPORT_ID


def test_a_malformed_feedback_id_is_422_without_a_query(client, monkeypatch):
    monkeypatch.setattr(feedback_routes, "_detail", fail_if_called)

    assert client.get("/api/v1/feedback/not-a-uuid", headers=VALID_KEY).status_code == 422


def test_an_unknown_feedback_id_is_404(client, monkeypatch):
    from fastapi import HTTPException

    def missing(identifier):
        raise HTTPException(status_code=404, detail="No such feedback.")

    monkeypatch.setattr(feedback_routes, "_detail", missing)

    assert client.get(f"/api/v1/feedback/{FEEDBACK_ID}", headers=VALID_KEY).status_code == 404


def test_an_unexpected_failure_leaks_nothing(client, monkeypatch):
    def crash(*args):
        raise RuntimeError("relation \"feedback\" does not exist at /var/lib/postgresql")

    monkeypatch.setattr(feedback_routes, "_list", crash)

    response = client.get("/api/v1/feedback", headers=VALID_KEY)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not list feedback."}
    assert "postgresql" not in response.text


# ---------------------------------------------------------------- analytics


def test_the_summary_shape(client, monkeypatch):
    result = AnalyticsSummary(
        total_feedback=10, analysed=9, not_analysed=1,
        sentiment_counts={"positive": 2, "neutral": 1, "negative": 6},
        sentiment_percentages={"positive": 22.2, "neutral": 11.1, "negative": 66.7},
        unclassified=1, unclassified_percentage=11.1, average_rating=2.4,
    )
    monkeypatch.setattr(analytics_routes, "_scoped", lambda *a: result)

    body = client.get("/api/v1/analytics/summary", headers=VALID_KEY).json()

    assert body["analysed"] == 9
    assert body["sentiment_counts"]["negative"] == 6
    assert body["average_rating"] == 2.4


def test_the_trend_shape(client, monkeypatch):
    from datetime import datetime, timezone

    points = [
        TrendPoint(period=datetime(2026, 3, 15, tzinfo=timezone.utc), feedback_count=4,
                   negative=3, neutral=1, average_rating=2.0)
    ]
    monkeypatch.setattr(analytics_routes, "_trend", lambda interval, filters: points)

    body = client.get("/api/v1/analytics/trend?interval=month", headers=VALID_KEY).json()

    assert body[0]["feedback_count"] == 4
    assert body[0]["negative"] == 3


def test_an_unknown_trend_interval_is_rejected(client, monkeypatch):
    monkeypatch.setattr(analytics_routes, "_trend", fail_if_called)

    assert client.get(
        "/api/v1/analytics/trend?interval=fortnight", headers=VALID_KEY
    ).status_code == 422


def test_the_category_breakdown_shape(client, monkeypatch):
    rows = [
        CategoryStat(category_key="wait_times_and_delays", category="Wait Times & Delays",
                     count=2, percentage=50.0,
                     sentiment_counts={"positive": 0, "neutral": 0, "negative": 2}),
        CategoryStat(category_key=None, category="Unclassified / Not categorised",
                     count=2, percentage=50.0,
                     sentiment_counts={"positive": 1, "neutral": 1, "negative": 0}),
    ]
    monkeypatch.setattr(analytics_routes, "_categories", lambda filters: rows)

    body = client.get("/api/v1/analytics/categories", headers=VALID_KEY).json()

    assert body[0]["category_key"] == "wait_times_and_delays"
    # The bucket with no category is reported, not hidden.
    assert body[1]["category_key"] is None


def test_the_available_categories_shape(client, monkeypatch):
    rows = [
        Category(key="billing_and_payments", name="Billing & Payments",
                 description="Charges, invoices or payments were wrong.", source="default")
    ]
    monkeypatch.setattr(analytics_routes, "_available", lambda: rows)

    body = client.get("/api/v1/categories", headers=VALID_KEY).json()

    assert body[0]["key"] == "billing_and_payments"
    assert body[0]["organisation_specific"] is False


# ---------------------------------------------------------------- imports and jobs


def test_listing_imports(client, monkeypatch):
    from feedbackiq.api.schemas import ImportSummary

    summary = ImportSummary(
        import_id=IMPORT_ID, organisation_id=ORG_ID, filename="february.csv",
        status="completed", rows_received=4, rows_imported=3, rows_rejected=1,
    )
    monkeypatch.setattr(import_routes, "_list", lambda limit: [summary])

    body = client.get("/api/v1/imports", headers=VALID_KEY).json()

    assert body[0]["rows_imported"] == 3


def test_reading_a_job(client, monkeypatch):
    from feedbackiq.api.schemas import JobSummary

    job = JobSummary(
        job_id=JOB_ID, organisation_id=ORG_ID, kind="analyse_import", status="succeeded",
        attempts=1, max_attempts=3, result={"analysed": 3, "succeeded": 3, "failed": 0},
    )
    monkeypatch.setattr(import_routes, "_job", lambda identifier: job)

    body = client.get(f"/api/v1/jobs/{JOB_ID}", headers=VALID_KEY).json()

    assert body["status"] == "succeeded"
    assert body["result"]["analysed"] == 3


@pytest.mark.parametrize("path", ["/api/v1/imports/not-a-uuid", "/api/v1/jobs/not-a-uuid"])
def test_malformed_ids_are_422(client, monkeypatch, path):
    monkeypatch.setattr(import_routes, "_import", fail_if_called)
    monkeypatch.setattr(import_routes, "_job", fail_if_called)

    assert client.get(path, headers=VALID_KEY).status_code == 422


def test_the_import_limit_is_capped(client, monkeypatch):
    monkeypatch.setattr(import_routes, "_list", fail_if_called)

    assert client.get("/api/v1/imports?limit=100000", headers=VALID_KEY).status_code == 422


# ---------------------------------------------------------------- documentation


def test_the_openapi_document_describes_the_v1_routes(client):
    """A frontend developer reads this; it should not be empty or unnamed."""
    schema = client.get("/openapi.json").json()

    v1_paths = {path for path in schema["paths"] if path.startswith("/api/v1")}
    auth_paths = {path for path in v1_paths if path.startswith("/api/v1/auth/")}
    # Nine customer-data paths (Milestone 6), plus four for signing in (Milestone 7).
    assert len(v1_paths - auth_paths) == 9
    assert len(auth_paths) == 4

    listing = schema["paths"]["/api/v1/feedback"]["get"]
    assert listing["summary"]
    assert listing["description"]
    assert any(p["name"] == "category_key" for p in listing["parameters"])
