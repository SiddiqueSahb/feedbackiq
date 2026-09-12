# 08 — Recommended Next Step

> Part of the FeedbackIQ productionisation audit · 2026-09-10 · **Recommendation only — waiting for your approval.**
> Previous: [07 — Production roadmap](07-production-roadmap.md) · Next: [09 — Learning map](09-learning-map.md)

---

## The recommendation

# Milestone 1 — Repository foundation & safety net

**One git-tracked working copy, a frozen dissertation baseline, and an automated test suite (runs in seconds, offline) that protects the behaviour the dissertation measured, before anything else is changed.**

No production code changes. No new dependencies. No files moved or deleted.

---

## Before we start: two decisions only you can make

### Decision 1 — which folder is the real project?

The audit found **two copies** of FeedbackIQ:

| | `~/Documents/reviewAnalytics` (this folder) | `~/Desktop/FeedbackAnalytics_LLM` |
|---|---|---|
| Git history | **none** | 17 commits on `main`, clean working tree, remote `github.com/SiddiqueSahb/FeedbackAnalytics_LLM` |
| `.gitignore`, `.env.example`, `.dockerignore`, `.streamlit/`, `.github/` | **missing** | present |
| `.env` (real secrets) | missing | present, gitignored, never committed |
| Source code | identical | identical |
| `docs/production/` | these audit docs | — |

Consequences of staying as-is: any change made here is untracked and can't be reverted. The frontend Docker build fails here (`COPY .streamlit/` has nothing to copy). And a naive `git init && git add .` here would try to add ~13 GB of data, because there's no `.gitignore`.

**Options:**
1. **Make this folder canonical (recommended, since you set it up for the product work):** copy `.git/` and the five dotfiles from the Desktop repo into this folder, then confirm `git status` shows only `docs/production/` and `claude.md` as new. Copy `.env` yourself; it must never be committed. Keep the Desktop folder untouched as a backup until everything is verified.
2. **Use the Desktop repo:** copy `docs/production/` there and open Claude Code in that folder instead.

### Decision 2 — public or private code?

The README's badges point at the GitHub repository, and `LICENSE` is MIT. If that repository is public, product code pushed there is open for anyone to use under MIT. Decide *before* product code is pushed: keep the public repo as your portfolio (tag the dissertation version) and continue the product in a private repository, or deliberately keep it open. This is a business/legal choice, not a technical one.

---

## 1. Why this should come first

- **There are zero automated tests.** `tests/` contains seven files but no pytest test functions. Two are genuine self-checking scripts (both pass). Five are manual scripts, some loading the ~1 GB FAISS index at import. CI lints and builds images but runs no tests.
- **Every later milestone restructures working code.** M2 changes every import; M3 rewires the model pipeline. Without tests, you'd find out you broke the categoriser's "Unclassified" threshold, the RAG refusal logic or API authentication only by clicking through Streamlit, if at all.
- **The dissertation's measured behaviour is the asset.** The 0.35 thresholds, the threshold ∩ MMR retrieval, the refusal paths and structured-output recovery are small pieces of logic with large consequences. They deserve tests before anyone touches them.
- **Version control is a precondition for small, reversible changes.** Right now this folder has none.
- **It's the cheapest milestone with the highest leverage.** It teaches git and pytest (needed for everything after) on code you already understand.

