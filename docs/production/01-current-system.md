# 01 — Current System

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · **Audit only — no project files were changed.**
> Next: [02 — Code classification](02-code-classification.md)

**How this was produced.**
- **Read in full:** every file in `backend/`, `frontend/`, `nlp/`, `rag/` and `tests/`, plus the root Python, config, Docker and deployment files.
- **Read as structure:** `scripts/`, `evaluate/` and `notebooks/` — docstrings, function lists and key operations (training columns, splits, hyperparameters).
- **Read-only runtime checks:**
  - imported `backend.main` without starting a server
  - ran the two self-checking test scripts
  - read the parquet schema and composition, the taxonomy file and model metadata
  - compared this folder with the original git repository on the Desktop

Nothing was installed or modified. Anything *inferred* rather than verified is labelled.

---

## 1. Project purpose

FeedbackIQ is an MSc Data Science dissertation project (University of Surrey). It asks: **which method should you use to analyse customer feedback?** It compares a lexicon, classical ML, a pretrained transformer, a fine-tuned transformer and retrieval-augmented generation, stage by stage, on **642,692 English reviews** from Amazon products, Yelp businesses and US airline tweets.

The repository has two halves.

1. **A research pipeline** (offline scripts and notebooks). It builds a unified corpus, trains and evaluates sentiment models, discovers a 24-category complaint taxonomy, builds vector indexes and evaluates RAG. Every reported figure is written to `data/results/`.
2. **A demo application** (FastAPI backend + Streamlit frontend) that lets an analyst:
   - view corpus-level dashboards (sentiment, ratings, keywords, monthly volume)
   - run **one** review through the full pipeline: sentiment → complaint category → similar reviews → LLM business analysis
   - search the corpus by meaning
   - ask questions answered only from retrieved reviews, with sources shown
   - score up to 200 reviews from a CSV
   - view stored evaluation results

**What it does not do today:** store anything a user submits, analyse a company's own dataset end to end, or serve more than one customer. The application is a window onto a fixed research corpus.

---

## 2. Main user flows

There is no single flow. There is an **offline build** that produces files, and **six online flows** that read them.

### 2.1 Offline build (run by hand)

```text
data/raw/amazon/*.jsonl.gz ──┐
data/raw/Yelp/*.json ────────┼──▶ notebook 1 / scripts/preprocess.py
data/raw/Twitter/Tweets.csv ─┘      title+text · clean_text · rating→label · per-class balance
                                    · dedupe on cleaned_text · drop short texts
                                              │
                                              ▼
                        data/processed/reviews_unified.parquet   (642,692 rows)
      ┌────────────────┬──────────────────┬──┴──────────────┬──────────────────────┬──────────────────┐
      ▼                ▼                  ▼                 ▼                      ▼                  ▼
 notebook 3       train_classical    build_index        discover_categories   precompute_keywords  evaluate/*,
 DistilBERT       NB + LogReg        MiniLM embeddings  BERTopic per platform  spaCy noun chunks    scripts/evaluate_*
 fine-tuning      (GridSearchCV,     → .npy +           → merge 121 topics     + filters
 (university HPC) MLflow)            LangChain index    → LLM names → 24 cats
      │                │                  │                 │                      │                  │
      ▼                ▼                  ▼                 ▼                      ▼                  ▼
 models/          models/classical   data/embeddings/   complaint_categories   data/results/        data/results/
 distilbert-      *.pkl              review_embeddings  _all_negative.json     keywords/            *.csv|json|png
 finetuned-final                     .npy, langchain_                          top_keywords.json
                                     index/
                                     (reviews.faiss: no script writes it — see §5)
```

### 2.2 Flow A — Analyse one review (the full pipeline)

```text
Review text  (Analyse page)
   │  POST /api/sentiment/analyse   {text, platform, model}
   ▼
Validation ── Pydantic: 3–5,000 chars, control characters stripped  (`platform` is accepted but never used)
   ▼
① Sentiment ── chosen model (default: fine-tuned DistilBERT, raw text, ≤128 tokens)
   │            → label, confidence, scores{positive, neutral, negative}
   │            on any exception → VADER
   ▼
② Complaint category ── first 400 chars → MiniLM embedding → cosine vs 24 category texts
   │            → shortlist of 6 → DeBERTa-v3 zero-shot NLI → top 3
   │            best score < 0.35 → "Unclassified / Emerging Complaint"
   │            on any exception → "General Feedback"
   ▼
③ Similar reviews ── MiniLM embedding → FAISS search (reviews.faiss) → keep same sentiment → top 5
   │            on any exception → []
   ▼
④ LLM business analysis ── similar reviews scoring ≥ 0.35 become evidence (else an explicit
   │            "none found" marker) → Groq LLM with a Pydantic output schema
   │            → summary · 5 keywords · business insight · severity · priority · department · executive summary
   │            on failure → recover JSON from the error payload, else empty strings
   ▼
Response → four tabs: Sentiment · Complaint category · Business analysis · Similar reviews
```

Everything runs synchronously inside one HTTP request. Nothing is stored.

### 2.3 Flow B — Ask a question (RAG assistant)

