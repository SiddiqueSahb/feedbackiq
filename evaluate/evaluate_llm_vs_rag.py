"""
Evaluate LLM-only vs Retrieval-Augmented Generation

Author : Mohammad Asim Siddique

This script compares

1. LLM Only
2. Retrieval-Augmented Generation

using RAGAS evaluation metrics.
"""

from __future__ import annotations

import os
import re
import time
import sys
import warnings
import pandas as pd

from datasets import Dataset

from feedbackiq.rag.pipeline import ask
from feedbackiq.core.config import settings

# Dedicated tuning for the RAGAS judge LLM. Deliberately separate from
# rag.pipeline._get_llm(), whose 30s timeout and unset max_tokens are
# tuned for interactive chat latency -- faithfulness/context_precision/
# context_recall ask the judge to emit a full JSON list of statements per
# retrieved context, which needs materially more room and time to finish.
RAGAS_JUDGE_MODEL = os.environ.get("RAGAS_JUDGE_MODEL", settings.GROQ_MODEL)
RAGAS_JUDGE_MAX_TOKENS = int(os.environ.get("RAGAS_JUDGE_MAX_TOKENS", "8192"))
RAGAS_JUDGE_TIMEOUT = int(os.environ.get("RAGAS_JUDGE_TIMEOUT", "90"))

from langchain_groq import ChatGroq

# ragas 0.4.x unconditionally imports langchain_community.chat_models.vertexai,
# which doesn't exist post LangChain 1.0 rebrand. Stub it out before importing
# ragas (the same compatibility shim noted in requirements.txt).
import types as _types

_vertexai_shim = _types.ModuleType("langchain_community.chat_models.vertexai")


class _StubChatVertexAI:  # pragma: no cover
    pass


_vertexai_shim.ChatVertexAI = _StubChatVertexAI
sys.modules.setdefault("langchain_community.chat_models.vertexai", _vertexai_shim)

import langchain_community.llms as _langchain_community_llms  # noqa: E402

if not hasattr(_langchain_community_llms, "VertexAI"):
    class _StubVertexAI:  # pragma: no cover
        pass

    _langchain_community_llms.VertexAI = _StubVertexAI

warnings.filterwarnings("ignore", category=DeprecationWarning, module="ragas")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="langchain_community")

from ragas import evaluate  # noqa: E402
from ragas.run_config import RunConfig  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from langchain_huggingface import HuggingFaceEmbeddings  # noqa: E402

from ragas.metrics import (  # noqa: E402

    faithfulness,

    answer_relevancy,

    context_precision,

    context_recall

)

# Groq's free tier caps out fast for llama-3.1-8b-instant; retry with
# backoff instead of crashing the run.
MAX_RATE_LIMIT_RETRIES = 6
SLEEP_BETWEEN_QUESTIONS = 8.0


def _extract_retry_after_seconds(exc: Exception, default: float = 8.0) -> float:
    match = re.search(r"try again in ([\d.]+)s", str(exc))
    if match:
        try:
            return float(match.group(1)) + 0.5
        except ValueError:
            pass
    return default


def call_with_retry(fn, *args, max_retries: int = MAX_RATE_LIMIT_RETRIES, **kwargs):
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            is_rate_limit = "rate_limit" in str(exc).lower() or "429" in str(exc)
            if not is_rate_limit or attempt >= max_retries:
                raise
            wait = _extract_retry_after_seconds(exc)
            print(f"    [rate limit] waiting {wait:.1f}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(wait)


def _get_judge_llm() -> ChatGroq:
    """Build the Groq client used to judge/score, not to generate answers.

    Kept separate from feedbackiq.rag.pipeline._get_llm(): that one is tuned for
    interactive chat (timeout=30, no explicit max_tokens) and truncates
    before the judge can finish a faithfulness/context_recall verdict on a
    multi-context RAG sample, which ragas surfaces as
    LLMDidNotFinishException. temperature=0 keeps scoring deterministic.
    """
    return ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model_name=RAGAS_JUDGE_MODEL,
        temperature=0,
        max_tokens=RAGAS_JUDGE_MAX_TOKENS,
        timeout=RAGAS_JUDGE_TIMEOUT,
    )


def _get_ragas_llm(llm) -> LangchainLLMWrapper:
    # bypass_n=True: ragas mutates llm.n to request multiple completions,
    # which Groq rejects (n>1) and only resets on success -- a failed call
    # leaves a shared llm permanently broken. Use a separate ChatGroq
    # instance to keep the judge isolated from the generator.
    return LangchainLLMWrapper(llm, bypass_n=True)


