"""
End-to-end RAG check: real FAISS index, real Groq calls. Diagnostic tool,
not part of the dissertation results - writes only to data/results/rag_check/.

Phases: 1) retrieval-only scoring (free), 2) full ask() pipeline (Groq),
3) RAGAS scoring of answered questions (Groq, skip with --no-ragas),
4) report (outcomes, latency, threshold sweep, IR metrics, RAGAS).

Unlike evaluate_llm_vs_rag.py: separates refusals from bad answers, reports
score distributions not just means, sweeps SIMILARITY_THRESHOLD, and scores
retrieval with plain IR metrics instead of an LLM judge.

Usage:
    python evaluate/rag_check.py                  # everything
    python evaluate/rag_check.py --no-ragas       # skip phase 3
    python evaluate/rag_check.py --limit 5        # first 5 questions only
    python evaluate/rag_check.py --delay 3        # 3s between questions

First run creates evaluate/rag_check_questions.json - edit it to add questions.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time

from feedbackiq.rag.pipeline import (                     # noqa: E402
    OUT_OF_SCOPE_MSG,
    SIMILARITY_THRESHOLD,
    _detect_platform_filter,
    _get_vectorstore,
    ask,
)
from feedbackiq.rag.prompts import is_complaint_question  # noqa: E402

QUESTIONS_FILE = os.path.join("evaluate", "rag_check_questions.json")
OUTPUT_DIR = os.path.join("data", "results", "rag_check")

K = 5
FETCH_K = 20
SWEEP = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60]

# ask() defines this inline so it can't be imported; fragile string match,
# but it's how we tell the two refusal types apart (no refusal_reason field yet).
NO_EVIDENCE_MSG = "I couldn't find enough relevant customer reviews to answer this question."


# Question set fields:
#   relevant  — review IDs that answer it; empty skips IR metrics
#   reference — short correct answer; empty skips RAGAS context precision/recall
#   kind      — what's being tested; never average across kinds
STARTER_QUESTIONS = [
    {"kind": "narrow", "q": "Why are customers unhappy with battery life?",
     "relevant": [], "reference": ""},
    {"kind": "narrow", "q": "What complaints mention broken or cracked screens?",
     "relevant": [], "reference": ""},
    {"kind": "narrow", "q": "What do customers say about late deliveries?",
     "relevant": [], "reference": ""},

    {"kind": "broad", "q": "Summarise the major customer complaints.",
     "relevant": [], "reference": ""},
    {"kind": "broad", "q": "What are the main themes in negative feedback?",
     "relevant": [], "reference": ""},

    {"kind": "platform", "q": "What are the top complaints on Amazon?",
     "relevant": [], "reference": ""},
    {"kind": "platform", "q": "What do Yelp reviewers complain about most?",
     "relevant": [], "reference": ""},

    {"kind": "obscure", "q": "Do any customers mention problems with packaging tape?",
     "relevant": [], "reference": ""},
    {"kind": "obscure", "q": "Are there complaints about staff uniforms?",
     "relevant": [], "reference": ""},

    {"kind": "out_of_scope", "q": "What is the weather in London today?",
     "relevant": [], "reference": ""},
    {"kind": "out_of_scope", "q": "Write me a Python function to sort a list.",
     "relevant": [], "reference": ""},
    {"kind": "out_of_scope", "q": "Who is the CEO of Apple?",
     "relevant": [], "reference": ""},
]


def load_questions() -> list[dict]:
    if not os.path.exists(QUESTIONS_FILE):
        os.makedirs(os.path.dirname(QUESTIONS_FILE), exist_ok=True)
        with open(QUESTIONS_FILE, "w", encoding="utf-8") as fh:
            json.dump(STARTER_QUESTIONS, fh, indent=2)
        print(f"Created {QUESTIONS_FILE} — edit it to add your own questions.\n")
    with open(QUESTIONS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


# metrics
def precision_at_k(retrieved, relevant, k):
    """Of the k reviews shown, what fraction were relevant?"""
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / k if top else 0.0


def recall_at_k(retrieved, relevant, k):
    """Of all relevant reviews, what fraction did we find?"""
    return sum(1 for r in retrieved[:k] if r in relevant) / len(relevant) if relevant else 0.0


def mrr(retrieved, relevant):
    """1/rank of the first relevant result. Rewards a good result on top."""
    for i, r in enumerate(retrieved, start=1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved, relevant, k):
    """Position-aware precision: rank 1 is worth more than rank 5."""
    dcg = sum(1 / math.log2(i + 1) for i, r in enumerate(retrieved[:k], 1) if r in relevant)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def describe(values):
    """Summarise without hiding the shape. Means lie about bimodal data."""
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return "no data"
    return (f"n={len(vals)}  min={min(vals):.3f}  median={statistics.median(vals):.3f}  "
            f"mean={statistics.mean(vals):.3f}  max={max(vals):.3f}")


def classify(answer: str) -> str:
    """Which of the four things happened? Determined from the returned text."""
    if answer == OUT_OF_SCOPE_MSG:
        return "refused_out_of_scope"
    if answer == NO_EVIDENCE_MSG:
        return "refused_no_evidence"
    if answer.startswith("No LLM API key"):
        return "error_no_key"
    return "answered"


# phases
def phase1_retrieval(vectorstore, questions):
    """Raw similarity scores. No LLM, so this is free and instant."""
    print("=" * 78)
    print("PHASE 1 — retrieval only (no LLM calls)")
    print("=" * 78)
    for item in questions:
        if not is_complaint_question(item["q"]):
            item["scores"], item["ids"] = [], []
            continue
        scored = vectorstore.similarity_search_with_relevance_scores(
            item["q"], k=FETCH_K, filter=_detect_platform_filter(item["q"]),
        )
        item["ids"] = [d.metadata.get("review_id") for d, _ in scored]
        item["scores"] = [float(s) for _, s in scored]
    print(f"Scored {len(questions)} questions against the index.\n")
    return questions


def phase2_pipeline(questions, delay):
    """The real ask(). Groq is called here: LLM scope guard, then generation."""
    print("=" * 78)
    print("PHASE 2 — full pipeline (Groq: scope classifier + generation)")
    print("=" * 78)
    for i, item in enumerate(questions, 1):
        started = time.time()
        try:
            result = ask(item["q"])
            item["answer"] = result.get("answer", "")
            item["sources"] = [s.get("review_id") for s in result.get("sources", [])]
            item["retrieval_count"] = result.get("retrieval_count", 0)
            item["grounded"] = result.get("grounded", False)
        except Exception as exc:                      # keep going; report at the end
            item["answer"] = f"ERROR: {exc}"
            item["sources"], item["retrieval_count"], item["grounded"] = [], 0, False

        item["latency_ms"] = round((time.time() - started) * 1000, 1)
        item["outcome"] = ("error_exception" if item["answer"].startswith("ERROR:")
                           else classify(item["answer"]))
        print(f"  [{i:>2}/{len(questions)}] {item['outcome']:<22} "
              f"{item['latency_ms']:>8.0f}ms  {item['q'][:38]}")
        if delay and i < len(questions):
            time.sleep(delay)                          # be kind to the free tier
    print()
    return questions


def phase3_ragas(questions):
    """RAGAS on answered questions only - scoring a refusal gives zeros
    indistinguishable from a hallucination, which defeats the point."""
    print("=" * 78)
    print("PHASE 3 — RAGAS scoring (Groq)")
    print("=" * 78)

    answered = [q for q in questions if q["outcome"] == "answered"]
    if not answered:
        print("  No answered questions to score.\n")
        return questions

    try:
        from datasets import Dataset
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import (answer_relevancy, context_precision,
                                   context_recall, faithfulness)
    except ImportError as exc:
        print(f"  RAGAS not available ({exc}). Skipping.\n")
        return questions

    # context_precision/recall need a reference answer; skip without one.
    have_refs = all(q.get("reference") for q in answered)
    metrics = [faithfulness, answer_relevancy]
    if have_refs:
        metrics += [context_precision, context_recall]
    else:
        print("  No reference answers set, so context precision/recall are skipped.")
        print("  Add a 'reference' to each question in the JSON to enable them.\n")

    # RAGAS schema needs these exact field names; old names (question/answer/
    # contexts/ground_truth) get silently dropped by pydantic -> all-None, no error.
    data = {
        "user_input": [q["q"] for q in answered],
        "response": [q["answer"] for q in answered],
        "retrieved_contexts": [[f"review {rid}" for rid in q["sources"]] or [""] for q in answered],
        "reference": [q.get("reference") or q["answer"] for q in answered],
    }

    print(f"  Scoring {len(answered)} answered questions on "
          f"{len(metrics)} metric(s)...")
    try:
        scores = ragas_evaluate(Dataset.from_dict(data), metrics=metrics)
        df = scores.to_pandas()
    except Exception as exc:
        print(f"  RAGAS failed: {exc}\n")
        return questions

    for q, (_, row) in zip(answered, df.iterrows()):
        q["ragas"] = {m.name: (None if row.get(m.name) is None else float(row[m.name]))
                      for m in metrics if m.name in row}
    print("  Done.\n")
    return questions


# report
def phase4_report(questions):
    print("=" * 78)
    print("PHASE 4 — REPORT")
    print("=" * 78)

    # 1. per question
    print("\n1. WHAT HAPPENED TO EACH QUESTION")
    print("-" * 78)
    print(f"{'kind':<13}{'outcome':<22}{'top sim':>9}{'ms':>8}  question")
    for q in questions:
        top = f"{max(q['scores']):.3f}" if q.get("scores") else "—"
        print(f"{q['kind']:<13}{q['outcome']:<22}{top:>9}"
              f"{q.get('latency_ms', 0):>8.0f}  {q['q'][:30]}")

    # 2. outcomes
    print("\n2. OUTCOMES  (refusals kept separate from answers, on purpose)")
    print("-" * 78)
    for outcome in ("answered", "refused_no_evidence", "refused_out_of_scope",
                    "error_exception", "error_no_key"):
        n = sum(1 for q in questions if q["outcome"] == outcome)
        if n:
            print(f"  {outcome:<24} {n:>3} / {len(questions)}")

    oos = [q for q in questions if q["kind"] == "out_of_scope"]
    leaked = [q for q in oos if q["outcome"] != "refused_out_of_scope"]
    if oos:
        print(f"\n  Scope guards caught {len(oos) - len(leaked)}/{len(oos)} "
              f"out-of-scope questions.")
        for q in leaked:
            print(f"    LEAKED: {q['q']}  ->  {q['outcome']}")

    # 3. latency by outcome
    print("\n3. LATENCY BY OUTCOME  (a refusal should be much cheaper)")
    print("-" * 78)
    for outcome in ("answered", "refused_no_evidence", "refused_out_of_scope"):
        lat = [q["latency_ms"] for q in questions if q["outcome"] == outcome]
        if lat:
            print(f"  {outcome:<24}{describe(lat)}")

    # 4. similarity distribution
    print("\n4. SIMILARITY SCORES  (the shape, not just the average)")
    print("-" * 78)
    print(f"  Best score per question:  {describe([max(q['scores']) for q in questions if q.get('scores')])}")
    print(f"  Every candidate score:    {describe([s for q in questions for s in q.get('scores', [])])}")

    # 5. threshold sweep
    print(f"\n5. THRESHOLD SWEEP  (pipeline uses {SIMILARITY_THRESHOLD})")
    print("-" * 78)
    print("  Higher threshold = refuses more, hallucinates less. Which mistake")
    print("  costs you more is a judgement — but it should be an informed one.\n")
    in_scope = [q for q in questions if q.get("scores")]
    print(f"  {'threshold':>10}{'would answer':>14}{'would refuse':>14}{'mean kept':>12}")
    for t in SWEEP:
        answered, kept = 0, []
        for q in in_scope:
            passing = [s for s in q["scores"] if s >= t]
            kept.append(min(len(passing), K))
            if passing:
                answered += 1
        marker = "  <-- current" if abs(t - SIMILARITY_THRESHOLD) < 1e-9 else ""
        print(f"  {t:>10.2f}{answered:>14}{len(in_scope) - answered:>14}"
              f"{statistics.mean(kept) if kept else 0:>12.1f}{marker}")

    # 6. retrieval quality (arithmetic, no LLM judge)
    print("\n6. RETRIEVAL QUALITY  (arithmetic, no LLM judge)")
    print("-" * 78)
    labelled = [q for q in questions if q.get("relevant")]
    if not labelled:
        print(f"  Skipped — no question in {QUESTIONS_FILE} has a 'relevant' list.")
        print("  To enable, paste genuinely relevant review IDs into that field.")
        for q in questions:
            if q.get("ids"):
                print(f"\n  IDs retrieved for {q['q'][:40]!r}:")
                for rid, sc in list(zip(q["ids"], q["scores"]))[:5]:
                    print(f"    {rid}   score={sc:.3f}")
                break
    else:
        p = [precision_at_k(q["ids"], set(q["relevant"]), K) for q in labelled]
        r = [recall_at_k(q["ids"], set(q["relevant"]), K) for q in labelled]
        m = [mrr(q["ids"], set(q["relevant"])) for q in labelled]
        n = [ndcg_at_k(q["ids"], set(q["relevant"]), K) for q in labelled]
        print(f"  Labelled questions: {len(labelled)}")
        print(f"  Precision@{K}: {describe(p)}")
        print(f"  Recall@{K}:    {describe(r)}")
        print(f"  MRR:         {describe(m)}")
        print(f"  nDCG@{K}:     {describe(n)}")

    # 7 ── RAGAS -------------------------------------------------------------
    scored = [q for q in questions if q.get("ragas")]
    if scored:
        print("\n7. GENERATION QUALITY  (RAGAS, answered questions only)")
        print("-" * 78)
        for name in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
            vals = [q["ragas"].get(name) for q in scored if name in q["ragas"]]
            if vals:
                print(f"  {name:<20}{describe(vals)}")
        print("\n  Per question:")
        for q in scored:
            bits = "  ".join(f"{k[:4]}={('—' if v is None else f'{v:.3f}')}"
                             for k, v in q["ragas"].items())
            print(f"    {bits}   {q['q'][:34]}")
        print("\n  Caveat: the same model family generates and grades. Treat these")
        print("  as indicative. An independent judge would be a stronger design.")

    # save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR, "rag_check.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(questions, fh, indent=2, default=str)
    print(f"\nRaw results written to {out}")
    print("(Nothing in data/results/llm_vs_rag/ was touched.)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-ragas", action="store_true", help="skip phase 3")
    ap.add_argument("--limit", type=int, help="only the first N questions")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between questions (free-tier rate limits)")
    args = ap.parse_args()

    questions = load_questions()
    if args.limit:
        questions = questions[: args.limit]

    print("Loading the FAISS index (~1.4GB, takes a moment)...")
    vectorstore = _get_vectorstore()
    print(f"Loaded. Running {len(questions)} questions.\n")

    questions = phase1_retrieval(vectorstore, questions)
    questions = phase2_pipeline(questions, args.delay)
    if not args.no_ragas:
        questions = phase3_ragas(questions)
    phase4_report(questions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
