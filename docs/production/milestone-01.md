# Milestone 1 — Repository Foundation + Safety Net

> Completed 2026-09-12 · Repository: **https://github.com/SiddiqueSahb/feedbackiq** (public, `main`)
> Audit that led here: [01–09](01-current-system.md) · Recommendation followed: [08 — Next step](08-next-step.md)

**Goal:** make change *safe* before anything is restructured. One git-tracked working copy, a dissertation baseline, and an automated test suite that runs in seconds with no models, data, API keys or network — plus CI that runs it.

**No production code was changed.** The one code-adjacent change was recovering two files that `.gitignore` had been hiding (see "What changed", item 6).

---

## 1. What changed

### 1.1 A new repository, with your decisions applied

| Decision | Outcome |
|---|---|
| New repo, fresh history | `git init` in `~/Documents/reviewAnalytics`; first commit is the dissertation code. The old private repo `SiddiqueSahb/FeedbackAnalytics_LLM` keeps its 17 commits and is untouched, as is `~/Desktop/FeedbackAnalytics_LLM`. |
| Public on GitHub | `SiddiqueSahb/feedbackiq`, pushed to `main` (never force-pushed). |
| Don't publish personal material | `SUBMISSION.md`, `FeedbackIQ_Case_Study.pdf`, `GCP_DEPLOY_RUNBOOK.pdf` and `notebooks/` are **not** in the repo. They stay on disk, gitignored, and were verified byte-identical to the dissertation version afterwards. |
| Don't publish your university email | Repo-local git identity is `Mohammad Asim <25613009+SiddiqueSahb@users.noreply.github.com>`. `ms05193@surrey.ac.uk` appears in no commit. |
| Don't tag yet | No `dissertation-submission` tag was created. Commit `6f8e221` serves as the baseline until you confirm which version you submitted. |

### 1.2 Commits

| Commit | What it does |
|---|---|
| `6f8e221` | **Baseline import** — 94 files, each verified byte-identical to `FeedbackAnalytics_LLM@3c4acf1` via `git hash-object` (100 tracked files there, minus the 6 withheld). |
| `19783e3` | Ignore rules for the withheld files and `.pytest_cache/`. |
| `c022f10` | The pytest safety net (`pytest.ini`, `tests/conftest.py`, `tests/unit/`, `tests/api/`). |
| `9090d63` | CI runs the tests before building images. |
| `38b18e5` | The audit documents `docs/production/01–09`. |
| `223be5e` | **Fix:** track `backend/models/`, which `.gitignore` excluded by accident (see 1.4). |

### 1.3 Project configuration recovered

These existed only in the Desktop repo, never in this folder. Each was copied unchanged and verified with `cmp`:

| File | Where it came from | Why it's needed | Affects production behaviour? |
|---|---|---|---|
| `.gitignore` | Desktop repo | Without it, `git add` would stage ~13 GB of data, models and venv | No — git only |
| `.dockerignore` | Desktop repo | Keeps `data/`, `models/`, `venv/` out of the build context (~15 GB otherwise) | No — build speed only |
| `.streamlit/config.toml` | Desktop repo | The frontend Dockerfile does `COPY .streamlit/`; **without it the frontend image cannot build** | Yes — required for the image |
| `.env.example` | Desktop repo | Documents the required environment variables; contains no secrets | No |
| `.github/workflows/ci.yml` | Desktop repo, then extended | Existing lint + image build; a test job was added | No — CI only |

`.env` was deliberately **not** copied. It holds a real Groq key, and this folder still has none.

### 1.4 One real bug found and fixed

CI failed on its very first run with `ModuleNotFoundError: No module named 'backend.models'`.

- `.gitignore` line 56 was `models/`, unanchored. Git applies such a pattern at *any* depth, so besides the intended top-level `models/` artefacts directory it also matched `backend/models/`.
- Consequently `backend/models/__init__.py` and `backend/models/schemas.py` — the Pydantic request/response models every route imports — **were never committed, in the dissertation repository either**. Everything worked locally only because the files sit on disk.
- **Impact beyond this milestone:** the `git clone` instructions in `GCP_DEPLOY_RUNBOOK.md` and `DEPLOY.md` could never have produced a working backend.
- **Fix:** anchored the artefact rules (`/models/`, `/data/`, and their sub-rules) so they still ignore the real artefacts but no longer swallow source directories with those names, then committed the two files unchanged.

This is precisely what the safety net was for, and it appeared within minutes of CI existing.

