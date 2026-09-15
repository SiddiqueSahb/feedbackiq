"""
The product taxonomy and migration 0003 must not drift apart.

`migrations/versions/0003_product_taxonomy.py` lists the 13 categories in full rather than
importing `core/taxonomy.py`. That duplication is deliberate — a migration has to keep doing
the same thing years later, whatever the application code has become — but duplication
without a check is how a database and a package quietly disagree.

This test is the check. It lives in the unit suite because it needs no database: it compares
two files.
"""

import importlib.util
import json
from pathlib import Path

from feedbackiq.core.taxonomy import load_default_taxonomy

MIGRATION = Path("migrations/versions/0003_product_taxonomy.py")


def migration_module():
    """Load the migration as a module without Alembic, to read its constants."""
    spec = importlib.util.spec_from_file_location("migration_0003", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_the_migration_installs_exactly_the_packaged_taxonomy():
    migration = migration_module()

    from_migration = [
        {"key": key, "category": name, "description": description, "exemplars": list(exemplars)}
        for key, name, description, exemplars in migration.PRODUCT_CATEGORIES
    ]

    assert from_migration == load_default_taxonomy()


def test_the_migration_retires_every_research_key():
    """All 24 research keys must be named, or a stray one stays active and gets offered to
    customers alongside the product taxonomy."""
    from feedbackiq.core.taxonomy import load_dissertation_taxonomy

    migration = migration_module()

    assert set(migration.RESEARCH_KEYS) == {
        entry["key"] for entry in load_dissertation_taxonomy()
    }


def test_the_migration_and_the_taxonomy_agree_on_the_count():
    migration = migration_module()

    assert len(migration.PRODUCT_CATEGORIES) == 13
    assert len(migration.RESEARCH_KEYS) == 24


def test_the_packaged_taxonomy_is_valid_json_on_disk():
    """Read as a file, not through the loader, so a malformed edit is caught even if the
    loader's validation were to change."""
    document = json.loads(
        Path("src/feedbackiq/core/product_categories.json").read_text(encoding="utf-8")
    )

    assert document["taxonomy_id"] == "product-13"
    assert document["taxonomy_version"] == "2.0.0"
    assert document["category_count"] == len(document["categories"]) == 13
