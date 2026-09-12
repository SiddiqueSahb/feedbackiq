# FeedbackIQ — instructions for Claude Code

FeedbackIQ is an MSc dissertation project (sentiment analysis, zero-shot complaint
categorisation, FAISS retrieval, grounded question answering) being turned into a
B2B SaaS product **incrementally**. The audit and milestone plan live in
`docs/production/`. Read `docs/production/07-production-roadmap.md` before proposing work.

## Ground rules

1. **Preserve working behaviour.** Structural work must not change what the app outputs.
   Where behaviour must change, say so explicitly and document it.
2. **Never start a milestone without approval.** Finish the current one, report, and wait.
3. **Small, reviewable changes.** Logical commits, not one large unexplained one.
   Never force-push; never rewrite pushed history.
4. **Run the tests after every meaningful change:** `pytest` must stay at
   **104 passed, 4 xfailed** or better. Never weaken or delete a test to get green,
   and never flip a strict `xfail` without documenting why the behaviour changed.
5. **Don't touch dissertation research artefacts** without asking: `notebooks/`,
   `evaluate/`, `scripts/`, `data/`, `models/`, `mlruns/`, `RESULTS.md`. Their results
   must stay reproducible. Import paths may be updated; methodology may not.
6. **Readable Python over clever Python.** Type hints where they help, clear names,
   comments that explain *why*. No abstraction without a concrete need.
7. **Don't over-engineer.** No microservices, Kubernetes, message queues, event buses
   or new databases unless the roadmap milestone calls for it. Modular monolith.
8. **No unnecessary dependencies.** Reuse what's installed; don't upgrade pins casually;
   keep runtime, dev and research dependencies separate (`pyproject.toml`).
9. **Never commit secrets or artefacts.** `.env`, credentials, `data/`, `models/`,
   `venv/`, `notebooks/`, `SUBMISSION.md` and the dissertation PDFs are gitignored and
   must stay that way. Check `git status` before committing.
10. **Explain architectural decisions** in the milestone document, including what was
    rejected and why.

## Layout

```text
src/feedbackiq/        the product (installed with `pip install -e .`)
  core/                settings, logging, paths, exceptions
  api/                 FastAPI app, auth dependency, routes, request/response schemas
  services/            orchestration between the API and the ML code
  nlp/                 sentiment, categorisation, embeddings, LLM analysis
  rag/                 retrieval-augmented question answering and its prompts
frontend/              Streamlit app (internal tool; talks to the API over HTTP only)
backend/               build files for the API image (Dockerfile, extra requirements)
tests/unit, tests/api  the safety net: offline, no models, no network
tests/*.py             older manual check scripts, not collected by pytest
evaluate/, scripts/    dissertation evaluation and offline pipeline
notebooks/             dissertation notebooks (gitignored; never edited)
docs/production/       audit and milestone documentation
```

## Commands

```bash
pip install -e ".[dev]"      # editable install; no sys.path manipulation anywhere
pytest                        # the safety net (seconds, offline)
flake8 src frontend tests/conftest.py tests/unit tests/api --select=E9,F
uvicorn feedbackiq.api.main:app --reload --port 8000
streamlit run frontend/app.py
docker build -f backend/Dockerfile --output type=cacheonly .   # local export is broken; see milestone-01
```

## Things to know

- Thresholds (0.35 for retrieval, categorisation and evidence) are settings with those
  defaults. Change a default only with a documented reason and a passing benchmark.
- The LLM (Groq) is external: no test may depend on it, and no test may need network
  access or model downloads.
- `logs/` is not used by the application any more; logging goes to stdout.
- Four known defects are recorded as strict `xfail` tests; see
  `docs/production/milestone-01.md` before "fixing" one.
