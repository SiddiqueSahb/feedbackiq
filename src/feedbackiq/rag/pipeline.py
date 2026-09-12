"""
RAG pipeline: conversational customer complaint exploration.

Business analysts ask questions about complaint patterns; retrieves
relevant reviews from the FAISS index and generates grounded answers.
Evaluated via RAGAS in evaluate/evaluate_llm_vs_rag.py.

Refusals (out of scope, no evidence) are returned as a normal result with
grounded=False; genuine failures raise EngineError/RetrievalError rather than
being dressed up as an answer.
"""

from __future__ import annotations
import os, re, time

# Must be set before torch/faiss import — both bundle OpenMP runtimes and
# loading both causes a hard segfault on macOS during parallel work.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import threading
from typing import Optional, Any
from feedbackiq.core.logging import get_logger
from functools import lru_cache
from feedbackiq.core.config import settings
from feedbackiq.engine.errors import EngineError, RetrievalError
from feedbackiq.rag.prompts import (
    RAG_PROMPT,
    CONDENSE_QUESTION_PROMPT,
    is_complaint_question,
    is_complaint_question_llm,
)
from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import PromptTemplate
from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

log = get_logger("rag.pipeline")

_vectorstore_lock = threading.Lock()

# Min relevance score to keep a retrieved review; 0.35 is untuned, revisit
SIMILARITY_THRESHOLD = 0.35

# Cap on review text entering the prompt; rarely binds today, guards
# against context overflow if longer content is added later.
MAX_REVIEW_CHARS = 1000

# Small retry budget for the live path (a user is waiting) — much smaller
# than evaluate_llm_vs_rag.py's. Only rate-limit errors retry.
MAX_LIVE_RETRIES = 2
RETRY_BASE_WAIT = 2.0


def _extract_retry_after_seconds(exc: Exception, default: float = RETRY_BASE_WAIT) -> float:
    match = re.search(r"try again in ([\d.]+)s", str(exc))
    if match:
        try:
            return float(match.group(1)) + 0.5
        except ValueError:
            pass
    return default


