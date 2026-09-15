"""user sessions

Server-side sessions for signed-in browsers (Milestone 7). A new table only; nothing existing
changes.

The cookie carries a random 256-bit token. This table stores **only its SHA-256 hash**
(`token_hash`), so whoever reads it - a leaked backup, say - holds nothing that works as a
cookie. A session is valid because its row exists and has not expired; there is no signing
key anywhere, so there is nothing to leak or rotate.

Named `user_sessions` rather than `sessions`, so it is never confused with a SQLAlchemy
Session in code or conversation.

Columns and constraints, and why:

    token_hash       uq_user_sessions_token_hash - the lookup every authenticated request
                     makes, and a guarantee that one token can only ever mean one session
    expires_at       absolute expiry, compared with the database clock on every lookup
    user_id          ON DELETE CASCADE - deleting a user signs them out everywhere;
                     ix_user_sessions_user_id serves that and "sign this user out"
    organisation_id  which organisation this sign-in acts for. ON DELETE SET NULL: deleting
                     an organisation leaves its members signed in with nothing to reach,
                     rather than deleting their sessions. Nullable, for a user who belongs to
                     no organisation. It is never trusted on its own - every request re-checks
                     it against organisation_memberships.

Expired rows are not cleaned up by anything yet; they are inert (every lookup filters on
`expires_at`) and recorded as a known limitation.

Revision ID: 0006
Revises: 0005
Created: 2026-09-15 14:02:57.227248+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sa.Uuid(), nullable=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['organisation_id'], ['organisations.id'],
            name=op.f('fk_user_sessions_organisation_id'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_user_sessions_user_id'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_sessions')),
        sa.UniqueConstraint('token_hash', name='uq_user_sessions_token_hash'),
    )
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')
