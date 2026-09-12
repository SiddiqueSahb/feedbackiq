# Milestone 5A — SaaS Product Foundation: Taxonomy Correctness + Architecture Direction

**Status:** complete, pending review
**Preceded by:** [Milestone 4 — PostgreSQL / Database Layer](milestone-04.md)
**Companion document:** [SaaS Architecture Direction](saas-architecture-direction.md)

---

## 1. Objective

Two things, one of them urgent:

1. **Correctness.** Make the product categorise against the canonical **24-category
   taxonomy** in every normal environment, with **no silent fallback** to the 7-category
   research set, one canonical source shared by the engine and the database seed, and a
   clear failure when that source is missing.
2. **Direction.** Record how FeedbackIQ becomes a multi-tenant SaaS: what the dissertation
   contributes, what must not dictate the architecture, and where the boundaries go.

From this milestone onward, **SaaS product requirements take priority over dissertation
compatibility**. The dissertation is source material and validated research, not the
architectural specification.

---

## 2. Starting state (end of Milestone 4)

| | |
|---|---|
| Tests | 248 passed, 2 xfailed; 42 database integration tests |
| Schema | 9 tables, Alembic `0001`, seed, persistence functions |
| Known defect #7 | `nlp/categoriser.py` silently degraded to 7 categories wherever `data/` was absent — **recorded and deliberately left unfixed**, because it changes what the product predicts |
| Taxonomy | the seed read a packaged copy at `src/feedbackiq/db/default_categories.json`; **the engine did not** |

Milestone 4 fixed the seed by giving it a packaged file. It did not cure the disease: the
engine — the path the API actually uses — still read the taxonomy from gitignored research
data.

---

## 3. Root cause

```text
BEFORE
                       data/processed/complaint_categories_all_negative.json
                         (gitignored, .dockerignore'd, absent in CI and the image)
                                          |
                                   present?  -- no -->  _STATIC_FALLBACK (7 categories)
                                          |                    log.warning(...)
                                          v
nlp/categoriser.py  COMPLAINT_CATEGORIES = _load_categories()   <-- at IMPORT time
                                          |
                    engine/defaults.py  default_categories()    <-- THE PRODUCTION PATH
                                          |
                    services/sentiment_service.get_engine()  ->  /api/sentiment/analyse

                    db/seed.py  ------------------------------>  its own packaged copy (24)
```

Three separate faults, and the combination is what made it invisible:

1. **A silent fallback.** A missing file produced a *different, smaller* taxonomy plus a
   log line. Nothing failed, so nothing was noticed.
2. **A developer-machine dependency.** The file exists on the machine that ran the
   dissertation's discovery script and nowhere else. Every local test therefore passed.
3. **Two sources.** After Milestone 4 the seed read a packaged copy while the engine read
   the research copy, so "the categories in the database" and "the categories the engine
   assigns" were free to diverge.

The consequence was not a crash but **wrong output that looked right**: in CI, in the
container image and in any fresh clone, the API categorised feedback into 7 generic buckets
while the database held the 24 real ones and every document claimed 24.

---

## 4. Changes made

### One canonical source

```text
AFTER
      src/feedbackiq/core/default_categories.json     <- canonical, versioned, packaged
                              |
              core/taxonomy.py  (stdlib only, no core.config import)
                              |
        +---------------------+---------------------+
        |                     |                     |
engine/defaults.py        db/seed.py        nlp/categoriser.py
 (engine default)        (seeded rows)      (legacy categorise())
```

