"""
Put a usable minimum into an empty database.

    python -m feedbackiq.db.seed

Creates one development organisation with one data source, and the 24 default complaint
categories as the *global* taxonomy (`organisation_id IS NULL`, shared by every
organisation).

Two properties matter:

* **Deterministic.** Rows are identified by natural keys (the organisation's slug, a
  category's name), so running it twice changes nothing and adds nothing. Safe to run
  after every migration, and safe for a test to call.
* **Small.** It does NOT import the 642,692-review dissertation corpus. That corpus is
  research data measured in gigabytes; a development database needs none of it, and
  loading it would make every test run slow.

The taxonomy comes from `feedbackiq.core.taxonomy` - the same canonical, packaged file the
analytics engine categorises against, so the seeded rows and the categoriser can never
disagree. See docs/production/milestone-05a.md.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from feedbackiq.core.logging import get_logger
from feedbackiq.core.taxonomy import load_default_taxonomy, taxonomy_version
from feedbackiq.db.models import Category, DataSource, Organisation
from feedbackiq.db.session import session_scope

log = get_logger("db.seed")

DEV_ORG_NAME = "Development Organisation"
DEV_ORG_SLUG = "dev"
DEV_SOURCE_NAME = "CSV upload"

def default_categories() -> list[dict]:
    """
    The canonical default categories to seed.

    A thin delegate on purpose: the seed must install exactly the taxonomy the engine
    categorises against, so it reads the same canonical source rather than owning a copy.
    Raises `TaxonomyError` if that source is missing or invalid.
    """
    return load_default_taxonomy()


def seed(session: Session) -> dict[str, int]:
    """Seed the database and report what was created. Idempotent."""
    organisation, org_created = _ensure_organisation(session)
    _, source_created = _ensure_data_source(session, organisation)
    categories_created = _ensure_default_categories(session)

    return {
        "organisations_created": int(org_created),
        "data_sources_created": int(source_created),
        "categories_created": categories_created,
    }


def _ensure_organisation(session: Session) -> tuple[Organisation, bool]:
    existing = session.scalar(select(Organisation).where(Organisation.slug == DEV_ORG_SLUG))
    if existing is not None:
        return existing, False

    organisation = Organisation(name=DEV_ORG_NAME, slug=DEV_ORG_SLUG)
    session.add(organisation)
    # Flushed so the generated id is available to the rows that reference it.
    session.flush()

    return organisation, True


def _ensure_data_source(session: Session, organisation: Organisation) -> tuple[DataSource, bool]:
    existing = session.scalar(
        select(DataSource).where(
            DataSource.organisation_id == organisation.id,
            DataSource.name == DEV_SOURCE_NAME,
        )
    )
    if existing is not None:
        return existing, False

    source = DataSource(
        organisation_id=organisation.id,
        name=DEV_SOURCE_NAME,
        kind="csv_upload",
    )
    session.add(source)
    session.flush()

    return source, True


def _ensure_default_categories(session: Session) -> int:
    """
    Insert any of the canonical default categories that are not there yet.

    Matched on `key`, not on `name`: the key is the stable identity, so a category whose
    display name was reworded is recognised as the same category instead of being seeded a
    second time. The name is updated in place when it differs.
    """
    log.info("Seeding the canonical taxonomy, version %s", taxonomy_version())

    existing = {
        category.key: category
        for category in session.scalars(
            select(Category).where(Category.organisation_id.is_(None))
        ).all()
    }

    created = 0
    for entry in default_categories():
        # The taxonomy file calls it "category"; the column is "name".
        key = entry["key"]
        name = entry["category"]

        current = existing.get(key)
        if current is not None:
            if current.name != name:
                log.info("Category %s renamed: %r -> %r", key, current.name, name)
                current.name = name
            continue

        session.add(
            Category(
                organisation_id=None,        # global default, shared by every organisation
                key=key,
                name=name,
                description=entry["description"],
                exemplars=list(entry.get("exemplars", ())),
                kind="complaint",
                source="default",
            )
        )
        created += 1

    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="override settings.DATABASE_URL (the seed target)",
    )
    args = parser.parse_args(argv)

    with session_scope(args.database_url) as session:
        counts = seed(session)

    for key, value in counts.items():
        print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
