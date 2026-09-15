"""
Shared FastAPI dependencies: who is calling, and what they may reach.

Two kinds of caller, two mechanisms:

    research routes   /api/sentiment, /api/search, ...   the shared `x-api-key` header
    signed-in users   /api/v1/auth/me, ...               a session cookie (Milestone 7)

**API key.** Sent in the `x-api-key` header (not a query parameter, to keep it out of logs)
and compared with `secrets.compare_digest` to avoid timing leaks. Health/root routes stay open
for load balancer probes. The API refuses to boot in production if API_KEY is unset or still
the development default.

**Session.** `get_current_user` turns the session cookie into a signed-in user;
`get_current_organisation` adds the organisation that user acts for. A missing or malformed
cookie is refused before the database is touched.
"""

from __future__ import annotations

import secrets
import uuid  # noqa: F401  (used in the return annotation of resolve_organisation_id)

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie, APIKeyHeader

from feedbackiq.auth.session_tokens import is_well_formed
from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.db.session import session_scope
from feedbackiq.services.auth import AuthContext, resolve_session

log = get_logger("api.deps")

API_KEY_HEADER = "x-api-key"

# Dev-only default; never use in production.
DEV_DEFAULT_KEY = "dev-key-feedbackiq"

IS_PRODUCTION = settings.is_production

if IS_PRODUCTION and settings.API_KEY in ("", DEV_DEFAULT_KEY):
    raise RuntimeError(
        "ENVIRONMENT=production but API_KEY is unset or still the development "
        f"default ({DEV_DEFAULT_KEY!r}). Set a real API_KEY — via .env locally, "
        "or Secret Manager / --set-secrets in GCP — before starting the server."
    )

if not IS_PRODUCTION and settings.API_KEY == DEV_DEFAULT_KEY:
    log.warning(
        "Using the development API key. Override API_KEY before deploying."
    )

# auto_error=False so we can return our own error message below.
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

# The session cookie, declared as a security scheme so OpenAPI documents how to sign in.
session_cookie = APIKeyCookie(
    name=settings.SESSION_COOKIE_NAME,
    auto_error=False,
    description="Set by POST /api/v1/auth/login or /register. HttpOnly: a browser sends it automatically.",
)

# Methods that change something, and so could be forged from another site.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def resolve_organisation_id(session) -> "uuid.UUID":
    """
    Which organisation the current request acts for.

    **The single temporary stand-in for authentication.** Until Milestone 7 there is no user
    identity, so every request acts for the seeded development organisation. It is
    deliberately *one* function so that adding real auth means changing one place, and it
    deliberately ignores anything the caller sends: inferring the tenant from a query
    parameter, a header or an uploaded file would be a cross-tenant read waiting to happen.

    Called from inside a handler with an open session, never as a FastAPI dependency, so an
    unauthenticated request never reaches the database.
    """
    from feedbackiq.core.config import settings as _settings
    from feedbackiq.db.persistence import get_organisation_by_slug

    organisation = get_organisation_by_slug(session, _settings.DEV_ORGANISATION_SLUG)

    if organisation is None:
        raise HTTPException(
            status_code=503,
            detail="No organisation is configured. Seed the database first.",
        )

    return organisation.id


# ---------------------------------------------------------------- signed-in users


def check_origin(request: Request) -> None:
    """
    Refuse a state-changing request sent from a web origin this API does not serve.

    Cross-site request forgery - another site making a signed-in browser send a POST - is
    stopped first by the cookie's SameSite=Lax, which modern browsers honour. This is a second,
    independent check: browsers always send `Origin` on such requests, and it must be one of
    ALLOWED_ORIGINS or this API's own.

    A request with no `Origin` header passes. Browsers add one to every cross-origin POST, so
    its absence means a non-browser client (curl, a server), which a forged request is not.
    With ALLOWED_ORIGINS="*" (local development) there is nothing to check against.
    """
    if request.method not in UNSAFE_METHODS:
        return

    origin = request.headers.get("origin")
    allowed = settings.allowed_origin_list

    if origin is None or "*" in allowed:
        return

    own_origin = str(request.base_url).rstrip("/")

    if origin != own_origin and origin not in allowed:
        log.warning("Refused a %s from a disallowed origin.", request.method)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This request's origin is not allowed.",
        )


def get_current_user(
    request: Request,
    token: str | None = Security(session_cookie),
) -> AuthContext:
    """
    The signed-in user behind this request's session cookie. 401 if there is none.

    A missing or malformed cookie is refused without touching the database; a well-formed one
    costs one query (services/auth.py::resolve_session). A plain `def`, so FastAPI runs that
    query in its thread pool rather than blocking the event loop.
    """
    check_origin(request)

    if not is_well_formed(token):
        raise _not_signed_in()

    context = _lookup_session(token)

    if context is None:
        raise _not_signed_in()

    return context


def get_current_organisation(context: AuthContext = Depends(get_current_user)) -> AuthContext:
    """
    The signed-in user **and** the organisation they act for. 403 if they have none.

    The organisation is the one on the user's session, confirmed against a live membership in
    the same query that found the session. Nothing in the request - URL, query string, body,
    header or uploaded file - can change it.
    """
    if context.organisation_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of an organisation.",
        )

    return context


def _lookup_session(token: str) -> AuthContext | None:
    """The database half of `get_current_user`; separate so API tests run without PostgreSQL."""
    with session_scope() as session:
        return resolve_session(session, token)


def _not_signed_in() -> HTTPException:
    # One message for no cookie, a malformed cookie, an unknown, expired or revoked session and
    # a disabled user: the caller's next step is the same, and the difference is nobody's business.
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")


# ---------------------------------------------------------------- research routes


async def require_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """Validate the x-api-key header (wired in at router level in main.py)."""

    if not settings.API_KEY:
        # Not the caller's fault, so this is a 500 rather than a 401.
        log.error("API_KEY is not configured; rejecting authenticated request.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured: no API key is set.",
        )

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key. Send it in the '{API_KEY_HEADER}' header.",
        )

    if not secrets.compare_digest(api_key, settings.API_KEY):
        log.warning("Rejected request with an invalid API key.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return api_key