def _get_ragas_embeddings() -> LangchainEmbeddingsWrapper:
    hf = HuggingFaceEmbeddings(
        model_name=settings.EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    return LangchainEmbeddingsWrapper(hf)

OUTPUT_DIR = "data/results/llm_vs_rag"

os.makedirs(

    OUTPUT_DIR,

    exist_ok=True

)

QUESTIONS = [
    "Why are customers unhappy with battery life?",
    "What delivery problems are customers reporting?",
    "Why are airline passengers dissatisfied?",
    "What customer service issues appear most often?",
    "Summarise the major customer complaints."
]

GROUND_TRUTHS = [
    "Customers mainly complain about battery drain, charging issues and poor battery performance.",
    "Customers frequently complain about delayed deliveries, damaged parcels and missing items.",
    "Passengers commonly complain about delays, cancellations and lost luggage.",
    "Customer service complaints include rude staff, poor communication and unresolved problems.",
    "Across all platforms, the main complaints involve product quality, delivery, refunds and customer service."
]

def load_llm():

    if settings.USE_GROQ and settings.GROQ_API_KEY:

        return ChatGroq(

            api_key=settings.GROQ_API_KEY,

            model_name=settings.GROQ_MODEL,

            temperature=0

        )

    raise SystemExit("No LLM configured (GROQ_API_KEY missing) — cannot run evaluation.")

def generate_llm_answer(

    llm,

    question

):

    response = call_with_retry(llm.invoke, question)

    return response.content


def generate_rag_answer(

    question

):

    # ask() never raises -- rate-limit errors come back as an "error
    # occurred" string, not an exception, so retry manually here.
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        result = ask(
            question
        )
        answer = result["answer"]
        looks_like_rate_limit = "error occurred" in answer.lower() and (
            "rate_limit" in answer.lower() or "429" in answer
        )
        if not looks_like_rate_limit or attempt >= MAX_RATE_LIMIT_RETRIES:
            break
        wait = _extract_retry_after_seconds(Exception(answer))
        print(f"    [rate limit] ask() hit a rate limit, waiting {wait:.1f}s before retry ({attempt + 1}/{MAX_RATE_LIMIT_RETRIES})...")
        time.sleep(wait)

    return (

        result["answer"],

        result["sources"]

    )

# Evaluate LLM and RAG

print("=" * 80)
print("Evaluating LLM Only and RAG")
print("=" * 80)

llm = load_llm()

llm_results = []
rag_results = []

for question in QUESTIONS:

    print(f"\nQuestion: {question}")

    # LLM only
    start = time.perf_counter()

    llm_answer = generate_llm_answer(llm, question)

    llm_latency = (time.perf_counter() - start) * 1000

    llm_results.append({
        "Question": question,
        "Answer": llm_answer,
        "Latency (ms)": round(llm_latency, 2)
    })

    # RAG
    start = time.perf_counter()

    rag_answer, sources = generate_rag_answer(question)

    rag_latency = (time.perf_counter() - start) * 1000

    # full_text, not "text" (a 200-char UI preview) -- RAGAS needs the same
    # evidence the LLM actually saw, or scores get deflated for no reason.
    contexts = [source["full_text"] for source in sources]

    rag_results.append({
        "Question": question,
        "Answer": rag_answer,
        "Contexts": contexts,
        "Retrieved Reviews": len(contexts),
        "Latency (ms)": round(rag_latency, 2)
    })

    print("✓ Completed")

    time.sleep(SLEEP_BETWEEN_QUESTIONS)

print("\nEvaluation Complete!")

# Save generated answers

llm_df = pd.DataFrame(llm_results)
rag_df = pd.DataFrame(rag_results)

llm_df.to_csv(
    os.path.join(OUTPUT_DIR, "llm_answers.csv"),
    index=False
)

rag_df.to_csv(
    os.path.join(OUTPUT_DIR, "rag_answers.csv"),
    index=False
)

print("✓ LLM answers saved")
print("✓ RAG answers saved")

# Prepare LLM dataset

print("\n" + "=" * 80)
print("Preparing LLM Dataset")
print("=" * 80)

# These field names (user_input/response/retrieved_contexts/reference) are
# what this ragas version's schema needs; older names (question/answer/...)
# get silently dropped by pydantic -> all-None samples, no error.
llm_data = {
    "user_input": [],
    "response": [],
    "retrieved_contexts": [],
    "reference": []
}

for i, result in enumerate(llm_results):

    llm_data["user_input"].append(result["Question"])

    llm_data["response"].append(result["Answer"])

    # LLM has no retrieved context
    llm_data["retrieved_contexts"].append([])

    llm_data["reference"].append(
        GROUND_TRUTHS[i]
    )

llm_dataset = Dataset.from_dict(llm_data)

print("✓ LLM dataset created")


# Prepare RAG dataset

print("\n" + "=" * 80)
print("Preparing RAG Dataset")
print("=" * 80)

rag_data = {
    "user_input": [],
    "response": [],
    "retrieved_contexts": [],
    "reference": []
}

for i, result in enumerate(rag_results):

    rag_data["user_input"].append(result["Question"])

    rag_data["response"].append(result["Answer"])

    rag_data["retrieved_contexts"].append(result["Contexts"])

    rag_data["reference"].append(
        GROUND_TRUTHS[i]
    )

rag_dataset = Dataset.from_dict(rag_data)

print("✓ RAG dataset created")

# Separate Groq instance for the judge -- without llm=, ragas defaults to
# OpenAI (fails, no key). max_workers=1 because Groq's free tier can't
# handle 16 concurrent judge calls; ragas' own retry runs on top of this.

judge_llm = _get_judge_llm()
ragas_llm = _get_ragas_llm(judge_llm)
ragas_embeddings = _get_ragas_embeddings()
# max_retries=12 / max_wait=90: a bit more headroom than before -- weaker
# open-weight judge models occasionally emit malformed JSON on the first
# try (surfaces as OutputParserException), and ragas' own retry-with-fix
# loop needs the room, not just this run_config, but extra slack here
# reduces how often a sample gives up and reports NaN.
eval_run_config = RunConfig(max_workers=1, max_retries=12, max_wait=90)

# Evaluate LLM only

print("\n" + "=" * 80)
print("Evaluating LLM Only")
print("=" * 80)

llm_scores = evaluate(
    dataset=llm_dataset,
    metrics=[
        answer_relevancy
    ],
    llm=ragas_llm,
    embeddings=ragas_embeddings,
    run_config=eval_run_config,
)

llm_scores_df = llm_scores.to_pandas()

print("\nLLM Evaluation")
print(llm_scores_df)

llm_scores_df.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "llm_ragas_scores.csv"
    ),
    index=False
)

