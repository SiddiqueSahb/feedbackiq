"""
Signing in over HTTP, against the real database - feedbackiq.api.v1.auth end to end

The API tests prove the contract with fakes; these prove the whole path is wired together:
**register -> cookie -> me -> logout**, with the session, the password hash and the cookie
token checked in the tables themselves.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update

from feedbackiq.auth.credentials import hash_password
from feedbackiq.core.config import settings
from feedbackiq.db.models import User, UserSession

pytestmark = pytest.mark.integration

COOKIE = settings.SESSION_COOKIE_NAME
PASSWORD = "correct horse battery staple"


def register(client, email="ana@example.com", organisation_name="Acme Ltd"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "organisation_name": organisation_name},
    )


def login(client, email="ana@example.com", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_register_sign_in_read_and_sign_out(api_client):
    registered = register(api_client, email="Ana@Example.com")
    assert registered.status_code == 201
    assert registered.json()["email"] == "ana@example.com"
    assert registered.json()["organisation"]["name"] == "Acme Ltd"
    assert registered.json()["organisation"]["role"] == "owner"

    me = api_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == registered.json()

    assert api_client.post("/api/v1/auth/logout").status_code == 204
    assert api_client.get("/api/v1/auth/me").status_code == 401


def test_a_signed_out_token_is_dead_even_if_replayed(api_client):
    """Sign-out deletes the server-side row; clearing the cookie is only a courtesy."""
    register(api_client)
    token = api_client.cookies.get(COOKIE)

    api_client.post("/api/v1/auth/logout")
    api_client.cookies.set(COOKIE, token)

    assert api_client.get("/api/v1/auth/me").status_code == 401


def test_signing_in_again_gives_a_new_session_and_keeps_the_old_one(api_client):
    register(api_client)
    first = api_client.cookies.get(COOKIE)
    api_client.cookies.clear()

    assert login(api_client, email="ANA@example.com").status_code == 200
    second = api_client.cookies.get(COOKIE)
    assert second != first

    for token in (first, second):
        api_client.cookies.set(COOKIE, token)
        assert api_client.get("/api/v1/auth/me").status_code == 200


def test_a_second_registration_with_the_same_email_is_409(api_client):
    register(api_client)
    api_client.cookies.clear()

    response = register(api_client, email="ana@EXAMPLE.com", organisation_name="Other")

    assert response.status_code == 409
    assert COOKIE not in api_client.cookies


@pytest.mark.parametrize("email, password", [("ana@example.com", "wrong password entirely"), ("nobody@example.com", PASSWORD)])
def test_wrong_password_and_unknown_email_are_indistinguishable(api_client, email, password):
    register(api_client)
    api_client.cookies.clear()

    response = login(api_client, email=email, password=password)

    assert (response.status_code, response.json()) == (401, {"detail": "Incorrect email or password."})


def test_a_disabled_user_cannot_sign_in_and_their_open_session_stops_working(api_client, session):
    register(api_client)
    open_session = api_client.cookies.get(COOKIE)

    session.execute(update(User).values(is_active=False))
    session.commit()

    api_client.cookies.clear()
    assert login(api_client).status_code == 403

    api_client.cookies.set(COOKIE, open_session)
    assert api_client.get("/api/v1/auth/me").status_code == 401


def test_an_expired_session_is_401(api_client, session):
    register(api_client)

    session.execute(update(UserSession).values(expires_at=func.now() - timedelta(seconds=1)))
    session.commit()

    assert api_client.get("/api/v1/auth/me").status_code == 401


def test_a_user_with_no_organisation_is_signed_in_with_a_null_organisation(api_client, session):
    session.add(User(email="loner@example.com", password_hash=hash_password(PASSWORD)))
    session.commit()

    response = login(api_client, email="loner@example.com")

    assert response.status_code == 200
    assert response.json() == {"email": "loner@example.com", "organisation": None}


def test_neither_the_password_nor_the_cookie_token_is_stored_anywhere(api_client, session):
    register(api_client)
    token = api_client.cookies.get(COOKIE)

    # Every column of every row of both identity tables, as text.
    rows = []
    for table in ("users", "user_sessions"):
        rows += session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t")).scalars().all()
    dump = " ".join(rows)

    assert PASSWORD not in dump
    assert token not in dump
    assert session.scalar(select(func.count()).select_from(UserSession)) == 1
