# Milestone 5B — Customer Feedback Ingestion + Background Analysis

**Status:** complete, pending review
**Preceded by:** [Milestone 5A](milestone-05a.md) · **Companion:** [Taxonomy product review](taxonomy-product-review.md)

---

## 1. Objective

Build the first path that carries **a customer's own data** end to end:

```text
CSV ──▶ validation ──▶ feedback rows ──▶ PostgreSQL ──▶ import batch
                                                            │
                                                     analysis job
                                                            │
                                                   background worker
                                                            │
                                            engine.analyse_batch ──▶ analysis_results
```

Everything before this milestone analysed the dissertation corpus. This is the first milestone
where FeedbackIQ ingests data that conceptually belongs to a company, stores it under that
company's ownership, and analyses it out of band.

---

## 2. Ingestion architecture

```text
POST /api/imports  (api/routes/imports.py)
        │  reads at most MAX_UPLOAD_BYTES+1, then hands off
        ▼
services/imports.py::import_csv
        ├─ _resolve_organisation      explicit owner, never inferred
        ├─ SHA-256 of the bytes       identical re-upload? return the original
        ├─ ingestion/csv_reader.py    validate: rows + per-row errors
        ├─ _drop_known_rows           external_id, then content_hash (2 queries)
        ├─ db.persistence.save_feedback
        ├─ finish_import_batch        counts + error summary
        └─ db.jobs.create_job         "analyse_import", queued
        ▼
returns 201 with import_id, job_id and what was rejected

python -m feedbackiq.worker
        ├─ claim_next_job             FOR UPDATE SKIP LOCKED, commits the claim
        ├─ services/analysis.py       chunk → engine.analyse_batch → save_batch_analysis
        └─ mark_succeeded / mark_failed
```

Four layers, one direction. `ingestion/` is pure (bytes in, dataclasses out — no database, no
HTTP, no engine), so every validation rule is testable on strings. `services/` orchestrates.
`db/` owns all SQL. `engine/` is untouched.

---

## 3. CSV schema

One required column. Everything else optional, because the first thing a customer tries is a
spreadsheet with one column of comments.

| Field | Required | Accepted header names | Notes |
|---|---|---|---|
| text | **yes** | `text`, `feedback`, `comment`, `review`, `body` | ≤ 20,000 chars after cleaning |
| external_id | no | `external_id`, `id`, `review_id`, `ticket_id` | ≤ 200 chars; unique per organisation + source |
| created_at | no | `created_at`, `date`, `created`, `submitted_at`, `timestamp` | ISO 8601 or `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `YYYY/MM/DD` |
| rating | no | `rating`, `score`, `stars` | numeric, 1–5 |
| platform | no | `platform`, `source`, `channel` | ≤ 100 chars, stored in `feedback.metadata` |

Headers are matched case-insensitively and trimmed. Encoding: UTF-8, UTF-8 with BOM (Excel on
Windows), then Latin-1 as a last resort — one mojibake row beats refusing the file.

---

## 4. Validation rules

**Whole-file problems raise `IngestionError`** and store nothing — not an empty batch, not a
partial import: empty file, undecodable bytes, no text column, more than 50,000 data rows,
larger than 10 MB.

**Row problems are counted and reported, never fatal.** Each produces `{row, field, message}`
where `row` is the **real line number in the customer's file**:

| Rejected when | Field |
|---|---|
| text empty, or empty after stripping control characters | `text` |
| text longer than 20,000 characters | `text` |
| more values in the row than columns in the header | `row` |
| date not recognised | `created_at` |
| rating not numeric, or outside 1–5 | `rating` |
| external_id longer than 200 characters | `external_id` |
| external_id repeated earlier in the same file | *(counted as `duplicates_in_file`)* |

Row numbers come from `csv.DictReader.line_num`, not from counting iterations: `DictReader`
silently skips blank lines, so counting drifts and the reported number stops matching the
customer's spreadsheet. Blank trailing lines are ignored, not rejected.

---

## 5. Import lifecycle

```text
importing ──┬──▶ completed     at least one row stored
            └──▶ failed        nothing usable in the file
