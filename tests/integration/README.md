# Database integration tests

These tests run against a **real PostgreSQL**. Nothing is mocked, because what they check
is what only a real database can tell you: that the migration builds the schema, that the
foreign keys and partial indexes behave as designed, and that engine output can be stored
and read back.

## Running them

```bash
docker compose up -d postgres        # PostgreSQL 16 on localhost:55432
pytest tests/integration
```

They are **not** selected by a bare `pytest` (see the comment in `pyproject.toml`): the
unit and API suites must stay runnable by anyone who has only cloned the repository.

To point them at a different server:

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:password@host:5432/postgres pytest tests/integration
```

Each run creates its own `feedbackiq_test` database, migrates it with Alembic, and drops
it at the end. Every table is truncated between tests. Your development database is not
touched.

If PostgreSQL is unreachable, these tests **fail** rather than skip — a suite that skips
itself reports green while testing nothing.

## What is where

| File | What it protects |
|---|---|
| `test_migrations.py` | empty database → `alembic upgrade head` → full schema; downgrade and re-upgrade; the migration still matches `db/models.py` |
| `test_tenant_data.py` | every row belongs to an organisation; the database refuses a result that points at another organisation's feedback; duplicate and taxonomy constraints |
| `test_persistence.py` | `BatchAnalysis` → rows: run versions, result columns, insights, category resolution, re-analysis history, rollback |
| `test_seed.py` | one development organisation, the 24 default categories, idempotent, no dissertation corpus |
| `test_engine_db_boundary.py` | the analytics engine never imports a database library and runs with no database at all |
