"""
Shared setup for the database integration tests.

These tests talk to a **real PostgreSQL**. Nothing here is mocked: a fake database would
prove nothing about foreign keys, partial indexes or CHECK constraints, which are exactly
what these tests exist to verify.

    docker compose up -d postgres
    pytest tests/integration

The server is named by TEST_DATABASE_URL (any database on it will do - a separate
`feedbackiq_test` database is created for the run and dropped afterwards). The default
points at the `postgres` service in docker-compose.yml as published on the host.

Deliberately **no skip-if-unreachable**: if the database cannot be reached, these tests
fail loudly. A suite that silently skips itself in CI reports green while testing nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The server to work on. CI passes its own throwaway service container's URL.
ADMIN_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://feedbackiq:feedbackiq@localhost:55432/postgres",
)

TEST_DB_NAME = "feedbackiq_test"

# A second database, for the migration tests: upgrading and downgrading it must not
# disturb the one every other test is using.
THROWAWAY_DB_NAME = "feedbackiq_migration_check"


# ---------------------------------------------------------------- database plumbing


def admin_connection():
    """
    A connection that can CREATE and DROP databases.

    AUTOCOMMIT because PostgreSQL refuses CREATE DATABASE inside a transaction.
    """
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")

    return engine, engine.connect()


def create_database(name: str) -> str:
    """Drop `name` if it is there, create it empty, and return its URL."""
    engine, connection = admin_connection()
    try:
        # Anything still connected would block the DROP.
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        connection.close()
        engine.dispose()

    # render_as_string(hide_password=False), not str(): str() on a SQLAlchemy URL replaces
    # the password with "***", which is right for a log and useless for connecting.
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def drop_database(name: str) -> None:
    engine, connection = admin_connection()
    try:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        connection.close()
        engine.dispose()


def migrate(url: str, revision: str = "head") -> None:
    """
    Run the project's real migrations against `url`.

    The tables are never created with `Base.metadata.create_all()` in these tests: that
    would test the models and leave the migrations - the thing a deployment actually runs -
    unverified.

    `migrations/env.py` reads `settings.DATABASE_URL`, so that is what gets pointed at the
    test database here.
    """
    from feedbackiq.core.config import settings

    previous = settings.DATABASE_URL
    settings.DATABASE_URL = url
    os.environ["DATABASE_URL"] = url

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    try:
        if revision == "base":
            command.downgrade(config, "base")
        else:
            command.upgrade(config, revision)
    finally:
        settings.DATABASE_URL = previous
        os.environ["DATABASE_URL"] = previous


def alembic_config(url: str) -> Config:
    """An Alembic config pointed at `url`, for tests that call Alembic directly."""
    from feedbackiq.core.config import settings

    settings.DATABASE_URL = url
    os.environ["DATABASE_URL"] = url

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    return config


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def database_url() -> str:
    """A migrated, empty test database for the whole session."""
    url = create_database(TEST_DB_NAME)
    migrate(url)

    yield url

    drop_database(TEST_DB_NAME)


@pytest.fixture()
def empty_database() -> str:
    """A brand-new, unmigrated database of its own. Dropped when the test finishes."""
    url = create_database(THROWAWAY_DB_NAME)

    yield url

    drop_database(THROWAWAY_DB_NAME)


@pytest.fixture()
def run_migrations():
    """`run_migrations(url)` upgrades to head; `run_migrations(url, "base")` downgrades.

    Handed over as a fixture rather than imported from this file: a test module that
    imports its own conftest depends on how pytest happens to have set up sys.path.
    """
    return migrate


@pytest.fixture()
def alembic_config_for():
    """`alembic_config_for(url)` - an Alembic config for tests that call Alembic directly."""
    return alembic_config


@pytest.fixture(scope="session")
def db_engine(database_url: str):
    engine = create_engine(database_url, future=True)

    yield engine

    engine.dispose()


@pytest.fixture()
def session(db_engine) -> Session:
    """
    A session per test, with every table emptied afterwards.

    Truncating rather than wrapping each test in a rolled-back transaction, because some
    of these tests are *about* commit and rollback behaviour, and a surrounding
    transaction would change what they observe.
    """
    factory = sessionmaker(bind=db_engine, expire_on_commit=False, future=True)
    session = factory()

    try:
        yield session
    finally:
        session.rollback()
        session.close()
        _truncate_everything(db_engine)


def _truncate_everything(engine) -> None:
    from feedbackiq.db.base import Base

    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        # CASCADE because the tables reference each other; alembic_version is not in
        # metadata, so the schema version survives.
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def organisation(session):
    from feedbackiq.db.persistence import create_organisation

    return create_organisation(session, name="Acme Ltd", slug="acme")


@pytest.fixture()
def other_organisation(session):
    """A second tenant, for proving one organisation cannot reach the other's rows."""
    from feedbackiq.db.persistence import create_organisation

    return create_organisation(session, name="Globex Inc", slug="globex")


@pytest.fixture()
def data_source(session, organisation):
    from feedbackiq.db.persistence import create_data_source

    return create_data_source(session, organisation_id=organisation.id, name="CSV upload")


@pytest.fixture()
def default_categories(session):
    """The 24 seeded global categories, for tests that resolve a category by name."""
    from feedbackiq.db.seed import _ensure_default_categories

    _ensure_default_categories(session)
    session.flush()


@pytest.fixture()
def api_client(database_url, session):
    """
    The real FastAPI app, talking to the test database, for tests that go through HTTP.

    Depends on `session`, so the tables are emptied afterwards like any other test - and this
    fixture's teardown runs first, disposing the app's connection pool before the TRUNCATE.
    Created without `with`, which skips the startup hook that loads the research corpus.
    """
    from fastapi.testclient import TestClient

    from feedbackiq.api.main import app
    from feedbackiq.core.config import settings
    from feedbackiq.db.session import get_engine, reset_engine

    previous = settings.DATABASE_URL
    settings.DATABASE_URL = database_url
    reset_engine()

    try:
        yield TestClient(app)
    finally:
        get_engine().dispose()
        reset_engine()
        settings.DATABASE_URL = previous
