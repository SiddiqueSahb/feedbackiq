"""
Organisation membership - feedbackiq.db.models.OrganisationMembership

A membership is what authentication will turn into an organisation scope, so its constraints
are part of the tenant boundary: **a role is always a known one, a person belongs to an
organisation at most once, and deleting either side never deletes the other.**
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from feedbackiq.auth.credentials import hash_password
from feedbackiq.db.models import (
    MEMBERSHIP_ROLES,
    Organisation,
    OrganisationMembership,
    User,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def password_hash() -> str:
    # Hashed once: argon2 is deliberately slow, and these tests are about rows, not hashing.
    return hash_password("correct horse battery staple")


@pytest.fixture()
def user(session, password_hash):
    user = User(email="ana@example.com", password_hash=password_hash)
    session.add(user)
    session.flush()

    return user


def join(session, user, organisation, role="member") -> OrganisationMembership:
    membership = OrganisationMembership(
        user_id=user.id, organisation_id=organisation.id, role=role
    )
    session.add(membership)
    session.flush()

    return membership


def memberships_of(session, user):
    return session.scalars(
        select(OrganisationMembership).where(OrganisationMembership.user_id == user.id)
    ).all()


# ---------------------------------------------------------------- roles


def test_the_initial_roles_are_owner_and_member():
    """Pinned so adding a role is a visible, deliberate change with its own migration."""
    assert MEMBERSHIP_ROLES == ("owner", "member")


@pytest.mark.parametrize("role", MEMBERSHIP_ROLES)
def test_a_user_can_hold_each_role(session, user, organisation, role):
    join(session, user, organisation, role=role)
    session.commit()

    [membership] = memberships_of(session, user)
    assert (membership.organisation_id, membership.role) == (organisation.id, role)


@pytest.mark.parametrize("role", ["admin", "OWNER", "", "superuser"])
def test_an_unknown_role_is_refused_by_the_database(session, user, organisation, role):
    with pytest.raises(IntegrityError, match="ck_organisation_memberships_role_valid"):
        join(session, user, organisation, role=role)


def test_a_membership_needs_a_role(session, user, organisation):
    with pytest.raises(IntegrityError):
        join(session, user, organisation, role=None)


# ---------------------------------------------------------------- uniqueness


def test_a_user_cannot_join_the_same_organisation_twice(session, user, organisation):
    """Not even with a different role: one row, one role, no ambiguity about which applies."""
    join(session, user, organisation, role="member")
    session.commit()

    with pytest.raises(IntegrityError, match="uq_organisation_memberships_user_id_organisation_id"):
        join(session, user, organisation, role="owner")


def test_a_user_can_belong_to_several_organisations(session, user, organisation, other_organisation):
    join(session, user, organisation, role="owner")
    join(session, user, other_organisation, role="member")
    session.commit()

    assert {m.organisation_id for m in memberships_of(session, user)} == {
        organisation.id, other_organisation.id,
    }


def test_a_user_with_no_membership_is_a_valid_account_with_no_organisation(session, user):
    """Allowed by the schema. Such a user can sign in but can reach no organisation's data -
    the API decides what that means, not the database."""
    session.commit()

    assert memberships_of(session, user) == []


# ---------------------------------------------------------------- integrity


def test_a_membership_cannot_point_at_a_missing_user_or_organisation(session, user, organisation):
    import uuid

    # Committed first, so the rollback between the two checks cannot take the real user and
    # organisation with it - each check must fail for the one reference it makes up.
    session.commit()

    session.add(OrganisationMembership(user_id=uuid.uuid4(), organisation_id=organisation.id, role="member"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(OrganisationMembership(user_id=user.id, organisation_id=uuid.uuid4(), role="member"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_a_user_removes_their_memberships_but_not_the_organisation(
    session, user, organisation
):
    join(session, user, organisation, role="owner")
    session.commit()

    session.execute(delete(User).where(User.id == user.id))
    session.commit()

    assert session.scalars(select(OrganisationMembership)).all() == []
    assert session.get(Organisation, organisation.id) is not None


def test_deleting_an_organisation_removes_its_memberships_but_not_the_user(
    session, user, organisation
):
    join(session, user, organisation, role="owner")
    session.commit()

    session.execute(delete(Organisation).where(Organisation.id == organisation.id))
    session.commit()

    assert session.scalars(select(OrganisationMembership)).all() == []
    assert session.get(User, user.id) is not None