print("✓ llm_ragas_scores.csv saved")


# Evaluate RAG

print("\n" + "=" * 80)
print("Evaluating RAG")
print("=" * 80)

rag_scores = evaluate(
    dataset=rag_dataset,
    metrics=[
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall
    ],
    llm=ragas_llm,
    embeddings=ragas_embeddings,
    run_config=eval_run_config,
)

rag_scores_df = rag_scores.to_pandas()

print("\nRAG Evaluation")
print(rag_scores_df)

rag_scores_df.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "rag_ragas_scores.csv"
    ),
    index=False
)

print("✓ rag_ragas_scores.csv saved")


# Comparison summary

print("\n" + "=" * 80)
print("Creating Comparison Summary")
print("=" * 80)

comparison = pd.DataFrame({

    "Metric": [

        "Answer Relevancy",
        "Faithfulness",
        "Context Precision",
        "Context Recall",
        "Average Latency (ms)"

    ],

    "LLM Only": [

        round(
            llm_scores_df["answer_relevancy"].mean(),
            3
        ),

        "-",

        "-",

        "-",

        round(
            llm_df["Latency (ms)"].mean(),
            2
        )

    ],

    "RAG": [

        round(
            rag_scores_df["answer_relevancy"].mean(),
            3
        ),

        round(
            rag_scores_df["faithfulness"].mean(),
            3
        ),

        round(
            rag_scores_df["context_precision"].mean(),
            3
        ),

        round(
            rag_scores_df["context_recall"].mean(),
            3
        ),

        round(
            rag_df["Latency (ms)"].mean(),
            2
        )

    ]

})

print(comparison)

comparison.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "comparison_summary.csv"
    ),
    index=False
)

print("✓ comparison_summary.csv saved")


# Overall results

