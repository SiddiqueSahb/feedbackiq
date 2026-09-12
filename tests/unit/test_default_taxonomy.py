"""
The canonical taxonomy - feedbackiq.core.taxonomy

These live in the *unit* suite deliberately: the failures they guard against need no
database and no models, so a bare `pytest` catches them.

The defect they exist for: the taxonomy used to be loaded at import time from
`data/processed/complaint_categories_all_negative.json`, which is gitignored research
output that `.dockerignore` also keeps out of the image. When absent - a fresh clone, CI,
any deployed container - the loader quietly substituted **7 static categories**, so the
product categorised against the wrong taxonomy while every test and document said 24. CI
caught it seeding 7; Milestone 5A removed the fallback and gave the engine, the seed and
the legacy categoriser one canonical source.
"""

import importlib
import json
from importlib import resources

import pytest

from feedbackiq.core.exceptions import FeedBackError, TaxonomyError
from feedbackiq.core.taxonomy import (
    TAXONOMY_FILENAME,
    TAXONOMY_PACKAGE,
    load_default_taxonomy,
    taxonomy_document,
    taxonomy_provenance,
    taxonomy_version,
)

EXPECTED_CATEGORIES = 24

# The 7 static categories that used to be substituted silently. Named here so a test can
# prove they are gone rather than merely unused.
RETIRED_FALLBACK_NAMES = {
    "Product Quality Issues",
    "Delivery & Shipping Issues",
    "Customer Service Issues",
    "Pricing & Value Issues",
    "User Experience Issues",
    "Technical Issues",
    "Refund & Returns Issues",
}


def write_taxonomy(path, **overrides):
    """A valid taxonomy document on disk, with fields overridden for the failure cases."""
    document = {
        "taxonomy_id": "test-taxonomy",
        "taxonomy_version": "9.9.9",
        "category_count": 1,
        "categories": [
            {"category": "Billing", "description": "Charged incorrectly.", "exemplars": []}
        ],
    }
    document.update(overrides)
    path.write_text(json.dumps(document), encoding="utf-8")

    return path


# ---------------------------------------------------------------- the default taxonomy


def test_the_taxonomy_file_is_packaged_with_the_code():
    """Read as package data, not through a path relative to the working directory: an
    installed package has no repository around it."""
    packaged = resources.files(TAXONOMY_PACKAGE).joinpath(TAXONOMY_FILENAME)

    assert packaged.is_file()
    json.loads(packaged.read_text(encoding="utf-8"))


def test_the_default_taxonomy_has_exactly_twenty_four_categories():
    categories = load_default_taxonomy()

    assert len(categories) == EXPECTED_CATEGORIES
    assert len({entry["category"] for entry in categories}) == EXPECTED_CATEGORIES


def test_every_category_carries_what_the_categoriser_needs():
    for entry in load_default_taxonomy():
        assert entry["category"].strip()
        # The description is the NLI hypothesis the zero-shot model compares against, so
        # it is functional rather than documentation.
        assert entry["description"].strip()
        assert isinstance(entry["exemplars"], list)
        assert all(isinstance(exemplar, str) for exemplar in entry["exemplars"])


def test_the_taxonomy_carries_no_research_only_fields():
    """Trimmed to what the product uses; `count`, `keywords`, `platforms`,
    `source_labels` and `source_topic_ids` stay in the dissertation's own output."""
    for entry in load_default_taxonomy():
        assert set(entry) == {"category", "description", "exemplars"}


# ---------------------------------------------------------------- versioning


def test_the_taxonomy_is_versioned_and_records_its_provenance():
    """So a stored analysis result can answer "which taxonomy produced this?"."""
    provenance = taxonomy_provenance()

    assert provenance["id"]
    assert provenance["version"] == taxonomy_version()
    assert provenance["categories"] == EXPECTED_CATEGORIES
    assert taxonomy_document()["source_file"]      # where it came from


def test_the_engine_manifest_reports_the_taxonomy_version():
    from feedbackiq.engine.versions import manifest

    versions = manifest()

    assert versions["taxonomy_version"] == taxonomy_version()
    assert versions["taxonomy_id"] == taxonomy_provenance()["id"]


# ---------------------------------------------------------------- no silent fallback


def test_a_missing_taxonomy_raises_instead_of_substituting_one(tmp_path):
    with pytest.raises(TaxonomyError) as error:
        load_default_taxonomy(tmp_path / "not-here.json")

    # Actionable: it says what to do, not just that something went wrong.
    assert "discover_categories" in str(error.value) or "git" in str(error.value)


def test_a_missing_taxonomy_does_not_fall_back_to_the_seven_research_categories(tmp_path):
    """The whole point of this milestone, stated as a test: absence must be an error, not
    a different taxonomy."""
    with pytest.raises(TaxonomyError):
        load_default_taxonomy(tmp_path / "gone.json")

    # And the retired 7 are nowhere in the product package any more.
    source = resources.files("feedbackiq.nlp").joinpath("categoriser.py").read_text(
        encoding="utf-8"
    )
    assert "_STATIC_FALLBACK" not in source
    assert "Refund & Returns Issues" not in source


