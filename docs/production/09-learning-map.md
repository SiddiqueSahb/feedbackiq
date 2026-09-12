# 09 — Learning Map

> Part of the FeedbackIQ productionisation audit · 2026-09-10
> Previous: [08 — Next step](08-next-step.md) · Back to start: [01 — Current system](01-current-system.md)

You already know the hardest part of this product: the machine learning, the evaluation and the reasoning behind each design choice. This map covers the **software engineering** around it, in the order the roadmap needs it. Each topic is tied to real FeedbackIQ code, so you learn it on a problem you already understand.

---

## The path

```text
Stage 1 — Work safely            Git · pytest · Python packages · configuration · logging      M1–M2
      ↓
Stage 2 — ML as a product        inference engineering · LLM application engineering          M3
      ↓
Stage 3 — Data & APIs            SQL · PostgreSQL · SQLAlchemy · Alembic · background jobs    M4–M6
      ↓                          · REST design · FastAPI in depth
Stage 4 — Users & tenants        web security · authentication · multi-tenancy · RBAC          M7–M9
      ↓
Stage 5 — Ship it                Docker · CI/CD · cloud deployment                              M10
      ↓
Stage 6 — Product surface        TypeScript & React · vector search with pgvector              M11–M12
      ↓
Stage 7 — Run a business         usage metering & Stripe · observability · privacy             M13–M17
```

**How to use this:** learn each topic just before the milestone that needs it, not all up front. Each topic ends with a **"You're ready when…"** check you can use to decide whether to move on.

---

## Stage 1 — Work safely

### 1. Git and GitHub · *M1*

**In one sentence:** a history of every change, so you can experiment, review and undo.

**In FeedbackIQ:** your Desktop copy has 17 commits; this working copy has none. Every step of the roadmap is meant to be a small commit you can inspect (`git diff`) and revert. Tagging the submitted dissertation version means you can always return to exactly what you handed in.

**Learn:** commit, branch, merge · `git status` / `git diff` before every commit · `.gitignore` (why `data/`, `models/`, `.env` must never be committed) · tags · pull requests and reviewing your own diff · recovering (`git restore`, `git revert`).

**You're ready when…** you can create a branch, make a change, see exactly what changed, and undo it without fear.

### 2. Testing with pytest · *M1 onward*

**In one sentence:** automated checks that run your code with known inputs and fail loudly when behaviour changes.

**In FeedbackIQ:** `tests/test_categoriser_shortlist.py` already does the clever part. It replaces DeBERTa with a fake classifier, so the logic is tested in a second without downloading a model. What's missing is pytest's structure: `def test_…` functions, `assert`, fixtures, markers.

**Learn:**
- test functions and `assert`; fixtures and `conftest.py`
- `monkeypatch` (swap a model, an LLM or `time.sleep` for a fake)
- markers (`slow`, `needs_llm`); `pytest.mark.xfail(strict=True)` for known bugs
- FastAPI's `TestClient`
- the test pyramid: many fast unit tests, fewer API tests, very few end-to-end tests

**You're ready when…** you can write a test that fails if someone changes `CATEGORY_CONFIDENCE_THRESHOLD` (0.35) by accident.

### 3. Python packages and imports · *M2*

**In one sentence:** a package is a folder Python can import from anywhere once it's installed, without editing `sys.path`.

**In FeedbackIQ:** almost every file starts with `sys.path.insert(0, os.path.dirname(os.path.dirname(...)))`. That's why the code only works when launched from the right folder. It's also why CI linting had to ignore import-order warnings.

**Learn:** modules vs packages · absolute imports · `pyproject.toml` · `pip install -e .` (editable install) · import cycles and how to avoid them (`rag/prompts.py` ↔ `rag/pipeline.py` is a small one today) · keeping heavy imports (torch) out of module top level when a process doesn't need them.

**You're ready when…** you can explain why `uvicorn feedbackiq.api.main:app` works from any directory after `pip install -e .`.

### 4. Configuration and secrets · *M2*

**In one sentence:** values that differ between laptop, staging and production live in the environment, not in code.

**In FeedbackIQ:** you already use `pydantic-settings` in `config.py`, a good start. Gaps: nine settings nothing reads, `ENVIRONMENT` read with `os.getenv` elsewhere, thresholds hard-coded in `rag/pipeline.py` and duplicated in `nlp/summariser.py`. `deps.py` refusing to boot in production on the dev key is exactly the right instinct.

