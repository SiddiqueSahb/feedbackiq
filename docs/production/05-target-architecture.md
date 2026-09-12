# 05 — Target Architecture

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · **Proposal only — nothing implemented.**
> Previous: [04 — Production gaps](04-production-gaps.md) · Next: [06 — SaaS data model](06-saas-data-model.md)

---

## 1. The principle: a modular monolith

**One codebase, one database, one container image, two process types (web and worker).**
Modules are separated by folders and import rules, not by network calls.

Why this fits FeedbackIQ:

- You are one developer who is also learning. Every network boundary you add is another thing to deploy, secure, version and debug.
- The existing code already has the right *shape*: `routes → services → nlp/rag`. We are extending a structure that works, not replacing it.
- The expensive part (model inference) needs a separate **process**, not a separate **service**. The same code started with a different command gives you that.

---

## 2. The big picture

```text
                           FeedbackIQ  (one repository · one image)

   Customer users                                    Internal team (you)
        │                                                   │
   React web app (M11)                               Streamlit app (today's frontend/,
        │  HTTPS + session cookie                    kept as internal demo / model QA)
        │                                                   │  HTTPS + internal API key
        └──────────────────────────┬────────────────────────┘
                                   ▼
 ┌──────────────────────────── web process (uvicorn) ─────────────────────────────┐
 │  api/v1     HTTP only: parse request → check auth → call a service → respond   │
 │  auth       users · sessions · org API keys · memberships · roles              │
 │  services   feedback · imports · analytics · insights · search & Q&A ·         │
 │             reports · usage · billing                                          │
 │  db         SQLAlchemy models and queries — always scoped by organisation_id   │
 └───────────────┬───────────────────────────────────────────────────┬────────────┘
                 │ enqueue work (insert a row into `jobs`)            │ light engine calls
                 ▼                                                    │ (embed a question,
 ┌──────── worker process (same code) ────────┐                       │  grounded answer)
 │ claims a job → runs the engine on batches  │                       ▼
 │ → writes results → records usage           │──────▶ ┌──────────── engine ─────────────┐
 └──────────────────┬─────────────────────────┘        │ sentiment · categorisation ·    │
                    │                                  │ keywords · embeddings ·         │
                    │                                  │ grounded Q&A · insights · llm   │
                    │                                  │ NO HTTP · NO database · NO      │
                    │                                  │ tenants — plain Python in/out   │
                    │                                  └───────────────┬─────────────────┘
                    ▼                                                  │
   ┌────────────────┴───────────────┬─────────────────────┐            │
 PostgreSQL + pgvector        Object storage          LLM provider ◀────┘
 (tenant data, jobs,          (uploaded files,        (Groq today, behind
  results, embeddings)         reports, model files)   one adapter module)
```

---

## 3. Why each component exists

| Component | Why it exists | Built from today's code | Milestone |
|---|---|---|---|
| **React web app** | Customers need sign-up/login, organisation switching, imports with progress, and dashboards over *their* data. Streamlit has no real multi-user auth model. | The Streamlit pages are the functional spec: Dashboard KPIs/charts, Analyse tabs, Search cards, Chat with sources. | M11 |
| **Streamlit internal app** | Still valuable for demos, model QA and research views. It costs nothing to keep. | `frontend/` | kept |
| **REST API `/api/v1`** | One stable contract for the web app, the internal app and future integrations. | `backend/api/routes/*` (thin-route pattern kept) | M6 |
| **Auth / RBAC module** | Knows *who* is calling and *what they may do* in *which* organisation. | `backend/api/deps.py` (becomes per-org API keys) | M7–M9 |
| **Application services** | Business rules: "import this CSV into organisation X", "sentiment trend for X over 90 days". Callable from API and worker alike. | `backend/services/*` | M5–M14 |
| **DB layer** | Tables, migrations and tenant-scoped queries in one place. | new | M4 |
| **Worker process** | Analysing thousands of feedback items on CPU takes minutes. That must not happen inside an HTTP request (timeouts, blocked web process, work lost on restart). | new; reuses the engine | M5 |
| **Engine** | The dissertation's value as a *library*: list of texts (+ the organisation's categories) in → typed results + model versions out. | `nlp/`, `rag/`, `sentiment_service.analyse_review` | M3 |
| **PostgreSQL + pgvector** | One store for relational data *and* embeddings. Tenant filter and vector search happen in one SQL query; there is no second database to run. | replaces parquet + two FAISS indexes + pickle docstore | M4, M12 |
| **Object storage** | Raw uploaded files, generated reports, model artefacts. Large blobs stay out of the database. | replaces host-mounted `data/`, `models/` | M5, M10 |
| **LLM adapter** | One module that sets timeouts, retries rate limits, recovers structured output, counts tokens and names the provider/model. Switching model becomes a config change. | `rag/pipeline._get_llm`, `_invoke_with_retry`, `nlp/summariser._get_chain`, `_recover_from_failed_tool_call` | M3 |

