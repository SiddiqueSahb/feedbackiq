# FeedbackIQ — instructions for Claude Code

FeedbackIQ is a **production B2B SaaS product** for multi-tenant customer-feedback
intelligence, built **incrementally** from an MSc dissertation (sentiment analysis,
zero-shot complaint categorisation, FAISS retrieval, grounded question answering). The
audit and milestone plan live in `docs/production/`. Read
`docs/production/07-production-roadmap.md` before proposing work, and
`docs/production/saas-architecture-direction.md` for the intended boundaries.

## Product priority (from Milestone 5A)

**SaaS product requirements take priority over dissertation compatibility.** The
dissertation is historical source material and validated research — it is *not* the
architectural specification.

- Prefer a **simple production SaaS architecture** over preserving the dissertation
  implementation exactly.
- Preserve dissertation work that provides product value (models, evaluation methodology,
  grounding rules, the taxonomy). Do not preserve research-specific implementation merely
  because it existed.
- "This would break dissertation compatibility" is **not** a valid reason to reject a
  change. Classify instead: *must preserve* (validated ML/research capability), *safe to
  change* (research implementation details unfit for production), or *intentionally
  replaced later* (Streamlit UI, file-based datasets, global corpus retrieval, synchronous
  processing). Document the decision.
- Research reproducibility still matters: keep it working where it costs little, and say so
  when it cannot be kept.

## Ground rules

1. **Preserve working behaviour.** Structural work must not change what the app outputs.
   Where behaviour must change, say so explicitly and document it.
2. **Never start a milestone without approval.** Finish the current one, report, and wait.
3. **Small, reviewable changes.** Logical commits, not one large unexplained one.
   Never force-push; never rewrite pushed history.
4. **Run the tests after every meaningful change:** `pytest` must stay at
   **330 passed, 2 xfailed** or better (it was 104 passed, 4 xfailed before Milestone 3).
   Never weaken or delete a test to get green, and never flip a strict `xfail` without
   documenting why the behaviour changed. The database suite is separate and needs a real
   PostgreSQL: `pytest tests/integration` (97 tests), not selected by a bare `pytest`.
5. **Don't touch dissertation research artefacts** without asking: `notebooks/`,
   `evaluate/`, `scripts/`, `data/`, `models/`, `mlruns/`, `RESULTS.md`. Their results
   must stay reproducible. Import paths may be updated; methodology may not.
6. **Readable Python over clever Python.** Type hints where they help, clear names,
   comments that explain *why*. No abstraction without a concrete need.
7. **Don't over-engineer.** No microservices, Kubernetes, message queues, event buses
   or new databases unless the roadmap milestone calls for it. Modular monolith.
8. **No unnecessary dependencies.** Reuse what's installed; don't upgrade pins casually;
   keep runtime, dev and research dependencies separate (`pyproject.toml`).
9. **Never commit secrets or artefacts.** `.env`, credentials, `data/`, `models/`,
   `venv/`, `notebooks/`, `SUBMISSION.md` and the dissertation PDFs are gitignored and
   must stay that way. Check `git status` before committing.
10. **Explain architectural decisions** in the milestone document, including what was
    rejected and why.

## Layout

```text
src/feedbackiq/        the product (installed with `pip install -e .`)
  core/                settings, logging, paths, exceptions
  api/                 FastAPI app, auth dependency, routes, request/response schemas
  services/            orchestration between the API and the ML code
  engine/              the analytics engine: typed inputs and results, no HTTP, no SQL
  db/                  models, session, persistence functions, job queue, seed
  ingestion/           CSV parsing and validation: bytes in, typed rows out, no I/O
  worker.py            the background worker (`python -m feedbackiq.worker`)
  nlp/                 sentiment, categorisation, embeddings, LLM analysis
  rag/                 retrieval-augmented question answering and its prompts
migrations/            Alembic environment and versioned migration scripts
frontend/              Streamlit app (internal tool; talks to the API over HTTP only)
backend/               build files for the API image (Dockerfile, extra requirements)
tests/unit, tests/api  the safety net: offline, no models, no network
tests/integration      database tests against a real PostgreSQL (run explicitly)
tests/*.py             older manual check scripts, not collected by pytest
evaluate/, scripts/    dissertation evaluation and offline pipeline
notebooks/             dissertation notebooks (gitignored; never edited)
docs/production/       audit and milestone documentation
```