def _invoke_with_retry(fn, *args, max_retries: int = MAX_LIVE_RETRIES, **kwargs):
    """Call fn(*args, **kwargs), retrying only on rate-limit errors.

    Wraps the two live LLM call sites (ask(), is_complaint_question_llm())
    so a transient Groq rate limit doesn't surface as a hard error.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            is_rate_limit = "rate_limit" in str(exc).lower() or "429" in str(exc)
            if not is_rate_limit or attempt >= max_retries:
                raise
            wait = _extract_retry_after_seconds(exc)
            log.warning(
                "Rate limit hit, retrying in %.1fs (%d/%d)",
                wait, attempt + 1, max_retries,
            )
            time.sleep(wait)


# Default document formatter only inserts page_content — tag each review
# with its metadata so RAG_PROMPT's grounding rules are enforceable.
DOCUMENT_PROMPT = PromptTemplate(
    input_variables=["page_content", "platform", "rating", "sentiment_label"],
    template="[Platform: {platform} | Rating: {rating} | Sentiment: {sentiment_label}]\n{page_content}",
)

# Out-of-scope response
OUT_OF_SCOPE_MSG: str = (
    "I'm designed specifically for exploring customer complaint patterns across "
    "Amazon, Yelp, and Twitter Airline reviews. Please ask me about complaints, "
    "sentiment trends, or customer feedback. For example: "
    "'What are the top complaints on Amazon?' or "
    "'Why are airline customers most dissatisfied?'"
)

# Load LLM
def _get_llm() -> Optional[Any]:
    """Return LangChain LLM wrapper (Groq or OpenAI). Returns None if no key configured."""
    if settings.USE_GROQ and settings.GROQ_API_KEY:
        from langchain_groq import ChatGroq
        return ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model_name=settings.GROQ_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            # Caps a stuck call before it outlives the frontend's 60s timeout
            timeout=30,
        )
    return None


# Load FAISS Vector DB
@lru_cache(maxsize=1)
def _get_vectorstore_uncached() -> Any:
    """Load LangChain FAISS vectorstore (cached after first load)."""
    from langchain_community.vectorstores import FAISS as LangFAISS
    from langchain_huggingface import HuggingFaceEmbeddings

    if not os.path.exists(settings.LANGCHAIN_INDEX_PATH):
        raise FileNotFoundError(
            f"LangChain index not found at {settings.LANGCHAIN_INDEX_PATH}. "
            "Run: python scripts/build_index.py"
        )
    embeddings = HuggingFaceEmbeddings(
        model_name=settings.EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    # Loads existing vectors — does not (re)compute embeddings.
    vs = LangFAISS.load_local(
        settings.LANGCHAIN_INDEX_PATH,
        embeddings,
        allow_dangerous_deserialization=True,
    )
    log.info("LangChain FAISS vectorstore loaded")
    return vs


def _get_vectorstore() -> Any:
    # Lock closes the gap lru_cache leaves open on its own — see the same
    # pattern/comment in nlp/embedding_service.py._load_resources.
    with _vectorstore_lock:
        return _get_vectorstore_uncached()


def _make_grounded_retriever(
    vectorstore: Any,
    platform_filter: Optional[dict[str, str]],
    k: int = 5,
    fetch_k: int = 20,
    lambda_mult: float = 0.5,
    threshold: float = SIMILARITY_THRESHOLD,
) -> RunnableLambda:
    """Retriever that only returns docs clearing `threshold` — MMR alone
    controls diversity, not relevance, so this adds a hard gate on top.

    Two passes over the same pool: relevance scoring picks the passing
    set, then MMR picks a diverse k, intersected with that set so MMR
    can't smuggle in a sub-threshold doc. Returns fresh Document copies
    with similarity_score attached, text capped at MAX_REVIEW_CHARS.
    """

    def _retrieve(query: str) -> list[Document]:
        scored = vectorstore.similarity_search_with_relevance_scores(
            query, k=fetch_k, filter=platform_filter,
        )
        passing_scores = {
            doc.metadata.get("review_id"): score
            for doc, score in scored
            if score >= threshold
        }
        if not passing_scores:
            return []

        mmr_docs = vectorstore.max_marginal_relevance_search(
            query, k=k, fetch_k=fetch_k, lambda_mult=lambda_mult, filter=platform_filter,
        )

        grounded_docs: list[Document] = []
        for doc in mmr_docs:
            score = passing_scores.get(doc.metadata.get("review_id"))
            if score is None:
                continue

            content = doc.page_content
            if len(content) > MAX_REVIEW_CHARS:
                content = content[:MAX_REVIEW_CHARS] + "…"

            meta = dict(doc.metadata)
            meta["similarity_score"] = round(float(score), 4)
            grounded_docs.append(Document(page_content=content, metadata=meta))

        return grounded_docs

    return RunnableLambda(_retrieve)

def _format_sources(source_docs: list) -> list[dict[str, object]]:
    """Convert source documents into structured dicts for UI citation display.

    "text" is truncated to 200 chars for preview; "full_text" carries the
    untruncated review for anything (e.g. RAGAS) that needs the LLM's actual input.
    """
    sources: list[dict[str, object]] = []
    for doc in source_docs:
        meta: dict = doc.metadata or {}
        sources.append({
            "text":             doc.page_content[:200],
            "full_text":        doc.page_content,
            "platform":         meta.get("platform",        "unknown"),
            "sentiment_label":  meta.get("sentiment_label", "unknown"),
            "rating":           meta.get("rating",          0),
            "review_id":        meta.get("review_id",       ""),
            "similarity_score": meta.get("similarity_score"),
        })
    return sources

def _build_history_string(chat_history: list[dict]) -> list[tuple[str, str]]:
    """
    Convert chat history from API format:
    [{"role": ..., "content": ...}]
    to LangChain conversation pairs:
    [(user_message, assistant_reply), ...]
    """

    pairs: list[tuple[str, str]] = []

    # skip malformed / non-dict messages
    history: list[dict] = [m for m in (chat_history or []) if isinstance(m, dict)]

    i: int = 0
    while i < len(history) - 1:

        if history[i].get("role") == "user" and history[i + 1].get("role") == "assistant":
            pairs.append((
                history[i].get("content", ""),
                history[i + 1].get("content", ""),
            ))
            i += 2

        else:
            i += 1

    return pairs



# Recognised platform names, checked against the raw question text.
_PLATFORM_KEYWORDS: dict[str, str] = {
    "amazon": "amazon",
    "yelp": "yelp",
    "twitter": "twitter_airline",
    "airline": "twitter_airline",
}


def _detect_platform_filter(question: str) -> Optional[dict[str, str]]:
    """If the question names a platform, return a FAISS filter for it.

    Without this, a question naming "Amazon" vs "Yelp" hits the same
    unfiltered search — the platform name is just more text to embed,
    not a constraint. Only looks at the current question, not chat_history.
    """
    text = question.lower()
    for keyword, platform in _PLATFORM_KEYWORDS.items():
        if keyword in text:
            return {"platform": platform}
    return None


def _format_chat_history_text(pairs: list[tuple[str, str]]) -> str:
    """Render (human, ai) pairs as text for CONDENSE_QUESTION_PROMPT.

    Returns "" for no history — create_history_aware_retriever treats that
    as falsy and skips reformulation, querying with the raw question.
    """
    if not pairs:
        return ""
    lines: list[str] = []
    for human, ai in pairs:
        lines.append(f"Human: {human}")
        lines.append(f"AI: {ai}")
    return "\n".join(lines)


def ask(question: str,chat_history: Optional[list[dict]] = None,) -> dict[str, object]:
    """
    Answer customer feedback questions using Retrieval-Augmented Generation (RAG).

    Args:
        question: User's question about customer reviews or complaints.
        chat_history: Previous conversation in API format
                      [{"role": "...", "content": "..."}].

    Returns:
        A dictionary containing:
            answer           - Response generated by the LLM.
            sources          - Retrieved reviews used to generate the answer.
            use_case         - Current application ("complaint exploration").
            retrieval_count  - Number of retrieved reviews.
    """

    log.info("RAG ask | question='%s' history_len=%d",question[:60], len(chat_history or []))


    # Distinct from OUT_OF_SCOPE_MSG: fires when in-scope but nothing in
    # the index clears SIMILARITY_THRESHOLD.
    NO_EVIDENCE_MSG = "I couldn't find enough relevant customer reviews to answer this question."

    llm = _get_llm()
    if llm is None:
        return {
            "answer":          "No LLM API key configured. Add GROQ_API_KEY to .env (free at console.groq.com).",
            "sources":         [],
            "use_case":        "complaint_exploration",
            "retrieval_count": 0,
            "grounded":        False,
        }

    # Cheap keyword check — catches named off-topic subjects with no LLM call.
    if not is_complaint_question(question):
        log.info("Out-of-scope question rejected by keyword guard: %s", question[:60])
        return {
            "answer":          OUT_OF_SCOPE_MSG,
            "sources":         [],
            "use_case":        "complaint_exploration",
            "retrieval_count": 0,
            "grounded":        False,
        }

    # Second-stage LLM guard catches what the keyword list misses (e.g.
    # "Who is the CEO of Apple?"), at the cost of one extra LLM call.
    if not is_complaint_question_llm(question, llm=llm):
        log.info("Out-of-scope question rejected by LLM guard: %s", question[:60])
        return {
            "answer":          OUT_OF_SCOPE_MSG,
            "sources":         [],
            "use_case":        "complaint_exploration",
            "retrieval_count": 0,
            "grounded":        False,
        }

    try:
        vectorstore = _get_vectorstore()

        platform_filter = _detect_platform_filter(question)
        if platform_filter:
            log.info("Platform filter applied: %s", platform_filter)

        fetch_k = 50 if platform_filter else 20
        retriever = _make_grounded_retriever(
            vectorstore, platform_filter, k=5, fetch_k=fetch_k,
        )
        log.debug("Grounded retriever ready (threshold=%.2f)", SIMILARITY_THRESHOLD)

        # Fast path: single-turn question needs no reformulation, so probe
        # retrieval directly and refuse here before an LLM generation call.
        if not chat_history:
            probe_docs = retriever.invoke(question)
            if not probe_docs:
                log.info("No documents cleared the similarity threshold (fast path): %s", question[:60])
                return {
                    "answer":          NO_EVIDENCE_MSG,
                    "sources":         [],
                    "use_case":        "complaint_exploration",
                    "retrieval_count": 0,
                    "grounded":        False,
                }

        # LCEL replacement for the deprecated ConversationalRetrievalChain;
        # chat_history passed through as plain text, no memory object.
        history_aware_retriever = create_history_aware_retriever(
            llm, retriever, CONDENSE_QUESTION_PROMPT
        )
        combine_docs_chain = create_stuff_documents_chain(
            llm, RAG_PROMPT, document_prompt=DOCUMENT_PROMPT
        )
        chain = create_retrieval_chain(history_aware_retriever, combine_docs_chain)
        log.debug("LCEL retrieval chain built")

        chat_history_text = _format_chat_history_text(_build_history_string(chat_history or []))

        # Absorbs a brief Groq rate limit instead of surfacing "An error occurred"
        result = _invoke_with_retry(
            chain.invoke, {"input": question, "chat_history": chat_history_text}
        )

        retrieved_docs = result.get("context", [])

        if not retrieved_docs:
            # Backstop for multi-turn questions (reformulated query the fast
            # path never sees): discard the answer if nothing cleared the
            # threshold — RAG_PROMPT's refusal rule is an instruction, not a guarantee.
            log.info(
                "No grounded context after chain execution, overriding generated answer: %s",
                question[:60],
            )
            return {
                "answer":          NO_EVIDENCE_MSG,
                "sources":         [],
                "use_case":        "complaint_exploration",
                "retrieval_count": 0,
                "grounded":        False,
            }

        sources = _format_sources(retrieved_docs)

        log.info(
            "RAG answer | sources=%d answer_len=%d",
            len(sources),
            len(result.get("answer", "")),
        )

        return {
            "answer":          result["answer"],
            "sources":         sources,
            "use_case":        "complaint_exploration",
            "retrieval_count": len(sources),
            "grounded":        True,
        }

    except FileNotFoundError as exc:
        # Milestone 3: failures raise. Returning the exception text as an "answer" with
        # HTTP 200 (what this did before) made a broken index indistinguishable from a
        # real answer, both to the user and to monitoring. The route maps this to 503/500.
        log.error("Search index not found: %s", exc)
        raise RetrievalError(
            "The feedback search index is unavailable."
        ) from exc

    except Exception as exc:
        log.error("RAG pipeline error: %s", exc, exc_info=True)
        raise EngineError(
            "Failed to answer the question."
        ) from exc