def test_the_shipped_taxonomy_is_not_the_retired_fallback():
    names = {entry["category"] for entry in load_default_taxonomy()}

    assert not (names & RETIRED_FALLBACK_NAMES)


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"categories": []}, "empty"),
        ({"categories": [{"category": "Billing"}]}, "no description"),
        ({"categories": [{"description": "No name."}]}, "no name"),
        (
            {
                "categories": [
                    {"category": "Billing", "description": "One."},
                    {"category": "Billing", "description": "Two."},
                ],
                "category_count": 2,
            },
            "duplicate names",
        ),
        ({"category_count": 99}, "count disagrees with the list"),
        ({"taxonomy_version": None, "taxonomy_id": None}, "usable but unversioned"),
    ],
)
def test_an_invalid_taxonomy_raises(tmp_path, overrides, reason):
    path = write_taxonomy(tmp_path / "taxonomy.json", **overrides)

    if reason == "usable but unversioned":
        # Present-but-null still parses; the loader must not crash on it, and the
        # categories are still usable. Only *structural* problems are fatal.
        assert load_default_taxonomy(path)
        return

    with pytest.raises(TaxonomyError):
        load_default_taxonomy(path)


def test_malformed_json_raises_a_taxonomy_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")

    with pytest.raises(TaxonomyError):
        load_default_taxonomy(path)


def test_a_taxonomy_error_is_a_feedbackiq_error():
    """So a caller can catch one family, and an API layer can map it to one response."""
    assert issubclass(TaxonomyError, FeedBackError)


# ---------------------------------------------------------------- one source, everywhere


def test_the_engine_default_and_the_database_seed_use_the_same_taxonomy():
    """The consistency this milestone is about: what the engine assigns and what the seed
    stores are the same categories, by construction rather than by coincidence."""
    from feedbackiq.db.seed import default_categories as seed_categories
    from feedbackiq.engine.defaults import default_categories as engine_categories

    seeded = [entry["category"] for entry in seed_categories()]
    engine = [category.name for category in engine_categories()]

    assert seeded == engine
    assert len(seeded) == EXPECTED_CATEGORIES


def test_the_legacy_categoriser_uses_the_canonical_taxonomy_too():
    import feedbackiq.nlp.categoriser as categoriser

    names = [entry["category"] for entry in categoriser.COMPLAINT_CATEGORIES]

    assert names == [entry["category"] for entry in load_default_taxonomy()]
    assert len(names) == EXPECTED_CATEGORIES


def test_the_default_taxonomy_does_not_depend_on_the_research_data_directory(monkeypatch):
    """Point the research directory at nothing and reimport: the default taxonomy is
    unaffected, because it no longer comes from there."""
    import feedbackiq.nlp.categoriser as categoriser
    from feedbackiq.core.config import settings

    monkeypatch.setattr(settings, "TAXONOMY_DIR", "/nonexistent/research/output")
    reloaded = importlib.reload(categoriser)

    try:
        assert len(reloaded.COMPLAINT_CATEGORIES) == EXPECTED_CATEGORIES
    finally:
        # Leave the module as the rest of the suite expects it.
        monkeypatch.undo()
        importlib.reload(categoriser)


def test_a_missing_research_taxonomy_raises_rather_than_degrading(monkeypatch):
    import feedbackiq.nlp.categoriser as categoriser

    monkeypatch.setattr(categoriser, "_PROCESSED_DIR", "/nonexistent/research/output")

    with pytest.raises(TaxonomyError) as error:
        categoriser.load_research_taxonomy("positive")

    assert "discover_categories.py" in str(error.value)


# ---------------------------------------------------------------- custom taxonomies


def test_a_caller_supplied_taxonomy_is_used_instead_of_the_default():
    """Injection must survive: a customer's own taxonomy is the product feature that the
    canonical default exists to back up, not replace."""
    from feedbackiq.engine.pipeline import AnalyticsEngine
    from feedbackiq.engine.categorisation import CategorisationOutcome
    from feedbackiq.engine.types import (
        Category,
        CategoryMatch,
        FeedbackItem,
        SentimentPrediction,
    )

    class FakeSentiment:
        def predict_batch(self, texts):
            return [
                SentimentPrediction(
                    label="negative", confidence=0.9, scores={"negative": 0.9},
                    model_version="fake",
                )
                for _ in texts
            ]

    class RecordingCategoriser:
        def __init__(self):
            self.taxonomies = []

        def categorise_batch(self, texts, categories):
            self.taxonomies.append([category.name for category in categories])
            match = CategoryMatch(category_id="c", name=categories[0].name, score=0.8)
            return [CategorisationOutcome(top=match, candidates=(match,)) for _ in texts]

    categoriser = RecordingCategoriser()
    engine = AnalyticsEngine(sentiment=FakeSentiment(), categoriser=categoriser)
    custom = [Category(id="own", name="Onboarding friction", description="Setup is hard.")]

    batch = engine.analyse_batch(
        [FeedbackItem(id="1", text="setting this up was painful")], categories=custom
    )

    assert categoriser.taxonomies == [["Onboarding friction"]]
    assert batch.results[0].category.name == "Onboarding friction"
    # And the result says the categories came from the caller, not the packaged default.
    assert batch.versions["taxonomy_source"] == "caller"


def test_a_result_records_when_the_default_taxonomy_was_used():
    from feedbackiq.engine.pipeline import AnalyticsEngine
    from feedbackiq.engine.types import FeedbackItem, SentimentPrediction

    class FakeSentiment:
        def predict_batch(self, texts):
            return [
                SentimentPrediction(
                    label="positive", confidence=0.9, scores={"positive": 0.9},
                    model_version="fake",
                )
                for _ in texts
            ]

    engine = AnalyticsEngine(sentiment=FakeSentiment(), categoriser=None)

    batch = engine.analyse_batch([FeedbackItem(id="1", text="love it")])

    assert batch.versions["taxonomy_source"] == "default"
    assert batch.versions["taxonomy_version"] == taxonomy_version()
