"""
Accounts and sessions - feedbackiq.services.auth

The rules these tests protect:

* **registration always creates a new organisation** and can never attach anyone to an
  existing one;
* **a failed sign-in looks the same whatever went wrong**, except a disabled account after
  the correct password;
* **the token is never stored**, only its hash;
* **the organisation a request acts for comes from the session and a live membership**, and
  from nothing else - removing the membership, deleting the organisation or disabling the user
  takes effect on the very next lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from argon2 import PasswordHasher
from sqlalchemy import event, func, select, update

from feedbackiq.auth.credentials import hash_password, verify_password
from feedbackiq.auth.session_tokens import hash_session_token, is_well_formed, new_session_token
from feedbackiq.core.config import settings
from feedbackiq.core.exceptions import (
    AccountDisabledError,
    AccountExistsError,
    CredentialError,
)
from feedbackiq.db.models import Organisation, OrganisationMembership, User, UserSession
from feedbackiq.services.auth import (
    authenticate,
    end_session,
    register,
    resolve_session,
    start_session,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def count_statements(engine, function):
    """How many SQL statements one call issues (as in test_analytics_queries.py)."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = function()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    return result, statements


@pytest.fixture()
def ana(session):
    """A registered user who owns her own organisation."""
    registration = register(
        session, email="Ana@Example.com", password=PASSWORD, organisation_name="Acme Ltd"
    )
    session.commit()

    return registration


# ---------------------------------------------------------------- registration


def test_registration_creates_a_user_a_new_organisation_and_an_owner_membership(session, ana):
    assert ana.user.email == "ana@example.com"
    assert verify_password(PASSWORD, ana.user.password_hash)

    assert ana.organisation.name == "Acme Ltd"
    assert ana.organisation.slug.startswith("acme-ltd-")

    assert (ana.membership.user_id, ana.membership.organisation_id, ana.membership.role) == (
        ana.user.id, ana.organisation.id, "owner",
    )
    assert (count(session, User), count(session, Organisation), count(session, OrganisationMembership)) == (1, 1, 1)


def test_registration_never_joins_an_existing_organisation(session, organisation):
    """Using an existing organisation's exact name creates a second, separate organisation."""
    session.commit()

    registration = register(
        session, email="ben@example.com", password=PASSWORD, organisation_name=organisation.name
    )
    session.commit()

    assert registration.organisation.id != organisation.id
    assert registration.organisation.slug != organisation.slug
    assert session.scalars(
        select(OrganisationMembership).where(OrganisationMembership.organisation_id == organisation.id)
    ).all() == []


def test_organisations_with_the_same_name_get_different_slugs(session):
    first = register(session, email="a@example.com", password=PASSWORD, organisation_name="Acme")
    second = register(session, email="b@example.com", password=PASSWORD, organisation_name="Acme")

    assert first.organisation.slug != second.organisation.slug


def test_an_organisation_name_is_tidied(session):
    registration = register(
        session, email="a@example.com", password=PASSWORD, organisation_name="  Acme \t  Ltd "
    )

    assert registration.organisation.name == "Acme Ltd"


def test_an_email_registers_only_once_whatever_its_case(session, ana):
    with pytest.raises(AccountExistsError):
        register(session, email="ANA@example.COM", password=PASSWORD, organisation_name="Other")

    # The savepoint kept the transaction usable, and nothing was left half-created.
    session.commit()
    assert (count(session, User), count(session, Organisation)) == (1, 1)


@pytest.mark.parametrize(
    "email, password, organisation_name, message",
    [
        ("not-an-email", PASSWORD, "Acme", "valid email"),
        ("ana@example.com", "short", "Acme", "at least"),
        ("ana@example.com", "ana@example.com", "Acme", "email"),
        ("ana@example.com", PASSWORD, "   ", "organisation name"),
        ("ana@example.com", PASSWORD, "x" * 201, "at most 200"),
    ],
)
def test_invalid_registration_details_create_nothing(session, email, password, organisation_name, message):
    with pytest.raises(CredentialError, match=message):
        register(session, email=email, password=password, organisation_name=organisation_name)

    session.commit()
    assert (count(session, User), count(session, Organisation), count(session, OrganisationMembership)) == (0, 0, 0)