```text
Question (+ up to 20 earlier messages)
   │  POST /api/rag/chat
   ▼
No GROQ_API_KEY? ───────────────────────────▶ "No LLM API key configured…"      (grounded = false)
   ▼
Scope guard 1: keyword regex ──out of scope─▶ fixed refusal
   ▼            (weather, politics, sports, recipe, coding, write code, poem, story, joke)
Scope guard 2: LLM "YES/NO" call ──NO──────▶ fixed refusal
   ▼
Load LangChain FAISS store (index.faiss + 430 MB index.pkl) + MiniLM
   ▼
Question mentions amazon / yelp / twitter / airline? → metadata filter, fetch_k = 50 (else 20)
   ▼
Grounded retriever: {docs with relevance ≥ 0.35} ∩ {MMR k=5, λ=0.5}; text capped at 1,000 chars
   ▼
Single-turn and nothing passes? ────────────▶ "I couldn't find enough relevant customer reviews…"
   ▼
LangChain chain: follow-up? LLM rewrites it as a standalone question → retrieve again
   → each review tagged [Platform | Rating | Sentiment] → RAG_PROMPT → LLM answer
   (rate-limit errors retried up to 2 times)
   ▼
Response: answer + sources (review_id, platform, rating, sentiment, similarity) + grounded flag
```

Per question that's **2 LLM calls** (single turn) or **3** (follow-up). A single-turn question also runs **4 vector searches**: a probe, then the chain, each doing a relevance search and an MMR search.

### 2.4 Flow C — Semantic search

```text
Query + filters (platform, sentiment, minimum rating, top_k ≤ 50)
   │  POST /api/search
   ▼
MiniLM embedding → FAISS reviews.faiss → top_k × 5 candidates
   ▼
for each hit: row = parquet.iloc[vector position] → skip if a filter doesn't match → stop at top_k
   ▼
[{review_id, text, platform, rating, sentiment_label, similarity_score}] → cards / table / CSV download
```

### 2.5 Flow D — Dashboard

```text
Page load → 6 GET requests (summary, sentiment-by-platform, rating-distribution, keywords, trends, platforms)
   ▼
pandas over the in-memory parquet (loaded once; warm-up starts when the API starts)
   ▼
KPI cards + Plotly charts; sidebar filters applied in Streamlit to the already-aggregated numbers
```

> **Important:** every sentiment figure on the dashboard is the **dataset label** (derived from star ratings; human annotation for Twitter), not a model prediction. No model runs for the dashboard. There is also no category chart. The README lists "category frequency", but the page has only Sentiment, Ratings, Keywords and Trends tabs.

### 2.6 Flow E — Batch upload

CSV read by Streamlit → user picks the text column → first 200 rows → `POST /api/sentiment/batch-predict` → the chosen model scores one text at a time → counts, table, agreement with a `sentiment_label` column if present → CSV download. **Nothing is saved server-side.**

### 2.7 Flow F — Evaluation viewer

`GET /api/evaluation/{sentiment, retrieval, rag}` → reads CSV/JSON from `data/results/` → model comparison, confusion matrices, McNemar tests, retrieval metrics, RAGAS per question. Runs no models.

---

## 3. Architecture

In plain terms: **a thin web UI calls a single API process, which wraps research code and reads large files from disk.** There is no database, no background processing and no concept of users.

```text
┌──────────────────── docker compose (or two terminals locally) ────────────────────┐
│                                                                                    │
│  frontend container — python:3.12-slim + streamlit, plotly, pandas, requests        │
│    streamlit run frontend/app.py  :8501                                            │
│    app.py · pages/01–06 · utils.py (API client) · theme.py (CSS)                    │
│            │  HTTP + x-api-key header (same API_KEY read from .env)                 │
│            ▼                                                                       │
│  backend container — python:3.12-slim + torch, transformers, faiss, langchain…      │
│    uvicorn backend.main:app --workers 1  :8000                                     │
│    main.py → api/routes (5 routers) → services (5) → nlp/ · rag/                    │
│            │ reads mounted volumes                        │ HTTPS                   │
└────────────┼──────────────────────────────────────────────┼─────────────────────────┘
             ▼                                              ▼
   ./data    parquet · FAISS ×2 · docstore .pkl        Groq API (LLM)
             taxonomy JSON · keywords JSON · results   Hugging Face Hub
   ./models  DistilBERT · classical .pkl               (MiniLM, DeBERTa-v3, RoBERTa downloaded
   ./logs    rotating log file                          on first use — inferred: referenced by
                                                        Hub name, no cache volume mounted)
```

**Key properties**

- **Layering already exists.** Routes handle HTTP only; services orchestrate; `nlp/` and `rag/` contain the ML. This is a good foundation.
- **Lazy loading.** Models and indexes load on first use, protected by locks against double loading. The parquet warm-up starts in a background thread at startup.
- **One process holds everything in memory.** Once every feature has been used, it holds:
  - the parquet **twice** (the analytics service and the search service each load it)
  - **two FAISS indexes of 987 MB each** (search and RAG read separate files containing the same vectors)
  - the 430 MB LangChain docstore
  - **three** separate MiniLM instances, DeBERTa-v3-base, DistilBERT
  - RoBERTa, the classical models and spaCy, if used

  The GCP runbook sizes a 16 GB VM and warns not to go smaller.
