"""
The seed - feedbackiq.db.seed

Two properties are worth tests: it produces a usable development database, and running it
again changes nothing. The second is what makes it safe to run after every migration.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from feedbackiq.db.models import Category, DataSource, Organisation
from feedbackiq.db.seed import DEV_ORG_SLUG, default_categories, seed

pytestmark = pytest.mark.integration


def test_seeding_an_empty_database_creates_a_development_organisation(session):
    counts = seed(session)
    session.commit()

    assert counts["organisations_created"] == 1
    assert counts["data_sources_created"] == 1

    organisation = session.scalar(select(Organisation).where(Organisation.slug == DEV_ORG_SLUG))
    assert organisation is not None
    source = session.scalar(
        select(DataSource).where(DataSource.organisation_id == organisation.id)
    )
    assert source.kind == "csv_upload"


def test_seeding_installs_the_twenty_four_default_categories(session):
    counts = seed(session)
    session.commit()

    assert counts["categories_created"] == 24

    categories = session.scalars(
        select(Category).where(Category.organisation_id.is_(None))
    ).all()
    assert len(categories) == 24
    # Global, so every organisation shares them, and every one has the NLI hypothesis the
    # categoriser needs.
    assert all(category.organisation_id is None for category in categories)
    assert all(category.description for category in categories)
    assert all(category.source == "default" for category in categories)


def test_the_seeded_categories_are_the_packaged_taxonomy(session):
    """Compared against the packaged file, not `nlp.categoriser.COMPLAINT_CATEGORIES`:
    that constant is loaded from gitignored research data and degrades to 7 static
    categories wherever `data/` is absent, which is what broke this suite in CI."""
    seed(session)
    session.commit()

    names = set(session.scalars(select(Category.name)).all())
    assert names == {entry["category"] for entry in default_categories()}


def test_seeding_twice_changes_nothing(session):
    seed(session)
    session.commit()

    counts = seed(session)
    session.commit()

    assert counts == {
        "organisations_created": 0,
        "data_sources_created": 0,
        "categories_created": 0,
    }
    assert session.scalar(select(func.count()).select_from(Organisation)) == 1
    assert session.scalar(select(func.count()).select_from(Category)) == 24


def test_every_seeded_category_has_a_stable_key(session):
    seed(session)
    session.commit()

    categories = session.scalars(select(Category)).all()

    assert all(category.key for category in categories)
    assert {c.key for c in categories} == {e["key"] for e in default_categories()}


def test_re_seeding_after_a_rename_updates_the_label_and_keeps_the_identity(session):
    """
    What stable keys are for. A category renamed in a future taxonomy version is recognised
    as the same category: its label is updated in place, and no second row appears - so every
    analysis result already pointing at it stays correct.
    """
    seed(session)
    session.commit()

    category = session.scalar(
        select(Category).where(Category.key == "service_and_wait_time_delays")
    )
    original_id, original_name = category.id, category.name
    category.name = "Slow Service"          # pretend an earlier taxonomy called it this
    session.commit()

    counts = seed(session)
    session.commit()

    session.expire_all()
    restored = session.scalar(
        select(Category).where(Category.key == "service_and_wait_time_delays")
    )
    assert counts["categories_created"] == 0        # recognised, not duplicated
    assert restored.id == original_id               # same row, so results still resolve
    assert restored.name == original_name           # label brought back into line
    assert session.scalar(select(func.count()).select_from(Category)) == 24


def test_the_seed_does_not_import_the_dissertation_corpus(session):
    """642,692 reviews of research data have no place in a development database - and a
    test suite that loaded them would be unusable."""
    from feedbackiq.db.models import Feedback

    seed(session)
    session.commit()

    assert session.scalar(select(func.count()).select_from(Feedback)) == 0