| File | Change |
|---|---|
| **`core/taxonomy.py`** (new) | The single loader. `load_default_taxonomy()`, `taxonomy_document()`, `taxonomy_version()`, `taxonomy_provenance()`. Validates structure and raises `TaxonomyError` with an actionable message. Standard library only — no `core.config`, no pydantic — so it can be verified in a bare container. |
| **`core/default_categories.json`** (moved) | From `db/`, with provenance metadata added (see §5). 24 categories, three fields each. |
| **`core/exceptions.py`** | New `TaxonomyError(FeedBackError)`. |
| **`engine/defaults.py`** | `default_categories()` now reads `core.taxonomy` instead of `nlp.categoriser.COMPLAINT_CATEGORIES`. **This is the fix that matters** — it is the production path. |
| **`nlp/categoriser.py`** | `_STATIC_FALLBACK` (7 categories) **deleted**. `_load_categories()` replaced: the module default is the canonical taxonomy; a discovered research taxonomy loads explicitly through `load_research_taxonomy(sentiment)`, which raises when absent. `reload_categories()` is now documented as the research escape hatch. |
| **`db/seed.py`** | `default_categories()` became a thin delegate to the canonical loader — it can no longer hold its own copy. Logs the taxonomy version it seeds. |
| **`engine/versions.py`** | Manifest gained `taxonomy_id` and `taxonomy_version`. |
| **`engine/pipeline.py`** | Every `BatchAnalysis` records `taxonomy_source`: `default` or `caller`. |
| **`db/persistence.py`** | `analysis_runs.model_versions` now stores `taxonomy_id`, `taxonomy_version` and `taxonomy_source`. |
| **`pyproject.toml`** | `[tool.setuptools.package-data]` follows the file to `feedbackiq.core`. |
| **`claude.md`** | Product-priority section; the taxonomy rule; test floor 267. |

### Why the file moved, against the brief's literal instruction

The brief named `src/feedbackiq/db/default_categories.json` as canonical. It is now
`src/feedbackiq/core/default_categories.json`, because the engine has to read it and
`claude.md` forbids the engine from importing `feedbackiq.db` (enforced by a test). Keeping
the file in `db/` allowed only two outcomes: break the engine/database boundary, or keep two
copies — which is the defect. Moving one file to a package both sides may depend on removes
the conflict. `git mv` preserved its history, and Milestone 4's document carries a forward
pointer.

### No silent fallback, anywhere

- Missing, unreadable, malformed, empty, internally inconsistent, or duplicate-named
  taxonomy → `TaxonomyError`, naming the file and how to restore it.
- Applies equally to the research loader: a missing discovered taxonomy raises instead of
  substituting.
- `TaxonomyError` derives from `FeedBackError`, so a future API layer can map one family to
  one response.

### Custom taxonomies preserved

Dependency injection is untouched: `analyse_batch(categories=...)` still overrides the
default, `ZeroShotCategoriser` still takes categories as an argument, and the canonical set
is only a *default*. Tests prove a caller-supplied taxonomy is the one used and that the
result records `taxonomy_source="caller"`.

---

## 5. Taxonomy versioning (Part 5)

The file is now self-describing:

```json
{
  "taxonomy_id": "complaint-24",
  "taxonomy_version": "1.0.0",
  "category_count": 24,
  "generated_on": "2026-08-08",
  "generated_by": "scripts/discover_categories.py --sentiment negative (BERTopic per platform, then cross-platform merge)",
  "source_file": "data/processed/complaint_categories_all_negative.json",
  "categories": [ ... ]
}
```

`category_count` is validated against the list, so a hand-edit that drops a category fails
instead of quietly shrinking the taxonomy.

**How a result answers "which taxonomy produced this?"**

```text
core/default_categories.json  ->  manifest()  ->  BatchAnalysis.versions  ->  analysis_runs.model_versions
   taxonomy_id, taxonomy_version                  + taxonomy_source           (JSONB, queryable)
```

No configuration-management system, no registry: two strings in a JSON file, carried by the
manifest that already existed.

---

## 6. Tests added

`tests/unit/test_default_taxonomy.py` grew from 4 tests to **23** (+19 overall).

| Requirement from the brief | Test |
|---|---|
| default taxonomy → exactly 24 | `test_the_default_taxonomy_has_exactly_twenty_four_categories` |
| explicit custom taxonomy → preserved | `test_a_caller_supplied_taxonomy_is_used_instead_of_the_default` |
| missing taxonomy → clear failure | `test_a_missing_taxonomy_raises_instead_of_substituting_one` (asserts the message is actionable) |
| **no fallback** | `test_a_missing_taxonomy_does_not_fall_back_to_the_seven_research_categories` — raises *and* asserts `_STATIC_FALLBACK` and the retired category names are absent from the installed source; `test_the_shipped_taxonomy_is_not_the_retired_fallback` |
| seed consistency | `test_the_engine_default_and_the_database_seed_use_the_same_taxonomy` (name-by-name, in order), `test_the_legacy_categoriser_uses_the_canonical_taxonomy_too` |
| independence from research data | `test_the_default_taxonomy_does_not_depend_on_the_research_data_directory` (points `TAXONOMY_DIR` at nothing, reimports, still 24) |
| versioning | `test_the_taxonomy_is_versioned_and_records_its_provenance`, `test_the_engine_manifest_reports_the_taxonomy_version`, `test_a_result_records_when_the_default_taxonomy_was_used` |
| invalid content | 6 parametrised cases (empty, no name, no description, duplicates, count mismatch, unversioned-but-usable) plus malformed JSON |
| research loader | `test_a_missing_research_taxonomy_raises_rather_than_degrading` |