- **Concurrency.** One uvicorn worker. Blocking model work runs in the default thread pool via `asyncio.to_thread`. Scaling means more containers, each loading its own copy of everything.
- **Cold start.** Just importing `backend.main`, before any model loads, took **48.9 s wall-clock (6.7 s CPU)** on this Mac during the audit.

---

## 4. Important components

| Component | File(s) | Responsibility | Inputs | Outputs | Depends on | Retain? ([02](02-code-classification.md)) |
|---|---|---|---|---|---|---|
| API entrypoint | `backend/main.py` | CORS, request timing log, global error handler, routers with API-key dependency, health | HTTP | JSON | FastAPI, config, deps, routes | Keep skeleton (A8) |
| API key auth | `backend/api/deps.py` | Check `x-api-key`; refuse production boot on dev key | header, `API_KEY`, `ENVIRONMENT` | pass / 401 / 403 / 500 | config | Keep pattern (A7); replace as sole auth (D1) |
| Schemas | `backend/models/schemas.py` | Request validation, response models | JSON bodies | Pydantic objects | pydantic | Keep (A6) |
| Sentiment routes | `backend/api/routes/sentiment.py` | `/models`, `/predict`, `/analyse`, `/compare`, `/batch-predict` | request models | result models / dicts | sentiment_service | Refactor (B11) |
| Pipeline service | `backend/services/sentiment_service.py` | Orchestrates sentiment → category → similar → LLM with fallbacks; batch loop | text, model id | dict | nlp.sentiment, categoriser, embedding_service, summariser | Refactor into engine (B9) |
| Analytics service | `backend/services/analytics_service.py` | KPIs, groupings, keywords, monthly counts | optional sentiment, n | lists of dicts | pandas, spaCy, parquet, keywords JSON | Refactor → replace (B10) |
| Search / RAG services | `backend/services/search_service.py`, `rag_service.py` | Thin wrappers | query/question | results | embedding_service, rag.pipeline | Refactor |
| Evaluation service | `backend/services/evaluation_service.py` | Shapes stored results for the UI | files in `data/results/` | dicts | pandas | Research only (C7) |
| Sentiment models | `nlp/sentiment.py`, `nlp/classical_models.py` | Five classifiers with one `predict(text)` interface, cached | text | `{label, confidence, scores, model}` | torch, transformers, vaderSentiment, pickles | DistilBERT keep (A1); others research (C7) |
| Categoriser | `nlp/categoriser.py` | Embedding shortlist → NLI rerank → threshold | text (first 400 chars) | top-k `{category, score, description}` | sentence-transformers, transformers, taxonomy JSON | Keep algorithm (A2); refactor module (B6) |
| Embedding search | `nlp/embedding_service.py` | Encode query, FAISS search, filter via parquet row lookup | query, filters | similar reviews | faiss, sentence-transformers, parquet | Refactor → replace (B7, D3) |
| LLM analysis | `nlp/summariser.py` | Structured business analysis for one review | text, sentiment, category, similar reviews | 7-field dict | langchain-core, langchain-groq | Keep (A5) |
| RAG | `rag/pipeline.py`, `rag/prompts.py` | Scope guards, grounded retrieval, chain, retries, sources | question, history | answer, sources, grounded | langchain-classic/community/huggingface, Groq, FAISS docstore | Keep logic (A3, A4); refactor module (B8) |
| Config | `config.py` | pydantic-settings from `.env` | environment | `settings` | pydantic-settings | Refactor (B2) |
| Logging | `logger.py` | Console + rotating file logger | name | Logger | stdlib | Refactor (B3) |
| Streamlit app | `frontend/app.py`, `pages/*`, `utils.py`, `theme.py` | UI, API client, styling | user actions | HTTP calls, charts | streamlit, plotly, requests, root `config.py` | Internal tool (A12); replace for customers (D4) |
| Corpus builder | `scripts/preprocess.py`, notebook 1 | Unify, clean, label, balance | raw datasets | parquet | pandas, emoji (optional) | Research (C4) |
| Index builder | `scripts/build_index.py` | Embed corpus; save `.npy` and LangChain index | parquet | `data/embeddings/` | sentence-transformers, langchain-community | Refactor later (B17) |
| Taxonomy discovery | `scripts/discover_categories.py` | BERTopic per platform → merge → LLM naming | parquet | `complaint_categories_all_<sentiment>.json` | bertopic, umap, hdbscan, groq, spaCy, langdetect | Research now, product later (B18) |
| Keyword precompute | `scripts/precompute_keywords.py` | Noun-chunk counts over the corpus | parquet | `top_keywords.json` | spaCy | Research / prototype |
| Evaluation harnesses | `evaluate/*`, `scripts/evaluate_*` | Metrics, significance, RAGAS, ablation, figures | parquet, models, Groq | `data/results/*` | sklearn, scipy, ragas, mlflow, matplotlib | Research (C1, C2) |

---

## 5. Data flow

### 5.1 The corpus

`data/processed/reviews_unified.parquet`: 642,692 rows, all string columns except `rating` (double).

