# 03 — The FeedbackIQ Core

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · Audit only.
> Previous: [02 — Code classification](02-code-classification.md) · Next: [04 — Production gaps](04-production-gaps.md)

This document identifies which parts of the dissertation carry the **product value**. Everything here is derived from the code, not from the product vision.

---

## 1. The core, as it actually exists

```text
                              THE FEEDBACKIQ CORE (as built)

   Feedback text
        │
        ▼
   ① Normalise ............. clean_text(): URLs, @mentions, "#", HTML removed · emoji → words ·
        │                    non-ASCII removed · lower-case
        │                    [scripts/preprocess.py · nlp/classical_models.py::_clean]
        ▼
   ② Sentiment ............. fine-tuned DistilBERT → negative | neutral | positive + confidence + scores
        │                    [nlp/sentiment.py::FineTunedSentiment]
        ▼
   ③ Issue category ........ MiniLM shortlist (6 of 24) → DeBERTa-v3 zero-shot NLI →
        │                    best ≥ 0.35 ? category : "Unclassified / Emerging Complaint"
        │                    [nlp/categoriser.py::categorise]
        ▼
   ④ Evidence retrieval .... MiniLM embedding → nearest similar feedback, similarity ≥ 0.35, MMR diversity
        │                    [nlp/embedding_service.py · rag/pipeline.py::_make_grounded_retriever]
        ▼
   ⑤ Grounded generation ... a) structured business analysis of one item   [nlp/summariser.py]
                             b) answers citing their evidence, or a refusal [rag/pipeline.py::ask, rag/prompts.py]

   Upstream, offline:  ⓪ Taxonomy discovery — BERTopic per source → merge → LLM naming
                         [scripts/discover_categories.py] → the 24 categories used by ③
   Alongside:          keyword extraction (spaCy noun chunks + filters)  [analytics_service.normalise_phrase]
                       corpus aggregates (counts by label, platform, rating, month)  [analytics_service]
```

Two corrections to the "obvious" picture:

- **Step ① is not applied before DistilBERT at inference today.** Only the classical models call `_clean`. DistilBERT was trained and evaluated on `cleaned_text` but receives raw text in the API (§5, item 3).
- **"Trend analysis" is only monthly counts of rating-derived labels.** There is no topic or category trend, no change detection and no model output over time. Those are *product* features to build (M12), not dissertation features to port.

---

## 2. What the core engine does

| Stage | What it does, in plain language | Why it's valuable to a business | Evidence it works |
|---|---|---|---|
| ② Sentiment | Tells you how positive or negative each piece of feedback is | The "how bad is it" monitoring signal | Macro F1 0.8375 (own clean split); **0.020 spread across very different writing styles**, where every alternative swings 15–31 points by platform. A customer's feedback won't look like one benchmark, so consistency matters more than peak accuracy. |
| ③ Issue category | Says *what* the complaint is about, and admits when it isn't sure | Turns thousands of comments into a ranked list of problems ("what is wrong") | Coherence 0.6334, separation 0.2831, 27.6% honestly left unclassified. No accuracy exists (no ground truth). |
| ④ Evidence retrieval | Finds other feedback that says the same thing, by meaning rather than keywords | "Is this a one-off or a pattern?" and the input to grounded answers | precision@3 0.90 in isolation; weaker in context (precision 0.416 / recall 0.400 on n = 5) |
| ⑤a Item analysis | Summarises one item and suggests severity, priority and owning department | Triage and routing | Schema-validated output; no quality evaluation reported |
| ⑤b Grounded Q&A | Answers questions *only* from retrieved feedback, lists the sources, or refuses | "Show me the evidence": trust for decisions | Faithfulness 0.540; tagging ablation +0.118; refusal paths in code. Directional (n = 5). |
| ⓪ Taxonomy discovery | Finds the categories from the data instead of hand-writing them | Categories that fit a customer's actual problems | 121 topics → 24 categories; merge logic regression-tested |

**The core value, in one sentence:** FeedbackIQ tells a team *how customers feel, what exactly is wrong, and shows the evidence*, and it was built by someone who measured each of those claims.

---

## 3. Inputs it accepts (today)

| Function | Input | Limits |
|---|---|---|
| `FineTunedSentiment.predict(text)` | one string | API: 3–5,000 chars; model: first 128 tokens |
| `categorise(text, top_k=3, shortlist_k=None, model_name=None)` | one string; categories from a module-level global loaded at import | first 400 chars used |
| `semantic_search(query, top_k, platform_filter, sentiment_filter, min_rating)` | query string + filters | platform ∈ amazon/yelp/twitter_airline |
| `analyse_review_with_llm(text, sentiment, category, similar_reviews)` | strings + list of dicts | review 600 chars; similar reviews 250 chars each |
| `rag.pipeline.ask(question, chat_history)` | question + `[{"role","content"}]` | API: question 3–500 chars; ≤20 history items, unbounded content length |
| `analyse_review(text, model)` (service) | one string + model id | the full pipeline for one item |
| `batch_predict(texts, model)` (service) | list of strings | API: ≤200; sentiment only |

