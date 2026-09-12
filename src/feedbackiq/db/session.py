"""
Connecting to PostgreSQL.

One engine per process, sessions created per unit of work. Synchronous on purpose: the
API already offloads slow work with `asyncio.to_thread`, and an async database layer
would add a second concurrency model for no measured gain.

    from feedbackiq.db.session import session_scope

    with session_scope() as session:
        session.add(Organisation(name="Acme", slug="acme"))
        # committed on a clean exit, rolled back if the block raises
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger

log = get_logger("db.session")


@lru_cache(maxsize=1)
def get_engine(url: str | None = None) -> Engine:
    """
    The process-wide SQLAlchemy engine (and its connection pool).

    `pool_pre_ping` costs one cheap round-trip per checkout and saves the classic
    "server closed the connection unexpectedly" after an idle period or a database
    restart - worth it for a long-running API process.
    """
    database_url = url or settings.DATABASE_URL

    log.info("Creating database engine for %s", _safe_url(database_url))

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """
    A session wrapped in a transaction: commit on success, roll back on any exception.

    `expire_on_commit=False` on the factory means objects stay readable after the commit,
    so a caller can return what it just wrote without a second query.
    """
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine and factory. Used by tests that switch database URL."""
    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _safe_url(url: str) -> str:
    """The URL with any password removed, so it can be logged."""
    if "@" not in url:
        return url

    prefix, _, host = url.partition("@")
    scheme, _, credentials = prefix.partition("://")
    user = credentials.split(":", 1)[0]

    return f"{scheme}://{user}:***@{host}"