```

An import with rejected rows is still `completed` — it ran, and its counts say what happened.
Only a file that yielded nothing is `failed`. `import_batches` records the owner, the data
source, the sanitised filename, the source hash, `row_count` / `imported_count` /
`failed_count`, timestamps, and an `error_summary` holding up to 100 row errors plus totals.

---

## 6. Idempotency decision

Two levels, both using indexes the schema already had, plus one new column.

| Level | Mechanism | Behaviour |
|---|---|---|
| **File** | `import_batches.source_hash` = SHA-256 of the uploaded bytes, indexed with `organisation_id` | An identical re-upload creates **nothing** — no batch, no rows, no job — and returns the original import with `duplicate_upload: true` |
| **Row** | `external_id` (unique per organisation + source), then `content_hash` (SHA-256 of the *normalised* text) | Rows this organisation already has are skipped and counted as `duplicates_in_database` |
| **Within one file** | first `external_id` wins | counted as `duplicates_in_file` |

**Why not a unique constraint on `source_hash`:** re-uploading a file is a legitimate thing to
do (a customer retries after a timeout). A constraint would turn that into a 500. An index plus
a service decision turns it into a correct, cheap answer — measured at **1 ms**.

Rejected as over-engineering: idempotency keys supplied by the client, a dedicated
`import_attempts` table, distributed locks. Nothing here needs coordination beyond one
transaction.

**Known gap:** `content_hash` is case-sensitive, so "The app crashed" and "the app crashed" are
two rows. Lower-casing would also merge genuinely distinct feedback; left as a documented
limitation rather than a silent decision.

---

## 7. Job lifecycle

```text
queued ──▶ running ──┬──▶ succeeded
                     └──▶ failed ──▶ queued   (while attempts < max_attempts, default 3)