---

## 4. Dependency rules — the "modular" in modular monolith

```text
   api ────▶ services ────▶ db
                │
                └─────────▶ engine ────▶ llm adapter
   worker ─▶ services   (the same functions the API uses)

   NOT allowed:
     engine  ─▶ db, api, services      engine stays reusable and testable with plain pytest
     api     ─▶ engine directly        routes never call models
     api     ─▶ db directly            queries live in services / db
     research/ ─▶ api, db              research code only uses the engine
```

The current code already mostly follows `routes → services → nlp/rag`. We keep that and add `db` and `worker`.

**How the engine stays tenant-unaware.** The engine never queries the database. When grounded Q&A needs evidence, the service passes in a function that already filters by organisation:

```python
# services/ask.py  (sketch — decided in M3/M12)
def ask(session, org_id, question):
    retrieve = lambda q: search.similar_feedback(session, org_id, q)   # tenant filter lives here
    return engine.grounded_qa.answer(question, retrieve=retrieve)      # engine just calls it
```

---

## 5. What should remain together

- **Engine, API and worker in one repository and one image.** They share types and model code, and deploying them separately now would only add version mismatches.
- **One PostgreSQL database for all tenants** — shared tables, with `organisation_id` on every tenant-owned row (details in [06](06-saas-data-model.md)).
- **Feedback and its embeddings in the same database** (pgvector), so deleting a customer's feedback also deletes its vectors in one transaction.
- **Research code in the same repository** (under `research/`). It is how you will evaluate model changes before customers see them.

## 6. What should be separated

| Separate | Why |
|---|---|
| Engine ↔ HTTP & database (package boundary) | Test with plain pytest; reuse in worker, scripts and notebooks. |
| Worker process ↔ web process (same code, different start command) | The heavy models (DeBERTa-v3 NLI, DistilBERT) only need loading in the worker, so the web process stays small and responsive. |
| Runtime dependencies ↔ research dependencies | The web image shouldn't ship `ragas`, `mlflow`, `bertopic`, `wordcloud`, `matplotlib`. |
| Customer UI ↔ internal UI | Different audiences, different security requirements. |
| Configuration & secrets ↔ code | Environment variables (pydantic-settings, already used) and a secrets manager in deployment. |
| Runtime artefacts ↔ research artefacts | ≈3 GB is served today; ≈13 GB of raw data and BERTopic models never should be. |

---

## 7. What NOT to introduce yet

| Not yet | Why not now | Revisit when… |
|---|---|---|
| Microservices | One developer; network calls add failure modes and deploy coordination. | A component has very different scaling needs *and* someone to own it. |
| Kubernetes | Large operational overhead for two processes. | You run many services, or a managed container platform can't meet your scaling needs. |
| Kafka, event bus, message broker | A PostgreSQL job table covers background work at this scale. | Sustained high-volume streaming ingestion is measured. |
| Celery + Redis | Extra infrastructure. `SELECT … FOR UPDATE SKIP LOCKED` on a `jobs` table is ~100 lines of readable code. | Measured job volume or latency exceeds what the table handles. |
| Dedicated vector DB (Pinecone, Weaviate, Qdrant) | pgvector handles millions of 384-dimension vectors with a tenant filter, in the database you already run. | Per-tenant vector counts or latency measured beyond pgvector. |
| Analytics warehouse (BigQuery, ClickHouse) | SQL aggregates with good indexes are enough at early volumes. | Dashboards are slow on real, measured volumes. |
| GraphQL | REST is simpler to learn, cache and secure. | Probably never for this product. |
| Database-per-tenant or schema-per-tenant | Every migration runs N times; operations get harder. | An enterprise contract requires physical isolation. |
| GPU serving / model servers (Triton, TorchServe) | Measure CPU batch throughput in the worker first. | Worker throughput is the measured bottleneck. |
| Automated retraining pipeline | No customer-labelled data exists yet. | Human corrections accumulate (M12 starts collecting them). |
| Self-built billing or auth cryptography | Solved problems with severe failure modes. | Never — use Stripe and well-known libraries. |
| Multi-region | — | Customers require data residency. |