# ---------------------------------------------------------------- signing in


def test_the_right_password_signs_in_whatever_the_email_case(session, ana):
    assert authenticate(session, email="  ANA@example.com ", password=PASSWORD).id == ana.user.id


@pytest.mark.parametrize(
    "email, password",
    [
        ("ana@example.com", "correct horse battery stapler"),   # wrong password
        ("nobody@example.com", PASSWORD),                        # unknown email
        ("not-an-email", PASSWORD),                              # malformed email
        ("ana@example.com", ""),                                 # empty password
        ("ana@example.com", "x" * 10_000),                       # oversized password
    ],
)
def test_every_failed_sign_in_is_the_same_none(session, ana, email, password):
    assert authenticate(session, email=email, password=password) is None


def test_a_disabled_account_is_refused_even_with_the_right_password(session, ana):
    ana.user.is_active = False
    session.commit()

    with pytest.raises(AccountDisabledError):
        authenticate(session, email="ana@example.com", password=PASSWORD)


def test_a_disabled_account_with_the_wrong_password_is_an_ordinary_failure(session, ana):
    """Without the password, "disabled" must not be distinguishable from "wrong"."""
    ana.user.is_active = False
    session.commit()

    assert authenticate(session, email="ana@example.com", password="not the password at all") is None


def test_an_old_weak_hash_is_upgraded_at_sign_in(session, ana):
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    ana.user.password_hash = weak
    session.commit()

    user = authenticate(session, email="ana@example.com", password=PASSWORD)
    session.commit()

    assert user.password_hash != weak
    assert user.password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert verify_password(PASSWORD, user.password_hash)


# ---------------------------------------------------------------- sessions


def test_only_the_hash_of_a_session_token_is_stored(session, ana):
    token = start_session(session, user=ana.user)
    session.commit()

    [stored] = session.scalars(select(UserSession)).all()

    assert is_well_formed(token)
    assert stored.token_hash == hash_session_token(token)
    assert token not in stored.token_hash


def test_a_session_acts_for_the_users_organisation_and_role(session, ana):
    token = start_session(session, user=ana.user)
    session.commit()

    context = resolve_session(session, token)

    assert (context.user_id, context.email) == (ana.user.id, "ana@example.com")
    assert (context.organisation_id, context.role) == (ana.organisation.id, "owner")


def test_a_session_expires_after_the_configured_lifetime(session, ana):
    start_session(session, user=ana.user)
    session.commit()

    stored = session.scalar(select(UserSession))
    expected = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS)

    assert abs(stored.expires_at - expected) < timedelta(minutes=1)


def test_a_member_session_carries_the_member_role(session, ana):
    ben = User(email="ben@example.com", password_hash=hash_password(PASSWORD))
    session.add(ben)
    session.flush()
    session.add(OrganisationMembership(user_id=ben.id, organisation_id=ana.organisation.id, role="member"))
    session.commit()

    context = resolve_session(session, start_session(session, user=ben))

    assert (context.organisation_id, context.role) == (ana.organisation.id, "member")


def test_a_user_with_no_membership_is_signed_in_with_no_organisation(session):
    loner = User(email="loner@example.com", password_hash=hash_password(PASSWORD))
    session.add(loner)
    session.commit()

    context = resolve_session(session, start_session(session, user=loner))

    assert context.user_id == loner.id
    assert (context.organisation_id, context.role) == (None, None)


def test_an_unknown_token_resolves_to_nobody(session, ana):
    start_session(session, user=ana.user)
    session.commit()

    assert resolve_session(session, new_session_token()) is None


