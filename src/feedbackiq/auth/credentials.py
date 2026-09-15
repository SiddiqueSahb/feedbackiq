"""
Credentials: email addresses, the password policy, and password hashing.

    normalise_email("  Ana@Example.COM ")   -> "ana@example.com"
    validate_email(address)                 -> the normalised address, or CredentialError
    validate_password(password, email=...)  -> None, or CredentialError
    hash_password(password)                 -> "$argon2id$v=19$m=65536,t=3,p=4$..."
    verify_password(password, stored_hash)  -> True / False

Pure functions: no database, no HTTP, no settings. Registration, login and their tests all
share this one definition of each rule.

**No cryptography is implemented here.** Hashing is argon2id through `argon2-cffi`, the
reference Python binding for the winner of the Password Hashing Competition and OWASP's
first recommendation for password storage. This module picks the library and keeps its
exceptions from leaking into the rest of the code - nothing more.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from feedbackiq.core.exceptions import CredentialError

# RFC 5321 limits a forward path to 256 characters including its angle brackets, so 254 is
# the longest address that can actually receive mail.
MAX_EMAIL_CHARS = 254

# Length is the rule that matters. There are deliberately no "one digit, one symbol" rules:
# NIST SP 800-63B advises against them because they push people towards predictable patterns
# ("Password1!") without adding real strength. 12 is the OWASP ASVS minimum.
PASSWORD_MIN_CHARS = 12

# An upper bound, so nobody can make the server hash a megabyte of text per login attempt.
# Comfortably long enough for a passphrase or a password manager.
PASSWORD_MAX_CHARS = 128

# argon2-cffi's defaults are RFC 9106's "low memory" profile: argon2id, 3 iterations, 64 MiB,
# 4 lanes, and a fresh 16-byte random salt per hash. The parameters are written into every
# hash string, so raising them later does not lock anyone out - see `needs_rehash`.
_hasher = PasswordHasher()

_INVALID_EMAIL = "Enter a valid email address."


def normalise_email(email: str) -> str:
    """
    The one spelling of an address that is stored and compared: trimmed and lower-cased.

    Strictly, the part before the @ may be case-sensitive. In practice no mail provider
    treats it that way, and separate accounts for "Ana@x.com" and "ana@x.com" would be a
    support problem and an impersonation risk.
    """
    return email.strip().lower()


def validate_email(email: str) -> str:
    """
    Return the normalised address, or raise `CredentialError`.

    A shape check, not proof: the only real test of an address is sending mail to it, which
    arrives with email verification in a later milestone.
    """
    address = normalise_email(email)

    if not address or len(address) > MAX_EMAIL_CHARS:
        raise CredentialError(_INVALID_EMAIL)

    local, at, domain = address.rpartition("@")

    if (
        not at
        or not local
        or "@" in local
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or any(character.isspace() for character in address)
    ):
        raise CredentialError(_INVALID_EMAIL)

    return address


def validate_password(password: str, *, email: str | None = None) -> None:
    """
    Raise `CredentialError` if `password` breaks the policy.

    The messages describe the rule and never repeat the password, so they are safe to return
    to a client or write to a log.
    """
    if len(password) < PASSWORD_MIN_CHARS:
        raise CredentialError(f"Password must be at least {PASSWORD_MIN_CHARS} characters.")

    if len(password) > PASSWORD_MAX_CHARS:
        raise CredentialError(f"Password must be at most {PASSWORD_MAX_CHARS} characters.")

    if not password.strip():
        raise CredentialError("Password cannot be only spaces.")

    if email is not None and password.strip().lower() == normalise_email(email):
        raise CredentialError("Password cannot be your email address.")


def hash_password(password: str) -> str:
    """A salted argon2id hash, safe to store. The same password hashes differently each time."""
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Whether `password` matches `stored_hash`.

    False, rather than an exception, for every way this can fail - a wrong password, or a
    corrupt or unrecognised hash - because the caller's answer is the same each time: not
    signed in.
    """
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """
    True when `stored_hash` was made with weaker parameters than today's.

    Checked after a successful login, which is the only moment the plain password is
    available to hash again.
    """
    return _hasher.check_needs_rehash(stored_hash)


def verify_against_dummy_hash(password: str) -> None:
    """
    Spend as long as a real verification would, for an email address with no account.

    Without this, "no such user" returns in microseconds while "wrong password" takes tens of
    milliseconds, and the difference tells an attacker which addresses are registered:
    account enumeration with a stopwatch.
    """
    verify_password(password, _dummy_hash())


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # A hash of a random value nobody knows, made once per process with today's parameters.
    return _hasher.hash(secrets.token_urlsafe(32))
