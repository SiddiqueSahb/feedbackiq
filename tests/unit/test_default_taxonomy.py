"""
The packaged default taxonomy - feedbackiq/db/default_categories.json

This lives in the *unit* suite deliberately, so a bare `pytest` catches it: the failure it
guards against is the file being missing from an installed package, which needs no database
to detect.

The history behind it: the seed originally read the taxonomy from
`feedbackiq.nlp.categoriser.COMPLAINT_CATEGORIES`, which is loaded at import time from
`data/processed/complaint_categories_all_negative.json`. That path is gitignored research
output, and `.dockerignore` keeps `data/` out of the container image, so in a fresh clone -
including CI and any deployed container - the constant silently degrades to 7 static
fallback categories. CI caught the seed installing 7 categories instead of 24.
"""

import json
from importlib import resources

from feedbackiq.db.seed import TAXONOMY_FILE, default_categories

REQUIRED_FIELDS = {"category", "description", "exemplars"}


def test_the_taxonomy_file_is_packaged_with_the_code():
    """Not read through a path relative to the working directory: an installed package has
    no repository around it."""
    packaged = resources.files("feedbackiq.db").joinpath(TAXONOMY_FILE)

    assert packaged.is_file()
    json.loads(packaged.read_text(encoding="utf-8"))


def test_the_default_taxonomy_has_all_twenty_four_categories():
    entries = default_categories()

    assert len(entries) == 24
    assert len({entry["category"] for entry in entries}) == 24   # no duplicates


def test_every_category_carries_what_the_categoriser_needs():
    for entry in default_categories():
        assert REQUIRED_FIELDS <= set(entry)
        assert entry["category"].strip()
        # The description is the NLI hypothesis the zero-shot model compares against, so
        # it is functional rather than documentation - an empty one breaks categorisation.
        assert entry["description"].strip()
        assert isinstance(entry["exemplars"], list)
        assert all(isinstance(exemplar, str) for exemplar in entry["exemplars"])


def test_the_taxonomy_carries_no_research_only_fields():
    """Trimmed to what the product uses; `count`, `keywords`, `platforms`,
    `source_labels` and `source_topic_ids` stay in the dissertation's own output."""
    for entry in default_categories():
        assert set(entry) == REQUIRED_FIELDS
