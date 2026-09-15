"""
Cross-tenant security over HTTP - the whole path, end to end

`test_tenant_isolation.py` proves every *service function* is scoped to an organisation. This
file proves the property a customer actually depends on, through the real app, real cookies and
a real database: **a signed-in user of one organisation cannot see, count, find or write the
data of another - whatever they put in the URL, query string, body, headers or CSV.**

Two companies register through the API and upload their own feedback (one through
/api/v1/imports, the other through the older /api/imports). Every customer route is then asked
for the other company's data, in both directions.

What each group of tests pins:

    leaks            no response from any customer route contains the other tenant's
                     identifiers or feedback text - with forged organisation ids attached
    not found        another tenant's valid id is indistinguishable from one that does not exist
    counts           listings and aggregates count only the caller's own rows
    ingestion        an upload always lands in the uploader's organisation
    membership       roles, missing, removed and forged memberships
    authentication   no, malformed, expired, revoked and disabled sessions
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from feedbackiq.auth.credentials import hash_password
from feedbackiq.core.config import settings
from feedbackiq.db.models import (
    Category,
    Feedback,
    Organisation,
    OrganisationMembership,
    User,
    UserSession,
)
from feedbackiq.db.persistence import feedback_for_import_batch, save_batch_analysis
from feedbackiq.engine.types import (
    BatchAnalysis,
    CategoryMatch,
    ItemAnalysis,
    SentimentPrediction,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
COOKIE = settings.SESSION_COOKIE_NAME

A_CSV = (
    b"text,external_id\n"
    b"acme private complaint about a double charge,A-1\n"
    b"acme second private complaint about late delivery,A-2\n"
)
B_CSV = b"text,external_id\nglobex private complaint about a rude agent,B-1\n"


@dataclass
class Tenant:
    """One company as seen from outside: a signed-in browser and what it created."""

    client: TestClient
    email: str
    organisation_id: str
    import_id: str
    job_id: str
    feedback_ids: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    def secrets(self) -> list[str]:
        """Everything that must never appear in another tenant's responses."""
        return [self.organisation_id, self.import_id, self.job_id, *self.feedback_ids, *self.texts]


# ---------------------------------------------------------------- building two tenants


def sign_up(app, email: str, organisation_name: str) -> tuple[TestClient, str]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "organisation_name": organisation_name},
    )
    assert response.status_code == 201, response.text

    return client, response.json()["organisation"]["id"]