**Learn:** the "Twelve-Factor App" config principle · one `Settings` object · validation of settings at startup · `.env` vs a secrets manager · never logging secrets.

**You're ready when…** you can list every setting FeedbackIQ needs in production and where each value comes from.

### 5. Logging and error handling · *M2, M17*

**In one sentence:** logs let you reconstruct what happened; good error handling decides what the user sees versus what you record.

**In FeedbackIQ:** `logger.py` prints `"Inside get_logger()"` every time it's called. `rag/pipeline.py` returns `"An error occurred: {str(e)}"` to users with HTTP 200, and the log file contains provider stack traces including an account identifier. On the other hand, the routes' pattern of a generic 500 message plus `log.exception(...)` is correct.

**Learn:** log levels · structured (JSON) logs · request IDs · what never to log (secrets, customer feedback text) · exceptions vs return values · mapping errors to HTTP status codes.

**You're ready when…** a single log line tells you which request, which organisation and which step failed, without containing customer text.

---

## Stage 2 — ML as a product

### 6. ML inference engineering · *M3*

**In one sentence:** running a trained model reliably, fast and reproducibly for inputs you've never seen.

**In FeedbackIQ:**
- **Train/serve skew:** notebook 3 fine-tuned DistilBERT on `cleaned_text` (URLs and @mentions removed, emoji converted, non-ASCII stripped), but `FineTunedSentiment.predict` feeds it the raw request text. The same model can behave differently when its input preparation differs.
- **Batching:** today one review goes through the model at a time; a customer import has thousands.
- **Versioning:** results don't record which model produced them.

**Learn:** batching and padding · CPU threads (`OMP_NUM_THREADS`) · loading models once per process · train/serve preprocessing parity · model manifests and versions · benchmarks as regression tests · silent fallbacks (why `roberta_fallback` should be visible) · domain shift (airline tweets ≠ support tickets).

**You're ready when…** you can state items/second for DistilBERT on your machine, and prove a refactor didn't change macro F1 on a fixed sample.

### 7. LLM application engineering · *M3, M12*

**In one sentence:** treating an LLM as an unreliable, metered external service whose output must be validated.

**In FeedbackIQ:** you've already met the real problems. `openai/gpt-oss-20b` fails structured output, so you wrote `_recover_from_failed_tool_call`. Groq's daily token limit was hit (visible in the log). `RAG_PROMPT` rule 2 defends against instructions hidden in reviews.

**Learn:** structured output and schema validation · timeouts, retries, back-off · token counting and cost per feature · prompt versioning · **prompt injection** (customer-uploaded feedback is untrusted input) · evaluation sets for LLM features (you know RAGAS; add small deterministic checks like `verify_rag_fixes.py`) · batching LLM work into aggregate insights instead of per-item calls.

**You're ready when…** you can say what one organisation's monthly LLM cost would be, and what happens when the provider is down.

---

## Stage 3 — Data & APIs

### 8. Relational databases and SQL · *M4*

**In one sentence:** data stored in tables with relationships and rules the database enforces for you.

**In FeedbackIQ:** today "the database" is `reviews_unified.parquet` read into pandas. Your `groupby(["platform","sentiment_label"]).size()` becomes `SELECT … GROUP BY …`. Your pandas instincts transfer directly.

**Learn:** tables, rows, primary and foreign keys · `JOIN` · `GROUP BY` · indexes (and why `organisation_id` comes first) · constraints (`NOT NULL`, `UNIQUE`, `CHECK`) · transactions ("import batch + feedback rows + job all succeed or all fail") · `EXPLAIN ANALYZE`.

**You're ready when…** you can write the SQL for "weekly count of negative feedback per category for one organisation" and explain which index makes it fast.

### 9. PostgreSQL features we'll use · *M4, M5, M12*

**In one sentence:** the specific PostgreSQL tools that let one database do the job of several.

