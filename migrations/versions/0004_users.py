"""users

The first identity table (Milestone 7). A new table only: no existing table, constraint or
row is touched, so this is safe on a database that already holds customer data.

`users` is deliberately **global** - no `organisation_id`. Which organisations a person may
act for is a separate fact (memberships, next migration), because one person can belong to
more than one.

Two constraints make email identity case-insensitive in the database, not only in code:

    ck_users_email_normalised   the address is stored trimmed and lower-case
    uq_users_email              and unique

A plain unique constraint alone would let "Ana@x.com" and "ana@x.com" become two accounts.
A unique index on `lower(email)` would also work, but the CHECK keeps one spelling in the
table, so every lookup is a simple equality that uses the ordinary index.

`password_hash` holds an argon2id hash string (auth/credentials.py). There is no column for
the password itself.

Revision ID: 0004
Revises: 0003
Created: 2026-09-15 12:45:47.338387+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('email = lower(btrim(email))', name=op.f('ck_users_email_normalised')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
        sa.UniqueConstraint('email', name='uq_users_email'),
    )


def downgrade() -> None:
    op.drop_table('users')
