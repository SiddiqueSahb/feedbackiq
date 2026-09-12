# Milestone 3 — Production-Ready Analytics Engine

> Completed 2026-09-12 · Repository: **https://github.com/SiddiqueSahb/feedbackiq** (public, `main`)
> Previous: [milestone-02.md](milestone-02.md) · Plan: [07 — roadmap](07-production-roadmap.md#m3--production-ready-analytics-engine)

The dissertation analytics are now a **reusable engine**: it analyses an arbitrary batch
of feedback, returns typed results, records which models and prompts produced them, and
knows nothing about HTTP, databases, organisations or customers.

```text
Customer/API  ->  AnalyticsEngine  ->  AnalysisResult objects  ->  (future) persistence
```

No database, authentication, tenancy, frontend, billing or worker was added.

---

## 1. Existing analytics architecture (before)

| Aspect | How it worked |
|---|---|
| Entry point | `services/sentiment_service.analyse_review(text, model)` - **one review per call**, driven by an HTTP request |
| Batch | `batch_predict()` looped `predict()` one text at a time, capped at 200 rows by the API |
| Types | dictionaries throughout; the shape was whatever each stage happened to return |
| Categories | loaded from a JSON file **at import time** into a module global (`COMPLAINT_CATEGORIES`) |
| Sentiment gate | none - positive feedback was given complaint categories (a defect the README stated and a strict `xfail` recorded) |
| Retrieval | hard-wired to the dissertation's global FAISS corpus via `semantic_search` |
| Errors | swallowed. `rag.pipeline.ask()` returned `"An error occurred: <exception>"` with HTTP 200; the analysis path returned empty strings |
| LLM | two separate call sites (`nlp/summariser.py`, `rag/pipeline.py`) each building their own Groq client, with their own retry and recovery logic |
| Versions | nothing recorded which model or prompt produced a result |
| Benchmark | the dissertation's evaluation scripts only - nothing guarding the serving path |

## 2. New engine architecture (after)

```text
src/feedbackiq/engine/
  types.py           FeedbackItem, Category | SentimentPrediction, CategoryMatch, Evidence,
                     Insight, ItemAnalysis, BatchAnalysis, GroundedAnswer, UsageStats
  errors.py          EngineError -> ModelUnavailableError, RetrievalError,
                     LLMError -> LLMTimeoutError, LLMRateLimitError, LLMResponseError
  versions.py        ENGINE_VERSION, PROMPT_VERSIONS, sentiment_model_version(), manifest()
  preprocessing.py   normalise_text() - and the documented decision about what it does not do
  sentiment.py       BatchSentimentModel (one forward pass per chunk), SentimentWithFallback,
                     SingleCallSentimentModel
  categorisation.py  ZeroShotCategoriser - shortlist -> NLI rerank -> threshold, categories injected
  retrieval.py       Retriever protocol, NullRetriever, CallableRetriever, CorpusRetriever
  llm.py             LLMAdapter - the single provider boundary
  insights.py        InsightGenerator - per-item LLM analysis, evidence-aware
  grounded_qa.py     answer_question() - scope guards, threshold, refusals, citations
  pipeline.py        AnalyticsEngine.analyse_batch() / analyse_one(), the sentiment gate
  defaults.py        build_default_engine(), build_sentiment_only_engine()
```

Dependency direction, unchanged from [05](05-target-architecture.md#4-dependency-rules--the-modular-in-modular-monolith):
`api -> services -> engine`. The engine imports **no** API, database or persistence code;
`grep` for `fastapi`, `sqlalchemy` or `psycopg` under `engine/` returns nothing.

## 3. Public engine interface

```python
from feedbackiq.engine import FeedbackItem, build_default_engine

engine = build_default_engine()                 # or AnalyticsEngine(...) with your own parts
batch  = engine.analyse_batch(
    [FeedbackItem(id="row-1", text="The battery died after a week.")],
    categories=None,          # defaults to the discovered taxonomy; pass a customer's own
    with_evidence=False,      # retrieval is opt-in
    with_insights=False,      # the LLM is opt-in
)

for result in batch.results:                    # same order as the input, one per item
    print(result.feedback_id, result.status, result.sentiment.label, result.category)

batch.usage        # UsageStats: llm_calls, input/output tokens, retries
batch.versions     # the manifest: engine, models, prompts, thresholds
```

Everything is injected, which is what makes it testable and what makes organisation-scoped
retrieval a later *implementation* rather than a later *rewrite*:

```python
AnalyticsEngine(
    sentiment=...,            # SentimentModel:  predict_batch(texts) -> [SentimentPrediction]
    categoriser=...,          # Categoriser:     categorise_batch(texts, categories)
    retriever=...,            # Retriever:       search(query, limit) -> [Evidence]
    insight_generator=...,    # InsightGenerator (wraps LLMAdapter)
    default_categories=...,
)
```

## 4. Input/output types

| Type | Purpose | Notes |
|---|---|---|
| `FeedbackItem(id, text)` | one input | `id` is the caller's - a CSV row, a future database UUID. Empty text is rejected at construction. |
| `Category(id, name, description, exemplars)` | a category the categoriser may assign | `description` **is** the NLI hypothesis, so it is required |
| `SentimentPrediction` | label, confidence, per-class scores, `model_version` | |
| `CategoryMatch` | `category_id` (None when unclassified), name, score, description | |
| `Evidence` | id, text, score, metadata | what a retriever returns |
| `Insight` | the 7 LLM fields + `evidence_ids`, `prompt_version`, `model_version` | says what it was based on |
| `ItemAnalysis` | one result: `status`, sentiment, category, candidates, `is_unclassified`, `categorisation_skipped`, evidence, insight, `error` | a failed item is still a result |
| `BatchAnalysis` | `results` (ordered), `usage`, `versions`, `.succeeded`, `.failed` | |
| `GroundedAnswer` | answer, evidence, `grounded`, `refusal_reason`, usage, versions | a refusal is a result, not an exception |
| `UsageStats` | `llm_calls`, `input_tokens`, `output_tokens`, `retries`, `.plus()` | zero when no LLM ran |

All frozen dataclasses. No Pydantic, no ORM, no HTTP objects.

## 5. Preprocessing decision

**Investigated first, then decided, then measured.**

What training used: notebook 1's `clean_text()` and `scripts/preprocess.py`'s are
step-for-step identical (URLs, @mentions, `#`, HTML, emoji→words, non-ASCII, whitespace,
lower-case) with one difference - the notebook calls `emoji.demojize` unconditionally,
the script wraps it in `try/except ImportError`. The corpus **was** built with `emoji`
active: `cleaned_text` contains demojized names (`confused face`, `thumbs down`,
`unamused face`; 1,042 rows contain "face with", 1,000 contain "thumbs up") and no row
retains non-ASCII. Notebook 3 then trained on `cleaned_text` at `max_length=128`, and
`scripts/evaluate_models.py` evaluated on `cleaned_text` too. The API has always passed
the caller's raw text to the tokenizer.

Measured effect (1,200 labelled reviews, 400 per class, seed 42, fine-tuned model):

| Input | Accuracy | Macro F1 | Predictions differing from training-style input |
|---|---|---|---|
| **raw text** (what the API sends) | 0.8217 | **0.8214** | 7 / 1200 |
| corpus `cleaned_text` (training-style) | 0.8175 | 0.8170 | — |
| `clean_text()` without the emoji step | 0.8183 | 0.8178 | 1 / 1200 |

**Decision: keep serving the caller's text.** Reasons:

- The mismatch is immaterial: 0.6% of predictions differ, and raw text scores
  *marginally higher*. There is no accuracy case for changing the serving path.
- The tokenizer is uncased and handles punctuation, URLs and mentions itself. Most of
  `clean_text()` mattered for the TF-IDF classical models, which still do their own
  cleaning in `nlp/classical_models.py`.
- Reproducing training exactly would require the `emoji` dependency for a 0.50% slice of
  rows (3,222 of 642,692). The standard library cannot substitute: `unicodedata` gives
  "thumbs down sign" where `emoji` gives "thumbs down", and multi-codepoint sequences
  (flags, skin tones, ZWJ families) raise `TypeError` - no single name exists.
- Adding a dependency to move 7 predictions in 1,200 is a bad trade, and it would be a
  silent behaviour change to the serving path.

What the engine *does* do: `normalise_text()` strips control characters and collapses
whitespace runs. Both are invisible to a WordPiece tokenizer, so model output is
unchanged - it just means one record with a null byte cannot poison a batch or a later
CSV export. Casing, punctuation, URLs, mentions and emoji are left as the customer wrote
them. Nine tests in `tests/unit/engine/test_preprocessing.py` pin that down, so a future
change to lower-casing or demojizing fails loudly.

**Dissertation benchmark preserved:** `scripts/evaluate_models.py` still evaluates
`cleaned_text` and its numbers are untouched (verified in §10). The new production
benchmark measures what the engine actually serves.

## 6. Sentiment-gate decision

The failing test said: *`analyse_review` on positive feedback must not call the
categoriser*. The current implementation called it for everything.

**Smallest defensible fix: do not ask the question when it cannot have an answer.**

```python
# engine/pipeline.py
def gated_sentiments() -> frozenset[str]:      # settings.CATEGORISE_SENTIMENTS
    return settings.categorise_sentiments      # default: {"negative", "neutral"}
```

Reasoning:

- **positive is excluded** because the taxonomy was *discovered from negative reviews*
  (BERTopic over negative feedback, 121 topics consolidated into 24 complaint
  categories). A positive item has no complaint to categorise, so any label is noise -
  which is exactly what the defect produced.
- **neutral is kept** because neutral-sounding text frequently contains a complaint, and
  the 0.35 confidence threshold already answers "Unclassified" when the match is weak.
  Excluding neutral would lose real issues; excluding positive loses nothing.
- **No new model.** No classifier, no heuristics, no extra inference - the gate reads a
  label the pipeline already produced.
- **Configurable** via `CATEGORISE_SENTIMENTS`, so a customer whose taxonomy includes
  praise categories widens it without a code change.

Behaviour change, deliberate and visible: a positive review now reports
`category: "General Feedback"` instead of a complaint category. The skip reason is
recorded on the result (`categorisation_skipped`), so it is never confused with a failure.
The Milestone 1 strict `xfail` is now a **passing** test in two places
(`tests/unit/engine/test_batch_pipeline.py`, `tests/unit/test_review_pipeline.py`).

## 7. Retrieval abstraction

```text
AnalyticsEngine  ->  Retriever protocol  ->  today:  CorpusRetriever (dissertation corpus)
                     search(query, limit)     later:  organisation-scoped implementation
```

- `Retriever` is a `Protocol`: one method, `search(query, limit) -> Sequence[Evidence]`.
- `NullRetriever` is the **default**, so an engine with no retrieval configured returns
  analyses without evidence rather than reaching for a global corpus by accident.
- `CorpusRetriever` wraps the existing `semantic_search` (compatibility), with the search
  function injectable so tests never load the 1 GB index.
- `CallableRetriever` adapts any plain function - the seam tests and future integrations use.
- A retriever that **fails** raises `RetrievalError`. It must never look like a retriever
  that found nothing: those two mean opposite things to a reader of the results.

No tenant-specific retrieval was implemented, as instructed. The protocol is the
extension point Milestone 12 needs.

## 8. LLM adapter

`engine/llm.py` is the only module that knows about the provider.

| Concern | Behaviour |
|---|---|
| timeout | passed to the client; surfaced as `LLMTimeoutError` |
| retries | **only** rate limits, honouring the provider's own "try again in 1.5s" (plus 0.5s margin), then `LLMRateLimitError`. A timeout or bad request is not retried - it will not fix itself and the caller is waiting. |
| structured output | `with_structured_output(schema)`, schema-validated |
| malformed recovery | preserved from the dissertation: when a model writes JSON as content instead of a tool call, Groq returns it in `failed_generation`; it is extracted (plain, fenced, or prose-wrapped) and validated. Real behaviour of the configured default model. |
| usage | `llm_calls`, `input_tokens`, `output_tokens`, `retries`, from the provider's own `usage_metadata` **when present**. Structured-output calls often carry none: the call is counted and tokens stay zero rather than being invented. |
| errors | typed exceptions, never a string that looks like an answer |
| injection | `LLMAdapter(chat_model=...)` takes any object with `.invoke()`; no test touches the network |

A bug this design caught during implementation: `complete()`/`structured()` wrapped
`ModelUnavailableError` ("no API key") into a generic `LLMError`, hiding *why* the call
could not be made. The test failed, the code was fixed.

## 9. Model/prompt versioning

`engine/versions.py`, attached to every `BatchAnalysis` as `versions`:

```json
{"engine": "1.0.0",
 "sentiment_model": "distilbert-finetuned-final@847cd6cd306a",
 "categoriser_model": "MoritzLaurer/deberta-v3-base-zeroshot-v2.0",
 "embedding_model": "all-MiniLM-L6-v2",
 "llm_model": "openai/gpt-oss-20b",
 "prompts": {"item_analysis": "item-analysis-2026-09", "grounded_answer": "grounded-answer-2026-09",
             "condense_question": "condense-question-2026-09", "scope_guard": "scope-guard-2026-09"},
 "thresholds": {"category_confidence": 0.35, "retrieval_similarity": 0.35, "evidence_similarity": 0.35}}
```

The sentiment version is the model directory name plus a 12-character digest of its small
metadata files (`config.json`, `label_map.json`) - those change when the architecture,
label order or class count changes, which is what would silently invalidate stored
results. The 256 MB weights file is deliberately not hashed (seconds per call).
Each `SentimentPrediction` and `Insight` also carries its own model/prompt version.

No MLflow, no registry, no model-management platform - constants and configuration, as
instructed.

## 10. Benchmark and metric floor

`tests/benchmark/test_production_benchmark.py`, marked `benchmark`, runs the **real**
model through the **engine** over a fixed stratified sample (200 per class, seed 42).

| | Measured 2026-09-12 | Floor enforced |
|---|---|---|
| Accuracy | **0.8133** | 0.80 |
| Macro F1 | **0.8137** | 0.80 |

~1.3 points of headroom: tight enough to catch a broken preprocessing step, a mis-wired
model or a batching bug; loose enough not to fail on harmless variation. Two further
checks run with it:

- **batched scoring agrees exactly with one-at-a-time** on 60 reviews - padding must not
  change a prediction, and this proves it does.
- **the manifest is populated**, so a stored result can always be traced.

It skips automatically when `data/` or `models/` are absent, which is the case in CI.
Run locally with `python -m pytest tests/benchmark -v -s`.

**The dissertation benchmark is untouched and verified unchanged:**

| Check | Result |
|---|---|
| `evaluate/generate_all_metrics.py` | exit 0; `data/results/metrics_summary/` **byte-identical** to the pre-Milestone-2 baseline |
| Deterministic prediction snapshot (10 fixed reviews × 4 models, legacy path) | md5 **`08aaf4cc8c88b9958245155185492ac8`** - identical to Milestones 2 and 3 |
| `tests/test_categoriser_shortlist.py`, `tests/test_merge_topics.py` | both PASS |

The benchmark sample is read from the local corpus at runtime rather than committed:
`data/` is gitignored research data, and the Yelp dataset's terms restrict redistribution.

## 11. Tests

**244 passed, 2 xfailed** (was 104 passed, 4 xfailed), offline and deterministic: no
model downloads, no network, no database.

| Area | File | Covers |
|---|---|---|
| Types & versions | `test_types_and_versions.py` | typed results, failed items keep their id, usage arithmetic, manifest contents, thresholds still 0.35, prompt versions exist |
| Preprocessing | `test_preprocessing.py` | control characters and whitespace only; casing/punctuation/URLs/mentions/emoji/non-ASCII preserved; idempotent |
| Batch pipeline | `test_batch_pipeline.py` | order preserved, one result per input, empty batch, normalisation before the model, **sentiment gate** (positive skipped, negative/neutral categorised, gate configurable), per-record isolation (categoriser/retrieval/insight), whole-batch sentiment failure marks every record, wrong prediction count raises, evidence/insights opt-in, usage reported, manifest attached |
| Sentiment gate | also `test_review_pipeline.py` | the former strict `xfail`, now passing, at the API-facing layer |
| Categorisation | `test_categorisation.py` | shortlist bounds the NLI stage, taxonomy encoded once per batch, threshold → Unclassified with near-misses kept, inclusive boundary, `top_k`, categories **passed in**, empty taxonomy → no opinion |
| Retrieval injection | `test_retrieval.py` | protocol conformance, `NullRetriever` default, callable adapter, row→Evidence mapping, threshold filtering, query/limit/filter passed through, **failure raises rather than faking "nothing found"** |
| LLM adapter | `test_llm_adapter.py` | missing key reported clearly, **timeout** → `LLMTimeoutError`, **rate-limit retry** honouring the provider's wait and the configured fallback, exhausted retries, other errors not retried, **token accounting** (and not invented), **structured output** success/dict/recovery/schema-violation/timeout/retry |
| Insights | `test_insights.py` | weak evidence excluded, explicit "none found" marker, metadata and IDs recorded, prompt carries feedback/sentiment/category/evidence and the injection rule, input capped, provider failure raises, schema enforced |
| Grounded Q&A | `test_grounded_qa.py` | both scope guards refuse before retrieval, guard can be disabled, no-evidence refusal **without** a generation call, inclusive threshold, citations, tagged evidence in the prompt, limit passed, retrieval/provider failures raise, usage reported |
| Adapter behaviour | `test_review_pipeline.py` | response shape unchanged, every legacy fallback (VADER, General Feedback, no evidence, empty analysis), batch order, one bad row isolated, non-default model injected into the engine |
| RAG errors | `test_rag_grounding.py` | the former `xfail`: internal failures now **raise** (`EngineError`/`RetrievalError`) with no internal path in the message, while refusals are still returned |
| Production floor | `tests/benchmark/` | metric floor, batched-vs-single agreement, manifest populated |

**Two xfails flipped to passing** (documented in §6 and §8): the sentiment gate, and RAG
errors no longer being returned as answers. **Two remain**, both API-level and out of this
milestone's scope: `/analyse` accepts `platform` and ignores it, and `min_length` is
checked before text is cleaned. No test was deleted or weakened.

## 12. Compatibility with the existing API and Streamlit

Adapters, not rewrites. The API's 22 routes, paths, schemas and status codes are unchanged.

| Path | Before | Now |
|---|---|---|
| `POST /api/sentiment/analyse` | `analyse_review` called four stages directly | same response dict, produced by the engine (sentiment + gate + category), then `engine.retriever` and `engine.insight_generator` |
| `POST /api/sentiment/batch-predict` | one `predict()` per row | `engine.analyse_batch` - one tokenised forward pass per chunk; a failing row is retried alone and labelled `"error"` |
| `POST /api/sentiment/predict`, `/compare` | five-model access | unchanged |
| `POST /api/rag/chat` | `ask()` returned error text as an answer | refusals still returned with `grounded=False`; failures raise, and the route maps them to 500 |
| Streamlit | — | untouched; verified running (`/_stcore/health` 200, main page 200) |

`services/sentiment_service.py` is now explicitly a **compatibility adapter**: the engine
is strict (raises, isolates failures, returns dataclasses); the adapter holds the
HTTP-facing tolerance the API has always promised - a missing index still returns a
sentiment, a dead LLM still returns a category. A non-default sentiment model is handled
by *injecting* it into the engine, which is the injectable design paying for itself.

**Docker:** both images build; the API image copies `src/` and installs the package, so
the engine ships with it (`Successfully installed feedbackiq-0.1.0`).

## 13. Known limitations

| # | Limitation |
|---|---|
| 1 | **Two xfails remain** (API-level): `/analyse` ignores `platform`; `min_length` is applied before cleaning. Both belong to the API milestone. |
| 2 | **`analyse_one` still costs one LLM call per item.** Bulk analysis defaults to no LLM, which is the right default, but aggregate insights (one call per category/period instead of per item) are Milestone 12 work. |
| 3 | **Token accounting is partial**: structured-output calls usually carry no `usage_metadata`, so their tokens read zero. Counting them properly needs `include_raw=True`, which changes the recovery path - deferred rather than guessed. |
| 4 | **`grounded_qa` is not yet what the API calls.** `/api/rag/chat` still uses `rag/pipeline.ask` (now raising correctly), because that path carries multi-turn question condensing and MMR diversity the engine's simpler version does not. Consolidating them belongs with pgvector in Milestone 12. |
| 5 | **`CorpusRetriever` still searches the global corpus** - by design; organisation-scoped retrieval is a later implementation of the protocol. |
| 6 | **The default taxonomy is still the dissertation's 24 categories**, discovered from Amazon/Yelp/airline reviews. Passing categories in is supported; a customer's own set is Milestone 4+ data work. |
| 7 | **The benchmark needs local artefacts**, so CI skips it. It is a local/nightly gate, not a pull-request gate. |
| 8 | **Batch size is fixed at 32** and unmeasured against alternatives; throughput tuning needs a real workload. |
| 9 | **`nlp/` retains the old single-item entry points** (used by `evaluate/` and the model picker). They are not dead, but there are now two ways to score one text. |
| 10 | **Emoji/preprocessing parity** remains a documented non-change (§5), not a fix. |

## 14. Recommended Milestone 4

**PostgreSQL / database layer** ([roadmap](07-production-roadmap.md#m4--postgresql--database-layer)).

- **Objective:** persist feedback and analysis results, with `organisation_id` on every
  tenant-owned row from the first migration - one seeded organisation for now.
- **Why now:** the engine emits exactly the shapes the schema should store
  (`ItemAnalysis`, `BatchAnalysis`, the manifest). Designing tables against typed results
  rather than ad-hoc dictionaries is the whole reason this milestone came first.
- **Main work:** Postgres + pgvector in `docker-compose.yml` for development; SQLAlchemy
  models and Alembic migrations for the M4 tables in
  [06](06-saas-data-model.md#6-when-each-table-arrives) (`organisations`, `data_sources`,
  `import_batches`, `feedback`, `categories`, `analysis_runs`, `analysis_results`,
  `jobs`); a seed script; integration tests against a real database in CI.
- **New dependency:** a PostgreSQL driver (`psycopg`). `sqlalchemy` and `alembic` are
  already installed.
- **Verification:** `alembic upgrade head` builds the schema on an empty database and the
  latest migration downgrades cleanly; integration tests run in CI; every tenant table has
  `organisation_id NOT NULL` with a leading index; the engine remains free of database imports.

**Not started.** Waiting for your approval.
