"""
The authentication HTTP contract - feedbackiq.api.v1.auth, feedbackiq.api.deps

No database. The blocking helpers each route delegates to are replaced with fakes, which also
proves the property the dependencies were written for: **a missing, malformed or forged
request is refused before anything touches the database.**

What a frontend relies on is pinned here: status codes, the cookie's security attributes, a
response body that never carries the token, and errors that never echo a password.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from feedbackiq.api import deps
from feedbackiq.api.main import app
from feedbackiq.api.v1 import auth as auth_routes
from feedbackiq.api.v1.schemas import CurrentUser, OrganisationContext
from feedbackiq.auth.session_tokens import new_session_token
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import (
    AccountDisabledError,
    AccountExistsError,
    CredentialError,
)
from feedbackiq.services.auth import AuthContext

COOKIE = settings.SESSION_COOKIE_NAME
PASSWORD = "correct horse battery staple"
TOKEN = new_session_token()
ORG_ID = "44444444-4444-4444-4444-444444444444"

REGISTRATION = {"email": "ana@example.com", "password": PASSWORD, "organisation_name": "Acme Ltd"}
CREDENTIALS = {"email": "ana@example.com", "password": PASSWORD}


@pytest.fixture()
def client():
    # A new client per test, so a cookie set in one test never leaks into the next.
    return TestClient(app)


def fail_if_called(*args, **kwargs):
    raise AssertionError("a request that should have been refused reached the database")


def current_user(organisation: bool = True) -> CurrentUser:
    return CurrentUser(
        email="ana@example.com",
        organisation=OrganisationContext(id=ORG_ID, name="Acme Ltd", role="owner") if organisation else None,
    )


def context(organisation: bool = True) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        email="ana@example.com",
        session_id=uuid.uuid4(),
        organisation_id=uuid.UUID(ORG_ID) if organisation else None,
        role="owner" if organisation else None,
    )


def session_cookie_header(response) -> str:
    [header] = [value for value in response.headers.get_list("set-cookie") if value.startswith(f"{COOKIE}=")]
    return header


# ---------------------------------------------------------------- register


def test_registering_returns_201_and_signs_in_with_a_cookie_not_a_body_token(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_register", lambda *a: (current_user(), TOKEN))

    response = client.post("/api/v1/auth/register", json=REGISTRATION)

    assert response.status_code == 201
    assert response.json() == {
        "email": "ana@example.com",
        "organisation": {"id": ORG_ID, "name": "Acme Ltd", "role": "owner"},
    }
    assert TOKEN not in response.text
    assert client.cookies.get(COOKIE) == TOKEN


def test_the_session_cookie_is_httponly_samesite_lax_and_expires_with_the_session(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_register", lambda *a: (current_user(), TOKEN))

    header = session_cookie_header(client.post("/api/v1/auth/register", json=REGISTRATION))

    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert f"Max-Age={settings.SESSION_TTL_HOURS * 3600}" in header
    # Development: plain http://localhost, where a Secure cookie would never come back.
    assert "Secure" not in header


def test_the_session_cookie_is_secure_when_configured_as_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(auth_routes, "_register", lambda *a: (current_user(), TOKEN))

    header = session_cookie_header(client.post("/api/v1/auth/register", json=REGISTRATION))

    assert "Secure" in header


def test_production_makes_the_cookie_secure_by_default(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    assert settings.session_cookie_secure is True


def test_registration_details_reach_the_service_unchanged(client, monkeypatch):
    received = {}

    def fake(email, password, organisation_name):
        received.update(email=email, password=password, organisation_name=organisation_name)
        return current_user(), TOKEN

    monkeypatch.setattr(auth_routes, "_register", fake)

    client.post("/api/v1/auth/register", json=REGISTRATION)

    assert received == REGISTRATION


def test_an_invalid_detail_is_422_with_the_rule_it_broke(client, monkeypatch):
    def fake(*args):
        raise CredentialError("Password must be at least 12 characters.")

    monkeypatch.setattr(auth_routes, "_register", fake)

    response = client.post("/api/v1/auth/register", json={**REGISTRATION, "password": "short"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Password must be at least 12 characters."}


def test_an_email_that_already_has_an_account_is_409(client, monkeypatch):
    def fake(*args):
        raise AccountExistsError("An account with this email already exists.")

    monkeypatch.setattr(auth_routes, "_register", fake)

    response = client.post("/api/v1/auth/register", json=REGISTRATION)

    assert response.status_code == 409
    assert COOKIE not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "extra",
    [
        {"organisation_id": ORG_ID},
        {"role": "owner"},
        {"organisation_slug": "acme"},
    ],
)
def test_ownership_cannot_be_supplied_in_the_registration_body(client, monkeypatch, extra):
    monkeypatch.setattr(auth_routes, "_register", fail_if_called)

    response = client.post("/api/v1/auth/register", json={**REGISTRATION, **extra})

    assert response.status_code == 422


def test_a_missing_field_is_422_without_echoing_the_password(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_register", fail_if_called)

    response = client.post(
        "/api/v1/auth/register", json={"password": PASSWORD, "organisation_name": "Acme"}
    )

    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "email"]


def test_an_oversized_password_is_422_without_echoing_it(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_login", fail_if_called)
    enormous = "secret-" * 300

    response = client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": enormous})

    assert response.status_code == 422
    assert enormous not in response.text


def test_other_routes_keep_fastapis_standard_422(client):
    """The echo is removed only under /api/v1/auth; elsewhere the shape is unchanged."""
    response = client.post(
        "/api/sentiment/predict", json={"text": "ab"}, headers={"x-api-key": settings.API_KEY}
    )

    assert response.status_code == 422
    assert "input" in response.json()["detail"][0]


def test_an_unexpected_registration_failure_leaks_nothing(client, monkeypatch):
    def explode(*args):
        raise RuntimeError(f'duplicate key value violates "uq_users_email" password={PASSWORD}')

    monkeypatch.setattr(auth_routes, "_register", explode)

    response = client.post("/api/v1/auth/register", json=REGISTRATION)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not register."}


# ---------------------------------------------------------------- login


def test_signing_in_sets_the_cookie_and_returns_the_organisation_context(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_login", lambda *a: (current_user(), TOKEN))

    response = client.post("/api/v1/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    assert response.json()["organisation"]["role"] == "owner"
    assert TOKEN not in response.text
    assert "HttpOnly" in session_cookie_header(response)


def test_a_failed_sign_in_is_401_with_one_message_and_no_cookie(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_login", lambda *a: None)

    response = client.post("/api/v1/auth/login", json=CREDENTIALS)

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password."}
    assert "set-cookie" not in response.headers


def test_a_disabled_account_is_403(client, monkeypatch):
    def fake(*args):
        raise AccountDisabledError("This account has been disabled.")

    monkeypatch.setattr(auth_routes, "_login", fake)

    response = client.post("/api/v1/auth/login", json=CREDENTIALS)

    assert response.status_code == 403
    assert response.json() == {"detail": "This account has been disabled."}


def test_an_organisation_cannot_be_chosen_at_sign_in(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "_login", fail_if_called)

    response = client.post("/api/v1/auth/login", json={**CREDENTIALS, "organisation_id": ORG_ID})

    assert response.status_code == 422


def test_an_unexpected_sign_in_failure_leaks_nothing(client, monkeypatch):
    def explode(*args):
        raise RuntimeError("connection to server at 10.0.0.5 failed")

    monkeypatch.setattr(auth_routes, "_login", explode)

    response = client.post("/api/v1/auth/login", json=CREDENTIALS)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not sign in."}


# ---------------------------------------------------------------- logout


def test_signing_out_ends_the_session_and_clears_the_cookie(client, monkeypatch):
    ended = []
    monkeypatch.setattr(auth_routes, "_logout", ended.append)
    client.cookies.set(COOKIE, TOKEN)

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert ended == [TOKEN]
    assert "Max-Age=0" in session_cookie_header(response)


@pytest.mark.parametrize("cookie", [None, "garbage"])
def test_signing_out_without_a_valid_cookie_still_succeeds_without_a_query(client, monkeypatch, cookie):
    monkeypatch.setattr(auth_routes, "_logout", fail_if_called)
    if cookie:
        client.cookies.set(COOKIE, cookie)

    assert client.post("/api/v1/auth/logout").status_code == 204


# ---------------------------------------------------------------- the current user


@pytest.mark.parametrize("cookie", [None, "", "garbage", "a" * 44, TOKEN + "x"])
def test_me_without_a_valid_cookie_is_401_before_any_query(client, monkeypatch, cookie):
    monkeypatch.setattr(deps, "_lookup_session", fail_if_called)
    monkeypatch.setattr(auth_routes, "_me", fail_if_called)
    if cookie is not None:
        client.cookies.set(COOKIE, cookie)

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not signed in."}


def test_me_with_an_unknown_expired_or_revoked_session_is_401(client, monkeypatch):
    monkeypatch.setattr(deps, "_lookup_session", lambda token: None)
    monkeypatch.setattr(auth_routes, "_me", fail_if_called)
    client.cookies.set(COOKIE, TOKEN)

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not signed in."}


def test_me_returns_the_signed_in_user_found_from_the_cookie(client, monkeypatch):
    looked_up = []

    def lookup(token):
        looked_up.append(token)
        return context()

    monkeypatch.setattr(deps, "_lookup_session", lookup)
    monkeypatch.setattr(auth_routes, "_me", lambda ctx: current_user())
    client.cookies.set(COOKIE, TOKEN)

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert looked_up == [TOKEN]
    assert response.json()["organisation"] == {"id": ORG_ID, "name": "Acme Ltd", "role": "owner"}


def test_me_for_a_user_with_no_organisation_has_a_null_organisation(client, monkeypatch):
    monkeypatch.setattr(deps, "_lookup_session", lambda token: context(organisation=False))
    monkeypatch.setattr(auth_routes, "_me", lambda ctx: current_user(organisation=False))
    client.cookies.set(COOKIE, TOKEN)

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == {"email": "ana@example.com", "organisation": None}


def test_the_organisation_dependency_refuses_a_user_with_no_organisation():
    with pytest.raises(HTTPException) as refused:
        deps.get_current_organisation(context(organisation=False))

    assert refused.value.status_code == 403


def test_the_organisation_dependency_passes_the_session_organisation_through():
    ctx = context()

    assert deps.get_current_organisation(ctx).organisation_id == uuid.UUID(ORG_ID)


# ---------------------------------------------------------------- cross-site request forgery


@pytest.fixture()
def explicit_origins(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", "https://app.example.com")


def test_a_sign_in_posted_from_a_foreign_origin_is_refused(client, monkeypatch, explicit_origins):
    monkeypatch.setattr(auth_routes, "_login", fail_if_called)

    response = client.post(
        "/api/v1/auth/login", json=CREDENTIALS, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "This request's origin is not allowed."}


def test_a_sign_out_posted_from_a_foreign_origin_is_refused(client, monkeypatch, explicit_origins):
    monkeypatch.setattr(auth_routes, "_logout", fail_if_called)
    client.cookies.set(COOKIE, TOKEN)

    response = client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["https://app.example.com", "http://testserver", None])
def test_the_allowed_origin_its_own_origin_and_non_browser_clients_are_accepted(
    client, monkeypatch, explicit_origins, origin
):
    monkeypatch.setattr(auth_routes, "_login", lambda *a: (current_user(), TOKEN))
    headers = {"Origin": origin} if origin else {}

    assert client.post("/api/v1/auth/login", json=CREDENTIALS, headers=headers).status_code == 200


def test_reading_is_not_origin_checked(client, monkeypatch, explicit_origins):
    """GET changes nothing, so there is nothing to forge; CORS decides whether a page may read it."""
    monkeypatch.setattr(deps, "_lookup_session", lambda token: context())
    monkeypatch.setattr(auth_routes, "_me", lambda ctx: current_user())
    client.cookies.set(COOKIE, TOKEN)

    response = client.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200


def test_with_wildcard_origins_there_is_nothing_to_check_against(client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", "*")
    monkeypatch.setattr(auth_routes, "_login", lambda *a: (current_user(), TOKEN))

    response = client.post(
        "/api/v1/auth/login", json=CREDENTIALS, headers={"Origin": "https://anything.example"}
    )

    assert response.status_code == 200


# ---------------------------------------------------------------- documentation


def test_the_openapi_document_describes_signing_in(client):
    schema = client.get("/openapi.json").json()

    for path in ["/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/me"]:
        [operation] = schema["paths"][path].values()
        assert operation["summary"]
        assert operation["description"]

    schemes = schema["components"]["securitySchemes"]
    assert any(s["type"] == "apiKey" and s["in"] == "cookie" and s["name"] == COOKIE for s in schemes.values())