def sign_in(app, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text

    return client


def upload(client: TestClient, csv: bytes, path: str = "/api/v1/imports", **kwargs):
    return client.post(path, files={"file": ("feedback.csv", csv, "text/csv")}, **kwargs)


def analyse(session, organisation_id: str, import_id: str) -> list:
    """Store analysis results for an import, as the worker would, without loading models."""
    rows = feedback_for_import_batch(
        session, organisation_id=uuid.UUID(organisation_id), import_batch_id=uuid.UUID(import_id)
    )
    save_batch_analysis(
        session,
        organisation_id=uuid.UUID(organisation_id),
        analysis=BatchAnalysis(
            results=tuple(
                ItemAnalysis(
                    feedback_id=str(row.id),
                    sentiment=SentimentPrediction(
                        label="negative", confidence=0.9, scores={"negative": 0.9},
                        model_version="fake-1",
                    ),
                    category=CategoryMatch(
                        category_id="billing_and_payments", name="Billing & Payments", score=0.8
                    ),
                )
                for row in rows
            ),
            versions={"engine": "1.0.0"},
        ),
    )
    session.commit()

    return rows


def build_tenant(app, session, email, organisation_name, csv, upload_path) -> Tenant:
    client, organisation_id = sign_up(app, email, organisation_name)

    response = upload(client, csv, upload_path)
    assert response.status_code == 201, response.text
    body = response.json()

    rows = analyse(session, organisation_id, body["import_id"])

    return Tenant(
        client=client,
        email=email,
        organisation_id=organisation_id,
        import_id=body["import_id"],
        job_id=body["job_id"],
        feedback_ids=[str(row.id) for row in rows],
        texts=[row.text for row in rows],
    )


@pytest.fixture()
def tenants(api_client, session, default_categories):
    # Committed so the app's own connections can see the categories the results point at.
    session.commit()

    acme = build_tenant(api_client.app, session, "ana@acme.example", "Acme Ltd", A_CSV, "/api/v1/imports")
    globex = build_tenant(api_client.app, session, "ben@globex.example", "Globex Inc", B_CSV, "/api/imports")

    return {"acme": acme, "globex": globex}


def customer_paths(target: Tenant) -> list[str]:
    """Every customer GET route, aimed at `target`'s data wherever a route takes an id."""
    return [
        "/api/v1/feedback",
        "/api/v1/feedback?search=private",
        f"/api/v1/feedback/{target.feedback_ids[0]}",
        "/api/v1/analytics/summary",
        "/api/v1/analytics/trend",
        "/api/v1/analytics/categories",
        "/api/v1/categories",
        "/api/v1/imports",
        f"/api/v1/imports/{target.import_id}",
        f"/api/v1/jobs/{target.job_id}",
        f"/api/imports/{target.import_id}",
        f"/api/jobs/{target.job_id}",
    ]


DIRECTIONS = [("acme", "globex"), ("globex", "acme")]


def forged(target: Tenant) -> dict:
    """Every place a client might try to name an organisation."""
    return {
        "params": {"organisation_id": target.organisation_id, "org": target.organisation_id},
        "headers": {
            "X-Organisation-Id": target.organisation_id,
            "Organisation-Id": target.organisation_id,
        },
    }


# ---------------------------------------------------------------- leaks


@pytest.mark.parametrize("caller, target", DIRECTIONS)
def test_no_customer_route_reveals_anything_of_another_organisation(tenants, caller, target):
    me, them = tenants[caller], tenants[target]

    for path in customer_paths(them):
        response = me.client.get(path, **forged(them))

        assert response.status_code in (200, 404), (path, response.status_code, response.text)
        for secret in them.secrets():
            assert secret not in response.text, f"{path} leaked {secret!r} to {caller}"


@pytest.mark.parametrize("caller, target", DIRECTIONS)
def test_each_organisation_still_sees_all_of_its_own_data(tenants, caller, target):
    """The other half of isolation: scoping must not hide a tenant's data from itself."""
    me = tenants[caller]

    listing = me.client.get("/api/v1/feedback").json()
    assert {item["id"] for item in listing["items"]} == set(me.feedback_ids)

    assert me.client.get(f"/api/v1/feedback/{me.feedback_ids[0]}").status_code == 200
    assert me.client.get(f"/api/v1/imports/{me.import_id}").status_code == 200
    assert me.client.get(f"/api/v1/jobs/{me.job_id}").status_code == 200
    assert me.client.get(f"/api/imports/{me.import_id}").status_code == 200
    assert me.client.get(f"/api/jobs/{me.job_id}").status_code == 200


# ---------------------------------------------------------------- not found


@pytest.mark.parametrize(
    "path_for",
    [
        lambda t: f"/api/v1/feedback/{t.feedback_ids[0]}",
        lambda t: f"/api/v1/imports/{t.import_id}",
        lambda t: f"/api/v1/jobs/{t.job_id}",
        lambda t: f"/api/imports/{t.import_id}",
        lambda t: f"/api/jobs/{t.job_id}",
    ],
    ids=["feedback", "v1-import", "v1-job", "import", "job"],
)
def test_another_organisations_id_is_indistinguishable_from_a_missing_one(tenants, path_for):
    acme, globex = tenants["acme"], tenants["globex"]

    theirs = acme.client.get(path_for(globex))

    nobody = Tenant(
        client=acme.client, email="", organisation_id="", import_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()), feedback_ids=[str(uuid.uuid4())],
    )
    missing = acme.client.get(path_for(nobody))

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


# ---------------------------------------------------------------- counts


def test_feedback_listings_count_only_their_own_organisation(tenants):
    acme, globex = tenants["acme"], tenants["globex"]

    assert acme.client.get("/api/v1/feedback", **forged(globex)).json()["total"] == 2
    assert globex.client.get("/api/v1/feedback", **forged(acme)).json()["total"] == 1


def test_searching_for_the_other_organisations_exact_text_finds_nothing(tenants):
    acme, globex = tenants["acme"], tenants["globex"]

    response = acme.client.get("/api/v1/feedback", params={"search": globex.texts[0]})

    assert response.json()["total"] == 0


def test_the_summary_counts_only_its_own_organisation(tenants):
    acme, globex = tenants["acme"], tenants["globex"]

    assert acme.client.get("/api/v1/analytics/summary", **forged(globex)).json()["total_feedback"] == 2
    assert globex.client.get("/api/v1/analytics/summary", **forged(acme)).json()["total_feedback"] == 1


