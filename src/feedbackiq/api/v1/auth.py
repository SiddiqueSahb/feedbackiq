"""
Signing up, in and out: /api/v1/auth/*

    POST /api/v1/auth/register   create an account and a new organisation, and sign in
    POST /api/v1/auth/login      sign in
    POST /api/v1/auth/logout     sign out; always succeeds
    GET  /api/v1/auth/me         who is signed in, and for which organisation

These need no API key: they are how a person gets a session in the first place (`me` needs
the session instead).

How the session travels, and why:

* The token is set in a cookie and **never appears in a response body**. The cookie is
  `HttpOnly` (page JavaScript - including anything injected into the page - cannot read it),
  `SameSite=Lax` (a browser does not attach it to another site's form posts) and `Secure` in
  production (never sent over plain HTTP). The development difference is in core/config.py.
* Request bodies refuse unknown fields, so a client that adds `organisation_id` or `role`
  gets a 422 instead of having it quietly ignored. Ownership is never something a caller
  supplies.
* A failed sign-in has one message whatever went wrong. The single exception is a disabled
  account, reported only after the correct password (services/auth.py).

Sessions open inside the handlers, as elsewhere in the API, so a request that fails
validation never reaches the database.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, status

from feedbackiq.api.deps import check_origin, get_current_user, session_cookie
from feedbackiq.api.v1.schemas import (
    CurrentUser,
    LoginRequest,
    OrganisationContext,
    RegisterRequest,
)
from feedbackiq.auth.session_tokens import is_well_formed
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import (
    AccountDisabledError,
    AccountExistsError,
    CredentialError,
)
from feedbackiq.core.logging import get_logger
from feedbackiq.db.models import Organisation
from feedbackiq.db.session import session_scope
from feedbackiq.services.auth import (
    AuthContext,
    authenticate,
    end_session,
    register,
    resolve_session,
    start_session,
)

router = APIRouter(prefix="/auth", tags=["Authentication (v1)"])

log = get_logger("api.v1.auth")


@router.post(
    "/register",
    response_model=CurrentUser,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and a new organisation",
    description=(
        "Creates a user, a new organisation they own, and signs them in by setting the "
        "session cookie. Registration always creates a *new* organisation; it cannot join "
        "an existing one. 422 for an invalid detail, 409 if the email already has an account."
    ),
)
async def register_account(body: RegisterRequest, request: Request, response: Response) -> CurrentUser:

    check_origin(request)

    try:
        current, token = await asyncio.to_thread(
            _register, body.email, body.password, body.organisation_name
        )

    except CredentialError as exc:
        raise HTTPException(status_code=422, detail=exc.message)

    except AccountExistsError as exc:
        raise HTTPException(status_code=409, detail=exc.message)

    except Exception:
        # Never the exception text: it could hold SQL, or the values that were submitted.
        log.exception("Registration failed.")
        raise HTTPException(status_code=500, detail="Could not register.")

    _set_session_cookie(response, token)

    return current


@router.post(
    "/login",
    response_model=CurrentUser,
    summary="Sign in",
    description=(
        "Sets the session cookie. 401 with the same message for an unknown email and a wrong "
        "password; 403 for a disabled account."
    ),
)
async def login(body: LoginRequest, request: Request, response: Response) -> CurrentUser:

    check_origin(request)

    try:
        signed_in = await asyncio.to_thread(_login, body.email, body.password)

    except AccountDisabledError as exc:
        raise HTTPException(status_code=403, detail=exc.message)

    except Exception:
        log.exception("Sign-in failed.")
        raise HTTPException(status_code=500, detail="Could not sign in.")

    if signed_in is None:
        # No email address in the log: a failed sign-in is not a reason to record who tried.
        log.info("Sign-in refused.")
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    current, token = signed_in
    _set_session_cookie(response, token)

    return current


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Sign out",
    description="Ends the session and clears the cookie. Succeeds whether or not anyone was signed in.",
)
async def logout(request: Request, token: str | None = Security(session_cookie)) -> Response:

    check_origin(request)

    if is_well_formed(token):
        try:
            await asyncio.to_thread(_logout, token)

        except Exception:
            log.exception("Sign-out failed.")
            raise HTTPException(status_code=500, detail="Could not sign out.")

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )

    return response


@router.get(
    "/me",
    response_model=CurrentUser,
    summary="The signed-in user",
    description=(
        "The user's email and the organisation they act for, with their role. `organisation` "
        "is null for a user who belongs to no organisation. 401 when not signed in."
    ),
)
async def read_current_user(context: AuthContext = Depends(get_current_user)) -> CurrentUser:

    try:
        return await asyncio.to_thread(_me, context)

    except Exception:
        log.exception("Reading the current user failed.")
        raise HTTPException(status_code=500, detail="Could not load the current user.")


# ---------------------------------------------------------------- the cookie


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


# ---------------------------------------------------------------- blocking work


def _register(email: str, password: str, organisation_name: str) -> tuple[CurrentUser, str]:
    with session_scope() as session:
        registration = register(
            session, email=email, password=password, organisation_name=organisation_name
        )
        token = start_session(session, user=registration.user)

        return _current_user(session, resolve_session(session, token)), token


def _login(email: str, password: str) -> tuple[CurrentUser, str] | None:
    with session_scope() as session:
        user = authenticate(session, email=email, password=password)

        if user is None:
            return None

        token = start_session(session, user=user)

        return _current_user(session, resolve_session(session, token)), token


def _logout(token: str) -> None:
    with session_scope() as session:
        end_session(session, token)


def _me(context: AuthContext) -> CurrentUser:
    with session_scope() as session:
        return _current_user(session, context)


def _current_user(session, context: AuthContext) -> CurrentUser:
    organisation = None

    if context.organisation_id is not None:
        row = session.get(Organisation, context.organisation_id)
        organisation = OrganisationContext(
            id=str(context.organisation_id), name=row.name, role=context.role
        )

    return CurrentUser(email=context.email, organisation=organisation)
