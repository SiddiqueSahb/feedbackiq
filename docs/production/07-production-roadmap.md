# 07 — Production Roadmap

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · **Plan only — no milestone after M0 has started.**
> Previous: [06 — SaaS data model](06-saas-data-model.md) · Next: [08 — Next step](08-next-step.md)

---

## Principles for every milestone

1. **Leave the app working** at the end of every milestone. No long-lived half-built states.
2. **Tests first around what you're about to change**, then change it.
3. **Smallest sensible change**, merged as small pull requests.
4. **Measure before and after** anything touching models (the dissertation habit).
5. **Document and explain**: problem → solution → architecture → change → tests → how to run/verify.
6. **No new infrastructure without a concrete need** ([05 §7](05-target-architecture.md#7-what-not-to-introduce-yet)).

## What changed from the suggested structure, and why

| Change | Reason found in the audit |
|---|---|
| **M1 is now "Repository foundation & safety net"**, separate from **M2 "Clean project structure"** | This working copy has no git history or dotfiles, and the project has zero pytest tests. Restructuring without those would be unverifiable. |
| **Feedback ingestion moved from M9 to M5** (right after the database) | Without ingestion, the database and API have nothing but the research corpus. Ingestion is what turns FeedbackIQ from a demo into a product. |
| **Docker + deployment moved from M15 to M10** (staging, before the customer frontend) | Deployment problems found at the end are expensive, and a staging environment must exist before real customer data. Docker already exists, so this is extending, not starting. |
| **Security & testing are continuous**, with a dedicated pre-launch pass (M16) | Tests and security review are part of every milestone's acceptance criteria. |

## Overview

```text
M0 ─ M1 ─ M2 ─┬─ M3 ─┐
              └─ M4 ─┴─ M5 ─ M6 ─ M7 ─ M8 ─ M9 ─ M10 ─ M11 ─ M12 ─ M13 ─ M14 ─ M15 ─ M16 ─ M17
                                                       ▲                  ▲
                                          internal pilot possible   first design-partner
                                          (staging, seeded data)    customer possible (manual billing)
```

| # | Milestone | Outcome in one line | Depends on |
|---|---|---|---|
| M0 | Repository & architecture audit ✅ | You understand what exists and what's missing | — |
| M1 | Repository foundation & safety net | One git-tracked copy, a dissertation baseline tag, a fast offline test suite in CI | M0 |
| M2 | Clean project structure | Installable package, one config, one logging setup, research code separated | M1 |
| M3 | Production-ready analytics engine | Batch analysis of *any* feedback list, typed outputs, model versions | M2 |
| M4 | PostgreSQL / database layer | Persistent, tenant-ready schema with migrations | M2 |
| M5 | Feedback ingestion & background analysis | CSV → stored feedback → worker → stored results | M3, M4 |
| M6 | Backend API v1 | Versioned REST API over the database | M5 |
| M7 | Authentication | Real users, sessions, secure passwords | M6 |
| M8 | Multi-tenancy | Many organisations safely share one deployment | M7 |
| M9 | Roles, permissions & invitations | Owner/admin/analyst/viewer, invites, org API keys | M8 |
| M10 | Docker + staging deployment | Automated deploy of web + worker + migrations to staging | M8 (M9 recommended) |
| M11 | Production frontend / dashboard | Customer-facing React app | M9, M10 |
| M12 | Analytics & insight workflows | Trends, emerging issues, tenant-scoped search & Q&A, evidence-linked insights | M5, M6, M11 |
| M13 | Reports | Exports and executive reports generated in the background | M12 |
| M14 | Usage tracking & limits | Per-organisation consumption known and capped | M12, M13 |
| M15 | Billing / subscriptions | Stripe plans connected to limits | M14 |
| M16 | Security & testing hardening | Pre-launch security review, RLS, E2E and load tests | M10–M15 |
| M17 | Monitoring & production hardening | Errors, performance, LLM cost and model drift visible, with alerts | M10 onward |

---

## M0 — Repository and architecture audit ✅

- **Objective:** understand the system before changing it.
- **Files/components affected:** `docs/production/01–09` (new). No code changes.
- **Dependencies:** none.
- **Risks:** the audit misreads intent → mitigated by your review of these documents.
- **Acceptance criteria:** nine documents exist; you have reviewed [08](08-next-step.md) and approved (or changed) the next milestone.

---

## M1 — Repository foundation & safety net

- **Objective:** make change *safe*. One git-tracked working copy, a frozen dissertation baseline, and an automated test suite that runs in seconds without models, data or network.
- **Files/components affected:** git history and dotfiles consolidated into one folder (`.git/`, `.gitignore`, `.env.example`, `.dockerignore`, `.streamlit/`, `.github/`); `pytest.ini`; `tests/conftest.py`; new `tests/unit/`, `tests/api/`; `.github/workflows/ci.yml` (test job); a short testing guide. **No production code changes.** Full detail in [08](08-next-step.md).
- **Dependencies:** M0 approval; your decision on which folder is canonical.
- **Risks:**
  - Committing data or `.env` by accident → restore the existing `.gitignore` *first*; check `git status` before every commit.
  - Slow or flaky tests that load real models → stubs, markers, offline environment variables.
  - Importing `backend.main` took 48.9 s during the audit (heavy libraries load at import) → keep API tests to one module until M2 improves it.
  - CI install of `torch`/`transformers` is slow → pip caching; CPU-only wheels.
- **Acceptance criteria:**
  - `git log` shows the existing 17 commits; a tag marks the dissertation version.
  - `pytest` passes locally and in CI, **offline** (no Hugging Face downloads, no Groq key).
  - `git diff <baseline-tag> -- nlp rag backend frontend` is empty.
  - Deliberately changing `CATEGORY_CONFIDENCE_THRESHOLD` or removing router-level auth makes a test fail (checked, then reverted).
  - Known defects are recorded as `xfail(strict=True)` tests, not silently ignored.

---

## M2 — Clean project structure

- **Objective:** make the code an installable package with clear boundaries, one configuration and one logging setup, **without changing behaviour**.
- **Files/components affected:**
  - `pyproject.toml` (new); package layout from [05 §9](05-target-architecture.md#9-proposed-folder-layout-final-names-decided-in-milestone-2)
  - `config.py` → `core/config.py`: remove the nine unused keys, move `ENVIRONMENT` and hard-coded thresholds into settings, anchor paths to the project root
  - `logger.py` → `core/logging.py`: remove `print`, log to stdout, optional JSON format
  - remove every `sys.path` edit; `frontend/` → `internal_app/`; `evaluate/`, `notebooks/`, research scripts → `research/` (with temporary re-export shims)
  - split requirements: runtime / research / dev; update Dockerfile `COPY` paths and CI lint (drop the E402 workaround)
  - fix stale references listed in [02 E9](02-code-classification.md#e-remove-later)
- **Dependencies:** M1 (the tests prove nothing broke).
- **Risks:**
  - Breaking dissertation reproduction → run `evaluate/generate_all_metrics.py` (reads only `data/results/`) before and after, and diff the CSVs.
  - Import cycles (`rag/prompts.py` imports from `rag/pipeline.py` inside a function today).
  - Docker paths → build both images in CI.
- **Acceptance criteria:**
  - No `sys.path` manipulation left in product code.
  - `pip install -e .` then starting the API works from any directory.
  - All M1 tests pass unchanged; `docker compose up` works; research metric outputs are byte-identical.
  - Backend import time measured before/after and recorded.

---

## M3 — Production-ready analytics engine

- **Objective:** turn `nlp/` + `rag/` into an engine library that analyses **any list of feedback texts** (not just the research corpus), in batches, with typed inputs/outputs and recorded model versions.
- **Scope:**
  - `analyse_batch(items, categories) → list[ItemAnalysis]`: sentiment → sentiment gate → categorisation → keywords. DistilBERT batched.
  - Categories **passed in**, not loaded from a file at import.
  - **Resolve the preprocessing mismatch:** DistilBERT was fine-tuned on `cleaned_text`, but the API feeds it raw text ([03 §5](03-feedbackiq-core.md#5-what-is-tightly-coupled-to-the-dissertation-prototype)). Measure both on the benchmark and choose deliberately.
  - Fix the known defect "positive reviews get complaint categories" behind a setting, evaluated before enabling.
  - Embeddings: an encode function separate from storage.
  - Grounded Q&A with retrieval injected as a function; errors raised, never returned as answer text.
  - Insights: single-item (existing) plus aggregate summary with evidence IDs (new).
  - LLM adapter: timeouts, retries, token counts, provider/model in one place.
  - Model manifest (name, version, artefact path/URI, checksum, evaluation reference); prompt version identifiers.
  - VADER/RoBERTa/NB/LR stay available to `research/`, not the product engine (logistic regression as fallback: decide here).
  - **Benchmark:** a fixed stratified sample of `reviews_unified.parquet` with a metric floor, run manually or nightly.
- **Files/components affected:** `nlp/sentiment.py`, `nlp/categoriser.py`, `nlp/embedding_service.py`, `nlp/summariser.py`, `rag/pipeline.py`, `rag/prompts.py`, `backend/services/sentiment_service.py` (becomes a thin caller), new `engine/types.py`, `engine/llm.py`, `tests/unit/engine/`, `tests/benchmark/`.
- **Dependencies:** M2.
- **Risks:**
  - Silent quality regression → benchmark before/after. Note that the README's RAG figures used `llama-3.1-8b-instant`, not the current default model.
  - Unknown CPU throughput → measure items/second.
  - The sentiment gate changes the reported 27.6% unclassified figure → document the new number.
- **Acceptance criteria:**
  - The engine imports without FastAPI or database code.
  - `analyse_batch` on 1,000 benchmark texts returns results carrying model versions.
  - Sentiment macro F1 on the benchmark is within ±0.01 of the pre-refactor run on the same sample (or the change is intentional and documented).
  - Throughput is measured and written down.
  - M1 API tests still pass; unit tests make zero network calls.

---

## M4 — PostgreSQL / database layer

- **Objective:** persistent storage for feedback and analysis results, **tenant-ready from day one** (one seeded organisation for now).
- **Files/components affected:** `docker-compose.yml` (Postgres + pgvector for development); `db/models.py`, `db/session.py`; `migrations/` (Alembic); `DATABASE_URL` in config; a seed script for a demo organisation with a sample of the research corpus; `tests/integration/` against real Postgres (CI service container). Tables: see [06 §6](06-saas-data-model.md#6-when-each-table-arrives).
- **Dependencies:** M2 (M3 for result shapes).
- **New dependency:** a PostgreSQL driver (`psycopg`), which is not installed. `sqlalchemy` and `alembic` are already in `venv/`.
- **Risks:**
  - Over-designing the schema → only M4 tables; migrations make later change cheap.
  - Forgotten indexes/constraints → [06](06-saas-data-model.md) lists them.
  - Schema drift → change the schema only through Alembic, never by hand.
- **Acceptance criteria:**
  - `alembic upgrade head` builds the schema on an empty database; the latest migration downgrades cleanly.
  - Integration tests run against real Postgres in CI.
  - Demo organisation seeded; every tenant table has `organisation_id NOT NULL` + index.

---

## M5 — Feedback ingestion & background analysis

- **Objective:** upload a CSV → stored feedback → background analysis → stored results. **The first milestone where FeedbackIQ analyses a company's own data.**
- **Scope:**
  - CSV validation: required text column; optional rating/date/external ID/metadata columns; size limits; encodings; row-level error report.
  - De-duplication; import status.
  - Worker using the `jobs` table (`python -m feedbackiq.worker`) with retries; idempotent execution (re-running a job never duplicates results).
  - Storage abstraction: local folder in development, bucket in production.
- **Files/components affected:** `services/imports.py`, `services/analysis.py`, `worker/`, db models, import routes (behind the existing API key until M7), optionally the Streamlit Upload page switched to persist, tests.
- **Dependencies:** M3, M4.
- **Risks:**
  - Large files → stream in chunks (`scripts/preprocess.py` already does this for JSONL).
  - Worker crash mid-run → commit per batch; resumable jobs.
  - Personal data in uploads → no text in logs.
- **Acceptance criteria:**
  - A 10,000-row CSV is fully analysed without web timeouts.
  - Killing the worker mid-run and restarting completes with no duplicate results.
  - Malformed rows are reported with row numbers; import progress is visible through the API.
  - Stored sentiment equals the engine's output for the same texts.

---

## M6 — Backend API v1

- **Objective:** a stable, versioned REST API over the database.
- **Scope:**
  - `/api/v1` prefix. Resources: imports, feedback (list, filter, paginate), analytics (summary, sentiment trend, category breakdown, category trend), categories (CRUD), keyword search.
  - One error format; pagination; request IDs; OpenAPI docs.
  - Analytics computed in SQL (replaces `analytics_service.py`'s pandas code).
  - Legacy `/api/*` routes kept for Streamlit until it's migrated.
- **Files/components affected:** `api/v1/*`, `services/analytics.py`, `services/feedback.py`, schemas, `tests/api/`.
- **Dependencies:** M5.
- **Risks:**
  - Breaking Streamlit → keep legacy routes.
  - Slow aggregates → indexes, `EXPLAIN ANALYZE`.
- **Acceptance criteria:**
  - Every v1 endpoint has tests for success, validation error and not-found.
  - No error response contains exception text (tested).
  - Dashboard aggregates over 100,000 feedback rows are measured and recorded (target well under a second locally).

---

## M7 — Authentication

- **Objective:** real user identity.
- **Scope:**
  - Sign-up, log-in, log-out; argon2 password hashing via a well-known library.
  - Server-side sessions in Postgres with `HttpOnly`, `Secure`, `SameSite` cookies; CSRF protection for state-changing cookie requests.
  - Email verification and password reset (email provider needed); login rate limiting; audit log entries.
- **Decision to confirm:** build sessions yourself with well-known libraries (recommended, for simplicity and learning value), or use a managed provider (Auth0/Clerk/Supabase Auth) if SSO/SAML is needed soon.
- **Files/components affected:** `auth/*`; `users`, `sessions`, `audit_logs` tables; `current_user` dependency; migrations; tests.
- **Dependencies:** M6.
- **Risks:**
  - Security mistakes → libraries, OWASP ASVS checklist, tests for expiry/lockout.
  - Email deliverability.
- **Acceptance criteria:**
  - Passwords are never stored or logged in plain text (tested).
  - Sessions expire and can be revoked.
  - A test enumerates all v1 routes and confirms each requires login, except health and auth (the same "protected by default" idea as today's router-level dependency).
  - Brute-force attempts are rate limited.

---

## M8 — Multi-tenancy

- **Objective:** many organisations safely share one deployment.
- **Scope:** create an organisation at sign-up; `organisation_members`; org in URL (`/api/v1/orgs/{org_id}/…`); a membership dependency; `org_id` required by every service function; composite foreign keys; cross-tenant test suite ([06 §4](06-saas-data-model.md#4-tenant-isolation--how-it-should-work)).
- **Files/components affected:** `auth/membership.py`, every service and route, migrations, `tests/tenancy/`.
- **Dependencies:** M7.
- **Risks:** one missed filter is a data leak → org-required function signatures; automated cross-tenant tests over every route; review checklist; RLS in M16.
- **Acceptance criteria:**
  - An automated test builds organisations A and B with data. For every v1 route, a user of A requesting B's IDs gets 404.
  - No service query on tenant tables lacks an `organisation_id` filter (review + test).

---

## M9 — Roles, permissions & invitations

- **Objective:** control who can do what.
- **Scope:**
  - Roles: owner / admin / analyst / viewer.
  - Permission matrix, for example:
    - viewer: read dashboards
    - analyst: imports, questions, reports
    - admin: members, categories, API keys
    - owner: billing, deleting the organisation
  - Email invitations with expiring single-use tokens.
  - Per-organisation API keys: hashed, prefix shown, revocable. This reuses today's constant-time comparison idea.
  - Audit entries.
- **Files/components affected:** `auth/permissions.py`, `invitations`, `api_keys` tables, member/key routes, tests.
- **Dependencies:** M8.
- **Risks:** permission checks scattered around → one `require_role(...)` dependency plus a matrix test.
- **Acceptance criteria:** a role × endpoint matrix test passes; the last owner cannot be removed; invitation tokens are single-use and expire; a revoked API key is rejected immediately.

---

## M10 — Docker + staging deployment

- **Objective:** automated deployment of web + worker + migrations to a **staging** environment, before any customer data exists.
- **Scope:**
  - One image with web and worker commands; multi-stage build (no `build-essential`/`git` in the final image); non-root user.
  - Model artefacts versioned in object storage (or baked in, if small).
  - Managed Postgres with pgvector and automated backups; secrets manager; HTTPS domain.
  - CI/CD: tests → build → push → migrate → deploy staging.
  - Liveness and readiness endpoints (today's health always says "ok"); demo organisation on staging.
- **Files/components affected:** Dockerfile(s), `docker-compose.yml` (development), `.github/workflows/*`, `DEPLOY.md` / `GCP_DEPLOY_RUNBOOK.md` updated, config.
- **Dependencies:** M8 (M9 recommended).
- **Risks:**
  - Cost → right-size after measuring; memory should drop without in-process FAISS/parquet copies.
  - Model cold starts.
  - Failing migrations → run as a separate step; write backwards-compatible migrations.
- **Acceptance criteria:**
  - A merge to `main` deploys to staging automatically, including `alembic upgrade`.
  - The rollback procedure is written down and rehearsed once.
  - Staging is reachable only over HTTPS; no secrets in the image or repository (scan).

---

## M11 — Production frontend / dashboard

- **Objective:** the customer-facing web app.
- **Scope:**
  - React + TypeScript (Vite) single-page app: sign-up/login, organisation switcher, CSV import with progress, feedback explorer (filters, search), dashboard (KPIs, sentiment trend, category breakdown and trend), categories editor, members page.
  - API client generated from OpenAPI; basic accessibility.
  - The Streamlit pages are the functional spec; Streamlit remains an internal tool.
- **Files/components affected:** new `web/`; small API additions discovered while building.
- **Dependencies:** M9, M10.
- **Risks:**
  - New language and tooling → [09](09-learning-map.md), stages 6–7.
  - UI polish scope creep.
  - CSRF with cookie auth → CSRF token pattern from M7.
- **Acceptance criteria:**
  - A new user can sign up → create an organisation → import the sample CSV → see the dashboard, with no internal tools.
  - A Playwright end-to-end test covers that journey.

---

## M12 — Analytics & insight workflows

- **Objective:** turn analysed feedback into decisions: trends, emerging issues, grounded Q&A and recommendations with evidence.
- **Scope:**
  - Period-over-period change per category, plus a simple spike rule.
  - **Emerging issues:** a rising share of "Unclassified", with lightweight grouping of unclassified feedback. A BERTopic-based "suggest categories" job (from `scripts/discover_categories.py`) comes later.
  - pgvector embeddings and tenant-scoped semantic search (replaces FAISS).
  - **Tenant-scoped grounded Q&A:** today's `rag/` rules kept (threshold, MMR, evidence IDs, refusal), with the scope guard generalised from "Amazon, Yelp, Twitter" to "this organisation's feedback".
  - LLM category summaries and recommendations stored in `insights` with evidence IDs, generated by the worker after imports or on a schedule, never on page load.
  - Users can correct sentiment/category (stored for future evaluation or training).
- **Files/components affected:** `services/insights.py`, `services/search.py`, `services/ask.py`, worker jobs, `feedback_embeddings`, `insights` tables, web pages, tests.
- **Dependencies:** M5, M6, M11.
- **Risks:**
  - LLM cost and rate limits (the development log already shows Groq's daily token limit exhausted) → per-org budgets, batching, caching.
  - Retrieval is the pipeline's measured weak point (context precision 0.416, recall 0.400) → evaluate on a labelled question set before customers rely on Q&A; label it "beta".
  - pgvector filtered-search recall for small tenants.
- **Acceptance criteria:**
  - Every insight links to its evidence feedback.
  - Q&A never returns another organisation's feedback (test).
  - Q&A refuses when evidence is below threshold (tests ported from `evaluate/verify_rag_fixes.py`).
  - Insight cost per organisation recorded; retrieval evaluation documented.

---

## M13 — Reports

- **Objective:** shareable outputs.
- **Scope:** CSV export of feedback + results; HTML/PDF executive report (period KPIs, top issues with evidence quotes, trends) generated by the worker and stored in object storage; authorised downloads; scheduled weekly email later.
- **Files/components affected:** `services/reports.py`, worker job, `reports` table, templates, web pages, tests.
- **Dependencies:** M12.
- **Risks:** PDF rendering complexity (start with CSV + HTML); CSV formula injection (escape cells starting with `=`, `+`, `-`, `@`); leaked download links (short-lived signed URLs).
- **Acceptance criteria:** reports run as jobs with status; content is reproducible from stored data; downloads require membership; CSV cells are escaped (tested).

---

## M14 — Usage tracking & limits

- **Objective:** know and cap each organisation's consumption.
- **Scope:** `usage_records` written by worker and services (feedback analysed, LLM tokens, questions, reports); plan limits in config; checks before enqueuing jobs or answering questions; usage page; API rate limiting per organisation/key.
- **Files/components affected:** `services/usage.py`, LLM adapter, worker, `usage_records` table, web page, tests.
- **Dependencies:** M12, M13.
- **Risks:** double counting on job retries (use `source_id` for idempotency); surprising customers (soft limits and warnings first).
- **Acceptance criteria:** usage totals match processed counts in tests, including retries; exceeding a limit returns a clear error; per-organisation LLM spend is visible.

---

## M15 — Billing / subscriptions

- **Objective:** charge for plans.
- **Scope:** Stripe Checkout + Customer Portal; webhooks (signature verification, idempotent handling) updating `subscriptions`; plan → limits; trials; downgrade behaviour.
- **Files/components affected:** `services/billing.py`, webhook route, `subscriptions` table, web pages, tests.
- **Dependencies:** M14.
- **Risks:** webhook edge cases; tax/VAT (Stripe Tax); legal documents (terms, privacy policy, data processing agreement).
- **Acceptance criteria:** in Stripe test mode, subscribing changes limits; cancelling follows the defined policy; a replayed webhook doesn't double-apply.

> Note: a first design-partner customer does **not** need M15. After M12 you can onboard a pilot on staging-grade production with manual invoicing.

---

## M16 — Security & testing hardening (pre-launch)

- **Objective:** confidence before real customers' data reaches production.
- **Scope:**
  - OWASP ASVS level 1 review; dependency and secret scanning in CI.
  - Row-Level Security policies as a safety net; security headers; CORS locked to the app origin; rate limits verified.
  - No pickle/unsafe deserialisation in runtime paths.
  - **Prompt-injection tests** (malicious feedback text trying to steer insights or Q&A).
  - Backup restore rehearsal; privacy documentation (retention, sub-processors including the LLM provider).
  - End-to-end suite for core journeys; load test of imports and dashboards.
- **Files/components affected:** CI workflows, migrations (RLS), middleware, tests (`e2e/`, `load/`), docs.
- **Dependencies:** M10–M15 (parts can start earlier).
- **Acceptance criteria:** checklist completed with evidence; no open high or critical findings; restore rehearsed; E2E and load results documented.

---

## M17 — Monitoring & production hardening

- **Objective:** know when things break or degrade, **including the models**.
- **Scope:**
  - Error tracking (e.g. Sentry); metrics for request latency, job queue depth/age, job failures and items/second.
  - LLM metrics: latency, tokens, cost, rate-limit errors, refusal rate.
  - **Model monitoring:** prediction distribution per organisation over time, unclassified share, confidence histograms, user correction rate.
  - Uptime checks; alerts with runbooks; log retention.
- **Files/components affected:** `core/logging.py`, middleware, worker, dashboards/alerts configuration, `docs/runbooks/`.
- **Dependencies:** M10 (basic error tracking can start there).
- **Acceptance criteria:** dashboards exist for API, worker, LLM and models; alerts tested by forcing failures; runbooks exist for the top five incidents (LLM provider down, worker stuck, database full, bad deploy, model file missing).

---

## Continuous tracks (every milestone)

| Track | Expectation |
|---|---|
| Tests | New behaviour has tests; the suite stays fast; CI green before merge. |
| Documentation | Your documentation principle: problem, solution, architecture, change, tests, explanation, how to run/verify. |
| Security | Each change is reviewed for auth, tenant scoping, input validation, secrets and logging of sensitive data. |
| Learning | Each milestone lists the concepts it relies on ([09](09-learning-map.md)). |
| Research | Model-affecting changes are benchmarked against the dissertation baseline. |

## Parking lot (valuable, not scheduled)

- SSO/SAML for enterprise customers
- Integrations: Zendesk, Intercom, App Store, Google Play, Trustpilot
- Multilingual feedback (current corpus, preprocessing and models are English-only)
- Fine-tuning on customer corrections; hierarchical taxonomy (README roadmap)
- Hybrid retrieval (BM25 + dense) and cross-encoder reranking, the README's own top-priority improvements for the retrieval bottleneck
- Streaming LLM responses; data residency options
