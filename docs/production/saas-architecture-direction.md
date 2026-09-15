# SaaS Architecture Direction

> Written during [Milestone 5A](milestone-05a.md) · planning document, not an
> implementation record. Nothing here is built yet beyond what Milestones 1–5A delivered.

FeedbackIQ is being built as a **multi-tenant B2B SaaS product for customer-feedback
intelligence**. This document records which parts of the dissertation are product assets,
which are research artefacts that must not dictate the architecture, and what the target
boundaries are.

The governing principle, from Milestone 5A onward:

> **SaaS product requirements take priority over dissertation compatibility.** The
> dissertation is valuable source material and validated research. It is not the
> architectural specification.

---

## A. What should be retained

Components that exist in this repository today and are genuine product assets.

| Component | Where it lives | Why it is a product asset |
|---|---|---|
| **Fine-tuned sentiment model** | `models/distilbert-finetuned-final/`, served via `engine/sentiment.py` | The measured core capability (macro-F1 0.8137 on the production benchmark at n=600). Batched inference, with a VADER fallback when the primary model fails. |
| **Zero-shot categorisation** | `engine/categorisation.py` | Embedding shortlist → DeBERTa NLI rerank → 0.35 threshold. Assigns categories **without per-customer training**, which is what makes onboarding a new tenant possible at all. |
| **The "Unclassified / Emerging Complaint" outcome** | `engine/categorisation.py` | Refusing to force a weak match is a product feature: the unclassified share is the emerging-issue signal, not a defect to hide. |
| **The canonical 24-category taxonomy** | `core/default_categories.json` + `core/taxonomy.py` | Versioned product reference data. Gives every new tenant a working taxonomy on day one, before they define their own. |
| **The sentiment gate** | `engine/pipeline.py` | Positive feedback gets no complaint category. Prevents the product from inventing complaints. |
| **Evidence-grounded Q&A rules** | `engine/grounded_qa.py`, `rag/` | Similarity threshold, MMR diversity, evidence IDs, explicit refusal when evidence is insufficient, scope guard. Refusing to answer ungrounded questions is a trust property a B2B buyer cares about. |
| **Insights with evidence IDs** | `engine/insights.py` | Every LLM claim traceable to the feedback behind it (the dissertation's RQ4). |
| **LLM adapter** | `engine/llm.py` | Timeouts, rate-limit retries, token accounting, structured-output recovery. Cost control and metering depend on it. |
| **Typed engine contracts + version manifest** | `engine/types.py`, `engine/versions.py` | Frozen dataclasses in, typed results out, with model/prompt/taxonomy versions attached. What makes results explainable and storable. |
| **Evaluation methodology** | `evaluate/`, `tests/benchmark/test_production_benchmark.py` | The habit of measuring before and after, with an explicit metric floor, carried into CI. The methodology is an asset even where the scripts are research-shaped. |
| **PostgreSQL schema, migrations, persistence** | `db/`, `migrations/` | Tenant-ready from Milestone 4: `organisation_id` everywhere, composite foreign keys, Alembic. |

---

## B. What should be treated as legacy / research code

These stay in the repository (they are the dissertation's evidence and remain runnable),
but they must **not** shape the SaaS architecture.

| Component | Where | Classification |
|---|---|---|
| Dissertation notebooks | `notebooks/` (gitignored) | Research record. Never edited, never imported by the product. |
| Discovery, training and evaluation scripts | `scripts/` (12 files), `evaluate/` (13 files) | Research workflows. One-off, corpus-wide, run by hand. Excluded from the images by `.dockerignore`. |
| The research corpus | `data/processed/reviews_unified.parquet` (427 MB, 642,692 reviews, ~70% Yelp) | Research input. Not a tenant's data; never migrated into PostgreSQL. |
| FAISS artefacts and pickle docstore | `data/embeddings/` (~2 GB) | Research retrieval index over the *global* corpus. |
| `CorpusRetriever` over one shared index | `engine/retrieval.py` | **Global corpus assumption.** Correct for the dissertation, wrong for multi-tenancy: retrieval must be scoped to one organisation's feedback. Already behind the injectable `Retriever` protocol, so the replacement is a substitution, not a rewrite. |
| Classical baselines (TF-IDF + Naive Bayes / logistic regression), pre-trained RoBERTa | `models/classical/`, `nlp/sentiment.py` | Comparison assets for the dissertation's five-model study. Reachable through the API's model-comparison endpoints; not the product path. (VADER is the one exception — it is a deliberate runtime fallback.) |
| Streamlit application | `frontend/` | **Internal tool.** Useful as a functional specification and for demos; it is not the customer-facing product. |
| `platform` = amazon / yelp / twitter_airline | corpus metadata, API `platform` field (a known strict `xfail`), RAG scope guard wording | Research dataset shape. The product concept is `data_sources` per organisation (Milestone 4). |
| Rating-derived sentiment labels | corpus `sentiment_label` column | Research label, not a prediction. Deliberately not stored on `feedback`. |
| Root-level manual check scripts | `test_pipeline.py`, `tests/*.py` (7 files) | Historical manual checks. Not collected by `pytest` (`testpaths`, `collect_ignore`). |
| Dissertation deliverables | `RESULTS.md`, `SUBMISSION.md`, the two PDFs (gitignored) | Historical record. |

---

## C. Target SaaS boundaries

### Request path

```text
Web application  (React later; Streamlit is an internal tool today)
      ↓
Authentication   (not built — sessions, password hashing, CSRF)
      ↓
Organisation / tenant scope   (not built — membership + enforced organisation_id)
      ↓
Application services   (services/ — orchestration, no ML, no SQL of its own)
      ↓
Analytics engine   (engine/ — typed in, typed out; no HTTP, no SQL)
      ↓
PostgreSQL   (db/ — the only place SQL exists)
```

### Data path

```text
Upload  →  Ingestion  →  Background job  →  Analysis  →  Stored results
                                                              ↓
                                    Dashboard / API / RAG / Insights / Reports
```

Today the product stops at "Analysis → Stored results", and only when called directly:
`import_batches`, `feedback`, `analysis_runs`, `analysis_results`, `insights` and `jobs`
exist and are tested, but no HTTP route reads or writes them, and nothing claims a `jobs`
row.

### What each boundary is for

| Boundary | Rule | Enforced by |
|---|---|---|
| API → services | Routes validate and serialise; they do not analyse | convention |
| services → engine | Orchestration only; no ML in services | convention |
| engine → nothing | No FastAPI, no SQLAlchemy, no `feedbackiq.db`, no HTTP | `tests/integration/test_engine_db_boundary.py` (AST scan, fresh-interpreter import check, runs with no database) |
| db → PostgreSQL | All SQL lives here; plain functions, not repositories | `tests/integration/` |
| taxonomy | One canonical versioned source, no fallback | `tests/unit/test_default_taxonomy.py` |

The engine boundary is **useful, not sacred**. If a SaaS requirement shows it should move —
tenant-scoped retrieval is the likely first case — it should be moved deliberately, with the
reason written down, not defended because it was drawn in Milestone 3.

---

## D. Product vs research separation (Milestone 5A decision)

The conceptual boundary:

```text
Product                      Research / experimentation
-------                      --------------------------
src/feedbackiq/              evaluate/     evaluation and RQ scripts
migrations/                  scripts/      training, discovery, index building
backend/  frontend/          notebooks/    dissertation notebooks
tests/unit tests/api         data/  models/  mlruns/   corpora, weights, runs
tests/integration            tests/*.py    manual check scripts
```

**No `research/` directory was created and no code was moved.** The repository's actual
research homes are already `evaluate/`, `scripts/` and `notebooks/`, and the separation is
enforced where it matters:

- `.dockerignore` keeps `scripts/`, `evaluate/`, `notebooks/`, `data/`, `models/` and
  `tests/` out of both images.
- `pyproject.toml` keeps research dependencies in the `research` extra, out of the runtime set.
- `pytest` `testpaths` collects only `tests/unit`, `tests/api` and `tests/benchmark`.
- The product package no longer reads anything from `data/` to categorise (Milestone 5A).

Moving `evaluate/` and `scripts/` under a new `research/` package would break the
dissertation's own entry points and the tests that import `scripts/discover_categories.py`,
for a cosmetic gain. Classification was the cheaper and safer half of the job; relocation can
happen later if it ever buys something concrete.

**The one direction that is not allowed:** product code importing from `evaluate/` or
`scripts/`, or depending on a file under `data/`. Research code importing the product package
is fine and expected (17 files do).

---

## E. Intentionally replaced later

| Today | Replaced by | When |
|---|---|---|
| Streamlit internal app | React/TypeScript customer app | roadmap M8 (foundation), M11 (remaining pages) |
| Global FAISS index over the research corpus | Tenant-scoped retrieval (pgvector or a vector service), decided on evidence | roadmap M12 |
| Synchronous per-request analysis | `jobs` table + worker | roadmap M5/M5B |
| Local file-based datasets | Customer-uploaded feedback in PostgreSQL + object storage | roadmap M5 |
| Single shared API key | Real authentication, then membership and roles | roadmap M7–M9 |
| `platform` filter | `data_sources` per organisation | with ingestion |
| Corpus-wide analytics in pandas (`services/analytics_service.py`) | SQL aggregates per organisation | roadmap M6 |

---

## F. Deliberately not introduced

Authentication, JWT/session infrastructure, RBAC, organisation middleware, billing, Stripe,
React/Next.js, background workers, Redis, Kubernetes, microservices, event buses, cloud
deployment, pgvector, observability platforms.

Each belongs to a later milestone, when a concrete requirement justifies it. The audit's
reasoning ([05 §7](05-target-architecture.md#7-what-not-to-introduce-yet)) still holds: the
goal is the simplest architecture that can *evolve* into a secure multi-tenant SaaS, not one
that imitates a mature one.
