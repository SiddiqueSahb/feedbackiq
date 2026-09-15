"""organisation memberships

Links users to the organisations they may act for (Milestone 7). A new table only; nothing
existing changes, and no organisation_id column on any tenant table is touched - membership
decides *which* organisation a request may use, while the tenant tables keep saying *whose*
each row is.

    users  1 ── * organisation_memberships * ── 1  organisations

Constraints, and why each exists:

    uq_organisation_memberships_user_id_organisation_id
        one membership per person per organisation, so there is never a question of which
        of two roles applies. Leads with user_id: it is also the index behind the lookup
        every authenticated request makes.
    ck_organisation_memberships_role_valid
        role in ('owner', 'member'). A CHECK rather than an enum, so adding a role later is
        a one-line migration without enum locks.
    fk_..._user_id, fk_..._organisation_id  ON DELETE CASCADE
        deleting either side removes the membership and never the other side.
    ix_organisation_memberships_organisation_id
        "who belongs to this organisation?"

No existing organisation gets a member here. The seeded development organisation simply has
none until someone registers into a new one; nothing needs backfilling.

Revision ID: 0005
Revises: 0004
Created: 2026-09-15 12:50:13.268582+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'organisation_memberships',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role in ('owner', 'member')", name=op.f('ck_organisation_memberships_role_valid')),
        sa.ForeignKeyConstraint(
            ['organisation_id'], ['organisations.id'],
            name=op.f('fk_organisation_memberships_organisation_id'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_organisation_memberships_user_id'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_organisation_memberships')),
        sa.UniqueConstraint(
            'user_id', 'organisation_id',
            name='uq_organisation_memberships_user_id_organisation_id',
        ),
    )
    op.create_index(
        'ix_organisation_memberships_organisation_id',
        'organisation_memberships',
        ['organisation_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_organisation_memberships_organisation_id', table_name='organisation_memberships')
    op.drop_table('organisation_memberships')