---

## 8. How the dissertation code maps into the new architecture

| Today | Target | Class ([02](02-code-classification.md)) | Milestone |
|---|---|---|---|
| `nlp/sentiment.py::FineTunedSentiment` + `models/distilbert-finetuned-final/` | `engine/sentiment.py` (batched, versioned) | A1 / B4 | M3 |
| `nlp/categoriser.py` | `engine/categorisation.py` — categories passed in, sentiment gate | A2 / B6 | M3 |
| `data/processed/complaint_categories_all_negative.json` | Seed rows for the default category set (reviewed for generality first) | A2 | M3–M4 |
| `nlp/embedding_service.py` | `engine/embeddings.py` (encode only) + `services/search.py` (pgvector query) | B7 / D3 | M3, M12 |
| `rag/pipeline.py` | `engine/grounded_qa.py` (retrieval injected) + `services/ask.py` (tenant-scoped) | A3 / B8 | M3, M12 |
| `rag/prompts.py` | `engine/prompts/` with version identifiers | A4 | M3 |
| `nlp/summariser.py` | `engine/insights.py` — single item (existing) + aggregate summaries (new) | A5 | M3, M12 |
| `analytics_service.normalise_phrase` + `scripts/precompute_keywords.py` | `engine/keywords.py`, run by the worker | A11 | M3, M12 |
| `backend/services/sentiment_service.py::analyse_review` | `engine.analyse_batch()` / `analyse_one()` | B9 | M3 |
| `backend/services/analytics_service.py` | `services/analytics.py` using SQL | B10 | M6 |
| `backend/api/deps.py` | `auth/api_keys.py` (hashed per-org keys) | A7 | M9 |
| `backend/models/schemas.py` | `api/v1/schemas/` | A6 | M6 |
| `backend/main.py` | `api/main.py` | A8 | M2 |
| `config.py`, `logger.py` | `core/config.py`, `core/logging.py` | B2, B3 | M2 |
| `frontend/` | `internal_app/` (Streamlit) | A12 | M2 |
| `evaluate/`, `notebooks/`, research `scripts/`, `nlp/tf_idf_embedding.py`, classical/VADER/RoBERTa comparison | `research/` (kept runnable) | C1–C7 | M2 |
| `scripts/discover_categories.py` | `research/` now; later a worker job "suggest categories" | B18 | after M12 |
| `docker-compose.yml` | web + worker + postgres/pgvector (+ internal app) | B13 | M4, M10 |
| `data/processed/reviews_unified.parquet` | Benchmark sample + demo organisation seed | A16 | M3–M4 |

---

## 9. Proposed folder layout (final names decided in Milestone 2)

```text
reviewAnalytics/                     ← repository root
├── pyproject.toml                   ← makes `feedbackiq` importable: no more sys.path edits
├── feedbackiq/
│   ├── core/        config.py · logging.py · errors.py
│   ├── engine/      sentiment.py · categorisation.py · keywords.py · embeddings.py
│   │                grounded_qa.py · insights.py · llm.py · prompts/ · types.py
│   ├── db/          models.py · session.py
│   ├── services/    feedback.py · imports.py · analytics.py · search.py · insights.py …
│   ├── auth/        (M7+)
│   ├── api/         main.py · deps.py · v1/
│   └── worker/      main.py · jobs.py
├── migrations/      Alembic
├── internal_app/    Streamlit (today's frontend/)
├── web/             React app (M11)
├── research/        evaluate/ · notebooks/ · scripts/  (reproduce dissertation results)
├── tests/           unit/ · api/ · integration/ · benchmark/
└── docs/            production/ · research/
```