| column | meaning | origin |
|---|---|---|
| `review_id` | source ID | Amazon: `asin + "_" + row index in load chunk`; Yelp: `review_id`; Twitter: `tweet_id` |
| `platform` | `amazon` · `yelp` · `twitter_airline` | |
| `product_category` | Amazon category, empty otherwise | |
| `text` | original text (Amazon: `title + ". " + text`) | |
| `cleaned_text` | `clean_text(text)` | URLs, @mentions, `#`, HTML removed; emoji → words; non-ASCII removed; lower-case |
| `rating` | 1–5 | Twitter: **synthetic**, mapped from annotation (negative 1, neutral 3, positive 5) |
| `sentiment_label` | negative / neutral / positive | rating ≤2 / =3 / ≥4; Twitter: human annotation |
| `date` | `YYYY-MM-DD` string | |

| Source | Rows | Share | Date range |
|---|---:|---:|---|
| Yelp | 449,858 | **70.0%** | 2005-02-16 → 2022-01-19 |
| Amazon · Grocery | 59,701 | 9.3% | |
| Amazon · All_Beauty | 59,638 | 9.3% | 2000-05-22 → 2023-09-08 (all Amazon) |
| Amazon · Electronics | 59,544 | 9.3% | |
| Twitter US Airline | 13,951 | 2.2% | **one week:** 2015-02-16 → 2015-02-24 |

Any corpus-wide number (dashboard KPIs, keyword counts) mostly reflects Yelp.

### 5.2 From corpus to runtime artefacts

```text
reviews_unified.parquet
  ├─ cleaned_text ─▶ MiniLM (384-d, L2-normalised) ─▶ review_embeddings.npy       (offline scripts only)
  │                                              └─▶ langchain_index/index.faiss (vectors)
  │                                                  langchain_index/index.pkl   (docstore: cleaned_text +
  │                                                      {platform, rating, sentiment_label, review_id,
  │                                                       product_category})                   ← RAG
  ├─ reviews.faiss  987 MB, 642,692 × 384 vectors                                       ← Search, Analyse
  │     vector i ↔ parquet row i  (assumed by code, never checked)
  │     NO script or notebook in the repository writes this file (grep for write_index: no hits)
  ├─ negative reviews ─▶ BERTopic per platform (69 Amazon + 18 Yelp + 34 Twitter = 121 topics)
  │     ─▶ merge by centroid similarity ─▶ LLM names ─▶ complaint_categories_all_negative.json (24)
  ├─ noun chunks ─▶ top_keywords.json (filter version 2)                                ← Dashboard
  └─ whole frame in pandas                                                              ← Dashboard, Search
```

**Which text each feature shows:**
- **Search** shows the original `text`, looked up in the parquet.
- **RAG sources** show `cleaned_text` (lower-case, no emoji), because the LangChain index was built from it.

### 5.3 At request time

| Request | Data read | Data written |
|---|---|---|
| Analyse | model weights, taxonomy (at import), `reviews.faiss` + parquet, Groq | log lines only |
| Chat | LangChain index + docstore, Groq | log lines only |
| Search | `reviews.faiss` + parquet | log lines only |
| Dashboard | parquet, `top_keywords.json` | log lines only |
| Batch | model weights | log lines only |
| Evaluation | `data/results/*` | log lines only |

**The running application never writes data.** It only appends to `logs/feedbackanalytics.log`.

---

## 6. ML/AI pipeline

### 6.1 Preprocessing

- **Schema unification** across three sources (above). Amazon title and body are joined.
- **`clean_text()`** (identical logic in `scripts/preprocess.py` and `nlp/classical_models.py::_clean`): strip URLs and @mentions, keep hashtag words, strip HTML, emoji → words (only if the optional `emoji` package is installed; notebook 1 calls it directly), remove non-ASCII, collapse whitespace, lower-case.
- **Labels from ratings:** ≤2 negative, 3 neutral, ≥4 positive. Twitter keeps its human annotation.
- **Balancing:** Amazon 20,000 per class per category; Yelp 150,000 per class; Twitter as-is.
- **Filtering:** drop rows with `cleaned_text` ≤ 15 characters; de-duplicate on `cleaned_text`.
- **Splits:** classical models and the shared evaluation use a stratified 80/20 split, seed 42. DistilBERT used 70/15/15, seed 42, on a separately generated corpus version (the README reports the overlap).
- **Reproducibility gap:** `data/raw/amazon/` currently contains only `All_Beauty.jsonl.gz`. The Grocery and Electronics files the corpus includes are absent, so `preprocess.py` could not rebuild the same corpus today.

### 6.2 Features

| Feature | Used by | Detail |
|---|---|---|
| TF-IDF | Naive Bayes, Logistic Regression | Vectoriser pickled in `models/classical/`; parameters logged in MLflow |
| WordPiece tokens | DistilBERT (`distilbert-base-uncased`) | `max_length=128`, truncation |
| Sentence embeddings | categoriser shortlist, search, RAG | `all-MiniLM-L6-v2`, 384-d, normalised |
| NLI hypothesis | categoriser rerank | the category's full `description` (template `"{}"`) |
| Noun chunks | keywords | spaCy `en_core_web_sm`, pronoun/determiner/stopword filters |
| Metadata tags | RAG prompt | `[Platform | Rating | Sentiment]` per retrieved review |

