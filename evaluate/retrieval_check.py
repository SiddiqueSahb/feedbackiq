"""
Retrieval-only check on the RAG pipeline. No LLM calls, so it's free, fast,
and deterministic. Not part of the dissertation results - writes to its own
folder (data/results/retrieval_check/).

Reports: per-question outcome, similarity score distribution, a threshold
sweep against SIMILARITY_THRESHOLD (0.35, chosen by hand, never validated),
and IR metrics (precision@k, recall@k, MRR, nDCG@k) once you label
"relevant" review IDs in the questions file.

Usage:
    python evaluate/retrieval_check.py
    # first run creates evaluate/retrieval_questions.json - edit and rerun
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.pipeline import (          # noqa: E402
    SIMILARITY_THRESHOLD,
    _detect_platform_filter,
    _get_vectorstore,
)
from rag.prompts import is_complaint_question   # noqa: E402

QUESTIONS_FILE = os.path.join("evaluate", "retrieval_questions.json")
OUTPUT_DIR = os.path.join("data", "results", "retrieval_check")

K = 5              # how many reviews the pipeline shows the LLM
FETCH_K = 20       # how many candidates FAISS scores before filtering
SWEEP = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60]


# "relevant" review IDs enable precision/recall (empty = skipped).
# "kind" groups questions by what's tested; never average across kinds.
STARTER_QUESTIONS = [
    # --- narrow, factual: retrieval should find these easily ---
    {"q": "Why are customers unhappy with battery life?", "kind": "narrow", "relevant": []},
    {"q": "What complaints mention broken screens?", "kind": "narrow", "relevant": []},
    {"q": "What do customers say about late deliveries?", "kind": "narrow", "relevant": []},

    # --- broad: many reviews are relevant, so k=5 can never cover it ---
    {"q": "Summarise the major customer complaints.", "kind": "broad", "relevant": []},
    {"q": "What are the main themes in negative feedback?", "kind": "broad", "relevant": []},

    # --- platform-specific: tests the metadata filter ---
    {"q": "What are the top complaints on Amazon?", "kind": "platform", "relevant": []},
    {"q": "What do Yelp reviewers complain about most?", "kind": "platform", "relevant": []},

    # --- obscure but answerable: tests whether the threshold refuses too much ---
    {"q": "Do any customers mention problems with packaging tape?", "kind": "obscure", "relevant": []},
    {"q": "Are there complaints about staff uniforms?", "kind": "obscure", "relevant": []},

    # --- out of scope: SHOULD be refused. If these get answered, the guard leaks ---
    {"q": "What is the weather in London today?", "kind": "out_of_scope", "relevant": []},
    {"q": "Write me a Python function to sort a list.", "kind": "out_of_scope", "relevant": []},
    {"q": "Who won the football match last night?", "kind": "out_of_scope", "relevant": []},
]


def load_questions() -> list[dict]:
    """Read the questions file, creating it from the starter set on first run."""
    if not os.path.exists(QUESTIONS_FILE):
        os.makedirs(os.path.dirname(QUESTIONS_FILE), exist_ok=True)
        with open(QUESTIONS_FILE, "w", encoding="utf-8") as fh:
            json.dump(STARTER_QUESTIONS, fh, indent=2)
        print(f"Created {QUESTIONS_FILE} — edit it to add your own questions.\n")

    with open(QUESTIONS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


# Doesn't reuse _make_grounded_retriever - we want raw scores before the
# threshold is applied, so the sweep below can reuse them without re-querying.
def retrieve_scored(vectorstore, question: str) -> list[tuple[str, float]]:
    """Return [(review_id, similarity_score), ...] for the top FETCH_K candidates."""
    platform_filter = _detect_platform_filter(question)
    scored = vectorstore.similarity_search_with_relevance_scores(
        question, k=FETCH_K, filter=platform_filter,
    )
    return [(doc.metadata.get("review_id"), float(score)) for doc, score in scored]


def classify_outcome(question: str, scores: list[float], threshold: float) -> str:
    """Reproduces the pipeline's decision, but only the keyword guard
    (is_complaint_question) - not the LLM scope classifier that runs after
    it live, which keeps this free and deterministic. So "answered" here
    just means "passed the keyword guard"."""
    if not is_complaint_question(question):
        return "refused_out_of_scope"      # keyword guard rejected it
    if not any(s >= threshold for s in scores):
        return "refused_no_evidence"       # nothing cleared the threshold
    return "answered"


# IR metrics - pure arithmetic, no LLM judge.
def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Of the k reviews we showed, what fraction were relevant?"""
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / k if top else 0.0


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Of all the relevant reviews that exist, what fraction did we find?"""
    if not relevant:
        return 0.0
    return sum(1 for r in retrieved[:k] if r in relevant) / len(relevant)


def mrr(retrieved: list[str], relevant: set[str]) -> float:
    """1/rank of the FIRST relevant result. Rewards putting a good one on top."""
    for i, r in enumerate(retrieved, start=1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Position-aware precision - rank 1 worth more than rank 5."""
    dcg = sum(1.0 / math.log2(i + 1)
              for i, r in enumerate(retrieved[:k], start=1) if r in relevant)
    ideal = sum(1.0 / math.log2(i + 1)
                for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def describe(values: list[float]) -> str:
    """Summarise without hiding the shape - a mean alone is misleading for
    bimodal data (e.g. context recall 0.400 was really [1,1,0,0,0])."""
    if not values:
        return "no data"
    return (f"n={len(values)}  min={min(values):.3f}  "
            f"median={statistics.median(values):.3f}  "
            f"mean={statistics.mean(values):.3f}  max={max(values):.3f}")


def main() -> int:
    questions = load_questions()

    print("Loading the FAISS index (this takes a moment the first time)...")
    vectorstore = _get_vectorstore()
    print(f"Loaded. Evaluating {len(questions)} questions.\n")

    rows = []
    for item in questions:
        question, kind = item["q"], item.get("kind", "unspecified")
        relevant = set(item.get("relevant") or [])

        # Out-of-scope questions never reach the index in the real pipeline either.
        if not is_complaint_question(question):
            rows.append({"q": question, "kind": kind, "outcome": "refused_out_of_scope",
                         "scores": [], "ids": [], "relevant": relevant})
            continue

        scored = retrieve_scored(vectorstore, question)
        ids = [rid for rid, _ in scored]
        scores = [s for _, s in scored]

        rows.append({
            "q": question, "kind": kind,
            "outcome": classify_outcome(question, scores, SIMILARITY_THRESHOLD),
            "scores": scores, "ids": ids, "relevant": relevant,
        })

    # 1. per question
    print("=" * 78)
    print("1. WHAT HAPPENED TO EACH QUESTION")
    print("=" * 78)
    print(f"{'kind':<14}{'outcome':<24}{'top score':>10}  question")
    print("-" * 78)
    for r in rows:
        top = f"{max(r['scores']):.3f}" if r["scores"] else "—"
        print(f"{r['kind']:<14}{r['outcome']:<24}{top:>10}  {r['q'][:34]}")

    # 2. outcomes
    print("\n" + "=" * 78)
    print("2. OUTCOMES  (refusals kept separate from answers, on purpose)")
    print("=" * 78)
    for outcome in ("answered", "refused_no_evidence", "refused_out_of_scope"):
        n = sum(1 for r in rows if r["outcome"] == outcome)
        print(f"  {outcome:<24} {n:>3} / {len(rows)}")

    oos = [r for r in rows if r["kind"] == "out_of_scope"]
    leaked = [r for r in oos if r["outcome"] != "refused_out_of_scope"]
    if oos:
        caught = len(oos) - len(leaked)
        print(f"\n  Keyword guard caught {caught}/{len(oos)} out-of-scope questions.")
    if leaked:
        print("  These got past it:")
        for r in leaked:
            print(f"    - {r['q']}  ({r['outcome']})")
        print("\n  This is expected, not a bug. is_complaint_question() is a regex over")
        print("  a fixed topic list — its own docstring says it 'will not catch every")
        print("  off-topic question, only the ones the current topic list explicitly")
        print("  names'. In the live pipeline the LLM classifier runs next and should")
        print("  catch these. What this tells you is how much work that second stage")
        print("  is doing, and therefore how much an API outage would cost you.")

    # 3. score shape
    print("\n" + "=" * 78)
    print("3. SIMILARITY SCORES  (the shape, not just the average)")
    print("=" * 78)
    top_scores = [max(r["scores"]) for r in rows if r["scores"]]
    print(f"  Best score per question:  {describe(top_scores)}")
    all_scores = [s for r in rows for s in r["scores"]]
    print(f"  Every candidate score:    {describe(all_scores)}")

    # 4. sweep
    print("\n" + "=" * 78)
    print(f"4. THRESHOLD SWEEP  (pipeline currently uses {SIMILARITY_THRESHOLD})")
    print("=" * 78)
    print("  A higher threshold refuses more but hallucinates less. The right")
    print("  value depends on which mistake costs you more — that is a judgement,")
    print("  but it should be an INFORMED one.\n")
    in_scope = [r for r in rows if r["outcome"] != "refused_out_of_scope"]
    print(f"  {'threshold':>10}{'answered':>12}{'refused':>10}   {'mean reviews kept':>18}")
    print("  " + "-" * 52)
    for t in SWEEP:
        answered, kept = 0, []
        for r in in_scope:
            passing = [s for s in r["scores"] if s >= t]
            if passing:
                answered += 1
                kept.append(min(len(passing), K))
            else:
                kept.append(0)
        marker = "  <-- current" if abs(t - SIMILARITY_THRESHOLD) < 1e-9 else ""
        mean_kept = statistics.mean(kept) if kept else 0
        print(f"  {t:>10.2f}{answered:>12}{len(in_scope) - answered:>10}"
              f"{mean_kept:>19.1f}{marker}")

    # 5. IR metrics
    labelled = [r for r in rows if r["relevant"]]
    print("\n" + "=" * 78)
    print("5. RETRIEVAL QUALITY  (needs labels)")
    print("=" * 78)
    if not labelled:
        print(f"  Skipped — no question in {QUESTIONS_FILE} has a 'relevant' list yet.")
        print("  To enable: run this script, look at the review IDs below, and paste")
        print("  the genuinely relevant ones into that question's 'relevant' field.")
        print("\n  Example IDs retrieved for the first answered question:")
        for r in rows:
            if r["ids"]:
                for rid, sc in list(zip(r["ids"], r["scores"]))[:5]:
                    print(f"    {rid}   score={sc:.3f}")
                break
    else:
        print(f"  Scored on {len(labelled)} labelled question(s).\n")
        print(f"  {'P@k':>8}{'R@k':>8}{'MRR':>8}{'nDCG@k':>9}   question")
        print("  " + "-" * 62)
        p, rc, mr, nd = [], [], [], []
        for r in labelled:
            vals = (precision_at_k(r["ids"], r["relevant"], K),
                    recall_at_k(r["ids"], r["relevant"], K),
                    mrr(r["ids"], r["relevant"]),
                    ndcg_at_k(r["ids"], r["relevant"], K))
            p.append(vals[0]); rc.append(vals[1]); mr.append(vals[2]); nd.append(vals[3])
            print(f"  {vals[0]:>8.3f}{vals[1]:>8.3f}{vals[2]:>8.3f}{vals[3]:>9.3f}   {r['q'][:30]}")
        print("\n  Precision@5: " + describe(p))
        print("  Recall@5:    " + describe(rc))
        print("  MRR:         " + describe(mr))
        print("  nDCG@5:      " + describe(nd))

    # save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR, "retrieval_check.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump([{**r, "relevant": sorted(r["relevant"])} for r in rows],
                  fh, indent=2, default=str)
    print(f"\nRaw results written to {out}")
    print("(Nothing in data/results/llm_vs_rag/ was touched.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
