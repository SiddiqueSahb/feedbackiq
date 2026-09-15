"""
Accounts and sessions: registration, sign-in, sign-out, and turning a cookie into a user.

    register(session, email=, password=, organisation_name=)   -> Registration
    authenticate(session, email=, password=)                    -> User | None
    start_session(session, user=)                               -> token for the cookie
    resolve_session(session, token)                             -> AuthContext | None
    end_session(session, token)                                 -> None

Every function takes an open SQLAlchemy session and never commits. The caller's
`session_scope` owns the transaction, so registration's inserts all land together or not at
all.

**Which organisation a request acts for is decided here**, from two rows a client cannot
write - its session and its membership. Nothing a request sends is consulted.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from feedbackiq.auth.credentials import (
    PASSWORD_MAX_CHARS,
    hash_password,
    needs_rehash,
    validate_email,
    validate_password,
    verify_against_dummy_hash,
    verify_password,
)
from feedbackiq.auth.session_tokens import (
    hash_session_token,
    is_well_formed,
    new_session_token,
)
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import (
    AccountDisabledError,
    AccountExistsError,
    CredentialError,
)
from feedbackiq.core.logging import get_logger
from feedbackiq.db.models import Organisation, OrganisationMembership, User, UserSession

log = get_logger("service.auth")

# Matches organisations.name.
MAX_ORGANISATION_NAME_CHARS = 200

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Registration:
    """What registering created."""

    user: User
    organisation: Organisation
    membership: OrganisationMembership


@dataclass(frozen=True)
class AuthContext:
    """
    Who is making a request, and which organisation they act for.

    `organisation_id` and `role` are None when the user has no usable membership - no
    organisation at all, or the session's organisation is no longer theirs. Such a request is
    signed in, but has no customer data to reach.
    """

    user_id: uuid.UUID
    email: str
    session_id: uuid.UUID
    organisation_id: uuid.UUID | None
    role: str | None


# ---------------------------------------------------------------- registration


def register(
    session: Session,
    *,
    email: str,
    password: str,
    organisation_name: str,
) -> Registration:
    """
    Create a user, a brand-new organisation, and make the user its owner.

    The organisation is always new and always named by the person registering. Registration
    can never attach anyone to an organisation that already exists, whatever the request
    contains: joining one needs an invitation from its owner, which is a later milestone.

    Raises `CredentialError` for an invalid detail and `AccountExistsError` for a taken email.
    """
    address = validate_email(email)
    validate_password(password, email=address)
    name = _clean_organisation_name(organisation_name)

    user = User(email=address, password_hash=hash_password(password))

    # A savepoint, so a taken email becomes a clear error while the caller's transaction stays
    # usable. Checking with a SELECT first would still race a simultaneous registration; the
    # unique constraint on users.email is the real guard.
    try:
        with session.begin_nested():
            session.add(user)
            session.flush()
    except IntegrityError:
        raise AccountExistsError("An account with this email already exists.") from None

    organisation = Organisation(name=name, slug=_new_slug(name))
    session.add(organisation)
    session.flush()

    membership = OrganisationMembership(
        user_id=user.id, organisation_id=organisation.id, role="owner"
    )
    session.add(membership)
    session.flush()

    log.info("Registered user %s as owner of organisation %s", user.id, organisation.id)

    return Registration(user=user, organisation=organisation, membership=membership)


def _clean_organisation_name(name: str) -> str:
    cleaned = " ".join(name.split())

    if not cleaned:
        raise CredentialError("Enter an organisation name.")

    if len(cleaned) > MAX_ORGANISATION_NAME_CHARS:
        raise CredentialError(
            f"Organisation name must be at most {MAX_ORGANISATION_NAME_CHARS} characters."
        )

    return cleaned


def _new_slug(name: str) -> str:
    """
    A URL-safe handle such as "acme-ltd-3f9a1c".

    The random suffix keeps two organisations with the same name apart without a
    look-up-then-insert race. No API accepts a slug to choose an organisation.
    """
    base = _NOT_SLUG.sub("-", name.lower()).strip("-")[:60] or "organisation"

    return f"{base}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------- signing in


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    """
    The user these credentials belong to, or None.

    None for every kind of wrong - unknown email, wrong password, malformed input - and each
    takes about as long as the others, so neither the answer nor a stopwatch reveals whether
    an address is registered.

    The one distinct outcome is a disabled account, raised as `AccountDisabledError` and only
    *after* the correct password: whoever knows the password learns nothing new from it.
    """
    if len(password) > PASSWORD_MAX_CHARS:
        # Never hash an oversized input. Refusing on length alone says nothing about accounts.
        return None

    try:
        address = validate_email(email)
    except CredentialError:
        verify_against_dummy_hash(password)
        return None

    user = session.scalar(select(User).where(User.email == address))

    if user is None:
        verify_against_dummy_hash(password)
        return None

    if not verify_password(password, user.password_hash):
        return None

    if not user.is_active:
        raise AccountDisabledError("This account has been disabled.")

    # The only moment the plain password is available: upgrade a hash made with older, weaker
    # parameters. The caller's commit stores it.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    return user


def start_session(session: Session, *, user: User) -> str:
    """
    Sign `user` in: store a new session, and return its token for the cookie.

    The token is returned once and never stored. The session acts for the user's earliest
    membership in an organisation that is not deleted - with one organisation per registration,
    simply theirs. Choosing between several is a later feature (an organisation switcher); it
    would change which row is picked here, not how access is checked.
    """
    organisation_id = session.scalar(
        select(OrganisationMembership.organisation_id)
        .join(Organisation, Organisation.id == OrganisationMembership.organisation_id)
        .where(
            OrganisationMembership.user_id == user.id,
            Organisation.deleted_at.is_(None),
        )
        .order_by(OrganisationMembership.created_at, OrganisationMembership.id)
        .limit(1)
    )

    token = new_session_token()

    session.add(
        UserSession(
            user_id=user.id,
            organisation_id=organisation_id,
            token_hash=hash_session_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS),
        )
    )
    session.flush()

    return token


# ---------------------------------------------------------------- every request


def resolve_session(session: Session, token: str | None) -> AuthContext | None:
    """
    The signed-in user, and the organisation they act for, behind a cookie - or None.

    None when the token is malformed (no query is made at all), unknown, expired, or belongs to
    a disabled user.

    The organisation comes from the session **only if** the user still holds a membership
    there and the organisation is not deleted; otherwise the context carries no organisation.
    The request contributes nothing but the token.

    One SQL statement, because every authenticated request pays for it.
    """
    if not is_well_formed(token):
        return None

    row = session.execute(
        select(
            UserSession.id.label("session_id"),
            User.id.label("user_id"),
            User.email,
            OrganisationMembership.organisation_id,
            OrganisationMembership.role,
        )
        .select_from(UserSession)
        .join(User, User.id == UserSession.user_id)
        # Outer joins: a missing organisation or membership removes the organisation from the
        # context, not the user from the request.
        .outerjoin(
            Organisation,
            and_(
                Organisation.id == UserSession.organisation_id,
                Organisation.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            OrganisationMembership,
            and_(
                OrganisationMembership.user_id == UserSession.user_id,
                OrganisationMembership.organisation_id == Organisation.id,
            ),
        )
        .where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.expires_at > func.now(),
            User.is_active.is_(True),
        )
    ).one_or_none()

    if row is None:
        return None

    return AuthContext(
        user_id=row.user_id,
        email=row.email,
        session_id=row.session_id,
        organisation_id=row.organisation_id,
        role=row.role,
    )


def end_session(session: Session, token: str | None) -> None:
    """Sign out: delete the session. Safe with an unknown, expired or malformed token."""
    if not is_well_formed(token):
        return

    session.execute(delete(UserSession).where(UserSession.token_hash == hash_session_token(token)))