### 1.5 Test suite added

```text
pytest.ini                      testpaths (tests/unit, tests/api) · pythonpath · -ra
tests/conftest.py               offline env vars · test API key · log redirect · ignores the old scripts
tests/unit/test_input_validation.py     16 tests
tests/unit/test_categoriser.py          10 tests
tests/unit/test_topic_merge.py           4 tests
tests/unit/test_rag_grounding.py        17 tests
tests/unit/test_llm_output_recovery.py  17 tests
tests/unit/test_review_pipeline.py       8 tests
tests/api/test_api.py                   36 tests
```

---

## 2. What did NOT change

Confirmed explicitly:

| Area | Status |
|---|---|
| **ML models** | Untouched. No file under `models/` modified; no training, no retraining, no threshold changed. |
| **Analytics algorithms** | Untouched. `analytics_service.py`, keyword rules and aggregations are byte-identical to the baseline. |
| **API design** | Untouched. Same 17 endpoints + root, same paths, same schemas, same status codes. 22 routes before and after. |
| **Database** | Still none. No PostgreSQL, no migrations, no ORM. |
| **Frontend** | Untouched. No Streamlit file modified; no React. |
| **SaaS functionality** | None added. No users, organisations, authentication, tenancy, billing or usage tracking. |
| **Dissertation files** | Nothing deleted. `evaluate/`, `scripts/`, `RESULTS.md`, `README.md`, `data/results/`, `mlruns/` all unchanged. |
| **Notebooks** | Unchanged on disk and verified byte-identical; simply not published. |
| **Benchmark/demo data** | Untouched. `data/` and `models/` contain no modified files (only macOS `.DS_Store` metadata has newer timestamps). |
| **Dependencies** | None added. pytest 8.1.1, pytest-asyncio, httpx and flake8 were already installed and already pinned in `requirements.txt`. |
| **Existing check scripts** | Unchanged and still runnable directly; pytest just doesn't collect them. |
| **Logging architecture** | Unchanged (see §6.5). |

Verified with: `git diff 6f8e221 --stat -- nlp rag backend frontend scripts evaluate config.py logger.py` → only `backend/models/` appears, as an addition.

---

## 3. Tests added

108 tests: 104 passing, 4 recording known defects. Behaviour first, not implementation details.

### Authentication — `tests/api/test_api.py`

| Test | Protects |
|---|---|
| `test_health_check_is_open_without_a_key` | Load balancers can probe `/api/health` unauthenticated |
| `test_missing_api_key_is_rejected` | 401 plus a message naming the `x-api-key` header |
| `test_wrong_api_key_is_rejected` | 403 "Invalid API key." |
| `test_valid_api_key_is_accepted` | A correct key works, and all five models are listed |
| `test_every_api_route_requires_a_key` (18 cases) | Enumerates every `/api` route from the app and asserts 401 without a key — so a new endpoint added later cannot be accidentally public |

### Thresholds and refusal logic

| Test | Protects |
|---|---|
| `test_settings_match_the_dissertation` | Category confidence is 0.35, shortlist size 6 |
| `test_best_score_below_threshold_is_reported_as_unclassified` | Weak matches become "Unclassified / Emerging Complaint" instead of a forced category, and only the top pick is replaced |
| `test_score_exactly_at_threshold_is_classified` | The boundary is inclusive |
| `test_only_the_shortlist_reaches_the_classifier` | The two-stage design: NLI only ever sees the shortlist |
| `test_default_shortlist_is_capped_by_the_taxonomy_size` | No crash when the taxonomy is smaller than the shortlist |
| `test_review_is_assigned_the_matching_category` | Ported from `tests/test_categoriser_shortlist.py` |
| `test_empty_taxonomy_returns_no_categories` | Missing taxonomy degrades safely |
| `test_similarity_threshold_matches_the_dissertation` | Retrieval threshold is 0.35 |
| `test_retriever_keeps_reviews_at_or_above_the_threshold` | Inclusive boundary; a review 0.0001 below is dropped |
| `test_mmr_cannot_bring_back_a_review_below_the_threshold` | The threshold ∩ MMR intersection — diversity cannot smuggle in weak evidence |
| `test_retrieved_reviews_carry_their_score_and_are_capped_in_length` | Scores attached, text capped at 1,000 chars, the store's own documents not mutated |
| `test_evidence_threshold_matches_the_dissertation` | The analysis path's 0.35 threshold |

