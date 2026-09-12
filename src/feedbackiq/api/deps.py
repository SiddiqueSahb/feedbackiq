"""
Shared FastAPI dependencies — API key auth.

Key goes in the `x-api-key` header (not a query param, to keep it out of
logs). Uses `secrets.compare_digest` to avoid timing leaks. Health/root
routes stay open for load balancer probes. Refuses to boot in production
if API_KEY is unset or still the dev default.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger

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
