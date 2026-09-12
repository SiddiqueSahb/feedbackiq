# Milestone 2 — Clean Project Structure + Installable Package

> Completed 2026-09-12 · Repository: **https://github.com/SiddiqueSahb/feedbackiq** (public, `main`)
> Previous: [milestone-01.md](milestone-01.md) · Plan: [07 — roadmap](07-production-roadmap.md#m2--clean-project-structure)

**Structural milestone.** FeedbackIQ is now an installable Python package with one
configuration object, container-friendly logging and separated dependency groups.
**No analytical behaviour changed** — proven below by identical model predictions and
identical evaluation output.

---

## 1. Before

| Aspect | How it worked |
|---|---|
| Imports | Nothing was installable. Every module began by editing `sys.path`: **50 occurrences** across `backend/` (11), `evaluate/` (10), `frontend/` (8), `scripts/` (8), `nlp/` (6), `tests/` (5), `rag/` (1) and `test_pipeline.py`. Imports only resolved if the process happened to start in the right directory. |
| Layout | Flat top-level folders: `backend/`, `nlp/`, `rag/`, plus `config.py`, `logger.py`, `exceptions.py` at the root. Product code and research code sat side by side with nothing distinguishing them. |
| Configuration | `config.py` held 28 settings, **9 of which nothing read**. `ENVIRONMENT` was read with `os.getenv` in two places instead of through settings. Tuning constants lived in code: `SIMILARITY_THRESHOLD = 0.35` in `rag/pipeline.py`, a duplicate `ANALYSE_SIMILARITY_THRESHOLD = 0.35` in `nlp/summariser.py`, `k=5`/`fetch_k=20`/`50`, `MAX_REVIEW_CHARS`, retry budgets, `max_length=128`, `text[:400]`, `timeout=30`. |
| Paths | Relative to the current working directory (`"data/processed/…"`), or rebuilt per module with `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` — 20+ times. |
| Logging | `logger.py` wrote to `logs/feedbackanalytics.log`, relative to wherever the process started, with a rotating file handler per logger — and a debug `print("Inside get_logger()")` on every call. `nlp/categoriser.py` and `nlp/sentiment.py` printed status to stdout. |
| Dependencies | One `requirements.txt` mixing runtime, research and test packages (torch next to `ragas`, `mlflow`, `wordcloud`), plus `backend/requirements-extra.txt` and `frontend/requirements.txt`. |
| Entry point | `uvicorn backend.main:app` |

## 2. After

```text
feedbackiq/
├── pyproject.toml              packaging · dependency groups · pytest configuration
├── src/feedbackiq/             the installable application package
│   ├── core/                   config.py · logging.py · paths.py · exceptions.py
│   ├── api/                    main.py · deps.py · routes/ · schemas.py
│   ├── services/               five service modules (unchanged logic)
│   ├── nlp/                    sentiment · classical_models · categoriser ·
│   │                           embedding_service · summariser · tf_idf_embedding
│   └── rag/                    pipeline.py · prompts.py
├── frontend/                   Streamlit app + app_settings.py + Dockerfile
├── backend/                    build files for the API image only (Dockerfile,
│                               requirements-extra.txt)
├── tests/unit, tests/api       the safety net (unchanged assertions)
├── evaluate/, scripts/         dissertation evaluation and offline pipeline (in place)
├── notebooks/, data/, models/  dissertation artefacts (untouched)
└── docs/production/            audit + milestone documentation
```

- `pip install -e .` works; **no `sys.path` manipulation remains anywhere** in tracked Python files.
- Entry point is now `uvicorn feedbackiq.api.main:app`.
- Every file moved with `git mv`, so history follows each one.

> **A note on the commit history.** Commit `b23e227` carries the message
> "Make FeedbackIQ an installable package", but a staging mistake in my commit script
> meant it contains only the `git mv` renames and the `pytest.ini` deletion —
> `pyproject.toml`, `core/paths.py` and every content change under `src/` were left out,
> and that incomplete state was pushed (its CI run failed at `pip install -e ".[dev]"`,
> exactly as it should have). Commit **`122b438`** adds what was missing. The published
> history was not rewritten, so `b23e227`'s message reads as broader than its contents;
> read the two commits together.

## 3. Package architecture

| Package | Responsibility | Why it exists |
|---|---|---|
| `feedbackiq.core` | Settings, logging, path resolution, exception types. | Everything else depends on these and they depend on nothing of ours — one obvious place to look for "how is this configured". |
| `feedbackiq.api` | HTTP only: the FastAPI app, the API-key dependency, the five routers, and the Pydantic request/response schemas. | Keeps transport concerns out of the ML code, which was already the project's shape. |
| `feedbackiq.services` | Orchestration between the API and the ML code (the analysis pipeline, analytics aggregation, search, RAG, stored evaluation results). | The layer a future database or worker plugs into without touching routes or models. |
| `feedbackiq.nlp` | Sentiment models, complaint categorisation, embeddings, keyword-free LLM analysis. | The dissertation's model layer, unchanged. |
| `feedbackiq.rag` | Retrieval-augmented question answering and its prompts. | Separate from `nlp` because it has its own evaluation regime and failure modes. |

**Two naming decisions worth recording:**

- `backend/models/schemas.py` became **`api/schemas.py`**. No source directory is called
  `models` any more, which is precisely what the unanchored `models/` gitignore rule
  exploited in Milestone 1 to hide those files from git.
- No `exceptions/` package was created — `core/exceptions.py` is a single module, because
  a folder holding one file adds nothing.

**Not created:** `feedbackiq/config/` (a package for one settings module), `feedbackiq/models/`,
`domain/`, `adapters/`, `interfaces/`. Nothing was added to make the tree look enterprise-like.

## 4. Configuration

Everything lives in **`src/feedbackiq/core/config.py`** — one `Settings` object read from
the environment, then `.env`, then defaults.

**Every default is the value the code already used.** Configuration controls behaviour here;
it does not change it.

| Change | Detail |
|---|---|
| Added `ENVIRONMENT` | Was `os.getenv("ENVIRONMENT", …)` in `api/deps.py` and `api/main.py`; now `settings.ENVIRONMENT` with an `is_production` helper. Same production boot guard. |
| Thresholds became settings | `SIMILARITY_THRESHOLD`, `ANALYSE_SIMILARITY_THRESHOLD`, `CATEGORY_CONFIDENCE_THRESHOLD` — **all still 0.35**. Also `RAG_TOP_K=5`, `RAG_FETCH_K=20`, `RAG_FETCH_K_WITH_FILTER=50`, `RAG_MMR_LAMBDA=0.5`, `MAX_REVIEW_CHARS=1000`, `MAX_LIVE_RETRIES=2`, `RETRY_BASE_WAIT=2.0`, `LLM_TIMEOUT=30`, `CATEGORY_MAX_CHARS=400`. |
| Module constants kept as aliases | `rag.pipeline.SIMILARITY_THRESHOLD` and `summariser.ANALYSE_SIMILARITY_THRESHOLD` still exist, now reading from settings. The existing tests assert `== 0.35` and were **not** rewritten. |
| `MAX_SEQ_LENGTH` wired up | It existed but nothing read it while `max_length=128` was hard-coded in `sentiment.py`. Same number, now one source. |
| Paths resolved, not guessed | `core/paths.py` finds the project root (nearest ancestor with `pyproject.toml`/`.git`, falling back to the working directory) and `settings.data_file`, `.model_dir`, `.index_file`, `.langchain_index_dir`, `.classical_model_dir`, `.taxonomy_dir`, `.results_dir`, `.keywords_file` return absolute paths. An absolute value in the environment is honoured unchanged, so a mounted volume works. |
| Path constants removed from modules | The `os.path.dirname(os.path.dirname(...))` chains in `classical_models.py`, `categoriser.py`, `evaluation_service.py` and the working-directory-relative `KEYWORDS_FILE` in `analytics_service.py` are gone. |
| 8 settings removed | `RAW_DATA_PATH`, `EMBEDDING_DIM`, `TOP_K_RETRIEVAL`, `API_HOST`, `API_PORT`, `GOOGLE_API_KEY`, `ZEROSHOT_BASELINE_MODEL` (nothing read them; the BART baseline is recorded in a comment), and `API_URL` (moved to the frontend's own settings). |
| `LOG_LEVEL` kept | It looked unused as `settings.LOG_LEVEL`, but `logger.py` read the same variable via `os.getenv`. It is now read through settings, as intended. |
| Deprecation fixed | `class Config` → `SettingsConfigDict`, which also removes the `PydanticDeprecatedSince20` warning the suite reported. `extra="ignore"` so the shared `.env` can hold frontend-only keys. |

The frontend has its own tiny **`frontend/app_settings.py`** (`API_URL`, `API_KEY`, two
timeouts, same defaults, same environment-then-`.env` order). It deliberately does not
import `feedbackiq.core.config`, so the Streamlit image never pulls in torch.

## 5. Dependencies

Declared in `pyproject.toml` with the **exact pins from `requirements.txt`** — no version
was added, removed or upgraded.

| Group | Contents | Rationale |
|---|---|---|
| runtime (`dependencies`) | fastapi, uvicorn, pydantic(+settings), python-dotenv, pandas, numpy, pyarrow, scikit-learn, torch, transformers, sentence-transformers, faiss-cpu, huggingface-hub, the six langchain packages, groq, spacy, vaderSentiment | What the API and the ML code import. `scikit-learn` is runtime, not research: the pickled TF-IDF vectoriser and classical models cannot be unpickled without it. |
| `dev` | pytest, pytest-asyncio, flake8, httpx | Test suite and linter. |
| `research` | mlflow, ragas, datasets, bertopic, umap-learn, hdbscan, langdetect, matplotlib, wordcloud, scipy, tqdm | Imported only by `evaluate/*` and `scripts/*`; deliberately absent from the API image. |
| `frontend` | streamlit, plotly, altair, pandas, requests, pydantic(+settings), python-dotenv | The Streamlit app only; keeps that image light. |

**The Milestone 1 discrepancy, investigated:**

| Package | Verdict |
|---|---|
| `ragas` (+`datasets`) | **Research.** Imported by `evaluate/{evaluate_llm_vs_rag,ablation_metadata_tagging,rag_check}.py` only. Pinned in the `research` extra; it was never needed at runtime, which is why its absence locally broke nothing. |
| `bertopic` (+`umap-learn`, `hdbscan`, `langdetect`) | **Research.** Imported only by `scripts/discover_categories.py`. Installed locally but unpinned before; now pinned at the installed versions (0.17.4 / 0.5.12 / 0.8.44 / 1.0.9). |
| `emoji` | **Deliberately not added.** Used behind a `try/except ImportError` in `nlp/classical_models.py` and `scripts/preprocess.py`, and not installed. Installing it would make emoji conversion start happening at inference, which **changes preprocessing behaviour** — exactly what this milestone forbids. It stays out, and the train/serve preprocessing question is a Milestone 3 decision (see [03 §5](03-feedbackiq-core.md#5-what-is-tightly-coupled-to-the-dissertation-prototype)). |
| `seaborn` | Not added. Used only inside notebooks, which are gitignored and never run in CI. |
| `mlflow` | Research. Only `scripts/train_classical_models.py` and `scripts/evaluate_models.py` import it. |

`requirements.txt` and the two other requirements files are **kept unchanged**: `README.md`
and `SUBMISSION.md` document them as the dissertation's reproduction pins. `pyproject.toml`
is the source of truth for the product; the pins in both agree.

## 6. Logging

`core/logging.py` replaces `logger.py`:

- **stdout only by default.** One handler on the root logger, so every module formats
  identically and nothing is duplicated. The container platform collects the stream.
- **No file unless asked.** `LOG_FILE` (empty by default) enables a rotating file handler.
  Nothing writes to `logs/feedbackanalytics.log` any more.
- **Same levels, names and message format** (`%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`),
  so existing lines read the same. Level comes from `settings.LOG_LEVEL` (still `WARNING`).
- **The debug `print` is gone**, and the status `print`s in `nlp/categoriser.py` and
  `nlp/sentiment.py` became `log.info`/`log.warning`.
- **Error meanings unchanged**: no exception handling, HTTP status mapping or message text was touched.
- **Tests** no longer patch anything: `tests/conftest.py` sets `LOG_FILE=""`. Verified — the old
  log file stayed at 1796 lines across every run in this milestone. (Its last two lines are
  timestamped 16:09 and 16:27, written by the old code before the rewrite landed.)

## 7. Research / product separation

**Product:** `src/feedbackiq/` (installable, linted in CI, covered by tests).
**Research:** `evaluate/`, `scripts/`, `notebooks/`, `data/`, `models/`, `mlruns/`.

**What moved:** only product modules — `backend/*`, `nlp/`, `rag/`, `config.py`, `logger.py`,
`exceptions.py` — into `src/feedbackiq/`.

**What deliberately did not move, and why:**

| Left in place | Reason |
|---|---|
| `evaluate/`, `scripts/` | `README.md`, `RESULTS.md` and `SUBMISSION.md` document exact reproduction commands (`python scripts/evaluate_models.py`, `python evaluate/generate_all_metrics.py`, …). Moving them under `research/` would invalidate published instructions for no functional gain. The roadmap's sketch suggested a `research/` folder; this is a documented deviation. |
| `notebooks/` | Gitignored, never run in CI, and full of absolute paths from the machines that ran them. Editing them would rewrite dissertation history. **Not touched at all.** |
| `data/`, `models/`, `mlruns/` | Artefacts. No file modified (verified). |
| `requirements*.txt` | Dissertation reproduction pins (see §5). |

**What changed in research code:** only import lines (`from nlp.sentiment import …` →
`from feedbackiq.nlp.sentiment import …`) and the now-pointless `sys.path` blocks. No
methodology, no parameters, no data, no results.

**How the separation is now enforced:** dependency groups (research libraries are not
runtime), `.dockerignore` (research directories never enter an image), and CI lint scope
(product code only).

## 8. Behaviour verification

| Check | Result |
|---|---|
| **Test suite** | **104 passed, 4 xfailed** — identical to Milestone 1. No test was weakened, deleted or rewritten to pass; the four strict `xfail`s still document the same four defects. 108 collected. |
| **Sentiment, classical models, VADER** | Ten fixed reviews scored with the local artefacts before and after the refactor. Canonical JSON md5 **`08aaf4cc8c88b9958245155185492ac8` both times — byte-identical**, covering labels, confidences and all per-class scores. |
| **Categorisation** | Threshold rules, shortlist behaviour and the "Unclassified / Emerging Complaint" outcome covered by 10 tests, all passing; `CATEGORY_CONFIDENCE_THRESHOLD` still 0.35. |
| **Retrieval** | Threshold ∩ MMR logic, inclusive 0.35 boundary, 1,000-character cap and score attachment covered by tests, all passing. |
| **Grounded Q&A and refusals** | 17 tests including the no-evidence refusal without an LLM call, the discarded follow-up answer, and both scope guards — all passing. |
| **Structured LLM output handling** | 17 tests covering all four recovery shapes, schema enforcement and the empty-analysis fallback — all passing. |
| **Evaluation reproducibility** | `python evaluate/generate_all_metrics.py` re-run: exit 0, and every file in `data/results/metrics_summary/` is **unchanged** (md5 comparison). |
| **API** | Imports cleanly and still exposes **22 routes**; authentication behaviour untouched. |
| **Streamlit** | Actually started (`streamlit run frontend/app.py`): `/_stcore/health` 200, main page 200, no import errors — confirming the pages no longer need `sys.path`. |
| **Settings** | Thresholds 0.35/0.35/0.35, k values 5/20/50/0.5, `MAX_SEQ_LENGTH` 128, shortlist 6, `CATEGORY_MAX_CHARS` 400; all resolved paths absolute and present on disk. |
| **Lint** | flake8 `E9,F` clean on `src/`, `frontend/` and `tests/`. |
| **Packaging** | `pip install -e . --no-deps` succeeds; `import feedbackiq` resolves to `src/feedbackiq/`; no `sys.path` in any tracked Python file. |

What could **not** be compared deterministically: anything requiring the Groq API (the LLM
generates different text per call, and there is no key in this folder) and the FAISS-backed
searches (loading the 1 GB index is out of scope for a structural check). Both are covered
instead by the unit tests, which exercise the surrounding logic with fakes, and by
`evaluate/verify_rag_fixes.py`, whose five refusal cases pass without a key.

## 9. Docker verification

| Image | Result |
|---|---|
| `frontend` | Full build, **exit 0**. Now copies only `frontend/` and `.streamlit/` — no `config.py`/`logger.py`, because it reads `frontend/app_settings.py`. |
| `backend` | All layers build, **exit 0** (`--output type=cacheonly`, which avoids the local Docker Desktop export bug from Milestone 1). The new layer reports `Successfully installed feedbackiq-0.1.0`. |

Changes were limited to what the package layout required: the API image now copies
`pyproject.toml`, `README.md` and `src/`, installs the package with `pip install -e . --no-deps`
(so no dependency version changes), and starts `uvicorn feedbackiq.api.main:app`.
`docker-compose.yml` needed no structural change. Both images also build in CI.

**CI** now runs: lint (`src/`, `frontend/`, `tests/`) → `pip install -e ".[dev]"` + pytest →
both Docker images. Installing from `pyproject.toml` in a clean runner is the real proof
that packaging works.

## 10. Known issues

| # | Issue | Note |
|---|---|---|
| 1 | Four known defects remain as strict `xfail` | Unchanged from Milestone 1: exception text returned to RAG users, complaint categories for positive feedback, `/analyse` ignoring `platform`, length limit applied before cleaning. |
| 2 | `scripts/` and `evaluate/` have pre-existing lint findings | 26 flake8 `F401`/`F811`/`F541` issues (unused `numpy`, duplicated `import os, sys`, f-strings without placeholders). They were never linted, and fixing them means editing research files, so CI lint stays scoped to product code. |
| 3 | `venv/` is still not self-contained | Milestone 1 issue 6.3. `venv/bin/pip` and `venv/bin/pytest` carry `~/Desktop/...` shebangs, so this milestone used `python -m pip` / `python -m pytest`, which always target the interpreter's own environment. A fresh `python -m venv` would fix it properly. |
| 4 | Two dependency files describe the same pins | `pyproject.toml` (product) and `requirements.txt` (dissertation reproduction). Kept deliberately (§5); they agree today and can drift if only one is edited. |
| 5 | `emoji` still absent | Deliberate (§5). The train/serve preprocessing mismatch is a Milestone 3 decision. |
| 6 | `backend/` holds only Docker build files | Keeping the path avoids churn in `docker-compose.yml`, CI and the deployment docs, but the name no longer describes the contents. |
| 7 | Local backend image export still fails | Docker Desktop containerd bug; CI builds the image fine. |
| 8 | Old log file retained | `logs/feedbackanalytics.log` (1796 lines, gitignored) is historical; nothing writes to it now. |
| 9 | Notebooks reference the old flat layout | Intentional: they are dissertation history and were not edited. |
| 10 | `reviews.faiss` still not reproducible from code | Audit finding, unchanged by this milestone; belongs to Milestone 3. |

## 11. Next milestone

**Milestone 3 — Production-ready analytics engine** ([roadmap](07-production-roadmap.md#m3--production-ready-analytics-engine)).

- **Objective:** turn `feedbackiq.nlp` + `feedbackiq.rag` into an engine that analyses **any**
  list of feedback texts — batched, typed inputs and outputs, model versions recorded —
  instead of one review at a time against the fixed research corpus.
- **Why next:** it is the last piece that is still shaped by the dissertation prototype, and
  the database (Milestone 4) should be designed around the engine's real output shapes, not
  the current ad-hoc dicts.
- **Main work:** `analyse_batch()` with batched DistilBERT inference; categories passed in
  rather than loaded at import; the sentiment gate that stops positive feedback receiving
  complaint categories (one of the four `xfail`s); decide and document the train/serve
  preprocessing question including `emoji`; retrieval injected as a function; errors raised
  instead of returned as answer text; one LLM adapter with timeouts, retries and token
  accounting; a model manifest and prompt versions; a benchmark on a fixed corpus sample
  with a metric floor.
- **Verification:** the suite stays green, benchmark macro F1 within ±0.01 of the pre-refactor
  run on the same sample, and throughput measured and recorded.

**Not started.** Waiting for your approval.