overall = pd.DataFrame({

    "Metric":[

        "Questions Evaluated",

        "Average Retrieved Reviews",

        "Average LLM Latency (ms)",

        "Average RAG Latency (ms)",

        "Average Answer Relevancy (LLM)",

        "Average Answer Relevancy (RAG)",

        "Average Faithfulness",

        "Average Context Precision",

        "Average Context Recall"

    ],

    "Value":[

        len(QUESTIONS),

        round(
            rag_df["Retrieved Reviews"].mean(),
            2
        ),

        round(
            llm_df["Latency (ms)"].mean(),
            2
        ),

        round(
            rag_df["Latency (ms)"].mean(),
            2
        ),

        round(
            llm_scores_df["answer_relevancy"].mean(),
            3
        ),

        round(
            rag_scores_df["answer_relevancy"].mean(),
            3
        ),

        round(
            rag_scores_df["faithfulness"].mean(),
            3
        ),

        round(
            rag_scores_df["context_precision"].mean(),
            3
        ),

        round(
            rag_scores_df["context_recall"].mean(),
            3
        )

    ]

})

print("\nOverall Results")
print(overall)

overall.to_csv(

    os.path.join(

        OUTPUT_DIR,

        "overall_results.csv"

    ),

    index=False

)

print("✓ overall_results.csv saved")


# Graphs

import matplotlib.pyplot as plt

# Answer relevancy

plt.figure(figsize=(6,5))

bars = plt.bar(

    ["LLM Only","RAG"],

    [

        llm_scores_df["answer_relevancy"].mean(),

        rag_scores_df["answer_relevancy"].mean()

    ]

)

plt.ylim(0,1)

plt.title("Answer Relevancy")

plt.ylabel("Score")

for bar in bars:

    value = bar.get_height()

    plt.text(

        bar.get_x()+bar.get_width()/2,

        value,

        f"{value:.2f}",

        ha="center",

        va="bottom"

    )

plt.tight_layout()

plt.savefig(

    os.path.join(

        OUTPUT_DIR,

        "answer_relevancy.png"

    )

)

plt.close()

# Faithfulness

plt.figure(figsize=(6,5))

bars = plt.bar(

    ["RAG"],

    [

        rag_scores_df["faithfulness"].mean()

    ]

)

plt.ylim(0,1)

plt.title("Faithfulness")

plt.ylabel("Score")

for bar in bars:

    value = bar.get_height()

    plt.text(

        bar.get_x()+bar.get_width()/2,

        value,

        f"{value:.2f}",

        ha="center",

        va="bottom"

    )

plt.tight_layout()

plt.savefig(

    os.path.join(

        OUTPUT_DIR,

        "faithfulness.png"

    )

)

plt.close()

# Context precision

plt.figure(figsize=(6,5))

bars = plt.bar(

    ["RAG"],

    [

        rag_scores_df["context_precision"].mean()

    ]

)

plt.ylim(0,1)

plt.title("Context Precision")

plt.ylabel("Score")

for bar in bars:

    value = bar.get_height()

    plt.text(

        bar.get_x()+bar.get_width()/2,

        value,

        f"{value:.2f}",

        ha="center",

        va="bottom"

    )

plt.tight_layout()

plt.savefig(

    os.path.join(

        OUTPUT_DIR,

        "context_precision.png"

    )

)

plt.close()

# Context recall

plt.figure(figsize=(6,5))

bars = plt.bar(

    ["RAG"],

    [

        rag_scores_df["context_recall"].mean()

    ]

)

plt.ylim(0,1)

plt.title("Context Recall")

plt.ylabel("Score")

for bar in bars:

    value = bar.get_height()

    plt.text(

        bar.get_x()+bar.get_width()/2,

        value,

        f"{value:.2f}",

        ha="center",

        va="bottom"

    )

plt.tight_layout()

plt.savefig(

    os.path.join(

        OUTPUT_DIR,

        "context_recall.png"

    )

)

plt.close()

# Latency comparison

plt.figure(figsize=(6,5))

bars = plt.bar(

    ["LLM Only","RAG"],

    [

        llm_df["Latency (ms)"].mean(),

        rag_df["Latency (ms)"].mean()

    ]

)

plt.title("Average Response Time")

plt.ylabel("Latency (ms)")

for bar in bars:

    value = bar.get_height()

    plt.text(

        bar.get_x()+bar.get_width()/2,

        value,

        f"{value:.1f}",

        ha="center",

        va="bottom"

    )

plt.tight_layout()

plt.savefig(

    os.path.join(

        OUTPUT_DIR,

        "latency_comparison.png"

    )

)

plt.close()

print("\n✓ Graphs saved successfully")