def test_the_trend_counts_only_its_own_organisation(tenants):
    acme, globex = tenants["acme"], tenants["globex"]

    def total(tenant, other):
        points = tenant.client.get("/api/v1/analytics/trend", **forged(other)).json()
        return sum(point["feedback_count"] for point in points)

    assert (total(acme, globex), total(globex, acme)) == (2, 1)


def test_the_category_breakdown_counts_only_its_own_organisation(tenants):
    acme, globex = tenants["acme"], tenants["globex"]

    def total(tenant, other):
        return sum(row["count"] for row in tenant.client.get("/api/v1/analytics/categories", **forged(other)).json())

    assert (total(acme, globex), total(globex, acme)) == (2, 1)


def test_an_organisations_custom_category_is_invisible_to_the_other(tenants, session):
    acme, globex = tenants["acme"], tenants["globex"]
    session.add(
        Category(
            organisation_id=uuid.UUID(acme.organisation_id), key="acme_private_theme",
            name="Acme Private Theme", description="Only Acme defines this.", source="custom",
        )
    )
    session.commit()

    def keys(tenant, other):
        return {row["key"] for row in tenant.client.get("/api/v1/categories", **forged(other)).json()}

    assert "acme_private_theme" in keys(acme, globex)
    assert "acme_private_theme" not in keys(globex, acme)


def test_import_listings_show_only_their_own_organisations_imports(tenants):
    acme, globex = tenants["acme"], tenants["globex"]

    assert [i["import_id"] for i in acme.client.get("/api/v1/imports", **forged(globex)).json()] == [acme.import_id]
    assert [i["import_id"] for i in globex.client.get("/api/v1/imports", **forged(acme)).json()] == [globex.import_id]


# ---------------------------------------------------------------- ingestion


@pytest.mark.parametrize("path", ["/api/v1/imports", "/api/imports"])
def test_an_upload_naming_another_organisation_lands_in_the_uploaders(tenants, session, path):
    """The other organisation's id in a CSV column, a form field and two headers at once."""
    acme, globex = tenants["acme"], tenants["globex"]
    smuggled = (
        b"text,organisation_id,organisation\n"
        b"smuggled complaint meant for globex," + globex.organisation_id.encode() + b",Globex Inc\n"
    )

    response = upload(
        acme.client, smuggled, path,
        data={"organisation_id": globex.organisation_id},
        headers={"X-Organisation-Id": globex.organisation_id, "Organisation-Id": globex.organisation_id},
    )

    assert response.status_code == 201, response.text
    assert response.json()["organisation_id"] == acme.organisation_id

    [row] = session.scalars(select(Feedback).where(Feedback.text == "smuggled complaint meant for globex")).all()
    assert str(row.organisation_id) == acme.organisation_id

    assert globex.client.get("/api/v1/feedback").json()["total"] == 1
    assert "smuggled" not in globex.client.get("/api/v1/feedback?search=smuggled").text


def test_the_same_file_uploaded_by_two_organisations_stays_separate(tenants):
    acme, globex = tenants["acme"], tenants["globex"]
    shared = b"text\nthe same complaint, word for word\n"

    first = upload(acme.client, shared)
    second = upload(globex.client, shared)

    assert first.status_code == second.status_code == 201
    assert first.json()["import_id"] != second.json()["import_id"]
    # Not flagged as a duplicate of the other organisation's upload.
    assert second.json()["duplicate_upload"] is False


# ---------------------------------------------------------------- membership and roles


def add_user(session, email: str, organisation_id: str | None = None, role: str = "member") -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD))
    session.add(user)
    session.flush()

    if organisation_id is not None:
        session.add(
            OrganisationMembership(user_id=user.id, organisation_id=uuid.UUID(organisation_id), role=role)
        )

    session.commit()

    return user


def test_a_member_has_the_same_data_access_as_the_owner_and_no_more(tenants, session, api_client):
    """Owner and member currently differ only in name: no owner-only route exists yet."""
    acme, globex = tenants["acme"], tenants["globex"]
    add_user(session, "cara@acme.example", acme.organisation_id, role="member")
    cara = sign_in(api_client.app, "cara@acme.example")

    assert cara.get("/api/v1/auth/me").json()["organisation"] == {
        "id": acme.organisation_id, "name": "Acme Ltd", "role": "member",
    }
    assert cara.get("/api/v1/analytics/summary").json() == acme.client.get("/api/v1/analytics/summary").json()
    assert upload(cara, b"text\na member can import too\n").status_code == 201

    for path in customer_paths(globex):
        response = cara.get(path, **forged(globex))
        for secret in globex.secrets():
            assert secret not in response.text