```

`ALLOWED_TRANSITIONS` in `db/jobs.py` is the whole state machine; anything else raises
`JobStateError`. Finishing a job nobody claimed is a caller bug, and hiding it would make a
stuck queue impossible to diagnose. Recorded per job: organisation, kind, payload
(**identifiers only** — never feedback text), status, attempts, `locked_by` / `locked_at`,
`last_error` (truncated to 2,000 chars), timestamps, and the result summary.

The existing vocabulary (`queued`/`running`/`succeeded`/`failed`) was kept rather than renamed
to the brief's illustrative `pending`/`completed`: the schema, its CHECK constraint and
Milestone 4's tests already use it, and churning it would cost a migration for a synonym.

---

## 8. Worker architecture

`python -m feedbackiq.worker` — one process, one job at a time, in a loop. `--once` and
`--drain` exist for tests and smoke checks; SIGTERM finishes the current job and exits.

**Three transactions per job, and the reason matters.** The obvious implementation claims and
works in one transaction — but rolling back a failure also rolls back the claim, so `attempts`
never increments and a poisonous job retries forever. This was a real bug in my first draft,
caught while writing the worker tests, and `test_a_failing_job_keeps_its_attempt_count_and_is_retried`
now guards it.

```text
1. claim + commit     job is `running`, attempts incremented, no other worker can take it
2. do the work        its own transaction; results commit or roll back alone
3. record the outcome succeeded, or failed (re-queued while attempts remain)
```

Several workers are safe: `SELECT ... FOR UPDATE SKIP LOCKED` means the second worker skips a
locked row rather than blocking or double-claiming (tested with two concurrent sessions).

**Deliberately not introduced:** Celery, RQ, Kafka, RabbitMQ, Redis, Kubernetes, microservices,
event buses. The queue is a table, which makes it transactional with the data it describes —
a job cannot exist for an import that was rolled back. `run_job` is the seam to replace if a
real broker is ever justified by measured load.

---

## 9. Engine integration

The ingestion layer contains **no analytics**. `services/analysis.py` maps rows to
`FeedbackItem`, calls `engine.analyse_batch`, and hands the `BatchAnalysis` to
`db.persistence.save_batch_analysis`. Sentiment, the gate, categorisation, thresholds and
versioning all stay in `feedbackiq.engine`.

Bulk analysis uses **sentiment + categorisation only** — `build_default_engine(retriever=NullRetriever(), with_insights=False)`.
Retrieval and LLM insights are per item and expensive; an import of ten thousand rows does not
need an LLM summary of each one. They arrive when a customer asks a question.

Rows are processed in chunks of `ANALYSIS_BATCH_SIZE` (50), one analysis run per chunk, which
bounds memory and means a crash loses one chunk rather than the import.

---

## 10. Database persistence

No new tables. Two new columns, one migration (`0002`):

| Change | Why |
|---|---|
| `categories.key` (NOT NULL, unique per organisation and among globals) | stable category identity — see §14 |
| `import_batches.source_hash` (nullable, indexed with `organisation_id`) | file-level idempotency |

Ownership is unchanged and still enforced by the database: `analysis_results` references
`feedback (organisation_id, id)` through the composite foreign key from Milestone 4, so a result
cannot point at another organisation's feedback even if application code has a bug. Nothing in
5B weakened it, and the Milestone 4 tenant tests still pass.

**Migration `0002` was not shipped as autogenerated.** Alembic proposed adding `key` as NOT NULL
with no default, which fails on any database that already holds the 24 seeded categories.
Rewritten as add-nullable → backfill → constrain, with the 24 key/name pairs listed explicitly
in the migration rather than imported from the taxonomy loader: a migration must keep doing the
same thing years later, whatever the application has become. Rows the list does not cover get a
key derived from their name by the same rule, in SQL.

Verified: backfill on the dev database (24/24 keys matching the packaged taxonomy, 0 nulls);
the derive-from-name path (`Billing & Refunds / Credits` → `billing_and_refunds_and_credits`);
downgrade → re-upgrade; `alembic check` clean.

---

## 11. API endpoints

| Method | Path | Returns |
|---|---|---|
| POST | `/api/imports` | 201 with `import_id`, `job_id`, counts, `duplicate_upload`, `row_errors` |
| GET | `/api/imports/{import_id}` | status and counts |
| GET | `/api/jobs/{job_id}` | status, attempts, and the result summary |

Error mapping: 401/403 (API key, as every route), 413 oversized, 422 unusable file or malformed
id, 404 unknown id (scoped to the organisation, so another tenant's id is indistinguishable from
a nonexistent one), 500 with `{"detail": "Import failed."}` and nothing internal.

Route count went from 22 to **25**. The existing suites' "every `/api` route requires a key" test
covers the new ones automatically.

**Sessions open inside the handlers, not as a FastAPI dependency.** A dependency would run
before the route body and touch the database on unauthenticated requests — and it would force
`tests/api` to need PostgreSQL. Blocking work runs through `asyncio.to_thread`, as elsewhere.

---

## 12. Organisation ownership

```text
organisation ──▶ data_source ──▶ import_batch ──▶ feedback ──▶ analysis_result
```

Every customer-owned row carries `organisation_id NOT NULL`. There is no global feedback table
and no implicit ownership.

**The temporary mechanism, stated plainly.** Authentication does not exist yet, so the owner is
named by the application layer: an explicit `organisation_id`, or the seeded development
organisation (`DEV_ORGANISATION_SLUG`). It is **never** taken from the filename, the CSV
contents, or any header the caller controls — `test_ownership_is_never_inferred_from_the_file`
uploads a CSV containing `organisation_id` and `organisation` columns and asserts they are
ignored. Two functions decide ownership, and both are marked as the stand-in that Milestone 7/8
replaces: `services/imports.py::_resolve_organisation` and
`api/routes/imports.py::_organisation_id`.

---

## 13. Security considerations

| Concern | Treatment |
|---|---|
| Upload size | `MAX_UPLOAD_BYTES` (10 MB) checked by reading at most limit+1 bytes → 413, never buffered in full |
| Row count | `MAX_IMPORT_ROWS` (50,000) → refused, not truncated |
| Text length | 20,000 characters per row |
| CSV parsing | stdlib `csv`; no `eval`, no formula evaluation, no pandas type coercion |
| Filenames | path components stripped, non-`[A-Za-z0-9._ -]` replaced, 200 chars max (`../../etc/passwd` → `passwd`) |
| Temporary files | none — the upload is parsed in memory and discarded |
| Error leakage | row errors quote the customer's own data only; unexpected failures return a fixed message, tracebacks go to the log; `last_error` is a summary |
| SQL injection | parameterised ORM/Core throughout; the only raw SQL is in the migration, with bound parameters |
| Ownership | `organisation_id NOT NULL` everywhere, composite FK, per-organisation reads |
| Logging | no feedback text is logged; job payloads carry identifiers only |

**This milestone does not provide tenant security.** One shared API key still guards every
route, any holder acts as the development organisation, and there is no authentication,
authorisation or rate limiting. That is Milestones 7–9.

---

## 14. Stable category keys

```text
category_key  = stable identity      ──▶  what stored results resolve through
category_name = customer-facing label ──▶ free to reword
```

`categories.key` is lower_snake_case, unique per organisation and among the global defaults
(the latter needs a partial index, because PostgreSQL treats NULLs as distinct). Keys were
derived from the current 24 names **once**, at taxonomy `1.1.0`, and are frozen: a rename must
never regenerate them.

The chain now resolves by identity rather than by label:

- `core/default_categories.json` carries `key` per category (taxonomy `1.1.0`; names,
  descriptions and exemplars **byte-identical** to `1.0.0` — verified, so no stored analysis was
  invalidated);
- `categories_from_dicts` uses `key` as the engine-side `Category.id`, falling back to the name
  when absent;
- `db/persistence.py::_resolve_category_id` tries UUID → key → name;
- `db/seed.py` matches on key and updates a changed display name in place.

`test_re_seeding_after_a_rename_updates_the_label_and_keeps_the_identity` proves the property
that matters: rename a seeded category, re-seed, and the same row survives with its label
corrected — so results pointing at it stay correct. No taxonomy registry, no version-management
system: one extra field and two indexes.

---

## 15. Taxonomy product review

Full document: [taxonomy-product-review.md](taxonomy-product-review.md). **No category was
renamed in this milestone**, as the brief required.

Of the 24 discovered categories: **8 usable** as a customer-facing default, **7 workable but
narrow**, **9 research artefacts** — including `spray_bottle_continuous_spray_dryer_diffuser_fit_dryer`,
which is an unmerged BERTopic term list rather than a name. Nine are single-vertical (salon,
hair, mirrors, GPS, laptop cooling), and the obvious business categories — billing, refunds,
account access, onboarding — are missing entirely, because the corpus decided the taxonomy and
the corpus was ~70% Yelp.

This milestone's own runs are evidence: with real models, "The delivery arrived a week late" and
"Charged me twice for the same order" both matched *Product Performance Failures*, and "The app
crashes every time I try to log in" came back **unclassified**.

Recommendation: a designed product default of 10–14 broad, industry-neutral categories as
taxonomy `2.0.0`, measured against the benchmark before adoption, with the discovered 24 kept as
a named alternative for reproducibility. Renaming is now safe; splitting and merging still needs
a per-key mapping decision.

---

## 16. Tests

| Suite | Count | Covers |
|---|---|---|
| `tests/unit/test_csv_validation.py` | 41 | valid CSV, header aliases, real line numbers, whole-file refusals, empty/oversized text, malformed rows, invalid dates and ratings, duplicate identity, BOM and Latin-1 |
| `tests/integration/test_imports.py` | 22 | batch counts, row errors, organisation-owned feedback, failed vs completed, oversize, analysis queued not run, file/row/in-file duplicates, two organisations in parallel, ownership never inferred, filename sanitising, file not stored |
| `tests/integration/test_jobs.py` | 15 | creation, every valid transition, invalid transitions raising, retry and attempt exhaustion, error truncation, **two workers never claiming the same job**, tenant-scoped reads |
| `tests/integration/test_worker_analysis.py` | 14 | stored feedback reaching the engine, results and run versions persisted, chunking, partial item failures, all-failed batches, worker end-to-end, drain, **attempts preserved on failure**, unknown job kind, re-run supersedes rather than duplicates |
| `tests/api/test_imports_api.py` | 15 | 201 shape, bytes and filename reaching the service, duplicate response, 413 before the service runs, 422, 404, malformed id, 500 without internals, auth on every route |
| Plus | | new taxonomy-key tests in `test_default_taxonomy.py`, `test_categorisation.py`, `test_seed.py`, `test_tenant_data.py` |

**Results: `pytest` 330 passed, 2 xfailed** (was 267/2). **`pytest tests/integration` 97 passed**
(was 42). flake8 clean.

Six failures during development were genuine and are worth recording: three were my test
expectations being wrong, one was the blank-line row-number drift (product code fixed, not the
test), and two pre-existing tests had been **passing for the wrong reason** — asserting
`IntegrityError` and receiving a `NotNullViolation` from the new column instead of the
uniqueness violation they claimed to check. They now assert the constraint by name.

---

## 17. Performance

Measured, not guessed. 5,600-row CSV (239 KB) on this laptop against PostgreSQL 16:

| Stage | Result |
|---|---|
| Validation only | 0.01 s — **446,411 rows/s** |
| Full import (validate + dedupe + store) | 0.64 s — **8,519 rows/s**, 5,487 stored |
| **SQL statements for the whole import** | **14** (5 SELECT, 8 INSERT, 1 UPDATE) = 0.0026 per row |
| Identical re-upload | **0.001 s**, nothing stored |
| Real-model analysis, 8 rows, cold start | 14 s wall clock including loading DistilBERT + DeBERTa |

The statement count is the number that matters: duplicate detection is two set-based queries for
the whole batch and inserts are batched, so there is no per-row query. Nothing was optimised
beyond that — no COPY, no server-side cursors, no async — because 8,500 rows/s is far beyond
current need.

---

## 18. Known limitations and future replacement points

| # | Limitation |
|---|---|
| 1 | **No authentication.** One shared API key; every caller acts as the development organisation. Milestones 7–9. |
| 2 | **A crashed worker leaves a job `running` forever.** No reaper for stale locks (`locked_at` is recorded for one). A worker killed mid-job needs manual requeueing. |
| 3 | **`content_hash` is case-sensitive**, so differently-capitalised duplicates both store. |
| 4 | **Per-row failures are not individually re-runnable.** Re-running the job re-analyses the whole import (safely — results supersede). |
| 5 | **No progress reporting during a job.** A job is `running` or finished; a 50,000-row import gives no percentage. |
| 6 | **Uploads are parsed in memory.** Fine at 10 MB; a larger limit needs streaming and object storage. |
| 7 | **One worker per container, one job at a time.** Concurrency comes from running more containers; there is no in-process parallelism. |
| 8 | **The default taxonomy is unfit for general customers** (§15) — correctness is fixed, suitability is not. |
| 9 | **No retry backoff.** A failed job is re-queued immediately (`run_after` supports delay; nothing sets it). |
| 10 | **`platform` from the CSV lands in `feedback.metadata`**, not in a column — deliberate, but it means it is not indexed for filtering. |

**Intended replacement points:** `run_job` (a real broker, if load ever justifies one);
`_resolve_organisation` / `_organisation_id` (authentication); in-memory upload parsing (object
storage + streaming); the `analyse_import` job kind (per-row retry, re-analysis, scheduled
insight generation).

---

## Next

Recommended: **Milestone 6 — Backend API v1** ([roadmap M6](07-production-roadmap.md#m6--backend-api-v1)):
a versioned `/api/v1` over the stored data — list and filter feedback, analytics aggregates in
SQL, category management — which is what makes the ingested data visible. It needs no
authentication to be useful, and it is the last piece before a real frontend or real users.

**Awaiting approval before starting.**