### 6.3 Models

| Model | Type | Training | Artefact | Used at runtime by | Role |
|---|---|---|---|---|---|
| **DistilBERT fine-tuned** | transformer classifier, 3 classes | notebook 3 on a university HPC: 4 epochs, lr 2e-5, batch 32, class-weighted loss, **trained on `cleaned_text`** | `models/distilbert-finetuned-final/` (256 MB, safetensors) | `/predict`, `/analyse`, `/batch-predict`, `/compare` | **production path** |
| Logistic Regression | TF-IDF linear | `train_classical_models.py` (GridSearchCV on C, 5-fold CV, MLflow) | `models/classical/*.pkl` | selectable, `/compare` | baseline |
| Naive Bayes | TF-IDF multinomial | same (alpha) | same | selectable, `/compare` | baseline |
| VADER | lexicon, ±0.05 thresholds | none | pip package | selectable, `/compare`, **runtime fallback** | baseline + fallback |
| RoBERTa | `cardiffnlp/twitter-roberta-base-sentiment-latest` | none (pretrained) | Hub download | selectable, `/compare`, fallback if DistilBERT missing | baseline |
| MiniLM | `all-MiniLM-L6-v2` | none | Hub download | categoriser, search, RAG | embeddings |
| DeBERTa-v3 NLI | `MoritzLaurer/deberta-v3-base-zeroshot-v2.0` | none | Hub download | categoriser | zero-shot rerank |
| BERTopic ×3 | UMAP + HDBSCAN + KeyBERT-inspired | `discover_categories.py` | `models/bertopic_model_*` (≈1.2–1.4 GB each) | offline only | taxonomy discovery |
| Groq LLM | default `openai/gpt-oss-20b`; **reported results used `llama-3.1-8b-instant`** | none | API | analysis, RAG answer, scope guard, question rewrite; offline category naming; RAGAS judge | generation |

MLflow's model registry marks DistilBERT as "Production". The app does not read the registry; it loads the folder path from `MODEL_PATH`.

### 6.4 Prompts

| Prompt | File | Purpose | Output | Safeguards |
|---|---|---|---|---|
| Business analysis | `nlp/summariser.py` | Analyse one review with optional similar reviews | Pydantic schema via tool calling: summary, exactly 5 keywords, insight ≤25 words, severity/priority/department enums, executive summary ≤30 words | Weak evidence (<0.35) dropped; explicit "none found" marker; schema validation; recovery from `tool_use_failed` |
| `RAG_PROMPT` | `rag/prompts.py` | Answer from retrieved reviews | Fixed structure: platforms covered, key complaint patterns, one recommendation | 7 rules: evidence only, **reviews are data not instructions**, cite only present platforms, no invented numbers, fixed refusal sentence |
| `DOCUMENT_PROMPT` | `rag/pipeline.py` | Format each retrieved review | `[Platform | Rating | Sentiment]\n text` | Makes platform rule enforceable (ablation: faithfulness +0.118) |
| `CONDENSE_QUESTION_PROMPT` | `rag/prompts.py` | Rewrite follow-ups as standalone questions | one question | "Do not add facts" |
| `SCOPE_CLASSIFIER_PROMPT` | `rag/prompts.py` | In-scope check | YES / NO | Falls back to keyword check on error |
| Category naming | `scripts/discover_categories.py` | Name merged topic clusters | category name/description | Offline only |
| `nlp/langchain_summariser.py` | — | Older copy of the analysis prompt | — | **Unused** |

### 6.5 Inference

- **CPU only** (`device=-1`, `device: cpu`), one worker, blocking calls in threads.
- **One text at a time** for every model, including the 200-row batch endpoint.
- **Input truncation:**
  - categoriser: first 400 characters
  - analysis prompt: review 600 chars, similar reviews 250 chars each
  - RAG: 1,000 chars per review
  - DistilBERT: 128 tokens
- **Preprocessing mismatch:** DistilBERT was trained on `cleaned_text`, and `scripts/evaluate_models.py` scored it on `cleaned_text`, but `FineTunedSentiment.predict` receives the **raw** request text. Lower-casing doesn't matter for an uncased tokenizer; URLs, @mentions, emoji and non-ASCII do. The size of the effect is unmeasured.
- **LLM calls:** 30 s timeout. RAG retries rate-limit errors twice; the analysis call does not retry.
- **Silent fallbacks:** RoBERTa if DistilBERT files are missing, VADER on sentiment errors, "General Feedback" on categoriser errors, empty strings on LLM failure, `"unknown"` labels if classical models are missing. All return HTTP 200.

### 6.6 Evaluation (as reported in README.md / RESULTS.md; not re-run in this audit)