These live in the **unit** suite on purpose: the failure they guard against needs no
database and no models, so a bare `pytest` catches it.

One test failed on the first run — `test_a_missing_taxonomy_raises_instead_of_substituting_one`,
because the explicit-path error lacked the "how to fix it" hint. **The message was fixed, not
the assertion.**

---

## 7. Docker verification (Part 1.4, Part 4)

Two independent checks, both run, both passed.

**A. Clean container from the built wheel** — `python:3.12-slim`, `pip install --no-deps`,
no repository, no `data/`, nothing else installed:

```text
python: 3.12.14
no repository present: True
third-party loaded: none
canonical categories: 24
version: 1.0.0 {'id': 'complaint-24', 'version': '1.0.0', 'categories': 24, 'generated_on': '2026-08-08'}
no 7-category fallback in the installed package: True
data/processed present in container: False
missing taxonomy raises TaxonomyError: [TaxonomyError] The canonical taxonomy ...
CLEAN CONTAINER CHECK: PASSED
```

The wheel contains exactly one JSON: `feedbackiq/core/default_categories.json`. The check
also **deletes** that file inside the container and confirms the loader raises rather than
substituting.

**B. The real backend image**, `feedbackiq-backend:m5a`, built from `backend/Dockerfile`
with **no volumes mounted**:

```text
data/processed exists: False
models/ exists: False
canonical: 24 seed: 24 engine: 24 legacy: 24
identical: True
taxonomy_version: 1.0.0
manifest: {'taxonomy_id': 'complaint-24', 'taxonomy_version': '1.0.0'}
routes: 22
IMAGE CHECK: PASSED
```

Before this milestone, the same check inside the image would have reported **7** for the
engine and the categoriser.

Note: the local image export bug recorded in Milestone 1 (`failed to Lchown … lto-dump`) did
**not** reproduce — the image built and loaded normally (exit 0). It appears environment-flaky
rather than permanent; `--output type=cacheonly` remains the documented fallback.

---

## 8. Fresh-clone safety (Part 1.5)

The product no longer reads anything from `data/` to categorise or to seed:

- the taxonomy is package data, installed with the wheel and present in the image;
- `core/taxonomy.py` resolves it via `importlib.resources`, never a path relative to the
  working directory and never an absolute developer path;
- `core/paths.py` continues to anchor *research* paths to the project root;
- a unit test proves the default is unaffected when `TAXONOMY_DIR` points nowhere.

Research reproduction is unchanged: `scripts/discover_categories.py` still writes to
`data/processed/` and still reloads its output through `reload_categories()`.

---

## 9. SaaS architecture direction & product/research boundary

Full detail in [saas-architecture-direction.md](saas-architecture-direction.md). Summary:

**Retained as product assets:** the fine-tuned sentiment model, zero-shot categorisation and
its "Unclassified" outcome, the canonical taxonomy, the sentiment gate, grounded Q&A rules
(threshold, MMR, evidence IDs, refusal), insights with evidence IDs, the LLM adapter, the
typed engine contracts and version manifest, the evaluation methodology and benchmark floor,
and the PostgreSQL schema.

**Classified as legacy/research** (kept, not deleted, and not allowed to shape the
architecture): notebooks, `scripts/`, `evaluate/`, the 642,692-review corpus, the ~2 GB FAISS
artefacts, the global-corpus retriever, classical/RoBERTa baselines, the Streamlit app, the
`platform` concept, rating-derived labels, and the root-level manual check scripts.

**Intentionally replaced later:** Streamlit → React (M11); global FAISS → tenant-scoped
retrieval (M12); synchronous analysis → worker (M5B); local datasets → customer uploads (M5);
single API key → authentication (M7–M9); pandas analytics → SQL aggregates (M6).