**In FeedbackIQ:** `uuid` IDs · `timestamptz` · `jsonb` for flexible metadata (today's `product_category`) · arrays for `evidence_feedback_ids` · `FOR UPDATE SKIP LOCKED` for the job queue (instead of adding Redis) · the `pgvector` extension (instead of FAISS files) · Row-Level Security later.

**Learn:** installing Postgres with Docker locally · `psql` basics · the types above · connection strings and pooling basics · backups.

**You're ready when…** you can run Postgres locally, create a table with a `jsonb` column, and query inside it.

### 10. SQLAlchemy 2.0 · *M4*

**In one sentence:** Python classes that map to tables, plus a safe way to build queries.

**In FeedbackIQ:** services will call functions like `list_feedback(session, org_id)`. SQLAlchemy builds parameterised SQL, which is also your protection against SQL injection. Never build SQL with f-strings.

**Learn:** declarative models (`Mapped[...]`) · `Session` and its lifecycle (one per request/job) · `select()`, `where()`, joins · relationships · transactions (`session.begin()`) · parameter binding.

**You're ready when…** you can write a query function that *requires* `org_id` and explain what SQL it sends.

### 11. Alembic migrations · *M4 onward*

**In one sentence:** version control for your database schema.

**In FeedbackIQ:** every table from [06](06-saas-data-model.md) arrives as a migration file, so your laptop, CI, staging and production databases stay identical.

**Learn:** `alembic revision --autogenerate` (and why you always read what it generated) · `upgrade` / `downgrade` · data migrations vs schema migrations · backwards-compatible changes (add column → deploy → backfill → then enforce `NOT NULL`).

**You're ready when…** you can add a column, apply it, roll it back, and explain why you never edit an already-applied migration.

### 12. Background jobs and workers · *M5*

**In one sentence:** slow work runs in a separate process that picks tasks from a queue, so HTTP requests return quickly.

**In FeedbackIQ:** `/analyse` already takes seconds for *one* review (DistilBERT + DeBERTa + FAISS + an LLM call). A 10,000-row import can't happen inside a request. The frontend's 90-second timeouts exist precisely because of slow first requests today.

**Learn:** job states (queued → running → succeeded/failed) · retries with back-off · **idempotency** (running the same job twice mustn't duplicate results) · progress reporting · why FastAPI `BackgroundTasks` isn't suitable for heavy model work (runs inside the web process, lost on restart) · the `SKIP LOCKED` pattern.

**You're ready when…** you can explain what happens to an import if the worker crashes halfway, and why no result gets duplicated.

### 13. REST API design · *M6*

**In one sentence:** URLs name *things*, HTTP methods say *what to do*, status codes say *what happened*.

**In FeedbackIQ:** today's routes are action-style (`POST /api/sentiment/analyse`, `POST /api/rag/chat`), fine for a demo. The product API is resource-style: `POST /api/v1/orgs/{org_id}/imports`, `GET /api/v1/orgs/{org_id}/feedback?sentiment=negative&limit=50`.

**Learn:** resources and nesting · GET/POST/PATCH/DELETE semantics · status codes (200, 201, 202 Accepted for async imports, 400/401/403/404/409/422/429, 500) · pagination · filtering · versioning (`/v1`) · one consistent error format · idempotency keys for retries.

**You're ready when…** you can design the endpoints for "import a CSV and watch its progress" and justify each status code.

### 14. FastAPI in depth · *M6 onward*

**In one sentence:** the framework you already use, used for more than routing.

**In FeedbackIQ:** you already use routers, Pydantic schemas, a lifespan hook, middleware, an exception handler and a router-level `Depends(require_api_key)`. Next: dependencies that yield a database session, return the current user, and check membership and role. Each is a small function you compose.

**Learn:** dependency injection with `yield` · `response_model` · `APIRouter` prefixes and versioning · sync vs async endpoints and when `asyncio.to_thread` is needed (you already use it) · lifespan for startup/shutdown · `TestClient` · OpenAPI generation.

**You're ready when…** you can write a `get_current_membership` dependency and use it on a route.

---

## Stage 4 — Users & tenants

### 15. Web security fundamentals · *M7, M16*

**In one sentence:** the common ways web apps get attacked, and the standard defences.

**In FeedbackIQ:** today's deployment guide exposes Streamlit on plain HTTP with no login, the API allows CORS `*`, there's no rate limiting, and containers run as root. None of that is unusual for a demo. All of it matters once customer data exists.

**Learn:** OWASP Top 10 (broken access control is #1, and that's what tenant isolation prevents) · HTTPS/TLS · CORS (what it does and does *not* protect) · CSRF with cookies · XSS · SQL injection and parameter binding · password hashing (argon2) · rate limiting · secrets management · pickle/unsafe deserialisation (the LangChain docstore uses `allow_dangerous_deserialization=True`).

**You're ready when…** you can explain why `API_KEY` sent from the Streamlit server doesn't make the app secure for multiple customers.

### 16. Authentication: sessions vs JWT · *M7*

**In one sentence:** proving who a user is on every request after they log in.

**In FeedbackIQ, the recommendation:**
- **Session (recommended):** a random ID stored in the `sessions` table and sent as an `HttpOnly` cookie. Logging out, or revoking a stolen session, is deleting a row. FeedbackIQ has one backend and one database, so this is the simpler and safer choice.
- **JWT:** a signed token the server doesn't store. Easy to verify anywhere, but hard to revoke: a stolen token works until it expires. It earns its place with many independent services or third-party token exchange, which FeedbackIQ doesn't need yet.

**Learn:** password hashing and verification · cookie flags (`HttpOnly`, `Secure`, `SameSite`) · CSRF tokens · session expiry and rotation · email verification and password-reset tokens (single-use, expiring, stored hashed) · login rate limiting · what OAuth/SSO add and when.

**You're ready when…** you can trace a login from form submit to the cookie, to the database row, to `current_user` on the next request.

### 17. Multi-tenancy · *M8*

**In one sentence:** many customers share one system, and none can ever see another's data.

**In FeedbackIQ:** today there is one global corpus and one global FAISS index, so every user sees everything. The design in [06 §4](06-saas-data-model.md#4-tenant-isolation--how-it-should-work) adds `organisation_id` everywhere, requires `org_id` in every service function, uses composite foreign keys, filters vector search in SQL, and proves it with cross-tenant tests.

**Learn:** pool (shared tables) vs schema-per-tenant vs database-per-tenant, and why pool first · deriving tenant from membership, never from input · returning 404 instead of 403 · tenant scoping beyond the database (files, caches, jobs, LLM prompts, logs) · Row-Level Security as a safety net.

**You're ready when…** you can write the test that proves organisation A cannot read organisation B's feedback through *any* endpoint.

### 18. Authorisation and RBAC · *M9*

**In one sentence:** after knowing *who* you are, deciding *what* you may do.

**In FeedbackIQ:** roles owner/admin/analyst/viewer. A viewer can read dashboards, only admins manage categories and API keys, and only owners handle billing.

**Learn:** authentication vs authorisation · role-based access control · a single permission-check dependency instead of scattered `if` statements · permission matrix tests · invitations · API keys for machines vs sessions for people · audit logging of sensitive actions.

**You're ready when…** you can add a new endpoint and its permission rule in one place, with a test that fails if the rule is missing.

---

## Stage 5 — Ship it

### 19. Docker and Docker Compose · *M4, M10*

**In one sentence:** package the app and its dependencies into an image that runs the same everywhere.

**In FeedbackIQ:** you already have two Dockerfiles with healthchecks, a `$PORT`-aware start command, a health-gated `depends_on` and a sensible reason for mounting data as volumes. Next: add Postgres to Compose for development, multi-stage builds, a non-root user, one image with two commands (web and worker).

**Learn:** layers and caching · multi-stage builds · `.dockerignore` (missing from this copy, so the build context would include ~15 GB) · volumes vs baked files · environment variables and secrets at runtime · image size (torch is large; CPU-only wheels help) · healthchecks vs readiness.

**You're ready when…** you can run web, worker and Postgres locally with one `docker compose up` and explain each line of the Dockerfile.

### 20. CI/CD with GitHub Actions · *M1, M10*

**In one sentence:** every push is automatically tested (CI) and, when green, deployed (CD).

**In FeedbackIQ:** `ci.yml` already lints and builds both images. M1 adds tests; M10 adds build → push → migrate → deploy to staging.

**Learn:** workflows, jobs, steps · caching dependencies · service containers (Postgres in CI) · secrets in Actions · required status checks · deployment environments and approvals · running migrations safely in a pipeline.

**You're ready when…** a pull request can't be merged unless tests pass, and merging to `main` updates staging without you touching a server.

### 21. Cloud deployment · *M10, M16*

**In one sentence:** running the containers, database and storage on managed infrastructure with HTTPS, backups and secrets.

**In FeedbackIQ:** `GCP_DEPLOY_RUNBOOK.md` already walks through a Compute Engine VM with Compose. The production shape adds managed Postgres (pgvector enabled, automated backups), a storage bucket, Secret Manager and HTTPS in front.

**Learn:** managed databases vs self-hosted · object storage and signed URLs · secrets managers · domains, DNS, TLS certificates · VM vs managed container platforms (trade-offs for model cold starts) · environments (staging vs production) · cost monitoring · backup *restore* rehearsal.

**You're ready when…** you can recreate staging from scratch using only the repository, the runbook and the secrets manager.

---

## Stage 6 — Product surface

### 22. TypeScript and React · *M11*

**In one sentence:** the standard way to build interactive web applications that talk to an API.

**In FeedbackIQ:** your Streamlit pages are the specification. The Dashboard KPI row, the tabbed Analyse page and chat messages with source expanders become React components that call `/api/v1`.

**Learn:** TypeScript basics (types catch API-shape mistakes) · React components, props, state · data fetching and loading/error states · routing · forms and file upload with progress · authentication in a single-page app (cookies + CSRF token) · a charting library · generating an API client from FastAPI's OpenAPI spec · Playwright end-to-end tests.

**You're ready when…** you can build a page that lists an organisation's imports with live progress, handling loading, empty and error states.

### 23. Vector search with pgvector · *M12*

**In one sentence:** storing embeddings in PostgreSQL and finding nearest neighbours with SQL.

**In FeedbackIQ:** you know embeddings, cosine similarity, FAISS and MMR deeply. What's new is *operational*: vectors live next to the feedback they belong to, filtered by `organisation_id` in the same query, added incrementally as imports arrive, deleted with the feedback.

**Learn:** the `vector` column type · distance operators · HNSW indexes and their build/recall trade-offs · filtered search recall (small tenants in a large table) · re-embedding when the model changes (store `model_name`) · reusing your `evaluate/retrieval_check.py` approach to measure recall.

**You're ready when…** you can reproduce today's "threshold 0.35 ∩ MMR, k=5" retrieval against pgvector for one organisation and measure its precision on a labelled question set.

---

## Stage 7 — Run a business

### 24. Usage metering and Stripe billing · *M14, M15*

**In one sentence:** count what each customer consumes, and let a billing provider handle money.

**In FeedbackIQ:** the expensive units are feedback analysed (CPU time) and LLM tokens (the log shows the free-tier daily limit already hit). `usage_records` makes both visible per organisation before billing exists.

**Learn:** append-only usage events · idempotency (a retried job must not be counted twice) · soft vs hard limits · Stripe Checkout and Customer Portal · webhooks (signature verification, idempotent handling) · trials, upgrades, downgrades · tax basics.

**You're ready when…** you can explain how a Stripe webhook changes an organisation's plan limits, and what happens if the same webhook arrives twice.

### 25. Observability · *M17*

**In one sentence:** being able to see, from the outside, whether the system and its models are healthy.

**In FeedbackIQ:** today, `/api/health` always says "ok" and logs sit in a local file. Production needs:
- error tracking
- request latency
- job queue depth and age
- LLM latency, tokens, cost and refusal rate
- **model monitoring**: distribution of predicted sentiment per organisation over time, share of "Unclassified", confidence histograms, how often users correct results

**Learn:** logs vs metrics vs traces · error tracking tools · dashboards and alert thresholds that don't cry wolf · liveness vs readiness checks · runbooks · model drift signals and how to act on them.

**You're ready when…** you'd find out that the worker is stuck, or that one customer's "Unclassified" share doubled, before the customer tells you.

### 26. Privacy and data protection · *cross-cutting, M16*

**In one sentence:** customer feedback often contains personal data, and handling it carries legal duties.

**In FeedbackIQ:** the dissertation used public datasets. A customer's support tickets can contain names, emails and order numbers. That affects logging (no feedback text), LLM usage (the provider becomes a sub-processor), retention, deletion and export.

**Learn:** GDPR basics (controller vs processor, lawful basis, data subject rights) · data processing agreements · sub-processor lists · retention and deletion (including backups) · data minimisation · encryption at rest and in transit · what to put in a privacy policy (with legal advice).

**You're ready when…** you can describe exactly what happens to a customer's data when they delete their organisation, including object storage, embeddings and backups.

---

## Official documentation to start from

Search for these by name: FastAPI tutorial · pytest "Get Started" · Python Packaging User Guide · The Twelve-Factor App · PostgreSQL tutorial · SQLAlchemy 2.0 Unified Tutorial · Alembic tutorial · pgvector README · OWASP Top 10 and OWASP ASVS · Docker "Get started" · GitHub Actions documentation · React documentation ("Learn React") · TypeScript Handbook · Stripe Billing documentation · ICO guide to UK GDPR.