| Stage | Method | Headline results |
|---|---|---|
| Sentiment | Shared stratified held-out set (n = 128,539), macro F1, per-platform, McNemar exact test on 10 pairs | DistilBERT 0.8244 overall (0.8375 on its own clean split), spread across platforms 0.020; LogReg 0.8033 (spread 0.151); NB 0.7580; RoBERTa 0.5694; VADER 0.4381 |
| Taxonomy | No ground truth: coherence, separation, unclassified share on a 450-review sample | 24 defined / 21 observed; coherence 0.6334; separation 0.2831; **27.6% unclassified**; largest category 28.2% |
| Retrieval | 30 queries, precision@3, latency; TF-IDF vs dense | precision@3 0.90; 21.71 ms mean |
| RAG vs LLM | RAGAS on **n = 5** questions, same model family as judge | faithfulness 0.540; answer relevancy 0.610 (LLM-only 0.753); context precision 0.416; recall 0.400; latency 1,583 ms vs 734 ms |
| Ablation | Metadata tags on/off, retrieval fixed | faithfulness +0.118; relevancy −0.202 |

**Stated limitations:**
- labels derived from ratings
- DistilBERT's shared-test overlap
- n = 5 generation evaluation
- the 0.35 threshold never validated
- three of four planned ablations not run
- positive reviews receive complaint categories

### 6.7 Outputs