**Not accepted today:** a dataset of a company's own feedback for analysis and storage, custom categories, feedback metadata (source, product, date) on analysis requests, or languages other than English.

## 4. Outputs it produces (today)

```jsonc
// Sentiment (SentimentResult)
{"label": "negative", "confidence": 0.9731,
 "scores": {"negative": 0.9731, "neutral": 0.0212, "positive": 0.0057},
 "model": "distilbert_finetuned"}

// Categories (top 3)
[{"category": "Product Performance Failures", "score": 0.62, "description": "…"},
 {"category": "…", "score": 0.21, "description": "…"}]
// or the first entry becomes "Unclassified / Emerging Complaint" when score < 0.35

// Similar reviews
[{"review_id": "B07…_1234", "text": "…", "platform": "amazon", "rating": 1.0,
  "sentiment_label": "negative", "similarity_score": 0.71}]

// LLM item analysis (ReviewAnalysisLLM)
{"summary": "…", "keywords": ["…" ×5], "business_insight": "…",
 "severity": "Low|Medium|High|Critical", "priority": "Low|Medium|High|Urgent",
 "department": "Engineering|Quality Assurance|Customer Support|Logistics|Finance|Marketing",
 "executive_summary": "…"}

// Grounded answer (ChatResponse)
{"answer": "Platform(s) covered: …\nKey complaint patterns:\n- …\nRecommendation: …",
 "sources": [{"text": "…", "full_text": "…", "platform": "yelp", "sentiment_label": "negative",
              "rating": 1.0, "review_id": "…", "similarity_score": 0.52}],
 "retrieval_count": 5, "grounded": true}
```

(Values above are illustrative. The shapes are exact.)

**Missing from every output:** which model version produced it, how long it took, whether a fallback was used (except the `model` name), and a stable ID linking it to stored feedback.

---

## 5. What is tightly coupled to the dissertation prototype

| # | Coupling | Evidence | Why it matters for the product |
|---|---|---|---|
| 1 | **Fixed corpus.** Search, similar reviews, Q&A and the dashboard all read one 642,692-row corpus built offline. | `DATA_PATH`, `INDEX_PATH`, `LANGCHAIN_INDEX_PATH`; `scripts/build_index.py` re-encodes everything | A customer's feedback can't be added without rebuilding global indexes, and it would be mixed with everyone else's. |
| 2 | **Row-position link between FAISS and parquet**; the search index is not reproducible from code. | `df.iloc[idx]` in `embedding_service.py`; no script or notebook writes `reviews.faiss` | Wrong results if files drift apart, and no documented way to rebuild the file. |
| 3 | **Preprocessing mismatch for the production model.** | Notebook 3 trains on `cleaned_text` (`'text': df['cleaned_text']`); `scripts/evaluate_models.py` scores on `cleaned_text`; `FineTunedSentiment.predict(text)` tokenizes the raw request text | The reported 0.82–0.84 F1 describes cleaned input. Served quality on raw input (URLs, @mentions, emoji) is unmeasured. |
| 4 | **Platform vocabulary hard-coded** to Amazon / Yelp / Twitter Airline. | `schemas.py` `Literal[...]`; `rag/pipeline._PLATFORM_KEYWORDS`; `OUT_OF_SCOPE_MSG`; `SCOPE_CLASSIFIER_TEMPLATE` ("…on Amazon, Yelp, or Twitter Airline"); Streamlit select boxes | For a real customer, the scope guard could reject valid questions about their own product. |
| 5 | **Taxonomy fitted to the research corpus.** | 24 categories from negative reviews of Amazon beauty/grocery/electronics, Yelp local businesses (70% of the corpus) and one week of airline tweets. Examples: "Service and Wait Time Delays" (restaurants/hotels); "Salon Service Failures", which groups nail salons, dentists, gyms, car washes and pharmacies. Only the *negative* file is loaded, at import. | A SaaS company's feedback (billing, onboarding, bugs, integrations) needs different categories. The zero-shot design makes that possible; the loading code doesn't. |
| 6 | **Labels from star ratings drive the dashboard.** | `analytics_service` reads `sentiment_label` | B2B feedback often has no rating; the dashboard must show predictions. |
| 7 | **Single-item, synchronous, stateless orchestration.** | `analyse_review` runs four stages inside one request; `batch_predict` loops one text at a time; nothing is stored | No bulk analysis, no history, no trends over time. |
| 8 | **Research model zoo exposed in the product path.** | Model picker, `/compare`, `/evaluation/*` | Customers need one reliable model. Five choices add memory, confusion and test surface. |
| 9 | **Provider-specific LLM error handling.** | Regex parsing of Groq messages ("try again in Xs", `failed_generation`) in `rag/pipeline.py` and `nlp/summariser.py` | Changing provider or client version can silently break retries and recovery. |
| 10 | **English only.** | Non-ASCII stripped in preprocessing; English filter in discovery; English training data | Must be stated to customers; multilingual support is a later decision. |
| 11 | **Hand-set constants.** | Similarity 0.35 ("chosen by hand and never validated", README); category confidence 0.35; k = 5, fetch_k = 20 | Need calibration on customer-like data, and should become configuration. |
| 12 | **Import-time side effects and path hacks.** | `COMPLAINT_CATEGORIES = _load_categories()` at import; `sys.path` edits everywhere; `settings = Settings()` at import | Hard to test, hard to give each organisation its own categories. |
| 13 | **Corpus imbalance by source.** | Yelp 449,858 of 642,692 rows | Corpus-wide statistics and discovered topics mostly reflect Yelp. |

