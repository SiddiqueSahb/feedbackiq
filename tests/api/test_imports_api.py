"""
The ingestion HTTP contract - feedbackiq.api.routes.imports

No database here. The three blocking helpers are replaced with fakes, which is also how
this suite proves something worth proving: an unauthenticated request never reaches the
database, and the app imports with no PostgreSQL running at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from feedbackiq.api.routes import imports as import_routes
from feedbackiq.api.main import app
from feedbackiq.api.schemas import ImportAccepted, ImportSummary, JobSummary
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import IngestionError

VALID_KEY = {"x-api-key": settings.API_KEY}

IMPORT_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"
ORG_ID = "33333333-3333-3333-3333-333333333333"

CSV = b"text,rating\nThe app crashes,1\n"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


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


def upload(client, raw: bytes = CSV, name: str = "feedback.csv", headers=None):
    return client.post(
        "/api/imports",
        files={"file": (name, raw, "text/csv")},
        headers=VALID_KEY if headers is None else headers,
    )


# ---------------------------------------------------------------- authentication


def test_uploading_without_a_key_is_rejected_before_anything_is_read(client, monkeypatch):
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda *a, **k: pytest.fail("an unauthenticated upload reached the service"),
    )

    response = upload(client, headers={})

    assert response.status_code == 401


@pytest.mark.parametrize("path", [f"/api/imports/{IMPORT_ID}", f"/api/jobs/{JOB_ID}"])
def test_reading_status_requires_a_key(client, monkeypatch, path):
    monkeypatch.setattr(
        import_routes, "_read_import",
        lambda *a, **k: pytest.fail("an unauthenticated read reached the database"),
    )
    monkeypatch.setattr(
        import_routes, "_read_job",
        lambda *a, **k: pytest.fail("an unauthenticated read reached the database"),
    )

    assert client.get(path).status_code == 401


# ---------------------------------------------------------------- upload


def test_a_successful_upload_returns_201_with_what_happened(client, monkeypatch):
    monkeypatch.setattr(import_routes, "_store_import", lambda raw, filename: accepted())

    response = upload(client)

    assert response.status_code == 201
    body = response.json()
    assert body["import_id"] == IMPORT_ID
    assert body["job_id"] == JOB_ID          # analysis was queued, not run
    assert (body["rows_received"], body["rows_imported"], body["rows_rejected"]) == (4, 3, 1)
    assert body["row_errors"][0]["row"] == 4


def test_the_uploaded_bytes_and_filename_reach_the_service(client, monkeypatch):
    seen = {}

    def record(raw, filename):
        seen["raw"], seen["filename"] = raw, filename
        return accepted()

    monkeypatch.setattr(import_routes, "_store_import", record)

    upload(client, raw=b"text\nhello\n", name="february.csv")

    assert seen == {"raw": b"text\nhello\n", "filename": "february.csv"}


def test_a_duplicate_upload_says_so_and_creates_no_job(client, monkeypatch):
    monkeypatch.setattr(
        import_routes, "_store_import",
        lambda raw, filename: accepted(duplicate_upload=True, job_id=None, rows_imported=0),
    )

    body = upload(client).json()

    assert body["duplicate_upload"] is True
    assert body["job_id"] is None


def test_an_unusable_file_is_422_with_an_actionable_message(client, monkeypatch):
    def refuse(raw, filename):
        raise IngestionError("No feedback text column found. Add a column named 'text'.")

    monkeypatch.setattr(import_routes, "_store_import", refuse)

    response = upload(client, raw=b"name\nAcme\n")

    assert response.status_code == 422
    assert "text" in response.json()["detail"]


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
    response = client.post("/api/imports", headers=VALID_KEY)

    assert response.status_code == 422


def test_an_unexpected_failure_is_500_without_internal_detail(client, monkeypatch):
    def crash(raw, filename):
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
    monkeypatch.setattr(import_routes, "_read_import", lambda import_id: summary)

    response = client.get(f"/api/imports/{IMPORT_ID}", headers=VALID_KEY)

    assert response.status_code == 200
    assert response.json()["rows_imported"] == 3


def test_reading_a_job_returns_its_progress(client, monkeypatch):
    job = JobSummary(
        job_id=JOB_ID, organisation_id=ORG_ID, kind="analyse_import", status="succeeded",
        attempts=1, max_attempts=3, result={"analysed": 3, "succeeded": 3, "failed": 0},
    )
    monkeypatch.setattr(import_routes, "_read_job", lambda job_id: job)

    body = client.get(f"/api/jobs/{JOB_ID}", headers=VALID_KEY).json()

    assert body["status"] == "succeeded"
    assert body["result"]["analysed"] == 3


@pytest.mark.parametrize(
    "path, helper",
    [(f"/api/imports/{IMPORT_ID}", "_read_import"), (f"/api/jobs/{JOB_ID}", "_read_job")],
)
def test_an_unknown_id_is_404(client, monkeypatch, path, helper):
    from fastapi import HTTPException

    def missing(identifier):
        raise HTTPException(status_code=404, detail="No such thing.")

    monkeypatch.setattr(import_routes, helper, missing)

    assert client.get(path, headers=VALID_KEY).status_code == 404


@pytest.mark.parametrize("path", ["/api/imports/not-a-uuid", "/api/jobs/not-a-uuid"])
def test_a_malformed_id_is_422(client, path):
    """Rejected by the route, so a bad id never becomes a database query."""
    response = client.get(path, headers=VALID_KEY)

    assert response.status_code == 422