- **Offline:** ~79 result files in `data/results/` (CSV, JSON, PNG); MLflow runs in `mlruns/`; figures in `docs/figures/`.
- **Online:** JSON responses only (shapes in [03 §4](03-feedbackiq-core.md#4-outputs-it-produces-today)).

---

## 7. Frontend

- **Framework:** Streamlit 1.33 multipage app (`frontend/app.py` + `frontend/pages/`), Plotly Express charts, custom CSS in `theme.py`. It imports the root `config.py` for `API_URL` and `API_KEY`, so the frontend image copies `config.py` and `logger.py`.
- **Audience and framing:** dissertation. The landing page is organised around four research questions, and every page header links to its research question.

| Page | Purpose | API calls | Session state | Visualisations |
|---|---|---|---|---|
| `app.py` (landing) | Research questions, findings, stages, navigation | `GET /api/analytics/summary` | — | KPI grid (HTML/CSS), cards |
| `01_Dashboard.py` | Corpus view | 6 × `GET /api/analytics/*` | widget state only | metrics; grouped bar; donut; rating bar; horizontal keyword bar; monthly line |
| `02_Analyse.py` | One review through the pipeline; compare 5 models | `GET /api/sentiment/models`, `POST /api/sentiment/analyse` (90 s timeout), `POST /api/sentiment/compare` | `analyse_text`, `last_model` | metric; score bar; tables; tabs |
| `03_Search.py` | Semantic search | `POST /api/search` | `search_query` | metrics; result cards; table; CSV download |
| `04_Chatbot.py` | RAG chat | `POST /api/rag/chat` (60 s) | `chat_messages`, `pending_question` | chat bubbles; refusal warnings; source expanders |
| `05_Upload.py` | Batch CSV scoring | `GET /api/sentiment/models`, `POST /api/sentiment/batch-predict` (120 s) | `last_model` | KPI grid; table; downloads (results and two sample CSVs) |
| `06_Evaluation.py` | Stored research results | `GET /api/evaluation/{sentiment,retrieval,rag}` | — | grouped bar; styled table; confusion-matrix heatmap; latency bars; per-question tables |
| Sidebar (every page) | Backend status | `GET /api/health` (10 s timeout) | — | status badges |

**State management:**
- Streamlit re-runs the page script on every interaction. `st.session_state` keeps per-browser-session values (chat history, last query, selected model).
- `utils.api_get` is wrapped in `st.cache_data(ttl=60)`. That cache is **shared by every user of the Streamlit process**, which is harmless for one public corpus but would leak data between customers if Streamlit ever served tenant data.

**API client:** `utils.api_get` / `api_post` add the `x-api-key` header and turn timeouts, connection errors and 401/403 into readable messages. There is no login of any kind.

---

## 8. Backend

- **Framework:** FastAPI 0.115.12 on uvicorn 0.35 (one worker), Pydantic 2.10.
- **Structure:** `main.py` → `api/routes/` (5 routers) → `services/` (5 modules) → `nlp/`, `rag/`. Every `/api/*` router gets `Depends(require_api_key)`; `/` and `/api/health` are open.

| Method | Path | Service → ML | Notes |
|---|---|---|---|
| GET | `/` | — | open |
| GET | `/api/health` | file-existence checks | open; always `status: "ok"` |
| GET | `/api/sentiment/models` | static list | |
| POST | `/api/sentiment/predict` | `predict_only` → one model | |
| POST | `/api/sentiment/analyse` | `analyse_review` → sentiment, categoriser, FAISS search, LLM | full pipeline |
| POST | `/api/sentiment/compare` | `compare_all` → 5 models | |
| POST | `/api/sentiment/batch-predict` | `batch_predict` → one model per text | ≤200 texts |
| POST | `/api/search` | `search_reviews` → `semantic_search` | |
| POST | `/api/rag/chat` | `ask_question` → `rag.pipeline.ask` | errors returned as 200 answers |
| GET | `/api/analytics/summary` | pandas | |
| GET | `/api/analytics/sentiment-by-platform` | pandas | |
| GET | `/api/analytics/rating-distribution` | pandas | |
| GET | `/api/analytics/keywords` | precomputed JSON (sample fallback) | |
| GET | `/api/analytics/platforms` | pandas | |
| GET | `/api/analytics/trends` | pandas (copies the frame each call) | |
| GET | `/api/evaluation/sentiment` | reads result files | |
| GET | `/api/evaluation/retrieval` | reads result files | |
| GET | `/api/evaluation/rag` | reads result files | |

(`/docs`, `/redoc` and `/openapi.json` are also served, without authentication. The import check counted 22 routes in total.)

**Business logic** lives mainly in `sentiment_service.analyse_review` (pipeline orchestration with fallbacks), `analytics_service` (aggregations, keyword filtering) and `rag/pipeline.ask` (guards, retrieval, generation).

**Database interaction:** none. All reads are files: see §9.

---

## 9. Database

**There is no database.** No SQL is executed anywhere in the application code. Data lives in files:

| Store | Format | Size | Written by | Read by | Access pattern |
|---|---|---|---|---|---|
| `data/processed/reviews_unified.parquet` | Parquet | 407 MB on disk | notebook 1 / `preprocess.py` | analytics service, search service | whole file into pandas, **twice** |
| `data/embeddings/reviews.faiss` | FAISS index | 987 MB | unknown (no current script) | search, analyse | whole index in memory; exact inner-product search |
| `data/embeddings/langchain_index/` | FAISS + pickle docstore | 987 MB + 430 MB | `build_index.py` | RAG | pickle loaded with `allow_dangerous_deserialization=True` |
| `data/embeddings/review_embeddings.npy` | NumPy | 987 MB | `build_index.py` | offline scripts only | |
| `data/processed/complaint_categories_all_negative.json` | JSON | 32 KB | `discover_categories.py` | categoriser, at import | |
| `data/results/keywords/top_keywords.json` | JSON | small | `precompute_keywords.py` | analytics service | cached per process |
| `data/results/**` | CSV / JSON / PNG | 5.7 MB | evaluation scripts | evaluation service | per request |
| `models/**` | safetensors, pickle | ≈5 GB incl. BERTopic | notebooks, scripts | sentiment, categoriser | lazy load |
| `mlruns/` | MLflow file store | 8.3 MB | training/evaluation scripts | nothing at runtime | absolute paths to the Desktop folder |
| `logs/feedbackanalytics.log` | text | — | every process | — | rotating 5 MB × 3 |

**"Queries" today** are pandas operations: `groupby(...).size()`, `value_counts()`, `nunique()`, `mean()`, month bucketing with `to_period("M")`, and `df.iloc[position]` lookups after a FAISS search.

**Limitations:**
- read-only, with no way to add, update or delete a review
- the whole dataset sits in memory per process, duplicated
- no relationships or constraints (e.g. nothing enforces unique `review_id`)
- search correctness depends on two separately built files sharing row order
- no transactions
- no concurrent writers possible
- no per-customer separation
- rebuilding indexes is an offline, full-corpus job

---

## 10. Configuration, environment and repository state

**Configuration:** `config.py` defines a pydantic-settings `Settings` object read from `.env`.
- 28 settings, of which 9 are never read.
- `ENVIRONMENT` is read with `os.getenv` in `deps.py` and `main.py`, not through settings.
- `LOG_LEVEL` is read directly in `logger.py`.
- Defaults include a development API key (`dev-key-feedbackiq`) and `ALLOWED_ORIGINS="*"`.

**Environment variables used:** `GROQ_API_KEY`, `GROQ_MODEL`, `API_KEY`, `ENVIRONMENT`, `ALLOWED_ORIGINS`, `LOG_LEVEL`, `API_URL`, `API_GET_TIMEOUT`, `API_HEALTH_TIMEOUT`, `PORT`, plus thread settings set in code (`OMP_NUM_THREADS`, `KMP_DUPLICATE_LIB_OK`, `TOKENIZERS_PARALLELISM`).

**Dependencies:**
- `requirements.txt` is pinned (torch 2.13, transformers 5.14, sentence-transformers 5.7, faiss-cpu 1.15, LangChain 1.x family, FastAPI 0.115, Streamlit 1.33, ragas 0.4.3, mlflow 2.12).
- `backend/requirements-extra.txt` adds spaCy and vaderSentiment; `frontend/requirements.txt` is a light separate set.
- Python 3.12.10 in `venv/`.
- **`venv/` does not match the requirements:** `ragas` and `emoji` are not installed (`bertopic` is installed but not listed).

**Repository state — two copies exist:**

| | `~/Documents/reviewAnalytics` (audited) | `~/Desktop/FeedbackAnalytics_LLM` |
|---|---|---|
| Git | **not a repository** | 17 commits on `main`, clean, remote `github.com/SiddiqueSahb/FeedbackAnalytics_LLM` |
| `.gitignore`, `.env.example`, `.dockerignore`, `.streamlit/`, `.github/workflows/ci.yml` | **missing** | present |
| `.env` | missing | present; gitignored; never committed (checked with `git log --all -- .env`) |
| Source code | identical (`diff -rq`, excluding data/models/venv) | identical |

Consequences in this copy:
- the frontend Docker build fails (`COPY .streamlit/` has nothing to copy)
- the Docker build context would include `data/`, `models/` and `venv/` (~15 GB) because there's no `.dockerignore`
- changes aren't tracked

In the git repository, `data/`, `models/`, `mlruns/`, `logs/`, `interview/`, `*.pkl`, `*.parquet` and `.env` are ignored; no data files are tracked.

---

## 11. Authentication, logging and error handling

**Authentication:**
- **API:** one shared API key in the `x-api-key` header, compared in constant time. The backend refuses to start with `ENVIRONMENT=production` and the development key. (`docker-compose.yml` sets `ENVIRONMENT=production` for the backend, so `.env` must define `API_KEY` even locally.)
- **Streamlit:** no login. It sends the key from its own environment.
- No users, roles or authorisation exist.

**Logging:** `logger.get_logger()` attaches a console handler and a rotating file handler (`logs/feedbackanalytics.log`, 5 MB × 3) per named logger, with a plain-text format.
- Issues: it prints `Inside get_logger()` on every call; `nlp/categoriser.py` and the DistilBERT fallback use `print`; the default level `WARNING` hides the per-request timing log.
- Questions are logged (first 60 characters).
- The log file contains full stack traces, including a Groq rate-limit message that names the Groq organisation and shows the 200,000 tokens-per-day limit being hit.

**Error handling:**
- **Routes:** `ValueError` → 422, missing files → 503 (search, analytics summary), anything else → 500 with a generic message. A global handler catches the rest.
- **Services:** degrade gracefully with fallbacks (§6.5), which keeps the demo working but hides failures.
- **RAG:** returns internal errors to the user as `"An error occurred: {str(e)}"` with HTTP 200.
- **Unused:** `exceptions.py` defines a hierarchy nothing uses; `ErrorResponse` is unused.
- **Health:** `/api/health` always reports `"ok"`.

---

## 12. Tests and CI

**`tests/` (7 files) contains no pytest test functions** (`grep "def test_"` finds none).

| File | What it is | Status |
|---|---|---|
| `test_categoriser_shortlist.py` | Self-checking script; stubs the embedder and NLI; checks shortlist and top category | **passes** when run directly (2026-09-10) |
| `test_merge_topics.py` | Self-checking script; stubs BERTopic/spaCy/Groq via `sys.modules`; checks topic merging avoids the "charge" lexical collision | **passes** when run directly (2026-09-10) |
| `test_faiss.py`, `test_retriever.py`, `test_semantic_search.py`, `debug_embedding.py` | Manual scripts that print results; load the FAISS index / parquet / models at import | not tests |
| `test_rag.py` | Interactive `input()` loop against the live RAG pipeline | not a test |
| `/test_pipeline.py` (root) | Manual end-to-end print script | not a test |

Running `pytest tests/` would collect zero tests. It would still execute module-level code during collection, including loading the ~1 GB index, and `test_merge_topics.py`'s module stubs would leak into the rest of the session. `evaluate/verify_rag_fixes.py` and `verify_analyse_fixes.py` are manual pass/fail checks that need Groq and the index.

**CI** (Desktop repo, `.github/workflows/ci.yml`): `flake8 --select=E9,F` on `backend/` and `frontend/`, then Docker builds of both images. No tests run; nothing is pushed or deployed.

---

## 13. Deployment

- **Docker:**
  - two images from the project root; both honour `$PORT` and define healthchecks
  - backend: python:3.12-slim + build-essential, git, curl; installs all requirements and the spaCy model; one uvicorn worker by design
  - frontend: light image; XSRF protection disabled
  - both images run as root (no `USER`)
- **Compose:** backend + frontend; the frontend waits for the backend healthcheck; `data/`, `models/`, `mlruns/`, `logs/` mounted from the host; one `.env` loaded into both containers.
- **Documentation:** `DEPLOY.md` and `GCP_DEPLOY_RUNBOOK.md` recommend a Compute Engine VM (e2-standard-4, 16 GB, ≈$100–140/month) running Compose. Artefacts (≈3 GB) are synced from a bucket with `scripts/deploy/sync_artifacts.sh`, and only port 8501 is opened (plain HTTP, TLS advised before sharing). A Cloud Run alternative with Cloud Storage FUSE and Secret Manager is described.
- **Not present:** staging environment, CD, image registry, database, backups, monitoring, alerting.

---

## 14. Verified vs inferred

| Statement | Basis |
|---|---|
| Routes, flows, fallbacks, thresholds, prompts, truncation limits | Read in source |
| Backend imports successfully; 22 routes; 48.9 s import time | Executed `import backend.main` |
| Two stubbed test scripts pass | Executed directly with `python -B` |
| Parquet schema, row counts, date ranges | Read with pyarrow/pandas |
| DistilBERT trained and evaluated on `cleaned_text`; API sends raw text | Notebook 3 and `scripts/evaluate_models.py` code vs `nlp/sentiment.py` |
| No script writes `reviews.faiss` | `grep` for `write_index`/`reviews.faiss` in `.py` files and notebooks (git history not searched) |
| Copies identical except dotfiles; `.env` never committed | `diff -rq`; `git log --all -- .env` |
| Evaluation figures | **Reported** in README.md / RESULTS.md, not re-run |
| Hugging Face models download on first use in containers | **Inferred** from Hub model names and no cache volume |
| Memory composition | **Inferred** from what each module loads; not measured |