**Why not database or authentication first?** Those are the most visible gaps ([04](04-production-gaps.md#top-10-production-gaps)). But building them on code that will be restructured in M2–M3, with no tests, bakes today's shapes into new code and makes every later change riskier.

## 2. What problem it solves

| Problem today | After M1 |
|---|---|
| Changes in this folder can't be tracked or reverted | Git history restored; every change is a reviewable commit |
| No way back to "the version I submitted" | A tag (e.g. `dissertation-submission`) marks it permanently |
| Regressions are invisible until someone clicks around | `pytest` fails within seconds when protected behaviour changes |
| Known defects live only in README prose | Recorded as `xfail(strict=True)` tests. When a later milestone fixes one, the test turns red and reminds you to update it. |
| CI proves images build, not that code works | CI runs the test suite on every push and pull request |

## 3. Files that will likely change

**Restored (Decision 1, option 1):** `.git/`, `.gitignore`, `.env.example`, `.dockerignore`, `.streamlit/config.toml`, `.github/workflows/ci.yml`.

**New:**

| File | Purpose |
|---|---|
| `pytest.ini` | `testpaths = tests/unit tests/api` (so the old manual scripts in `tests/` aren't collected); `pythonpath = .` (a pytest setting, pytest ≥ 7, replacing `sys.path` tricks in tests); markers `slow`, `needs_artifacts`, `needs_llm` |
| `tests/conftest.py` | Shared fixtures; forces offline mode (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, empty `GROQ_API_KEY`) so no test can reach the network |
| `tests/unit/test_categoriser.py` | Port of `test_categoriser_shortlist.py` as pytest functions + best score below threshold → `Unclassified / Emerging Complaint`; `top_k` respected; empty taxonomy → `[]` |
| `tests/unit/test_topic_merge.py` | Port of `test_merge_topics.py`, with stubbed modules installed via `monkeypatch.setitem(sys.modules, …)` so they're undone after each test |
| `tests/unit/test_grounded_retriever.py` | Fake vector store: only docs passing the threshold *and* chosen by MMR are returned; text capped at 1,000 chars; nothing passing → `[]` |
| `tests/unit/test_rag_ask.py` | `ask()` with fake LLM + fake store: keyword out-of-scope → refusal, `grounded=False`; no evidence → "couldn't find enough relevant customer reviews"; no API key → configuration message |
| `tests/unit/test_rag_helpers.py` | `_build_history_string`, `_detect_platform_filter`, `_extract_retry_after_seconds`, `_invoke_with_retry` (retries only rate-limit errors; `time.sleep` patched) |
| `tests/unit/test_summariser.py` | `_recover_from_failed_tool_call` (valid payload, wrong error code, invalid JSON, fenced JSON); weak similar reviews are dropped and the "no similar reviews" marker is sent (fake chain) |
| `tests/unit/test_schemas.py` | Control characters stripped; whitespace-only rejected; length and list limits |
| `tests/unit/test_keywords.py` | `normalise_phrase` with tiny fake noun-chunk objects (pronoun heads dropped, determiners stripped, stopwords removed) |
| `tests/unit/test_vader.py` | VADER thresholds (±0.05) → label; empty text raises. VADER is local, no download |
| `tests/api/test_api_contract.py` | FastAPI `TestClient` with service functions replaced by fakes: `/api/health` open; missing key → 401; wrong key → 403; every `/api/*` route except health requires the key (enumerated from `app.routes`); 422 on invalid body; a failing service → 500 with a generic message, not exception text; `/analyse` maps `score`→`confidence`; `/rag/chat` field mapping |
| `docs/production/testing-guide.md` | How to run tests, what the markers mean, how to add a test |

**Known-defect tests (`xfail(strict=True)`, documenting today's behaviour):**
- `/api/sentiment/analyse` accepts `platform` but never uses it
- the pipeline categorises feedback even when sentiment is positive (README's stated limitation)
- a RAG pipeline exception comes back as HTTP 200 with the exception text in `answer`

**Modified:** `.github/workflows/ci.yml` gets a `test` job (`pytest -m "not slow and not needs_artifacts and not needs_llm"`) with pip caching.

**Already installed in `venv/` (verified), so nothing new to install:** pytest 8.1.1, pytest-asyncio, httpx 0.28.1, flake8.

## 4. What we should NOT change yet

- ❌ Any code in `nlp/`, `rag/`, `backend/`, `frontend/`, `scripts/`, `evaluate/`, `config.py`, `logger.py`: **not even known bugs**. Record them as `xfail`; fix them in M2/M3.
- ❌ Moving, renaming or deleting files, including the manual scripts in `tests/`, which stay runnable exactly as before.
- ❌ Dependencies, `requirements*.txt`, Dockerfiles, `docker-compose.yml`.
- ❌ Data, models, `mlruns/`, notebooks.
- ❌ Package structure / `pyproject.toml` packaging (that's M2).
- ❌ Database, auth, new endpoints.
- ❌ README rewrites.
- ❌ Pushing to GitHub before Decision 2.

## 5. How we will test it

This milestone *is* tests, so we verify that the tests themselves are trustworthy:

| Check | How |
|---|---|
| Suite passes | `pytest` green locally; runtime recorded (`pytest --durations=10`) |
| Truly offline | Run with Wi-Fi off and no `GROQ_API_KEY` → still green |
| Tests actually guard behaviour | Temporarily change `CATEGORY_CONFIDENCE_THRESHOLD` → categoriser test fails. Remove `dependencies=protected` for one router → auth test fails. Change `SIMILARITY_THRESHOLD` → retriever test fails. **Revert each.** |
| Nothing else changed | `git diff dissertation-submission -- nlp rag backend frontend scripts evaluate config.py logger.py` is empty |
| Old manual checks still work | `python tests/test_categoriser_shortlist.py` and `python tests/test_merge_topics.py` still print their PASS lines |
| CI | A branch push shows the new test job green |

**Expected cost to be aware of:** importing `backend.main` took **48.9 s** during the audit (torch, transformers and LangChain load at import). API tests will pay that once per run. M2 is where it gets fixed; M1 only measures it.

## 6. What the project will look like afterwards

```text
~/Documents/reviewAnalytics          git: main · tag dissertation-submission
├── .git/  .gitignore  .env.example  .dockerignore  .streamlit/  .github/   ← restored
├── pytest.ini                                                          ← new
├── tests/
│   ├── conftest.py                                                     ← new
│   ├── unit/      ~10 files · offline · seconds                         ← new
│   ├── api/       auth + route contract tests                          ← new
│   └── test_*.py, debug_embedding.py   (manual scripts, untouched, not collected)
├── docs/production/   01–09 + testing-guide.md
└── backend/ frontend/ nlp/ rag/ scripts/ evaluate/ …   byte-for-byte unchanged
```

**What you'll be able to do:** start M2, move every import and restructure configuration, and know within a minute whether you broke categorisation thresholds, grounded refusals, structured-output recovery, API authentication or response shapes.

**Rough size:** about 20–30 small test functions plus configuration, no production code. It can be delivered as 3–4 small commits:
1. Restore repository and tag the baseline
2. pytest config + conftest + unit tests
3. API contract tests + known-defect `xfail`s
4. CI test job + testing guide

---

**Waiting for your approval** (and Decisions 1 and 2) before starting.
