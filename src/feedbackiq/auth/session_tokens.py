"""
Session tokens: the random value in a signed-in browser's cookie.

    new_session_token()         -> 43 URL-safe characters, for the cookie
    hash_session_token(token)   -> 64 hex characters, what the database stores
    is_well_formed(value)       -> False for anything that could not be one of ours

The token is 32 bytes from the operating system's secure random generator (`secrets`), so it
cannot be guessed. Only its SHA-256 hash is stored: whoever reads the `user_sessions` table -
a leaked backup, say - gets values they cannot put in a cookie.

Why SHA-256 here when passwords use argon2? Argon2 is slow on purpose, to make guessing a
*human-chosen* password expensive. A 256-bit random token has nothing to guess, so a fast hash
is enough, and it lets every request find its session with one indexed equality lookup.

No signing key is involved. The database row is what makes a token valid, so there is no
secret that could leak, and nothing to rotate.
"""

from __future__ import annotations

import hashlib
import re
import secrets

TOKEN_BYTES = 32

# secrets.token_urlsafe(32) is unpadded base64url: always exactly 43 characters from this set.
_WELL_FORMED = re.compile(r"[A-Za-z0-9_-]{43}")


def new_session_token() -> str:
    """A fresh, unguessable session token. Never stored; only its hash is."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """The value stored in, and looked up from, `user_sessions.token_hash`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_well_formed(value: str | None) -> bool:
    """
    Whether `value` has the shape of a token we issue.

    Checked before any database lookup, so a missing or garbage cookie is turned away without
    costing a query.
    """
    return value is not None and _WELL_FORMED.fullmatch(value) is not None
