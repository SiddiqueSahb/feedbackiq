"""
The ingestion HTTP contract - feedbackiq.api.routes.imports

No database here. The three blocking helpers are replaced with fakes, which is also how
this suite proves something worth proving: an unauthenticated request never reaches the
database, and the app imports with no PostgreSQL running at all.

Since Milestone 7 these routes need a signed-in user rather than the API key. The autouse
`signed_in` fixture stands in for one; the authentication tests remove it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from feedbackiq.api import deps
from feedbackiq.api.routes import imports as import_routes
from feedbackiq.api.main import app
from feedbackiq.api.schemas import ImportAccepted, ImportSummary, JobSummary
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import IngestionError
from feedbackiq.services.auth import AuthContext

VALID_KEY = {"x-api-key": settings.API_KEY}

IMPORT_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"
ORG_ID = "33333333-3333-3333-3333-333333333333"
OTHER_ORG_ID = "99999999-9999-9999-9999-999999999999"

CSV = b"text,rating\nThe app crashes,1\n"

CONTEXT = AuthContext(
    user_id=uuid.uuid4(),
    email="ana@example.com",
    session_id=uuid.uuid4(),
    organisation_id=uuid.UUID(ORG_ID),
    role="owner",
)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def signed_in():
    app.dependency_overrides[deps.get_current_organisation] = lambda: CONTEXT
    yield
    app.dependency_overrides.pop(deps.get_current_organisation, None)
    app.dependency_overrides.pop(deps.get_current_user, None)


@pytest.fixture()
def signed_out(signed_in):
    app.dependency_overrides.pop(deps.get_current_organisation, None)


def accepted(**overrides) -> ImportAccepted:
    fields = {
        "import_id": IMPORT_ID,
        "organisation_id": ORG_ID,
        "filename": "feedback.csv",
        "status": "completed",
        "rows_received": 4,
        "rows_imported": 3,
        "rows_rejected": 1,
        "job_id": JOB_ID,
        "duplicates_in_file": 0,
        "duplicates_in_database": 0,
        "duplicate_upload": False,
        "row_errors": [{"row": 4, "field": "text", "message": "Feedback text is empty."}],
    }
    fields.update(overrides)

    return ImportAccepted(**fields)


def upload(client, raw: bytes = CSV, name: str = "feedback.csv", headers=None, data=None):
    return client.post(
        "/api/imports",
        files={"file": (name, raw, "text/csv")},
        headers=headers or {},
        data=data,
    )


# ---------------------------------------------------------------- authentication


def test_uploading_without_signing_in_is_rejected_before_anything_is_stored(client, monkeypatch, signed_out):
    monkeypatch.setattr(deps, "_lookup_session", lambda *a: pytest.fail("no cookie, yet a session lookup ran"))
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda *a, **k: pytest.fail("an unauthenticated upload reached the service"),
    )

    response = upload(client)

    assert response.status_code == 401


def test_the_research_api_key_no_longer_opens_ingestion(client, monkeypatch, signed_out):
    """Option A (Milestone 7): customer data needs a signed-in user, not the shared key."""
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda *a, **k: pytest.fail("an upload with only the API key reached the service"),
    )

    assert upload(client, headers=VALID_KEY).status_code == 401


@pytest.mark.parametrize("path", [f"/api/imports/{IMPORT_ID}", f"/api/jobs/{JOB_ID}"])
def test_reading_status_requires_signing_in(client, monkeypatch, signed_out, path):
    monkeypatch.setattr(
        import_routes, "_read_import",
        lambda *a, **k: pytest.fail("an unauthenticated read reached the database"),
    )
    monkeypatch.setattr(
        import_routes, "_read_job",
        lambda *a, **k: pytest.fail("an unauthenticated read reached the database"),
    )

    assert client.get(path, headers=VALID_KEY).status_code == 401


def test_a_user_with_no_organisation_cannot_upload(client, monkeypatch, signed_out):
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda *a, **k: pytest.fail("an upload with no organisation reached the service"),
    )
    app.dependency_overrides[deps.get_current_user] = lambda: AuthContext(
        user_id=uuid.uuid4(), email="loner@example.com", session_id=uuid.uuid4(),
        organisation_id=None, role=None,
    )

    assert upload(client).status_code == 403


# ---------------------------------------------------------------- upload


def test_a_successful_upload_returns_201_with_what_happened(client, monkeypatch):
    monkeypatch.setattr(import_routes, "_store_import", lambda organisation_id, raw, filename: accepted())

    response = upload(client)

    assert response.status_code == 201
    body = response.json()
    assert body["import_id"] == IMPORT_ID
    assert body["job_id"] == JOB_ID          # analysis was queued, not run
    assert (body["rows_received"], body["rows_imported"], body["rows_rejected"]) == (4, 3, 1)
    assert body["row_errors"][0]["row"] == 4


def test_the_uploaded_bytes_and_filename_reach_the_service_for_the_sessions_organisation(client, monkeypatch):
    seen = {}

    def record(organisation_id, raw, filename):
        seen["organisation_id"], seen["raw"], seen["filename"] = organisation_id, raw, filename
        return accepted()

    monkeypatch.setattr(import_routes, "_store_import", record)

    upload(client, raw=b"text\nhello\n", name="february.csv")

    assert seen == {
        "organisation_id": uuid.UUID(ORG_ID),
        "raw": b"text\nhello\n",
        "filename": "february.csv",
    }


def test_an_organisation_named_in_the_form_header_or_file_is_ignored(client, monkeypatch):
    seen = []

    def record(organisation_id, raw, filename):
        seen.append(organisation_id)
        return accepted()

    monkeypatch.setattr(import_routes, "_store_import", record)

    response = upload(
        client,
        raw=b"text,organisation_id\nhello," + OTHER_ORG_ID.encode() + b"\n",
        data={"organisation_id": OTHER_ORG_ID},
        headers={"X-Organisation-Id": OTHER_ORG_ID},
    )

    assert response.status_code == 201
    assert seen == [uuid.UUID(ORG_ID)]


def test_a_duplicate_upload_says_so_and_creates_no_job(client, monkeypatch):
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda organisation_id, raw, filename: accepted(duplicate_upload=True, job_id=None, rows_imported=0),
    )

    body = upload(client).json()

    assert body["duplicate_upload"] is True
    assert body["job_id"] is None


def test_an_unusable_file_is_422_with_an_actionable_message(client, monkeypatch):
    def refuse(organisation_id, raw, filename):
        raise IngestionError("No feedback text column found. Add a column named 'text'.")

    monkeypatch.setattr(import_routes, "_store_import", refuse)

    response = upload(client, raw=b"name\nAcme\n")

    assert response.status_code == 422
    assert "text" in response.json()["detail"]


def test_an_unusable_file_message_is_the_readable_one_without_an_internal_class_name(client, monkeypatch):
    """Customers read this detail as it is (the web app shows it verbatim). str() of the exception
    used to prefix it with "[IngestionError] "."""
    message = "No feedback text column found. Add a column named 'text'."

    def refuse(organisation_id, raw, filename):
        raise IngestionError(message)

    monkeypatch.setattr(import_routes, "_store_import", refuse)

    response = upload(client, raw=b"name\nAcme\n")

    assert response.status_code == 422
    assert response.json() == {"detail": message}


def test_an_oversized_upload_is_413_and_never_reaches_the_service(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda *a, **k: pytest.fail("an oversized upload reached the service"),
    )

    response = upload(client, raw=b"text\n" + b"x" * 200 + b"\n")

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_a_missing_file_is_a_validation_error(client):
    response = client.post("/api/imports")

    assert response.status_code == 422


def test_an_unexpected_failure_is_500_without_internal_detail(client, monkeypatch):
    def crash(organisation_id, raw, filename):
        raise RuntimeError("connection to /var/run/postgresql failed")

    monkeypatch.setattr(import_routes, "_store_import", crash)

    response = upload(client)

    assert response.status_code == 500
    assert response.json() == {"detail": "Import failed."}
    assert "postgresql" not in response.text


# ---------------------------------------------------------------- status


def test_reading_an_import_returns_its_counts(client, monkeypatch):
    summary = ImportSummary(
        import_id=IMPORT_ID, organisation_id=ORG_ID, filename="feedback.csv",
        status="completed", rows_received=4, rows_imported=3, rows_rejected=1,
    )
    monkeypatch.setattr(import_routes, "_read_import", lambda organisation_id, import_id: summary)

    response = client.get(f"/api/imports/{IMPORT_ID}")

    assert response.status_code == 200
    assert response.json()["rows_imported"] == 3


def test_reading_a_job_returns_its_progress(client, monkeypatch):
    job = JobSummary(
        job_id=JOB_ID, organisation_id=ORG_ID, kind="analyse_import", status="succeeded",
        attempts=1, max_attempts=3, result={"analysed": 3, "succeeded": 3, "failed": 0},
    )
    monkeypatch.setattr(import_routes, "_read_job", lambda organisation_id, job_id: job)

    body = client.get(f"/api/jobs/{JOB_ID}").json()

    assert body["status"] == "succeeded"
    assert body["result"]["analysed"] == 3


@pytest.mark.parametrize("helper, path", [("_read_import", f"/api/imports/{IMPORT_ID}"), ("_read_job", f"/api/jobs/{JOB_ID}")])
def test_status_reads_use_the_sessions_organisation(client, monkeypatch, helper, path):
    seen = []

    def record(organisation_id, identifier):
        seen.append(organisation_id)
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No such thing.")

    monkeypatch.setattr(import_routes, helper, record)

    client.get(path, params={"organisation_id": OTHER_ORG_ID})

    assert seen == [uuid.UUID(ORG_ID)]


@pytest.mark.parametrize(
    "path, helper",
    [(f"/api/imports/{IMPORT_ID}", "_read_import"), (f"/api/jobs/{JOB_ID}", "_read_job")],
)
def test_an_unknown_id_is_404(client, monkeypatch, path, helper):
    from fastapi import HTTPException

    def missing(organisation_id, identifier):
        raise HTTPException(status_code=404, detail="No such thing.")

    monkeypatch.setattr(import_routes, helper, missing)

    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/api/imports/not-a-uuid", "/api/jobs/not-a-uuid"])
def test_a_malformed_id_is_422(client, path):
    """Rejected before a session opens, so a bad id never becomes a database query."""
    response = client.get(path)

    assert response.status_code == 422