## Commands

```bash
pip install -e ".[dev]"      # editable install; no sys.path manipulation anywhere
pytest                        # the safety net (seconds, offline)
flake8 src frontend migrations tests/conftest.py tests/unit tests/api tests/integration --select=E9,F
uvicorn feedbackiq.api.main:app --reload --port 8000
streamlit run frontend/app.py
docker build -f backend/Dockerfile --output type=cacheonly .   # local export is broken; see milestone-01

docker compose up -d postgres          # PostgreSQL 16 on localhost:55432
alembic upgrade head                   # create/update the schema
python -m feedbackiq.db.seed           # dev organisation + the 24 default categories
pytest tests/integration               # needs the database above; fails (not skips) without it
alembic revision --autogenerate --rev-id 000N -m "what changed"

python -m feedbackiq.worker            # run background jobs until stopped
python -m feedbackiq.worker --drain    # run until the queue is empty (tests, smoke checks)
docker compose up -d postgres worker   # the same worker in a container
```

## Things to know

- Thresholds (0.35 for retrieval, categorisation and evidence) are settings with those
  defaults. Change a default only with a documented reason and a passing benchmark.
- The LLM (Groq) is external: no test may depend on it, and no test may need network
  access or model downloads.
- `logs/` is not used by the application any more; logging goes to stdout.
- Two known defects remain as strict `xfail` tests (both API-level); see
  `docs/production/milestone-01.md` before "fixing" one. The other two were fixed in
  Milestone 3 (the sentiment gate, and RAG errors raising instead of being returned).
- **The engine must never import SQLAlchemy, psycopg, Alembic or `feedbackiq.db`.** The
  engine returns typed results; `db/persistence.py` stores them. Enforced by
  `tests/integration/test_engine_db_boundary.py`.
- **The taxonomy has exactly one canonical source:**
  `src/feedbackiq/core/default_categories.json`, read through `core/taxonomy.py` (stdlib
  only, no `core.config` import, so it works in a bare container). The engine default, the
  database seed and `nlp/categoriser.py` all read it — **24 categories**. It is versioned
  (`taxonomy_version`) and the version reaches stored results through the engine manifest.
  - **Never add a fallback taxonomy.** A missing or invalid file raises `TaxonomyError`.
    The 7-category `_STATIC_FALLBACK` was deleted in Milestone 5A because it silently
    produced wrong categories wherever gitignored `data/` was absent (CI, the container
    image, any fresh clone).
  - Custom taxonomies stay injected: `analyse_batch(categories=...)`. Results record
    `taxonomy_source` as `default` or `caller`.
  - Research taxonomies (other sentiment classes) load explicitly via
    `nlp.categoriser.load_research_taxonomy()`, which also raises rather than degrading.
- **Ingestion (Milestone 5B).** `POST /api/imports` validates a CSV, stores organisation-owned
  `feedback`, and queues an `analyse_import` job; the worker runs the engine. Rules:
  - **Never analyse in a request.** The engine runs in the worker, not in the HTTP handler.
  - **Ownership is explicit**, never inferred from a file, a filename or a CSV column.
    `services/imports.py::_resolve_organisation` and `api/routes/imports.py::_organisation_id`
    are the only two places that decide it, and both are the temporary stand-in that
    authentication replaces.
  - Whole-file problems raise `IngestionError`; a bad *row* is counted and reported with its
    line number, never silently dropped and never fatal to the upload.
  - Uploaded files are parsed in memory and not kept: the database is the source of truth.
  - **Category identity is `categories.key`**, not the name. Results resolve by key, so a
    display name may be reworded freely; splitting or merging categories may not.
  - API routes open their own session *inside* the handler, after auth — never as a FastAPI
    dependency, or an unauthenticated request would touch the database and `tests/api` would
    need PostgreSQL.
- Beyond ingestion, nothing in the API reads or writes the database yet.
