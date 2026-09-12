# 04 — Production Gap Analysis

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · Audit only.
> Previous: [03 — FeedbackIQ core](03-feedbackiq-core.md) · Next: [05 — Target architecture](05-target-architecture.md)

The comparison is against a **realistic early-stage B2B SaaS**: a small team, real customer data, paying users. It is not a large enterprise platform. Every "current state" entry points to evidence in the repository.

**Severity:**
- **Critical:** blocks any real customer use, or creates data-exposure risk
- **High:** needed before the first paying customer
- **Medium:** needed for reliable operation or growth
- **Low:** worthwhile improvement

---

## Scorecard

```text
Area          Readiness   Summary (judgement, for orientation)
────────────  ─────────   ─────────────────────────────────────────────────────────────────────────
Application   ■■□□□       good route/service/ML layering; sys.path imports, scattered config, print logging
Backend API   ■■□□□       validated, documented, protected-by-default; unversioned, single shared key, no rate limits
Database      □□□□□       none — files loaded into memory
SaaS          □□□□□       no organisations, users, roles, tenancy, usage or billing
Security      ■□□□□       good instincts (constant-time key check, boot guard); not safe for customer data
ML/AI         ■■■□□       excellent evaluation; weak operations (batching, versioning, monitoring, cost)
Testing       ■□□□□       2 working check scripts, 0 pytest tests, no tests in CI
Deployment    ■■□□□       Dockerfiles, compose, CI build, GCP runbook; no staging, CD, DB, backups, monitoring
```

---

## 1. Application

| Aspect | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| Code organisation | Clear top-level split: `backend/` → `nlp/`, `rag/`. But nearly every module edits `sys.path`; research and product code share one requirements file and folder tree. Two copies of the repository exist; the working one has no git. | Installable package; research separated; one tracked repository | High | M1, M2 |
| Separation of concerns | Routes contain no ML (good). But `nlp/categoriser.py` reads files at import; `rag/pipeline.ask` mixes guards, retrieval, generation, error handling and response formatting; the frontend imports the backend's `config.py`. | Engine without I/O at import; services own orchestration; frontend independent | Medium | M2, M3 |
| Configuration | pydantic-settings (good); production boot guard (good). 9 unused keys; `ENVIRONMENT`/`LOG_LEVEL` bypass settings; thresholds hard-coded and duplicated; paths relative to working directory; `ALLOWED_ORIGINS="*"` default; `GROQ_MODEL` differs from evaluated model. | One validated settings object; tuning values in config; absolute/anchored paths | Medium | M2 |
| Error handling | Routes map `ValueError`→422 and other errors→generic 500; global handler (good). RAG returns `"An error occurred: {str(e)}"` with HTTP 200. Silent fallbacks (RoBERTa, VADER, "General Feedback", empty LLM analysis, `"unknown"` labels) look like success. `exceptions.py` unused. | Typed errors; explicit degraded status; never exception text to clients | High | M3, M6 |
| Validation | Request bodies validated with lengths, list caps and control-character stripping (good). `ChatRequest.chat_history` is `list[dict]` capped at 20 items, but each message's content is unbounded and untyped. CSV validation exists only in Streamlit. | Typed, bounded chat history; server-side upload validation | Medium | M5, M6 |
| Logging | `print("Inside get_logger()")` on every call; `print` in categoriser/sentiment; plain text to local file; no request or tenant IDs; default WARNING hides request logs; question text and stack traces (with Groq organisation ID) logged. | Structured stdout logs with request/org IDs; no customer text; no provider account details | Medium | M2, M17 |

## 2. Backend

| Aspect | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| API structure | 17 endpoints (+ root), action-style (`/sentiment/analyse`, `/rag/chat`); the full pipeline sits under `/sentiment`; model list duplicated in schema and route | Resource-oriented, organisation-scoped routes | Medium | M6 |
| API versioning | None (`/api/...`); `version="1.0.0"` is metadata only | `/api/v1`; deprecation path for legacy routes | Medium | M6 |
| Authentication | One shared `x-api-key` for everything (constant-time compare, header-only, production boot guard: all good practice). Streamlit has no login and forwards the server's key. | User accounts, sessions; per-org API keys for integrations | **Critical** | M7, M9 |
| Authorisation | None: any key holder can do everything | Membership + role checks on every route | **Critical** | M8, M9 |
| Request validation | Pydantic on all bodies; query params validated for keywords | Chat history typing; pagination/filter params for list endpoints | Medium | M6 |
| Rate limiting | None. `/analyse` makes 1 LLM call; `/rag/chat` makes 2–3. The log shows Groq's 200,000 tokens/day limit exhausted. | Per-user/org/key limits, especially on LLM endpoints | High | M7, M14 |
| Error responses | FastAPI default `{"detail": …}`: string for 4xx/5xx, list for 422; no error codes; RAG failures return 200 | One error format with codes; correct status codes | Medium | M6 |
| Timeouts and concurrency | LLM timeout 30 s; one uvicorn worker; heavy work in the default thread pool; no limit on concurrent model inference | Heavy work moved to worker; request time budgets | Medium | M5 |
| Health checks | `/api/health` always `"ok"`; checks file existence only | Liveness vs readiness (models loadable, DB reachable) | Medium | M10 |
| API docs | `/docs`, `/redoc`, `/openapi.json` public without auth | Fine for staging; decide for production | Low | M16 |

