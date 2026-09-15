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
   **513 passed, 2 xfailed** or better (508 before Milestone 8; 382 before Milestone 7). Never
   weaken or delete a test to get green, and never flip a strict `xfail` without documenting why
   the behaviour changed. The database suite is separate and needs a real PostgreSQL:
   `pytest tests/integration` (273 tests), not selected by a bare `pytest`. The web app has its
   own: `npm test` in `web/` (117 Vitest tests) and `npm run e2e` (15 Playwright tests against the
   real API).
   Gate commits on pytest's exit code explicitly: `set -e` does not stop a chain when
   `pytest | tail` fails.
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
  api/                 FastAPI app, auth dependencies, routes, request/response schemas
  auth/                credential rules and session tokens: pure functions, no DB, no HTTP
  services/            orchestration between the API and the ML code (auth.py: accounts, sessions)
  engine/              the analytics engine: typed inputs and results, no HTTP, no SQL
  db/                  models, session, persistence functions, job queue, seed
  ingestion/           CSV parsing and validation: bytes in, typed rows out, no I/O
  worker.py            the background worker (`python -m feedbackiq.worker`)
  nlp/                 sentiment, categorisation, embeddings, LLM analysis
  rag/                 retrieval-augmented question answering and its prompts
migrations/            Alembic environment and versioned migration scripts
web/                   the customer web app: React + TypeScript + Vite, served by nginx (Milestone 8)
frontend/              Streamlit app (internal tool; research routes only, over HTTP)
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
python -m feedbackiq.db.seed           # a `dev` organisation + the default categories (no users)
pytest tests/integration               # needs the database above; fails (not skips) without it
alembic revision --autogenerate --rev-id 000N -m "what changed"

python -m feedbackiq.worker            # run background jobs until stopped
python -m feedbackiq.worker --drain    # run until the queue is empty (tests, smoke checks)
docker compose up -d postgres worker   # the same worker in a container

cd web && npm ci && npm run dev        # the web app on :5173, /api proxied to :8000
npm run lint && npm run typecheck && npm run api:check && npm test && npm run build
npm run e2e                            # Playwright against a running API (see web/README.md)
docker compose up -d --build postgres backend web   # the web app behind nginx on :8080
python -m feedbackiq.api.openapi_export web/openapi.json && (cd web && npm run api:types)
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
- **Two taxonomies, one default.** Both are packaged and read through `core/taxonomy.py`
  (stdlib only, no `core.config` import, so they work in a bare container):
  - **Product taxonomy — the default.** `core/product_categories.json`, `product-13`,
    version **2.0.0**, **13 customer-facing categories**. `load_default_taxonomy()` returns
    it; the engine, the seed and `nlp/categoriser.py` all use it.
  - **Research taxonomy — retained, not the default.** `core/default_categories.json`,
    `complaint-24`, version 1.1.0. `load_dissertation_taxonomy()` returns it. Its 24 rows
    stay in the `categories` table marked `source='discovered'`, `is_active=false`, because
    analysis produced before Milestone 6 points at them. **Never delete them**:
    `analysis_results.category_id` is `ON DELETE SET NULL`, so deleting would blank history.
  - The product taxonomy **may evolve independently of the dissertation**. The dissertation
    taxonomy is research evidence and does not constrain product design (Milestone 6).
  - A taxonomy change needs evidence. Accuracy cannot be measured — there is no
    category-labelled data — so judge coverage, distribution (how many categories are ever
    chosen, how large the biggest bucket is), description distinctness and face validity on
    business scenarios. See `docs/production/taxonomy-product-review.md`.
  - `migrations/versions/0003_product_taxonomy.py` **duplicates the 13 categories
    deliberately** — a migration must not import today's loader. If you edit the packaged
    file, edit the migration too; a test asserts they match.
  - Versions reach stored results through the engine manifest (`taxonomy_id`,
    `taxonomy_version`, `taxonomy_source`).
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
  - **Ownership is explicit**, never inferred from a file, a filename or a CSV column, and
    never defaulted. `import_csv` requires `organisation_id`, which the API takes from
    `get_current_organisation`. The dev-organisation fallback and `DEV_ORGANISATION_SLUG`
    were removed in Milestone 7; do not reintroduce a default owner.
  - Whole-file problems raise `IngestionError`; a bad *row* is counted and reported with its
    line number, never silently dropped and never fatal to the upload.
  - Uploaded files are parsed in memory and not kept: the database is the source of truth.
  - **Category identity is `categories.key`**, not the name. Results resolve by key, so a
    display name may be reworded freely; splitting or merging categories may not.
  - API routes open their own session *inside* the handler, after auth — never as a FastAPI
    dependency, or `tests/api` would need PostgreSQL. The one exception is the auth
    dependency's single session lookup, which runs only for a well-formed cookie: a request
    with no or malformed credentials never touches the database.
- **The customer API is `/api/v1` (Milestone 6).** Reads over stored feedback and
  SQL-computed analytics: `feedback` (list/detail), `analytics/summary`, `analytics/trend`,
  `analytics/categories`, `categories`, `imports` (GET, and POST upload since M7), `jobs/{id}`.
  Rules:
  - **Every customer-data query is organisation-scoped**, and the scope comes only from
    `api/deps.py::get_current_organisation` (Milestone 7, below). Never infer the tenant from
    a URL, query parameter, body, header or uploaded file.
  - **Filter and aggregate in PostgreSQL**, never by fetching rows into Python.
    `services/feedback.py` and `services/analytics.py` own those queries;
    `services/analytics_service.py` is the old pandas/parquet version and is *not* the model
    to copy. Tests assert statement counts, so a stray N+1 fails.
  - Filter by **`category_key`**, never by display name.
  - An unknown id returns **404 scoped to the organisation**, so another tenant's id is
    indistinguishable from one that does not exist.
  - The dissertation-era research routes (`/api/sentiment`, `/api/search`, `/api/rag`,
    `/api/analytics`, `/api/evaluation`) keep the API key and Streamlit still uses them.
    The ingestion routes (`/api/imports`, `/api/jobs`) need a signed-in user since
    Milestone 7; Streamlit calls neither.
