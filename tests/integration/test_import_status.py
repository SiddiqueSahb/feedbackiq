"""
Analysis status on imports - GET /api/v1/imports, /api/v1/imports/{id}, /api/imports/{id}

An imports page must say whether each import's analysis is queued, running, done or failed - after a
reload, not only straight after the upload (Milestone 8). A job refers to its import only through its
JSON payload, so these tests pin the lookup behind it (db/jobs.py::latest_import_jobs):

* the upload response and a later read agree about the job;
* the status follows the job's real lifecycle;
* an import with several jobs shows the latest, and other kinds of job are ignored;
* **another organisation's job is never attached, even if its payload names the import**;
* the number of queries does not grow with the number of imports listed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, update

from feedbackiq.db import jobs as job_queue
from feedbackiq.db.models import Job
from feedbackiq.services.imports import import_csv

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
CSV = b"text\nWaited forty minutes for a table\nThe food was cold\n"


def register(client: TestClient, email: str, organisation: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "organisation_name": organisation},
    )
    assert response.status_code == 201, response.text

    return response.json()["organisation"]["id"]


def upload(client: TestClient, csv: bytes = CSV, name: str = "feedback.csv"):
    response = client.post("/api/v1/imports", files={"file": (name, csv, "text/csv")})
    assert response.status_code == 201, response.text

    return response.json()


def listed(client: TestClient) -> list[dict]:
    return client.get("/api/v1/imports").json()


@pytest.fixture()
def acme(api_client) -> TestClient:
    register(api_client, "ana@acme.example", "Acme Ltd")
    return api_client


# ---------------------------------------------------------------- what the API reports


def test_an_upload_reports_the_analysis_it_queued(acme):
    body = upload(acme)

    assert body["job_id"] is not None
    assert body["analysis_status"] == "queued"


def test_a_reload_shows_the_same_analysis_on_the_list_and_both_detail_routes(acme):
    uploaded = upload(acme)

    [summary] = listed(acme)
    v1_detail = acme.get(f"/api/v1/imports/{uploaded['import_id']}").json()
    older_detail = acme.get(f"/api/imports/{uploaded['import_id']}").json()

    for view in (summary, v1_detail, older_detail):
        assert (view["job_id"], view["analysis_status"]) == (uploaded["job_id"], "queued")


def test_the_status_follows_the_job_through_its_lifecycle(acme, session):
    upload(acme)

    job = job_queue.claim_next_job(session, worker_id="test-worker")
    session.commit()
    assert listed(acme)[0]["analysis_status"] == "running"

    job_queue.mark_succeeded(session, job, result={"analysed": 2})
    session.commit()
    assert listed(acme)[0]["analysis_status"] == "succeeded"


def test_a_failure_with_attempts_left_reads_as_queued_again_and_a_final_one_as_failed(acme, session):
    upload(acme)

    job = job_queue.claim_next_job(session, worker_id="test-worker")
    job_queue.mark_failed(session, job, error="model unavailable", retry=True)
    session.commit()
    assert listed(acme)[0]["analysis_status"] == "queued"

    job = job_queue.claim_next_job(session, worker_id="test-worker")
    job_queue.mark_failed(session, job, error="model unavailable", retry=False)
    session.commit()
    assert listed(acme)[0]["analysis_status"] == "failed"


def test_an_import_that_stored_nothing_has_no_analysis(acme):
    body = upload(acme, csv=b"text\n   \n")

    assert (body["rows_imported"], body["job_id"], body["analysis_status"]) == (0, None, None)
    assert (listed(acme)[0]["job_id"], listed(acme)[0]["analysis_status"]) == (None, None)


def test_an_identical_re_upload_queues_nothing_but_the_list_keeps_the_original_analysis(acme):
    first = upload(acme)
    again = upload(acme)

    assert again["duplicate_upload"] is True
    assert (again["job_id"], again["analysis_status"]) == (None, None)

    [only] = listed(acme)
    assert (only["import_id"], only["job_id"]) == (first["import_id"], first["job_id"])


# ---------------------------------------------------------------- the lookup itself


def test_the_latest_job_wins_when_an_import_has_several(session, organisation):
    first = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    later = job_queue.create_job(
        session, organisation_id=organisation.id, payload={"import_batch_id": str(first.batch.id)}
    )
    # Created in the same second as the first job would tie; make "later" unambiguous.
    session.execute(update(Job).where(Job.id == later.id).values(created_at=Job.created_at + timedelta(seconds=5)))
    session.commit()

    jobs = job_queue.latest_import_jobs(session, organisation_id=organisation.id, import_batch_ids=[first.batch.id])

    assert jobs[first.batch.id].id == later.id


def test_other_kinds_of_job_are_ignored(session, organisation):
    imported = import_csv(session, raw=CSV, organisation_id=organisation.id)
    other = job_queue.create_job(
        session, organisation_id=organisation.id, kind="compute_insights",
        payload={"import_batch_id": str(imported.batch.id)},
    )
    session.execute(update(Job).where(Job.id == other.id).values(created_at=Job.created_at + timedelta(seconds=5)))
    session.commit()

    jobs = job_queue.latest_import_jobs(session, organisation_id=organisation.id, import_batch_ids=[imported.batch.id])

    assert jobs[imported.batch.id].id == imported.job.id


def test_another_organisations_job_is_never_attached_even_if_its_payload_names_the_import(
    session, organisation, other_organisation
):
    ours = import_csv(session, raw=CSV, organisation_id=organisation.id)
    session.commit()

    forged = job_queue.create_job(
        session, organisation_id=other_organisation.id,
        payload={"import_batch_id": str(ours.batch.id)},
    )
    session.execute(update(Job).where(Job.id == forged.id).values(created_at=Job.created_at + timedelta(seconds=5)))
    session.commit()

    jobs = job_queue.latest_import_jobs(session, organisation_id=organisation.id, import_batch_ids=[ours.batch.id])

    # The forged job is newer, but it belongs to another organisation, so our import keeps ours.
    # (The other organisation cannot use the forgery either: every caller only looks up imports it
    # has already fetched for its own organisation - see the HTTP test below.)
    assert jobs[ours.batch.id].id == ours.job.id


def test_the_other_organisation_never_sees_the_import_or_its_job_over_http(api_client):
    acme = api_client
    register(acme, "ana@acme.example", "Acme Ltd")
    ours = upload(acme)

    globex = TestClient(api_client.app)
    register(globex, "ben@globex.example", "Globex Inc")

    assert globex.get("/api/v1/imports").json() == []
    assert globex.get(f"/api/v1/imports/{ours['import_id']}").status_code == 404
    assert ours["job_id"] not in globex.get("/api/v1/imports").text


def test_an_empty_list_of_imports_makes_no_query(session, organisation, db_engine):
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        assert job_queue.latest_import_jobs(session, organisation_id=organisation.id, import_batch_ids=[]) == {}
    finally:
        event.remove(db_engine, "before_cursor_execute", record)

    assert statements == []


def test_listing_imports_costs_the_same_queries_for_one_import_as_for_many(acme):

    def statements_for_listing() -> list[str]:
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        # Listened for on the Engine class, not on one engine: the app's engine is cached per
        # argument (`get_engine()` and `get_engine(None)` are different cache entries), and a
        # listener on the wrong instance hears nothing - which is how an earlier version of this
        # test passed with zero statements counted on both sides.
        event.listen(Engine, "before_cursor_execute", record)
        try:
            assert acme.get("/api/v1/imports").status_code == 200
        finally:
            event.remove(Engine, "before_cursor_execute", record)

        return statements

    upload(acme, csv=b"text\nfirst file complaint\n", name="one.csv")
    with_one = statements_for_listing()

    for number in range(2, 7):
        upload(acme, csv=f"text\ncomplaint in file {number}\n".encode(), name=f"file-{number}.csv")
    with_six = statements_for_listing()

    assert len(listed(acme)) == 6
    assert with_one, "no statements were recorded, so the comparison below would prove nothing"
    assert len(with_six) == len(with_one), "listing imports must not query once per import"
    assert sum("FROM jobs" in statement for statement in with_six) == 1