### Grounded Q&A — `tests/unit/test_rag_grounding.py`

| Test | Protects |
|---|---|
| `test_answer_is_built_from_retrieved_evidence_and_cites_it` | Valid evidence produces an answer; sources carry review IDs and scores; the prompt actually contains the metadata tags, the review text and the refusal rule |
| `test_no_evidence_means_a_refusal_and_no_generated_answer` | Insufficient evidence → fixed refusal, **and the LLM is never called** (no chance to invent) |
| `test_follow_up_answer_without_evidence_is_discarded` | The multi-turn backstop: the model did write an answer, and it was thrown away rather than shown |
| `test_keyword_guard_refuses_out_of_scope_questions_before_retrieval` | Off-topic and prompt-injection attempts are refused before any retrieval (ported from `verify_rag_fixes.py` B/C cases) |
| `test_llm_guard_refuses_questions_it_marks_out_of_scope` | The second-stage guard, with nothing retrieved |
| `test_missing_llm_configuration_is_reported_not_answered` | No API key produces a clear message, not a fabricated answer |
| `test_platform_named_in_the_question_filters_retrieval` | "on Amazon" applies a metadata filter and widens the pool to 50 (ported from `verify_rag_fixes.py` A1) |
| `test_rate_limit_errors_are_retried_until_success` / `test_other_errors_are_not_retried` | Only rate limits retry, with the provider's own wait plus a margin |

### LLM structured output recovery — `tests/unit/test_llm_output_recovery.py`

| Test | Protects |
|---|---|
| `test_recovers_json_from_a_failed_tool_call` | The real Groq `tool_use_failed` path that fires with `openai/gpt-oss-20b` |
| `test_recovers_json_wrapped_in_a_markdown_fence` / `..._surrounded_by_prose` | Both messy shapes the model produces |
| `test_recovers_from_an_error_that_only_has_a_message` | The fallback for clients that expose the payload only in the message text |
| `test_other_provider_errors_are_not_treated_as_recoverable` | A rate-limit error is not mistaken for recoverable output |
| `test_recovered_json_must_still_match_the_schema` (3 cases) | Recovery still enforces 5 keywords and the severity/department enums |
| `test_valid_structured_output_is_returned_as_a_dict` | The happy path |
| `test_unrecoverable_llm_failure_returns_an_empty_analysis` / `test_schema_violation_...` | Failures degrade to an empty analysis, never a 500 |
| `test_weak_or_missing_evidence_is_declared_as_none` (2 cases) | Weak matches never reach the prompt as evidence; the explicit marker is sent (ported from `verify_analyse_fixes.py` cases 1–2) |
| `test_only_evidence_at_or_above_threshold_reaches_the_prompt` | Strong evidence included, weak dropped (case 3) |

### Input validation — `tests/unit/test_input_validation.py`, plus API-level checks

Length limits, control-character stripping, rejection of unknown models/platforms, `top_k` 1–50, rating 1–5, blank batch rows dropped, 200-row cap, 20-message history cap. At HTTP level, six invalid requests return 422 while every service function is wired to fail the test if called — proving invalid input never reaches a model.

### Pipeline behaviour — `tests/unit/test_review_pipeline.py`, `tests/unit/test_topic_merge.py`

Four-stage composition and each fallback (sentiment → VADER, categoriser → "General Feedback", retrieval → none, LLM → empty analysis), one bad batch row isolated as `"error"`, blank review rejected. Topic merging keeps battery and fee topics apart despite both containing the word "charge" (ported from `tests/test_merge_topics.py`).

### Error mapping — `tests/api/test_api.py`

Expected errors → 422 with their message; unexpected errors → 500 with a generic detail and **no internal text leaked**; missing index → 503; response shapes the Streamlit pages rely on (`score` → `confidence`, flat LLM fields nested under `analysis`, numeric pandas IDs coerced to strings); RAG refusals passed through as `grounded: false`.

### Known defects recorded as strict `xfail`

They fail today by design, and will turn red the moment they're fixed, forcing the test to be updated:

| Test | Defect |
|---|---|
| `test_internal_errors_are_not_shown_to_the_user` | `rag.pipeline.ask()` returns `"An error occurred: {exception}"` to users with HTTP 200 |
| `test_positive_feedback_is_not_given_a_complaint_category` | No sentiment gate before categorisation (the README states this) |
| `test_analyse_passes_platform_to_the_pipeline` | `/api/sentiment/analyse` accepts `platform` and ignores it |
| `test_length_limit_applies_after_cleaning` | `min_length=3` is checked before stripping, so `"  a  "` is accepted |

