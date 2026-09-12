"""
The declarative base every model inherits from.

Two things happen here and nowhere else:

1. **A constraint naming convention.** PostgreSQL will happily invent names for indexes,
   foreign keys and unique constraints, and those names differ between what SQLAlchemy
   creates and what Alembic autogenerates - which makes migrations noisy and hard to
   review. Naming them explicitly keeps `alembic revision --autogenerate` deterministic
   and makes a constraint violation readable in a log ("uq_feedback_org_source_external"
   says what went wrong).

2. **Shared column helpers** for the columns almost every table needs, so they are
   spelled the same way everywhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_primary_key() -> Mapped[uuid.UUID]:
    """
    A UUID primary key generated in Python.

    UUIDs rather than serial integers because these identifiers end up in URLs and API
    responses: sequential integers let one customer guess another's record count and
    identifiers. Generated with `uuid4()` in Python rather than by the database, so no
    extension (pgcrypto / uuid-ossp) is required and the default works on any backend.
    """
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def created_at_column() -> Mapped[datetime]:
    """When the row was written, according to the database clock, in UTC."""
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
