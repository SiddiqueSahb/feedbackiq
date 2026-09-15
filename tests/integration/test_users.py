"""
Users - feedbackiq.db.models.User

What the database itself guarantees about identity, independently of any service code:
**one account per email address whatever its capitalisation, and nothing in the row that is
the password.**
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from feedbackiq.auth.credentials import hash_password, verify_password
from feedbackiq.db.models import User

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


def add_user(session, email="ana@example.com", password=PASSWORD) -> User:
    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    session.flush()

    return user


def test_a_user_is_stored_with_a_hash_and_never_the_password(session):
    user = add_user(session)
    session.commit()

    row = session.execute(
        text("SELECT email, password_hash, is_active FROM users WHERE id = :id"), {"id": user.id}
    ).one()

    assert row.email == "ana@example.com"
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash
    assert verify_password(PASSWORD, row.password_hash)


def test_a_new_user_is_active_by_default_even_when_inserted_directly(session):
    """The server default, not only the Python one: raw SQL inserts get it too."""
    session.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (gen_random_uuid(), 'raw@example.com', 'x')")
    )

    assert session.scalar(select(User.is_active).where(User.email == "raw@example.com")) is True


def test_an_email_can_only_register_once(session):
    add_user(session)
    session.commit()

    with pytest.raises(IntegrityError, match="uq_users_email"):
        add_user(session)


def test_an_email_that_is_not_normalised_is_refused_by_the_database(session):
    """With the unique constraint, this is what makes uniqueness case-insensitive: "ANA@..."
    cannot be stored at all, so it cannot sit beside "ana@..." as a second account."""
    with pytest.raises(IntegrityError, match="ck_users_email_normalised"):
        add_user(session, email="ANA@example.com")


def test_an_email_with_surrounding_spaces_is_refused_by_the_database(session):
    with pytest.raises(IntegrityError, match="ck_users_email_normalised"):
        add_user(session, email=" ana@example.com")


def test_updated_at_moves_when_the_user_changes(session):
    user = add_user(session)
    session.commit()
    created = user.updated_at

    user.is_active = False
    session.commit()
    session.refresh(user)

    assert user.updated_at > created