During the move, thin re-export files (for example an `nlp/sentiment.py` that imports from `feedbackiq.engine.sentiment`) can keep `evaluate/` scripts working until they are updated. The boundaries matter more than the exact names.

---

## 10. Key flows in the target system

### Importing feedback

```text
User uploads CSV in the web app
  → POST /api/v1/orgs/{org_id}/imports                 auth: member, role ≥ analyst
  → services.imports  (one database transaction)
       validate columns · store file at orgs/{org_id}/imports/{id}.csv
       insert import_batch + feedback rows (de-duplicated) · insert job
  ← 202 Accepted + import id

Worker
  → claims job (FOR UPDATE SKIP LOCKED)
  → engine.analyse_batch(texts, org_categories)  in batches (e.g. 64)
  → writes analysis_results + embeddings, records usage, marks job done

Web app polls GET …/imports/{id} → progress → dashboard shows new data
```

### Dashboard

```text
GET /api/v1/orgs/{org_id}/analytics/sentiment-trend?from=…&to=…
  → services.analytics:
      SELECT date_trunc('week', f.feedback_at), r.sentiment_label, count(*)
      FROM feedback f JOIN analysis_results r ON …
      WHERE f.organisation_id = :org AND r.is_current
      GROUP BY 1, 2
  → uses stored MODEL PREDICTIONS (today's dashboard uses star-rating labels)
```

### Asking a question (today's RAG, tenant-scoped)

```text
POST /api/v1/orgs/{org_id}/ask {question}
  → usage limit + rate limit check
  → engine.embeddings.encode(question)
  → pgvector: WHERE organisation_id = :org ORDER BY embedding <=> :q LIMIT 20
  → same rules as today: similarity ≥ 0.35 ∩ MMR(k=5) · refuse when nothing passes
  → engine.grounded_qa.answer(question, evidence) → LLM adapter
  ← answer + evidence feedback IDs  (the traceability property from RQ4 is kept)
```

---

## 11. Cross-cutting decisions

| Topic | Decision |
|---|---|
| IDs | UUIDs for all public identifiers (not guessable, no enumeration). |
| Time | `timestamptz`, stored in UTC. |
| Errors | One JSON shape, e.g. `{"error": {"code": "import_not_found", "message": "…"}}`. Never exception text to clients. |
| Configuration | Environment variables via pydantic-settings (already used); secrets from a secrets manager in deployment. |
| Logging | JSON to stdout with `request_id` and `organisation_id`. **Never log feedback text.** |
| LLM usage | Every call goes through the adapter: timeout, rate-limit retry, token counts written to usage, prompt version recorded. Feedback text is always untrusted input (today's RAG rule 2, applied everywhere). No per-item LLM call in bulk analysis. |
| Models | Loaded once per process (today's lazy + lock pattern). Model name + version stored with every result. |
| Taxonomy | Each organisation gets a copy of a default category set and can edit it. This is possible *because* categorisation is zero-shot: categories are text, not trained classes. |
| Language | English only at launch. The corpus, preprocessing (non-ASCII stripped) and models are English. State this to customers. |

---

## 12. Initial deployment shape

```text
┌──────────────── VM or managed container platform ────────────────┐
│   web     (uvicorn, 1 worker)          ┐                          │
│   worker  (python -m feedbackiq.worker) ├─ same image              │
│   internal Streamlit (optional, not public)                        │
└──────────────────────────────┬────────────────────────────────────┘
                               │
   managed PostgreSQL (pgvector enabled, automated backups)
   object storage bucket
   secrets manager
   HTTPS load balancer / reverse proxy
```

**On memory.** The current backend is sized for a 16 GB VM ("Don't go smaller than this; the app will OOM", `GCP_DEPLOY_RUNBOOK.md`). Most of that is data held in process memory: two parquet copies, two 987 MB FAISS indexes, the 430 MB docstore and three MiniLM instances. Once data lives in PostgreSQL, model weights dominate instead. Measure in M3 and M10 rather than guessing.
