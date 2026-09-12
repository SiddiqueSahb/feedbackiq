"""
The canonical complaint taxonomy: one source, read by everything.

    core/default_categories.json   <- the canonical 24 categories, shipped in the package
              |
              +-- engine/defaults.py   the analytics engine's default taxonomy
              +-- db/seed.py           the organisation-independent rows in `categories`
              +-- nlp/categoriser.py   the legacy categorise() entry point

**Why this module exists.** The taxonomy used to be loaded from
`data/processed/complaint_categories_all_negative.json`, which is gitignored research
output and is excluded from the container image. When absent - a fresh clone, CI, any
deployed container - the loader silently substituted 7 static categories, so the product
categorised against the wrong taxonomy while every test and document said 24. Shipping the
file with the package and reading it from one place removes the whole failure mode.

**Deliberately dependency-free.** Only the standard library, and no import of
`core.config`, so the taxonomy can be verified in a bare container with
`pip install --no-deps` and nothing else installed.

**No fallback.** If the canonical file is missing or invalid this raises `TaxonomyError`.
A wrong taxonomy that looks right is worse than a process that refuses to start.

Custom taxonomies are unaffected: they are passed *into* the engine
(`analyse_batch(categories=...)`), and this module is only the default.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from feedbackiq.core.exceptions import TaxonomyError

# The package the canonical file is installed into, and its name there.
TAXONOMY_PACKAGE = "feedbackiq.core"
TAXONOMY_FILENAME = "default_categories.json"

# What every category must carry. `description` is the NLI hypothesis the zero-shot model
# compares against, so an empty one silently breaks categorisation - hence required.
REQUIRED_FIELDS = ("category", "description")

# How to regenerate the file, quoted in error messages so a failure is actionable.
REGENERATE_HINT = (
    "Regenerate it from the research taxonomy with "
    "scripts/discover_categories.py --sentiment negative, or restore "
    f"src/{TAXONOMY_PACKAGE.replace('.', '/')}/{TAXONOMY_FILENAME} from git."
)


def taxonomy_document(path: Path | None = None) -> dict:
    """
    The whole taxonomy file: provenance metadata plus the categories.

    `path` is for tests and for checking a file before shipping it; production always uses
    the packaged copy.
    """
    text = _read_text(path)

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaxonomyError(
            f"The canonical taxonomy is not valid JSON ({exc}). {REGENERATE_HINT}"
        ) from exc

    if not isinstance(document, dict):
        raise TaxonomyError(
            "The canonical taxonomy must be a JSON object with a 'categories' list, "
            f"found {type(document).__name__}. {REGENERATE_HINT}"
        )

    _validate(document)

    return document


def load_default_taxonomy(path: Path | None = None) -> list[dict]:
    """
    The canonical categories, as the dicts the rest of the code already expects
    (`{"category": ..., "description": ..., "exemplars": [...]}`).

    Raises `TaxonomyError` if the file is missing, unreadable, malformed, empty, or
    internally inconsistent. It never returns a partial or substitute taxonomy.
    """
    return list(taxonomy_document(path)["categories"])


def taxonomy_version(path: Path | None = None) -> str:
    """The version recorded in the file, e.g. "1.0.0"."""
    return str(taxonomy_document(path)["taxonomy_version"])


def taxonomy_provenance(path: Path | None = None) -> dict[str, object]:
    """
    Identity and origin of the taxonomy, for the engine's version manifest.

    This is what lets a stored analysis result answer "which taxonomy produced this?".
    """
    document = taxonomy_document(path)

    return {
        "id": str(document["taxonomy_id"]),
        "version": str(document["taxonomy_version"]),
        "categories": len(document["categories"]),
        "generated_on": str(document.get("generated_on", "")),
    }


# ---------------------------------------------------------------- internals


def _read_text(path: Path | None) -> str:
    """Read the packaged file, or an explicit path when one is given."""
    if path is not None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            # Every missing-taxonomy error says what to do about it. A bare "file not
            # found" is what made the old silent fallback tempting in the first place.
            raise TaxonomyError(
                f"Cannot read the taxonomy at '{path}': {exc}. {REGENERATE_HINT}"
            ) from exc

    resource = resources.files(TAXONOMY_PACKAGE).joinpath(TAXONOMY_FILENAME)

    if not resource.is_file():
        raise TaxonomyError(
            f"The canonical taxonomy '{TAXONOMY_FILENAME}' is not installed in "
            f"{TAXONOMY_PACKAGE}. It ships with the package: check that "
            "[tool.setuptools.package-data] still includes it and reinstall "
            f"(pip install -e .). {REGENERATE_HINT}"
        )

    try:
        return resource.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaxonomyError(f"Cannot read the packaged taxonomy: {exc}") from exc


def _validate(document: dict) -> None:
    """Reject a taxonomy that would degrade categorisation quietly."""
    for key in ("taxonomy_id", "taxonomy_version", "categories"):
        if key not in document:
            raise TaxonomyError(
                f"The canonical taxonomy is missing '{key}'. {REGENERATE_HINT}"
            )

    categories = document["categories"]
    if not isinstance(categories, list) or not categories:
        raise TaxonomyError(
            f"The canonical taxonomy contains no categories. {REGENERATE_HINT}"
        )

    names: set[str] = set()
    for position, entry in enumerate(categories, start=1):
        if not isinstance(entry, dict):
            raise TaxonomyError(
                f"Category {position} is {type(entry).__name__}, expected an object. "
                f"{REGENERATE_HINT}"
            )

        for field in REQUIRED_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise TaxonomyError(
                    f"Category {position} has no '{field}'. Every category needs a name "
                    f"and a description (the description is what the zero-shot model "
                    f"compares against). {REGENERATE_HINT}"
                )

        name = entry["category"]
        if name in names:
            raise TaxonomyError(
                f"Category '{name}' appears twice. Names identify categories in stored "
                f"results, so they must be unique. {REGENERATE_HINT}"
            )
        names.add(name)

    # A stated count that disagrees with the list means the file was edited by hand and
    # something was lost - exactly the kind of quiet mistake this module exists to catch.
    declared = document.get("category_count")
    if declared is not None and declared != len(categories):
        raise TaxonomyError(
            f"The canonical taxonomy declares {declared} categories but contains "
            f"{len(categories)}. {REGENERATE_HINT}"
        )