**Product/research separation: no code was moved.** The repository's research homes are
already `evaluate/`, `scripts/` and `notebooks/`, and separation is enforced where it counts
(`.dockerignore`, dependency extras, `testpaths`, and now the taxonomy). Creating a
`research/` package and relocating those directories would break the dissertation's entry
points and the tests that import `scripts/discover_categories.py`, for a cosmetic gain. The
rule that matters is written down instead: **product code must never import from `evaluate/`
or `scripts/`, or depend on a file under `data/`.**

---

## 10. Deliberately NOT implemented

Authentication, JWT/sessions, RBAC, organisation middleware, tenant enforcement, customer CSV
ingestion, background workers, Redis, React/Next.js, billing, Stripe, cloud deployment,
Kubernetes, microservices, event buses, pgvector, observability platforms. No API route reads
or writes the database yet, and the 22 routes are unchanged.

The analytics engine was not redesigned: it still runs without FastAPI, PostgreSQL,
authentication or HTTP, and the boundary tests still enforce that.

---

## 11. Verification summary

| Check | Result |
|---|---|
| `pytest` | **267 passed, 2 xfailed** (was 248/2; +19 taxonomy tests) |
| `pytest tests/integration` | **42 passed** against PostgreSQL 16.15 (includes `alembic check`) |
| `flake8 … --select=E9,F` | clean |
| Wheel packaging | `feedbackiq/core/default_categories.json` present |
| Clean container (`--no-deps`) | 24 categories, no third-party imports, missing file raises |
| Real image, no volumes | engine = seed = categoriser = 24; manifest version 1.0.0; 22 routes |
| API surface | 22 routes, unchanged |
| Engine/database boundary | intact (3 boundary tests pass) |
| Seed | fresh database → 24 (integration suite); already-seeded dev database → `0/0/0`, idempotent |
| Dissertation artefacts | nothing under `data/`, `models/`, `notebooks/`, `evaluate/`, `scripts/` modified |

---

## 12. Known limitations

| # | Limitation |
|---|---|
| 1 | **A caller-supplied taxonomy has no version.** Results record `taxonomy_source="caller"` but cannot say *which* custom taxonomy. Fix when organisation taxonomies are stored in the database: give `categories` rows a taxonomy version and record that id on the run. |
| 2 | **Seeded `categories` rows do not carry the taxonomy version.** The run records it; the rows do not. A schema column is the obvious fix, deferred until organisation-specific taxonomies exist. |
| 3 | **Category identity is the name.** `db/persistence.py` resolves a category by name, so renaming one in a future taxonomy version would orphan earlier results rather than migrate them. Needs a stable per-category key before taxonomy `1.1.0`. |
| 4 | **The 24 names are BERTopic artefacts.** Several are unfit as a customer-facing default — e.g. *"Spray Bottle, Continuous Spray, Dryer Diffuser, Fit Dryer"*, *"Tablet Accessory Quality Failures"*, *"Mirror Performance Failures"* — because they were discovered from a 70%-Yelp research corpus, not designed as a product taxonomy. Correctness is fixed; **suitability is not.** Recommended as a small, explicit piece of product work with a benchmark run, not a silent rewrite. |
| 5 | **Research taxonomies for other sentiment classes still need `data/`.** By design — they are research output — but it means `reload_categories("positive")` only works on a machine that has run discovery. |
| 6 | **`nlp/categoriser.py` remains a second categorisation implementation** alongside `engine/categorisation.py`. They now share the taxonomy, not the code. Consolidation belongs with the API/service milestone. |
| 7 | **No code was moved for product/research separation** (§9) — classification only. |

---

## 13. Recommended next milestone (5B)

**Feedback ingestion and background analysis** — roadmap
[M5](07-production-roadmap.md#m5--feedback-ingestion--background-analysis). It is the
milestone that makes everything built in M4 and 5A actually do something: CSV → validated
rows → `feedback` → a `jobs` row → worker → `analysis_results`, all scoped to an
organisation. It needs no authentication (the existing API key still guards the routes), and
it is the first point at which FeedbackIQ analyses a *customer's own* data.

Two small items worth folding in, or doing first:

1. **Review the default taxonomy's category names** (limitation #4) — a product decision with
   a measured before/after, now cheap because the taxonomy is versioned.
2. **A stable per-category key** (limitation #3) before any taxonomy revision ships.

**Awaiting approval before starting.**