---

## 6. What is reusable

These parts can move into the engine with local changes only:

| Reusable piece | Where | Reuse as |
|---|---|---|
| DistilBERT weights, tokenizer, label map | `models/distilbert-finetuned-final/` | Production sentiment model (after the preprocessing decision) |
| `FineTunedSentiment` inference logic | `nlp/sentiment.py` 97–145 | Batched `predict_batch` |
| Two-stage categorisation: `_hypothesis_text`, `_nli_hypothesis`, shortlist, NLI rerank, threshold → Unclassified | `nlp/categoriser.py` | `categorise_batch(texts, categories)` |
| Category data shape `{category, description, exemplars}` | taxonomy JSON | The `categories` table ([06](06-saas-data-model.md)) |
| `clean_text` | `scripts/preprocess.py` | Engine preprocessing (single copy) |
| Grounded retriever: threshold ∩ MMR, truncation, fresh documents with scores | `rag/pipeline.py::_make_grounded_retriever` | Same logic over pgvector results |
| `_format_sources`, `_build_history_string`, `_format_chat_history_text` | `rag/pipeline.py` | Evidence formatting and history handling |
| Refusal backstops (single-turn probe, post-chain empty context) | `rag/pipeline.py::ask` | Grounded Q&A guarantees |
| `RAG_PROMPT`, `CONDENSE_QUESTION_PROMPT`, `DOCUMENT_PROMPT` | `rag/prompts.py`, `rag/pipeline.py` | Versioned prompt templates (platform wording generalised) |
| `ReviewAnalysisLLM` schema, structured output, `_recover_from_failed_tool_call`, evidence threshold + "none found" marker | `nlp/summariser.py` | Item analysis and pattern for aggregate insights |
| `_invoke_with_retry`, `_extract_retry_after_seconds` | `rag/pipeline.py` | LLM adapter |
| `normalise_phrase`, `KEYWORD_STOPWORDS`, filter version | `analytics_service.py` | `engine/keywords.py` |
| Thread-safe lazy model cache | `nlp/sentiment.py::_thread_safe_cache` | One shared helper |
| Topic merge + coherence (`merge_topics`, `taxonomy_coherence`) | `scripts/discover_categories.py` | Later "suggest categories" job |
| Evaluation approach: fixed seeds, per-segment metrics, McNemar, threshold sweeps, IR metrics | `evaluate/*` | Benchmark and quality gate for model changes |

---

## 7. What needs to change to make it production-ready

### 7.1 Per stage

