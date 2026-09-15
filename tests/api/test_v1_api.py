"""
The /api/v1 HTTP contract - feedbackiq.api.v1

No database. The blocking helpers each route delegates to are replaced with fakes, which
also proves the property they were written for: **an invalid or unauthenticated request
never reaches the database**, and this suite can import the app with no PostgreSQL running.

Signing in is replaced too: the `signed_in` fixture overrides `get_current_organisation` with a
fixed context, the way FastAPI intends dependencies to be swapped in tests. The tests that are
*about* authentication remove that override and use the real dependencies.

What is checked here is the contract a frontend depends on: status codes, validation, that the
organisation every query receives is the session's, and that nothing internal leaks out of an
error.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from feedbackiq.api import deps
from feedbackiq.api.main import app
from feedbackiq.api.routes import imports as upload_routes
from feedbackiq.api.schemas import ImportAccepted, ImportSummary, JobSummary
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
from feedbackiq.services.auth import AuthContext
from feedbackiq.services.feedback import InvalidFilter

VALID_KEY = {"x-api-key": settings.API_KEY}

FEEDBACK_ID = "11111111-1111-1111-1111-111111111111"
IMPORT_ID = "22222222-2222-2222-2222-222222222222"
JOB_ID = "33333333-3333-3333-3333-333333333333"
ORG_ID = "44444444-4444-4444-4444-444444444444"
OTHER_ORG_ID = "55555555-5555-5555-5555-555555555555"

CONTEXT = AuthContext(
    user_id=uuid.uuid4(),
    email="ana@example.com",
    session_id=uuid.uuid4(),
    organisation_id=uuid.UUID(ORG_ID),
    role="member",
)

# Every customer-data GET route under /api/v1, with the helper that does its database work.
DATA_ROUTES = [
    ("/api/v1/feedback", feedback_routes, "_list"),
    (f"/api/v1/feedback/{FEEDBACK_ID}", feedback_routes, "_detail"),
    ("/api/v1/analytics/summary", analytics_routes, "_scoped"),
    ("/api/v1/analytics/trend", analytics_routes, "_trend"),
    ("/api/v1/analytics/categories", analytics_routes, "_categories"),
    ("/api/v1/categories", analytics_routes, "_available"),
    ("/api/v1/imports", import_routes, "_list"),
    (f"/api/v1/imports/{IMPORT_ID}", import_routes, "_import"),
    (f"/api/v1/jobs/{JOB_ID}", import_routes, "_job"),
]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def signed_in():
    """Every test acts as a signed-in member of ORG_ID unless it asks for `signed_out`."""
    app.dependency_overrides[deps.get_current_organisation] = lambda: CONTEXT
    yield
    app.dependency_overrides.pop(deps.get_current_organisation, None)
    app.dependency_overrides.pop(deps.get_current_user, None)


@pytest.fixture()
def signed_out(signed_in):
    """The real authentication dependencies, for tests about authentication itself."""
    app.dependency_overrides.pop(deps.get_current_organisation, None)


def fail_if_called(*args, **kwargs):
    raise AssertionError("a request that should have been rejected reached the database")


def refuse_every_helper(monkeypatch):
    monkeypatch.setattr(deps, "_lookup_session", fail_if_called)
    monkeypatch.setattr(upload_routes, "_store_import", fail_if_called)
    for _, module, name in DATA_ROUTES:
        monkeypatch.setattr(module, name, fail_if_called)


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


def summary_result() -> AnalyticsSummary:
    return AnalyticsSummary(
        total_feedback=10, analysed=9, not_analysed=1, analysis_pending=0, analysis_failed=1,
        sentiment_counts={"positive": 2, "neutral": 1, "negative": 6},
        sentiment_percentages={"positive": 22.2, "neutral": 11.1, "negative": 66.7},
        unclassified=1, unclassified_percentage=11.1, average_rating=2.4,
    )


def import_summary() -> ImportSummary:
    return ImportSummary(
        import_id=IMPORT_ID, organisation_id=ORG_ID, filename="february.csv",
        status="completed", rows_received=4, rows_imported=3, rows_rejected=1,
    )


def job_summary() -> JobSummary:
    return JobSummary(
        job_id=JOB_ID, organisation_id=ORG_ID, kind="analyse_import", status="succeeded",
        attempts=1, max_attempts=3, result={"analysed": 3, "succeeded": 3, "failed": 0},
    )


# ---------------------------------------------------------------- authentication


@pytest.mark.parametrize("path", [path for path, _, _ in DATA_ROUTES])
def test_every_v1_data_route_requires_a_signed_in_user(client, monkeypatch, signed_out, path):
    refuse_every_helper(monkeypatch)

    response = client.get(path)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not signed in."}


@pytest.mark.parametrize("path", [path for path, _, _ in DATA_ROUTES])
def test_the_research_api_key_is_not_a_way_into_customer_data(client, monkeypatch, signed_out, path):
    refuse_every_helper(monkeypatch)

    assert client.get(path, headers=VALID_KEY).status_code == 401


def test_uploading_requires_a_signed_in_user(client, monkeypatch, signed_out):
    refuse_every_helper(monkeypatch)

    response = client.post(
        "/api/v1/imports", files={"file": ("f.csv", b"text\nhello\n", "text/csv")}, headers=VALID_KEY
    )

    assert response.status_code == 401


@pytest.mark.parametrize("path", [path for path, _, _ in DATA_ROUTES])
def test_a_signed_in_user_with_no_organisation_is_403_before_any_query(
    client, monkeypatch, signed_out, path
):
    refuse_every_helper(monkeypatch)
    app.dependency_overrides[deps.get_current_user] = lambda: AuthContext(
        user_id=uuid.uuid4(), email="loner@example.com", session_id=uuid.uuid4(),
        organisation_id=None, role=None,
    )

    response = client.get(path)

    assert response.status_code == 403
    assert response.json() == {"detail": "You are not a member of an organisation."}


@pytest.mark.parametrize(
    "path, module, name, result",
    [
        ("/api/v1/feedback", feedback_routes, "_list", page()),
        (f"/api/v1/feedback/{FEEDBACK_ID}", feedback_routes, "_detail", FeedbackDetail(**item().model_dump())),
        ("/api/v1/analytics/summary", analytics_routes, "_scoped", summary_result()),
        ("/api/v1/analytics/trend", analytics_routes, "_trend", []),
        ("/api/v1/analytics/categories", analytics_routes, "_categories", []),
        ("/api/v1/categories", analytics_routes, "_available", []),
        ("/api/v1/imports", import_routes, "_list", [import_summary()]),
        (f"/api/v1/imports/{IMPORT_ID}", import_routes, "_import", import_summary()),
        (f"/api/v1/jobs/{JOB_ID}", import_routes, "_job", job_summary()),
    ],
)
def test_every_route_queries_the_sessions_organisation_whatever_the_request_says(
    client, monkeypatch, path, module, name, result
):
    """A forged organisation id in the query string and in a header changes nothing: the
    helper that runs the query receives the organisation of the session, and only that."""
    received = []

    def record(organisation_id, *rest):
        received.append(organisation_id)
        return result

    monkeypatch.setattr(module, name, record)

    response = client.get(
        path,
        params={"organisation_id": OTHER_ORG_ID},
        headers={"X-Organisation-Id": OTHER_ORG_ID, "Organisation-Id": OTHER_ORG_ID},
    )

    assert response.status_code == 200
    assert received == [uuid.UUID(ORG_ID)]


# ---------------------------------------------------------------- feedback


def test_listing_returns_a_page(client, monkeypatch):
    monkeypatch.setattr(feedback_routes, "_list", lambda *a: page())

    response = client.get("/api/v1/feedback")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["category_key"] == "wait_times_and_delays"


def test_filters_reach_the_service(client, monkeypatch):
    seen = {}

    def record(organisation_id, filters, page_number, page_size, sort, descending):
        seen.update(
            organisation_id=organisation_id,
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
    )

    assert seen == {
        "organisation_id": uuid.UUID(ORG_ID),
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

    assert client.get(f"/api/v1/feedback?{query}").status_code == 422


def test_a_filter_the_service_rejects_becomes_422(client, monkeypatch):
    """Validation the route cannot do itself - a date range that is inverted, say."""
    def reject(*args):
        raise InvalidFilter("date_from is after date_to.")

    monkeypatch.setattr(feedback_routes, "_list", reject)

    response = client.get("/api/v1/feedback")

    assert response.status_code == 422
    assert "date_from" in response.json()["detail"]


def test_feedback_detail_returns_provenance(client, monkeypatch):
    detail = FeedbackDetail(
        **item().model_dump(), sentiment_model_version="distilbert@abc123",
        import_batch_id=IMPORT_ID,
    )
    monkeypatch.setattr(feedback_routes, "_detail", lambda organisation_id, identifier: detail)

    body = client.get(f"/api/v1/feedback/{FEEDBACK_ID}").json()

    assert body["sentiment_model_version"] == "distilbert@abc123"
    assert body["import_batch_id"] == IMPORT_ID


def test_a_malformed_feedback_id_is_422_without_a_query(client, monkeypatch):
    monkeypatch.setattr(feedback_routes, "_detail", fail_if_called)

    assert client.get("/api/v1/feedback/not-a-uuid").status_code == 422


def test_an_unknown_feedback_id_is_404(client, monkeypatch):
    from fastapi import HTTPException

    def missing(organisation_id, identifier):
        raise HTTPException(status_code=404, detail="No such feedback.")

    monkeypatch.setattr(feedback_routes, "_detail", missing)

    assert client.get(f"/api/v1/feedback/{FEEDBACK_ID}").status_code == 404


def test_an_unexpected_failure_leaks_nothing(client, monkeypatch):
    def crash(*args):
        raise RuntimeError("relation \"feedback\" does not exist at /var/lib/postgresql")

    monkeypatch.setattr(feedback_routes, "_list", crash)

    response = client.get("/api/v1/feedback")

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not list feedback."}
    assert "postgresql" not in response.text


# ---------------------------------------------------------------- analytics


def test_the_summary_shape(client, monkeypatch):
    monkeypatch.setattr(analytics_routes, "_scoped", lambda *a: summary_result())

    body = client.get("/api/v1/analytics/summary").json()

    assert body["analysed"] == 9
    assert body["sentiment_counts"]["negative"] == 6
    assert body["average_rating"] == 2.4
    # Unanalysed feedback is split into analysis still coming and analysis that gave up.
    assert (body["not_analysed"], body["analysis_pending"], body["analysis_failed"]) == (1, 0, 1)


def test_the_trend_shape(client, monkeypatch):
    from datetime import datetime, timezone

    points = [
        TrendPoint(period=datetime(2026, 3, 15, tzinfo=timezone.utc), feedback_count=4,
                   negative=3, neutral=1, average_rating=2.0)
    ]
    monkeypatch.setattr(analytics_routes, "_trend", lambda organisation_id, interval, filters: points)

    body = client.get("/api/v1/analytics/trend?interval=month").json()

    assert body[0]["feedback_count"] == 4
    assert body[0]["negative"] == 3


def test_an_unknown_trend_interval_is_rejected(client, monkeypatch):
    monkeypatch.setattr(analytics_routes, "_trend", fail_if_called)

    assert client.get("/api/v1/analytics/trend?interval=fortnight").status_code == 422


def test_the_category_breakdown_shape(client, monkeypatch):
    rows = [
        CategoryStat(category_key="wait_times_and_delays", category="Wait Times & Delays",
                     count=2, percentage=50.0,
                     sentiment_counts={"positive": 0, "neutral": 0, "negative": 2}),
        CategoryStat(category_key=None, category="Unclassified / Not categorised",
                     count=2, percentage=50.0,
                     sentiment_counts={"positive": 1, "neutral": 1, "negative": 0}),
    ]
    monkeypatch.setattr(analytics_routes, "_categories", lambda organisation_id, filters: rows)

    body = client.get("/api/v1/analytics/categories").json()

    assert body[0]["category_key"] == "wait_times_and_delays"
    # The bucket with no category is reported, not hidden.
    assert body[1]["category_key"] is None


def test_the_available_categories_shape(client, monkeypatch):
    rows = [
        Category(key="billing_and_payments", name="Billing & Payments",
                 description="Charges, invoices or payments were wrong.", source="default")
    ]
    monkeypatch.setattr(analytics_routes, "_available", lambda organisation_id: rows)

    body = client.get("/api/v1/categories").json()

    assert body[0]["key"] == "billing_and_payments"
    assert body[0]["organisation_specific"] is False


# ---------------------------------------------------------------- imports and jobs


def test_listing_imports(client, monkeypatch):
    monkeypatch.setattr(import_routes, "_list", lambda organisation_id, limit: [import_summary()])

    body = client.get("/api/v1/imports").json()

    assert body[0]["rows_imported"] == 3


def test_reading_a_job(client, monkeypatch):
    monkeypatch.setattr(import_routes, "_job", lambda organisation_id, identifier: job_summary())

    body = client.get(f"/api/v1/jobs/{JOB_ID}").json()

    assert body["status"] == "succeeded"
    assert body["result"]["analysed"] == 3


@pytest.mark.parametrize("path", ["/api/v1/imports/not-a-uuid", "/api/v1/jobs/not-a-uuid"])
def test_malformed_ids_are_422(client, monkeypatch, path):
    monkeypatch.setattr(import_routes, "_import", fail_if_called)
    monkeypatch.setattr(import_routes, "_job", fail_if_called)

    assert client.get(path).status_code == 422


def test_the_import_limit_is_capped(client, monkeypatch):
    monkeypatch.setattr(import_routes, "_list", fail_if_called)

    assert client.get("/api/v1/imports?limit=100000").status_code == 422


def test_uploading_stores_the_file_for_the_sessions_organisation(client, monkeypatch):
    """The same upload as POST /api/imports. A form field naming another organisation is not
    a parameter of the route at all, so it cannot reach the service."""
    received = {}

    def record(organisation_id, raw, filename):
        received.update(organisation_id=organisation_id, raw=raw, filename=filename)
        # job_id is a field of ImportSummary itself since Milestone 8, so it is set in the dump
        # rather than passed alongside it.
        return ImportAccepted(**{**import_summary().model_dump(), "job_id": JOB_ID})

    monkeypatch.setattr(upload_routes, "_store_import", record)

    response = client.post(
        "/api/v1/imports",
        files={"file": ("february.csv", b"text,organisation_id\nhello," + OTHER_ORG_ID.encode() + b"\n", "text/csv")},
        data={"organisation_id": OTHER_ORG_ID},
        headers={"X-Organisation-Id": OTHER_ORG_ID},
    )

    assert response.status_code == 201
    assert response.json()["job_id"] == JOB_ID
    assert received["organisation_id"] == uuid.UUID(ORG_ID)
    assert received["filename"] == "february.csv"


def test_an_oversized_v1_upload_is_413_without_reaching_the_service(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(upload_routes, "_store_import", fail_if_called)

    response = client.post(
        "/api/v1/imports", files={"file": ("big.csv", b"text\n" + b"x" * 200, "text/csv")}
    )

    assert response.status_code == 413


# ---------------------------------------------------------------- documentation


def test_the_openapi_document_describes_the_v1_routes(client):
    """A frontend developer reads this; it should not be empty or unnamed."""
    schema = client.get("/openapi.json").json()

    v1_paths = {path for path in schema["paths"] if path.startswith("/api/v1")}
    auth_paths = {path for path in v1_paths if path.startswith("/api/v1/auth/")}
    # Nine customer-data paths (Milestone 6), plus four for signing in (Milestone 7).
    # POST /api/v1/imports shares the /api/v1/imports path with the listing.
    assert len(v1_paths - auth_paths) == 9
    assert len(auth_paths) == 4
    assert set(schema["paths"]["/api/v1/imports"]) == {"get", "post"}

    listing = schema["paths"]["/api/v1/feedback"]["get"]
    assert listing["summary"]
    assert listing["description"]
    assert any(p["name"] == "category_key" for p in listing["parameters"])