def test_a_user_with_no_membership_is_refused_every_customer_route(tenants, session, api_client):
    globex = tenants["globex"]
    add_user(session, "loner@example.com")
    loner = sign_in(api_client.app, "loner@example.com")

    assert loner.get("/api/v1/auth/me").json()["organisation"] is None

    for path in customer_paths(globex):
        response = loner.get(path, **forged(globex))
        assert response.status_code == 403, path
        assert response.json() == {"detail": "You are not a member of an organisation."}

    assert upload(loner, b"text\nno organisation to put this in\n").status_code == 403


def forge_session_organisation(session, tenant: Tenant, organisation_id: str) -> None:
    user_id = session.scalar(select(User.id).where(User.email == tenant.email))
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id)
        .values(organisation_id=uuid.UUID(organisation_id))
    )
    session.commit()


def test_a_session_edited_to_point_at_another_organisation_reaches_nothing(tenants, session):
    """A tampered session row - a bug, a bad migration, a hand edit - is caught by the membership
    re-check on every request: the answer is 403, never the other organisation's data."""
    acme, globex = tenants["acme"], tenants["globex"]
    forge_session_organisation(session, acme, globex.organisation_id)

    for path in customer_paths(globex):
        response = acme.client.get(path)
        assert response.status_code == 403, path
        for secret in globex.secrets():
            assert secret not in response.text


def test_a_session_pointing_at_an_organisation_that_does_not_exist_reaches_nothing(tenants, session):
    acme = tenants["acme"]
    user_id = session.scalar(select(User.id).where(User.email == acme.email))
    # Detach the session from any real organisation by pointing it at a fresh one nobody joined.
    stranger = Organisation(name="Nobody's", slug=f"nobody-{uuid.uuid4().hex[:6]}")
    session.add(stranger)
    session.flush()
    session.execute(update(UserSession).where(UserSession.user_id == user_id).values(organisation_id=stranger.id))
    session.commit()

    assert acme.client.get("/api/v1/feedback").status_code == 403


def test_removing_a_membership_takes_effect_on_the_next_request(tenants, session):
    acme = tenants["acme"]
    assert acme.client.get("/api/v1/feedback").status_code == 200

    session.execute(
        OrganisationMembership.__table__.delete().where(
            OrganisationMembership.organisation_id == uuid.UUID(acme.organisation_id)
        )
    )
    session.commit()

    assert acme.client.get("/api/v1/feedback").status_code == 403


def test_a_soft_deleted_organisations_data_is_no_longer_reachable(tenants, session):
    acme = tenants["acme"]
    session.execute(
        update(Organisation)
        .where(Organisation.id == uuid.UUID(acme.organisation_id))
        .values(deleted_at=datetime.now(timezone.utc))
    )
    session.commit()

    assert acme.client.get("/api/v1/feedback").status_code == 403


# ---------------------------------------------------------------- authentication


@pytest.mark.parametrize("cookie", [None, "garbage", "a" * 43])
def test_without_a_valid_session_every_customer_route_is_401(tenants, api_client, cookie):
    globex = tenants["globex"]
    stranger = TestClient(api_client.app)
    if cookie is not None:
        stranger.cookies.set(COOKIE, cookie)

    for path in customer_paths(globex):
        response = stranger.get(path, headers={"x-api-key": settings.API_KEY, **forged(globex)["headers"]})
        assert response.status_code == 401, path
        for secret in globex.secrets():
            assert secret not in response.text

    assert upload(stranger, b"text\nanonymous\n").status_code == 401


def test_an_expired_session_is_401_on_customer_routes(tenants, session):
    acme = tenants["acme"]
    session.execute(update(UserSession).values(expires_at=func.now() - timedelta(seconds=1)))
    session.commit()

    assert acme.client.get("/api/v1/feedback").status_code == 401


def test_a_signed_out_session_cannot_be_replayed_against_customer_data(tenants):
    acme = tenants["acme"]
    token = acme.client.cookies.get(COOKIE)

    assert acme.client.post("/api/v1/auth/logout").status_code == 204
    acme.client.cookies.set(COOKIE, token)

    assert acme.client.get("/api/v1/feedback").status_code == 401


def test_a_disabled_users_session_stops_reaching_customer_data(tenants, session):
    acme = tenants["acme"]
    session.execute(update(User).where(User.email == acme.email).values(is_active=False))
    session.commit()

    assert acme.client.get("/api/v1/feedback").status_code == 401
    assert upload(acme.client, b"text\nafter being disabled\n").status_code == 401