| Stage | Keep | Change |
|---|---|---|
| ① Normalise | `clean_text` rules | One copy in the engine; decide and test whether DistilBERT gets cleaned text (measure both on the benchmark); make `emoji` a real dependency or remove the step consistently |
| ② Sentiment | DistilBERT model and logic | Batch inference; model version in output; explicit error instead of silent RoBERTa fallback; benchmark regression test; measure on customer-like text (domain shift) |
| ③ Categories | Two-stage zero-shot design, Unclassified outcome | Categories passed in per organisation; **sentiment gate** (complaints only for negative/neutral, fixing the stated defect); batch; calibrate 0.35 on labelled samples; review the default 24 for generality |
| ④ Retrieval | Threshold ∩ MMR logic, evidence IDs | pgvector with `organisation_id` filter; incremental embeddings on import; a single store for search and Q&A; retrieval evaluation set (the known weak point) |
| ⑤a Item analysis | Schema, recovery, evidence marker | Not called per item in bulk imports (cost); departments/enums configurable; store with evidence IDs |
| ⑤b Grounded Q&A | Prompt rules, refusals, sources | Scope guard generalised to "this organisation's feedback"; errors raised, not returned as answers; retrieval injected as a function; token/cost accounting; label as beta until retrieval evaluated |
| ⓪ Discovery | Merge logic | Later: background job suggesting categories from an organisation's own negative feedback |
| New: aggregation | — | Trends per category and period, emerging-issue signal from the Unclassified share, evidence-linked category summaries |

### 7.2 Proposed engine interface (sketch, finalised in M3)

The goal is plain functions and small data classes, with no framework and no hidden globals:

```python
# engine/types.py
@dataclass
class FeedbackItem:
    id: str                 # the caller's ID (a database UUID later)
    text: str

@dataclass
class Category:
    id: str
    name: str
    description: str        # used as the NLI hypothesis, exactly as today
    exemplars: list[str]

@dataclass
class ItemAnalysis:
    feedback_id: str
    sentiment_label: str
    sentiment_confidence: float
    sentiment_scores: dict[str, float]
    category_id: str | None           # None when the sentiment gate skips categorisation
    category_confidence: float | None
    is_unclassified: bool
    candidates: list[tuple[str, float]]
    keywords: list[str]
    model_versions: dict[str, str]

# engine/pipeline.py
def analyse_batch(items: list[FeedbackItem], categories: list[Category]) -> list[ItemAnalysis]: ...

# engine/grounded_qa.py
def answer_question(question: str, retrieve: Callable[[str], list[Evidence]]) -> GroundedAnswer: ...
```

The engine **does not know** about databases, organisations, users or HTTP. The service layer loads an organisation's categories, calls `analyse_batch`, and stores results. For Q&A, it passes a `retrieve` function that already filters by organisation.

### 7.3 Definition of "production-ready engine"

- [ ] Imports without FastAPI, database or Streamlit code; no work at import time
- [ ] Analyses a list of arbitrary texts with caller-supplied categories
- [ ] Batched inference with measured throughput (items/second on target hardware)
- [ ] Every result carries model and prompt versions
- [ ] Failures are explicit (typed errors or per-item status), never silent fallbacks disguised as success
- [ ] Preprocessing used at inference matches what was evaluated, and that choice is documented
- [ ] Sentiment gate before categorisation, evaluated on the benchmark
- [ ] Unit tests with stubbed models run offline in seconds
- [ ] Benchmark on a fixed sample reproduces the pre-refactor metrics (±0.01) or documents why not
- [ ] LLM calls go through one adapter with timeout, retries and token counting

---

## 8. Product vision vs what exists

| Vision capability | Exists today? | Where / notes |
|---|---|---|
| Create an organisation | No | — |
| Invite team members | No | — |
| Upload/import feedback | **Partial** | CSV ≤200 rows, sentiment only, not saved (`05_Upload.py`) |
| Analyse feedback | **Yes, one item at a time** | `/api/sentiment/analyse` |
| View sentiment | **Yes** | per item (model); corpus dashboard (rating labels) |
| Identify topics/issues | **Yes** | 24 categories + Unclassified; discovery pipeline offline |
| Discover trends | **Partial** | monthly counts of dataset labels only |
| View customer insights | **Partial** | per-item LLM analysis; grounded Q&A with sources |
| Generate reports | No | — |
| Track changes over time | No | nothing is stored |
| Actionable recommendations | **Partial** | per-item "business insight"; one "Recommendation" line in Q&A answers |
| Manage users and permissions | No | — |

## 9. Recommended minimum product core

What a first design-partner customer needs, built from the pieces above:

```text
Import feedback (CSV)
      ↓
Sentiment (DistilBERT, batched)            ← dissertation ②
      ↓
Issue categories (zero-shot, org-editable) ← dissertation ③, with sentiment gate
      ↓
Stored results over time
      ↓
Dashboard: sentiment and category trends   ← new aggregation
      ↓
Category insights with evidence            ← dissertation ⑤a pattern + ④ evidence
      ↓
Ask-your-feedback Q&A (beta)               ← dissertation ⑤b, once retrieval is evaluated on customer-like data
```

Everything in the first five boxes has strong dissertation evidence behind it. The last box is the most impressive feature, but it has the weakest measured component (retrieval), so ship it clearly labelled as beta.