---

## 4. Test results

```text
$ pytest
104 passed, 4 xfailed, 1 warning in 10.77s        (this folder)
104 passed, 4 xfailed, 1 warning in  9.36s        (fresh clone of the public repo: no data, no models, no .env)
104 passed, 4 xfailed, 2 warnings in 6.71s        (CI, ubuntu-latest)
```

- **Failures: 0. Skipped: 0. Errors: 0.**
- **Warnings:** 1 locally — `PydanticDeprecatedSince20`, raised by the class-based `Config` in `config.py`. Pre-existing, unrelated to the tests, and scheduled for Milestone 2. CI shows a second, also pre-existing: an `anyio.abc.BlockingPortal` deprecation inside Starlette's TestClient.
- **Offline:** no Hugging Face downloads, no Groq calls, no database. `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` are set in `conftest.py` and the Groq key is forced empty.
- **No log pollution:** the project log stayed at 1794 lines across pytest runs (§6.5).

### Existing check scripts (Step 5, run unchanged)

| Script | Exit | Result |
|---|---|---|
| `tests/test_categoriser_shortlist.py` | 0 | ALL TESTS PASSED |
| `tests/test_merge_topics.py` | 0 | All three PASS assertions hold; coherence −0.0396 |
| `evaluate/verify_analyse_fixes.py` | 0 | All three cases report a valid shape — but **only in degraded mode**: with no Groq key every analysis is the empty fallback, which this script accepts as a pass. Its own output tells you to eyeball the insight text, which was blank. |
| `evaluate/verify_rag_fixes.py` | 1 | **3 of 8 cases fail** (A1, A2, A3): they need a live LLM to produce grounded answers, and there is no `GROQ_API_KEY` in this folder. The five refusal cases pass. Not a regression — the script requires a key by design. |
| `tests/test_faiss.py`, `test_retriever.py`, `test_semantic_search.py`, `debug_embedding.py`, `test_rag.py`, `test_pipeline.py` | — | Not run: exploratory scripts that load the ~1 GB index or wait for keyboard input. Unchanged and still runnable by hand. |

### Docker

| Image | Local | CI (ubuntu-latest) |
|---|---|---|
| `frontend` | ✅ built in 57 s | ✅ |
| `backend` | ⚠️ every layer built (pip install, spaCy model, all `COPY`s), then Docker Desktop failed writing the image: `failed to extract layer … Lchown …/aarch64-linux-gnu-lto-dump-14: no such file or directory`. A repeat with `--output type=cacheonly` succeeded, and `feedbackiq-backend:m1-check` (3.5 GB) is present locally. | ✅ built in the same job |

The Dockerfiles and Compose configuration are unchanged and working; the local failure is a Docker Desktop containerd storage problem, not a project problem.

---

## 5. CI

`.github/workflows/ci.yml` now runs, on pushes to `main` and PRs into it:

```text
lint  (flake8 E9,F on backend/, frontend/ and tests/)
   ↓
test  (install CPU-only PyTorch, install requirements + backend extras, pytest)
   ↓
docker-build  (backend and frontend images, cached via GitHub Actions cache)
```

Each job waits for the previous one, so images are only built if the tests pass. **No deployment, no cloud infrastructure, no databases, no secrets.**

| Run | Result |
|---|---|
| `34701321494` | **failure** — lint ✅, pytest ❌: found the missing `backend/models/` files (§1.4) |
| `34701519506` | **success** — lint ✅ 10 s · pytest ✅ 1m47s (104 passed, 4 xfailed) · Docker ✅ 9m44s |

CPU-only PyTorch is installed first, at the version `requirements.txt` pins, because the default Linux wheel bundles several GB of CUDA libraries the tests never use.

### How to run everything locally

```bash
pytest                                     # the safety net (seconds, offline)
pytest -m "" -q                            # same; no markers are in use yet
flake8 backend/ frontend/ tests/conftest.py tests/unit tests/api --select=E9,F
python tests/test_categoriser_shortlist.py # the original manual checks, unchanged
python tests/test_merge_topics.py
docker build -f frontend/Dockerfile -t feedbackiq-frontend:check .
docker build -f backend/Dockerfile  --output type=cacheonly .   # avoids the local export bug
```

---

## 6. Known issues