- **Authentication and tenant enforcement (Milestone 7).** See `docs/production/milestone-07.md`.
  - **Two kinds of caller.** Research routes: the shared `x-api-key`. Customer data
    (`/api/v1/*`, `/api/imports`, `/api/jobs`): a server-side session cookie. The API key is
    never a way into customer data. The only unauthenticated routes are health and
    register/login/logout, pinned by `PUBLIC_ROUTES` in `tests/api/test_api.py`; adding a
    public route means editing that set on purpose.
  - **The organisation comes from one place**: `get_current_organisation`, i.e. the session's
    organisation confirmed against a live membership, re-checked on every request in one SQL
    statement (`services/auth.py::resolve_session`). Customer routers are mounted behind it
    in `main.py`; each handler takes `context: AuthContext = Depends(get_current_organisation)`
    and passes `context.organisation_id` to its helper. A new customer route does the same.
  - **Registration always creates a new organisation** with the registrant as owner. It must
    never attach anyone to an existing organisation; joining one needs invitations (later).
  - **Credentials.** argon2id via `auth/credentials.py` (argon2-cffi) — never hash by hand.
    The session token lives only in the `HttpOnly` cookie, never a response body; only its
    SHA-256 is stored. There is no auth secret, so don't add JWTs or signing without a
    documented decision. Never log passwords, tokens or email addresses.
  - **Status codes.** 401 not signed in (one message for every cause); 403 no organisation,
    a disabled account after the correct password, or a disallowed origin on a POST; 404 for
    another tenant's id, identical to a missing one; 409 email taken; 422 invalid details —
    and 422s under `/api/v1/auth` never echo the submitted input.
  - **Roles** are `owner` and `member` (`MEMBERSHIP_ROLES` + a CHECK). Both have the same
    data access today; no owner-only route exists yet. Add a role with the tuple plus a
    migration.
  - Disable a user with `is_active=false`, which ends their sessions immediately. Deleting a
    user or organisation cascades to memberships; deleting an organisation sets sessions'
    `organisation_id` to NULL.
  - **Tests.** API tests sign in with `app.dependency_overrides[get_current_organisation]`.
    Any new customer route must be added to `customer_paths` in
    `tests/integration/test_tenant_isolation_http.py`, which checks that no route leaks the
    other tenant's identifiers or text. `test_tenant_isolation.py` and the composite foreign
    keys stay as the lower layers.
  - The worker still takes the organisation from the job row, never from a request.
- **The web app (Milestone 8).** `web/`; see `docs/production/milestone-08.md` and `web/README.md`.
  - **Same origin only.** The app calls `/api/...` on its own origin; the Vite dev server and nginx
    (`web/nginx.conf`) proxy it, keeping the `Host` header. Never call the backend's host directly and
    never switch `fetch` to `credentials: "include"`.
  - **No auth state in JavaScript.** The session is an HttpOnly cookie. The app's only record of who
    is signed in is `GET /api/v1/auth/me`, cached under `["session"]`. Nothing auth-related goes in
    `localStorage` or `sessionStorage`, and no request ever carries an organisation id.
  - **Write the session before dropping the rest of the cache** (`auth/sessionCache.ts::replaceSession`).
    Clearing first detaches the route guards from the entry they watch, and a 401 then leaves the
    refused page on screen — a real bug found in Milestone 8.
  - Any 401 from a data request ends the session in the app (`app/queryClient.ts`); sign-in,
    registration and sign-out carry `meta: { credentialCheck: true }` so a wrong password is not one.
  - **API types are generated.** After changing a backend request or response shape, regenerate
    `web/openapi.json` and `src/api/schema.d.ts` (command above). CI fails on drift in either.
  - **A `Literal` with the same values must be written in the same order everywhere** (or share an
    alias): `typing` caches equal Literals as one object, so the OpenAPI enum order otherwise depends
    on import order. `tests/unit/test_literal_ordering.py` enforces it.
  - **Chart colours were measured**, not picked: the sentiment scale and category colour in
    `src/styles/tokens.css` passed the dataviz palette validator. Re-run it before changing them.
    Every chart with two or more series has a legend, values reachable as text or a table, and gets
    rendered and looked at with real data before it is called done — four layout defects were found
    that way that tests had passed over.
  - nginx sends a strict same-origin Content-Security-Policy: no inline scripts or styles (React
    `style` props are fine). Every Playwright spec calls `failOnContentSecurityPolicyViolations()`.
  - Playwright role and label names match **substrings** unless `{ exact: true }`.
  - To count SQL statements in a test, listen on the `Engine` class: `get_engine()` and
    `get_engine(None)` are different `lru_cache` entries, i.e. different engines.
  - CI's browser tests have no ML models, so they stop at "Queued"; analysis completing is verified in
    Docker with the worker.
- **Release prerequisites — before any public exposure** (`docs/production/milestone-08.md` §13):
  rate limiting and account lockout on sign-in, registration and upload; HTTPS with HSTS;
  `ALLOWED_ORIGINS` set to the real web origin and uvicorn trusting the proxy's forwarded headers; an
  audit log. None of these is built yet.
- **Stale jobs** are recovered by `services/maintenance.py` — `running` for longer than
  `STALE_JOB_MINUTES` is requeued (or abandoned once attempts are spent). Run on worker
  startup or via `python -m feedbackiq.worker --reclaim`. Deliberately not on a timer in
  every worker.