@pytest.mark.parametrize("token", [None, "", "garbage", "a" * 44, "' OR 1=1 --" + "a" * 32])
def test_a_malformed_token_resolves_to_nobody_without_a_query(session, db_engine, token):
    result, statements = count_statements(db_engine, lambda: resolve_session(session, token))

    assert result is None
    assert statements == []


def test_resolving_a_session_is_one_sql_statement(session, db_engine, ana):
    token = start_session(session, user=ana.user)
    session.commit()

    context, statements = count_statements(db_engine, lambda: resolve_session(session, token))

    assert context is not None
    assert len(statements) == 1


def test_an_expired_session_resolves_to_nobody(session, ana):
    token = start_session(session, user=ana.user)
    session.execute(update(UserSession).values(expires_at=func.now() - timedelta(seconds=1)))
    session.commit()

    assert resolve_session(session, token) is None


def test_disabling_a_user_ends_their_existing_sessions(session, ana):
    token = start_session(session, user=ana.user)
    session.commit()
    assert resolve_session(session, token) is not None

    ana.user.is_active = False
    session.commit()

    assert resolve_session(session, token) is None


def test_removing_a_membership_removes_the_organisation_on_the_next_lookup(session, ana):
    token = start_session(session, user=ana.user)
    session.commit()

    session.delete(ana.membership)
    session.commit()

    context = resolve_session(session, token)
    assert context.user_id == ana.user.id
    assert (context.organisation_id, context.role) == (None, None)


def test_a_soft_deleted_organisation_is_no_longer_reachable(session, ana):
    token = start_session(session, user=ana.user)
    ana.organisation.deleted_at = datetime.now(timezone.utc)
    session.commit()

    assert resolve_session(session, token).organisation_id is None


def test_a_session_pointing_at_an_organisation_without_membership_reaches_nothing(
    session, ana, other_organisation
):
    """Even if a session row named another organisation - a bug, a bad migration, a hand edit -
    the membership re-check means it grants nothing there."""
    token = start_session(session, user=ana.user)
    session.execute(update(UserSession).values(organisation_id=other_organisation.id))
    session.commit()

    context = resolve_session(session, token)

    assert context.organisation_id is None
    assert context.role is None


def test_two_users_sessions_each_resolve_to_their_own_organisation(session, ana):
    ben = register(session, email="ben@example.com", password=PASSWORD, organisation_name="Globex")
    session.commit()

    ana_token = start_session(session, user=ana.user)
    ben_token = start_session(session, user=ben.user)
    session.commit()

    assert resolve_session(session, ana_token).organisation_id == ana.organisation.id
    assert resolve_session(session, ben_token).organisation_id == ben.organisation.id


def test_signing_out_ends_the_session_and_is_safe_to_repeat(session, ana):
    token = start_session(session, user=ana.user)
    session.commit()

    end_session(session, token)
    session.commit()
    assert resolve_session(session, token) is None

    end_session(session, token)
    end_session(session, None)
    end_session(session, "garbage")
    session.commit()


def test_signing_out_one_session_leaves_the_others(session, ana):
    laptop = start_session(session, user=ana.user)
    phone = start_session(session, user=ana.user)
    session.commit()

    end_session(session, laptop)
    session.commit()

    assert resolve_session(session, laptop) is None
    assert resolve_session(session, phone) is not None


def test_deleting_a_user_deletes_their_sessions(session, ana):
    start_session(session, user=ana.user)
    session.commit()

    session.delete(ana.user)
    session.commit()

    assert count(session, UserSession) == 0


def test_deleting_the_organisation_keeps_the_user_signed_in_with_nothing_to_reach(session, ana):
    token = start_session(session, user=ana.user)
    session.commit()

    session.delete(ana.organisation)
    session.commit()

    context = resolve_session(session, token)
    assert context.user_id == ana.user.id
    assert context.organisation_id is None


def test_a_fresh_random_uuid_is_not_a_session(session, ana):
    """Session ids are internal; only the token in the cookie means anything."""
    session.commit()

    assert resolve_session(session, str(uuid.uuid4())) is None