## 3. Database

| Aspect | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| Schema | None. One parquet file (8 columns), two FAISS files, a pickle docstore, JSON/CSV | Relational schema ([06](06-saas-data-model.md)) | **Critical** | M4 |
| Migrations | None | Alembic | High | M4 |
| Indexes | Exact flat FAISS search in memory; pandas full scans per request (`get_trend_data` copies the whole DataFrame) | B-tree indexes led by `organisation_id`; HNSW for vectors | High | M4, M12 |
| Constraints | None; `review_id` uniqueness not enforced; FAISS↔parquet alignment by row position assumed, not checked | PK/FK/UNIQUE/CHECK; composite tenant keys | High | M4 |
| Transactions | None (read-only data) | Import batch + rows + job in one transaction | High | M5 |
| Data isolation | None: one global corpus and index for everyone | `organisation_id` everywhere + scoped queries + tests | **Critical** | M4, M8 |

## 4. SaaS capabilities

| Capability | Supported? | Needed? | Notes | Milestone |
|---|---|---|---|---|
| Organisations | No | Yes | the tenant boundary | M4 (column), M8 |
| Users | No | Yes | | M7 |
| Roles | No | Yes | owner / admin / analyst / viewer | M9 |
| Tenant isolation | No | Yes | shared schema + `organisation_id` | M8 |
| Organisation membership | No | Yes | users in several orgs | M8 |
| Invitations | No | Yes | email, expiring token | M9 |
| Usage tracking | No | Yes | analysis volume and LLM tokens drive cost | M14 |
| Subscription plans | No | Yes, but not for the first pilot | Stripe | M15 |
| Data import (customer's own data) | Partial: 200 rows, sentiment only, not saved | Yes: **core** | | M5 |
| Data export / deletion | No | Yes | portability, offboarding, GDPR | M13, M16 |
| Audit logs | No | Yes | who did what | M7+ |
| Onboarding / sample data | Sample CSVs exist | Yes | reuse `frontend/assets/*.csv` | M11 |
| Email notifications | No | Later | invites, reports | M9, M13 |
| Internal admin / support tooling | Streamlit (no auth) | Later | keep internal, protect it | M10 |

## 5. Security

| Aspect | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| Secrets | `.env` gitignored and never committed (verified); `.env.example` has no values (good). The same `.env` is loaded into **both** containers, so the frontend gets `GROQ_API_KEY` it doesn't need. Log file contains the Groq organisation identifier. No secrets manager in the VM path. | Least-privilege env per process; secrets manager; scrub logs | High | M10 |
| Passwords | No passwords exist | argon2 hashing, reset flows, breach-safe practices | High | M7 |
| Authentication | Shared static key; Streamlit public with no login; GCP runbook opens port 8501 to the internet over plain HTTP | Per-user sessions; HTTPS only; internal tools not public | **Critical** | M7, M10 |
| Authorisation | None | RBAC + tenant checks | **Critical** | M8, M9 |
| Input validation | Good body validation; unbounded chat-history content; uploads parsed only in Streamlit | Server-side validation of uploads and history | Medium | M5, M6 |
| SQL injection | **No SQL is executed today**, so there's no current risk | Use SQLAlchemy parameter binding only; never f-string SQL | Low now | M4 |
| Deserialisation | Pickle for classical models; LangChain docstore loaded with `allow_dangerous_deserialization=True`. Safe only because the files are self-built. | Never load pickles from untrusted sources; remove pickle from runtime path (pgvector) | Medium | M12, M16 |
| Prompt injection | RAG rule 2 treats reviews as data (good). The item-analysis prompt has no equivalent rule. Customer-uploaded text will be untrusted. | Injection rule in every prompt; tests with malicious feedback | High | M3, M16 |
| API security | No rate limits; CORS `*` by default; OpenAPI public; errors leak exception text via RAG | Rate limits; CORS locked to app origin; generic errors | High | M6, M7, M16 |
| Sensitive data | Public research datasets today. Customer feedback will contain personal data. Question text logged; text sent to Groq. | No text in logs; DPA with LLM provider; retention/deletion | High | M12, M16 |
| CORS | `ALLOWED_ORIGINS` configurable, `*` default; credentials not allowed (correct with `*`) | Explicit origins in every deployed environment | Medium | M10 |
| Environment configuration | Production boot guard refuses the dev key (good); compose sets `ENVIRONMENT=production` even locally | Explicit dev/staging/prod settings | Low | M2, M10 |
| Containers | Both images run as root; build tools (`build-essential`, `git`) in the runtime image; Streamlit XSRF protection disabled | Non-root user; multi-stage builds; revisit XSRF when internal tool is protected | Medium | M10 |
| Frontend caching | `st.cache_data` is shared across all Streamlit users | Never serve tenant data through a shared cache | Low now (Critical if Streamlit ever shows tenant data) | M11 |
| Dependencies | Pinned versions (good); no vulnerability or secret scanning | Scanning in CI | Medium | M16 |

## 6. ML/AI

| Aspect | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| Model loading | Lazy with locks (good). First request pays load time; `import backend.main` alone took 48.9 s. MiniLM loaded **three times**; two identical-size FAISS indexes in memory; Hugging Face models fetched by name at runtime (inferred: no cache volume). | Load once per worker; artefacts versioned and local; heavy models only in worker | Medium | M3, M10 |
| Inference performance | CPU, one text at a time everywhere, including the batch endpoint; `/analyse` runs DistilBERT + DeBERTa + FAISS + LLM per request; single-turn RAG runs retrieval twice (4 vector searches) | Batched inference in a worker; measured throughput | High | M3, M5 |
| Reproducibility | Fixed seeds, pinned versions, script-generated results (excellent). But: `venv/` lacks `ragas` and `emoji`; no script writes `reviews.faiss`; `data/raw/amazon/` has only 1 of 3 source files; notebook 3 saves to an HPC path; MLflow registry paths point to the Desktop folder; default LLM changed after experiments. | Reproducible training script, environment lock, artefact manifest | Medium | M2, M3 |
| Model versioning | None at runtime: outputs don't say which model/version produced them; MLflow registry not used by the app | Model manifest; versions stored with results | High | M3 |
| **Preprocessing parity** | DistilBERT trained and evaluated on `cleaned_text`; the API sends raw text. Classical `_clean` silently skips emoji conversion because `emoji` isn't installed. | Decide, test and document one inference preprocessing path; benchmark it | High | M3 |
| Failure handling | Graceful fallbacks keep the demo alive but hide failures (RoBERTa fallback, VADER, "General Feedback", empty analysis, "unknown") | Explicit per-item status; alerts on fallback rates | High | M3, M17 |
| Prompt management | Prompts in code, unversioned; an older duplicate in `nlp/langchain_summariser.py`; platform names hard-coded in prompts and guards | Versioned prompts; generic wording; prompt tests | Medium | M3 |
| API failures | 30 s timeout; RAG retries rate limits twice; item analysis doesn't retry; retry logic depends on parsing Groq error text | Provider-agnostic adapter with retry/back-off and circuit-breaking behaviour | Medium | M3 |
| Latency | Reported RAG mean 1,583 ms (n = 5) plus a scope-guard call; end-to-end `/analyse` not measured; cold start tens of seconds | Latency budgets; background processing; measurement | Medium | M3, M17 |
| Cost | One LLM call per analysed review; 2–3 per question; no token accounting; free-tier daily limit already hit | Aggregate insights instead of per-item calls; usage records; per-org budgets | High | M12, M14 |
| Monitoring | None: no prediction distributions, unclassified-rate tracking, confidence drift or LLM metrics | Model and LLM monitoring | Medium | M17 |
| Evaluation in the loop | Outstanding offline evaluation, but not automated as a regression gate; generation evaluated on n = 5 with a same-family judge | Benchmark sample in tests; larger labelled retrieval/QA sets | Medium | M3, M12 |
| Domain fit | Trained on product/local-business/airline reviews in English; 70% Yelp; categories fitted to that corpus; positives get complaint categories | Measure on customer-like feedback; sentiment gate; editable categories | High | M3, M12 |

## 7. Testing

| Type | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| Unit tests | 0 pytest tests. 2 self-checking scripts with stubs, both passing (`test_categoriser_shortlist.py`, `test_merge_topics.py`) | Real pytest suite, offline, fast | High | M1 |
| Integration tests | Manual scripts needing the 1 GB index and Groq (`test_rag.py` interactive; `evaluate/verify_*.py`) | Integration tests against real Postgres (later) and stubbed providers | Medium | M4 |
| API tests | None | `TestClient` contract + auth tests; later tenant tests | High | M1, M8 |
| ML tests | Offline evaluation scripts; no automated quality gate | Benchmark regression tests with metric floors | Medium | M3 |
| Frontend tests | None | Component/E2E tests for the React app | Medium | M11 |
| End-to-end tests | None | Playwright journey: sign-up → import → dashboard | Medium | M11, M16 |
| CI | Lint (`E9,F` only) + Docker builds; **no tests** | Test job required for merge | High | M1 |

## 8. Deployment

| Aspect | Current state (evidence) | Gap | Severity | Milestone |
|---|---|---|---|---|
| Docker | Two images with healthchecks and `$PORT`; one worker by design; root user; build tools in runtime image; this working copy lacks `.dockerignore` (≈15 GB context) and `.streamlit/` (frontend build fails here) | Consolidated repo; one image with web/worker commands; multi-stage; non-root | Medium | M1, M10 |
| Environment configuration | One `.env` shared by both containers; `ENVIRONMENT=production` hard-coded in compose | Per-environment config; secrets manager | Medium | M10 |
| CI/CD | CI lint + build; no CD, no registry push, no environments | Tests → build → push → migrate → deploy staging | High | M10 |
| Database deployment | Not applicable (no DB) | Managed PostgreSQL + pgvector; migrations in pipeline | High | M4, M10 |
| Cloud readiness | GCP runbook: VM (e2-standard-4, 16 GB) with compose and artefacts from a bucket; plain HTTP; Cloud Run option documented | HTTPS, managed DB/storage, right-sized after data moves out of memory | High | M10 |
| Monitoring | `docker compose logs` only | Error tracking, metrics, uptime, alerts | Medium | M17 |
| Backups | None (artefacts in a bucket is the closest thing) | Automated DB backups + rehearsed restore | High | M10, M16 |
| Staging | None | Staging before customer data | High | M10 |
| Scaling | Scale by containers, each loading ≈3 GB of artefacts plus models | Stateless web; worker scaled by queue depth | Low (now) | M10, M17 |

---

## Top 10 production gaps

Ranked by how much each one blocks FeedbackIQ from serving real customers.

| Rank | Gap | Why it ranks here | Milestone |
|---|---|---|---|
| 1 | **No database or persistence.** Everything is read-only files; nothing a user submits is stored. | A SaaS product *is* its customers' data over time. | M4 |
| 2 | **The ML engine never runs over a customer's dataset.** Analysis happens one review at a time inside a request, nothing is saved, and dashboard sentiment comes from star ratings rather than models. | This is the gap between "demo" and "product". | M3, M5 |
| 3 | **No user authentication.** One shared API key; the Streamlit app is open to anyone who can reach it. | Blocks any real deployment with customer data. | M7 |
| 4 | **No multi-tenancy or data isolation.** One global corpus and index. | A cross-customer leak is the worst possible B2B failure. | M8 |
| 5 | **No authorisation or roles.** | Teams need owners, analysts and viewers. | M9 |
| 6 | **No automated tests and no test gate in CI.** 0 pytest tests. | Every later milestone restructures working code. | M1 |
| 7 | **Not hardened for internet exposure.** Plain HTTP and a public port in the runbook, CORS `*`, no rate limiting, exception text returned by RAG, root containers. | Required before anyone else's data arrives. | M10, M16 |
| 8 | **ML operations gaps.** No batching, no model versions on outputs, a train/serve preprocessing mismatch for the production model, silent fallbacks, no quality gate, domain fit unmeasured. | Customers will trust these numbers; they must be explainable and stable. | M3 |
| 9 | **No LLM cost and reliability controls.** Per-item LLM calls, 2–3 calls per question, no token accounting or budgets; the provider's daily limit was already hit in development. | Unbounded cost and outages tied to one provider. | M3, M12, M14 |
| 10 | **Operability and repository hygiene.** Two diverging copies (the working one without git); no structured logging, monitoring, staging, CD or backups; health always "ok"; the search index can't be rebuilt from code. | You can't fix what you can't see, reproduce or roll back. | M1, M10, M17 |