### 6.1 Four known defects (recorded as `xfail`, not fixed)

Listed in §3. Fixing any of them changes behaviour, which Milestone 1 deliberately avoided.

### 6.2 No dissertation tag

You chose to defer it. Nothing marks the submitted version in this repo yet; `6f8e221` is byte-identical to `FeedbackAnalytics_LLM@3c4acf1`, and the private repo retains the full history. Tag it once you've checked your submission archive.

### 6.3 `venv/` is not self-contained

`venv/pyvenv.cfg` records that it was created at `~/Desktop/FeedbackAnalytics_LLM/venv`, and `venv/bin/pytest` begins `#!/Users/mohammadasim/Desktop/FeedbackAnalytics_LLM/venv/bin/python`. The local test runs therefore used the Desktop copy's interpreter and packages. It works only while that folder exists. Recreating the environment (`python -m venv venv && pip install -r requirements.txt -r backend/requirements-extra.txt`) would fix it; CI is unaffected since it installs from scratch.

### 6.4 Installed packages don't match `requirements.txt`

`ragas` and `emoji` are pinned but not installed locally; `bertopic` is installed but not pinned. Because `emoji` is missing, `nlp/classical_models._clean()` silently skips emoji conversion at inference, while the training corpus was built with it. CI installs the full pinned set successfully. Milestone 2 covers splitting runtime from research dependencies.

### 6.5 Logging (Step 7)

**Why the warning appears:** `backend/api/deps.py` logs `"Using the development API key. Override API_KEY before deploying."` at import time, because `settings.API_KEY` still equals the `dev-key-feedbackiq` default and `ENVIRONMENT` is not `production`. It is **harmless and intentional** — a reminder, not an error.

**Why it reached the real log:** `logger.py` writes to `logs/feedbackanalytics.log` relative to the working directory, for any process that imports a project module.

**What was done, without redesigning logging:** `tests/conftest.py` sets `API_KEY` to a test value (so the warning has no reason to fire) and points `logger.LOG_FILE` at a temporary directory before any project module is imported. Verified: the project log stayed at 1794 lines across pytest runs.

**Still true:** the *manual* scripts write to the real log — they added 24 lines (`ERROR | nlp.summariser | LLM request failed.`, from running without a Groq key) during this milestone. That is existing behaviour and was left alone. Structured stdout logging is Milestone 2.

### 6.6 Smaller items

| Item | Note |
|---|---|
| README CI badge | Still points at `FeedbackAnalytics_LLM`, which is private, so the badge will not render in the new repo. README was deliberately not edited. |
| `claude.md` | Empty file in the project root, untracked. Left for you to keep, fill in as `CLAUDE.md`, or delete. |
| Notebooks not in the repo | Your choice. The only record of how DistilBERT was trained now lives on disk and in the private repo; Milestone 3 should convert it into a reproducible training script. |
| Old scripts still in `tests/` | Six manual scripts remain there, excluded from collection via `conftest.py`. Moving them to `scripts/dev/` is a Milestone 2 tidy-up. |
| `.DS_Store` timestamps | macOS wrote Finder metadata inside `data/`, `models/` and `mlruns/` while browsing. No real data or model file changed; all are gitignored. |
| `reviews.faiss` still not reproducible | No script writes it ([01 §5.2](01-current-system.md#52-from-corpus-to-runtime-artefacts)). Unchanged by this milestone; it matters in Milestone 3. |

---

## 7. Next milestone

**Milestone 2 — Clean project structure** ([roadmap](07-production-roadmap.md#m2--clean-project-structure)), now unblocked because the safety net exists.

- **Objective:** make the code an installable package with clear boundaries, one configuration object and one logging setup, **without changing behaviour**.
- **Main work:** `pyproject.toml` and `pip install -e .`; remove the `sys.path` edits from every module; `config.py` → `core/config.py` (drop the 9 unused settings, bring `ENVIRONMENT` and the hard-coded 0.35 thresholds in, anchor paths); `logger.py` → `core/logging.py` (remove the debug `print`, log to stdout); move `evaluate/`, `notebooks/` and research scripts under `research/`; split runtime/research/dev dependencies; fix the stale references and the Pydantic deprecation.
- **Why now:** it touches almost every import in the project, which is exactly the kind of change the 104 tests make safe.
- **How you'll verify it:** the same suite must stay green, `evaluate/generate_all_metrics.py` must produce identical output, and both Docker images must still build.

**Not started.** Waiting for your approval.
