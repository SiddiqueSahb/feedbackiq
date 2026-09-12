from __future__ import annotations

import re
import json
import os
import sys

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from config import settings

from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from logger import get_logger

log = get_logger("nlp.summariser")

EMPTY_ANALYSIS = {
    "summary": "",
    "keywords": [],
    "business_insight": "",
    "severity": "",
    "priority": "",
    "department": "",
    "executive_summary": "",
}

# Min relevance score for a "similar review" to enter the prompt as evidence;
# same basis as rag.pipeline.SIMILARITY_THRESHOLD. See Analyse_LLM_Audit#1.
ANALYSE_SIMILARITY_THRESHOLD = 0.35

NO_SIMILAR_REVIEWS_MARKER = "(No sufficiently similar historical reviews found.)"


class ReviewAnalysisLLM(BaseModel):
    """Schema the LLM's JSON response must conform to -- JsonOutputParser alone
    only guarantees valid JSON, not that keys/types/enums match. See Audit#4."""

    summary: str
    keywords: list[str] = Field(min_length=5, max_length=5)
    business_insight: str
    severity: Literal["Low", "Medium", "High", "Critical"]
    priority: Literal["Low", "Medium", "High", "Urgent"]
    department: Literal[
        "Engineering", "Quality Assurance", "Customer Support",
        "Logistics", "Finance", "Marketing",
    ]
    executive_summary: str


prompt = ChatPromptTemplate.from_template(
"""
You are a Senior Customer Experience Analyst.

IMPORTANT

Return ONLY ONE valid JSON object.

Do NOT include:
- explanations
- markdown
- notes
- reasoning
- analysis
- code fences

Analyse the customer review using BOTH:

1. The current customer review.
2. Similar historical customer reviews retrieved from the review database, if any are listed below.

Current Review
--------------
{review}

Predicted Sentiment
-------------------
{sentiment}

Complaint Category
------------------
{category}

Retrieved Similar Reviews (RAG)
-------------------------------
{rag_context}


{{
    "summary": "",
    "keywords": [],
    "business_insight": "",
    "severity": "",
    "priority": "",
    "department": "",
    "executive_summary": ""
}}

Instructions

1. Summary
- Maximum 2 sentences.
- Describe ONLY the current customer review.
- Preserve important product or service details.
- Do not invent information.

2. Keywords
- Return exactly FIVE business-related keywords.
- Each keyword should contain 1-3 words.
- Avoid duplicates.

3. Business Insight
- Maximum 25 words.
- Start with an action verb.
- If "Retrieved Similar Reviews" lists actual reviews, ground the insight in both the current review and that evidence.
- If "Retrieved Similar Reviews" states that none were found, base the insight only on the current review — do not reference or imply the existence of similar cases.

4. Severity
Choose ONE:
Low
Medium
High
Critical

5. Priority
Choose ONE:
Low
Medium
High
Urgent

6. Department

Engineering
- Software defects
- Hardware design issues

Quality Assurance
- Product failures
- Manufacturing defects
- Reliability issues

Customer Support
- Communication
- Complaint handling

Logistics
- Shipping
- Delivery
- Packaging

Finance
- Billing
- Refunds

Marketing
- Advertising
- Product information

7. Executive Summary
Maximum 30 words.

IMPORTANT

Use retrieved reviews ONLY as supporting evidence, and only if any are actually listed above.

Never invent information.

Return ONLY JSON.
"""
)


# Lazy, not at import time -- used to raise RuntimeError on import if
# GROQ_API_KEY was blank, crashing the whole API before it could bind a port.
@lru_cache(maxsize=1)
def _get_chain():
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    llm = ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model=settings.GROQ_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        timeout=30,
    )

    # Structured output (tool-calling), not prompt+JsonOutputParser -- the latter
    # had llama-3.1-8b-instant silently drop executive_summary ~2/3 of the time.
    return prompt | llm.with_structured_output(ReviewAnalysisLLM)


def _recover_from_failed_tool_call(exc: Exception):
    """Rescue a valid analysis from Groq's `tool_use_failed` error -- some models
    write JSON as content, not a tool call, but Groq still returns it via
    `failed_generation`. Returns a dict, or None if it doesn't validate."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        # Older/other client versions surface the payload only in the message.
        match = re.search(r"'failed_generation':\s*'(.*)'\}\}\s*$", str(exc), re.S)
        raw = match.group(1).encode().decode("unicode_escape") if match else None
    else:
        error = body.get("error") or {}
        if error.get("code") != "tool_use_failed":
            return None
        raw = error.get("failed_generation")

    if not raw:
        return None

    # The model sometimes wraps the JSON in prose or a ```json fence.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else raw
    if not fenced:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end == -1:
            return None
        candidate = candidate[start:end + 1]

    try:
        return ReviewAnalysisLLM.model_validate_json(candidate).model_dump()
    except (ValidationError, ValueError):
        log.warning("Recovered payload from failed tool call did not validate.")
        return None


def analyse_review_with_llm(
    text: str,
    sentiment: str,
    category: str,
    similar_reviews: list[dict],
) -> dict:

    # Drop weak matches -- never tell the LLM a weak match is supporting evidence.
    grounded_reviews = [
        r for r in similar_reviews
        if r.get("similarity_score", 0) >= ANALYSE_SIMILARITY_THRESHOLD
    ]

    if grounded_reviews:
        rag_context = ""
        for i, review in enumerate(grounded_reviews, start=1):
            rag_context += (
                f"\nSimilar Review {i}\n"
                f"Platform: {review.get('platform', 'unknown')}\n"
                f"Rating: {review.get('rating', 'N/A')}\n"
                f"Sentiment: {review.get('sentiment_label', 'N/A')}\n"
                f"Similarity Score: {review.get('similarity_score', 0):.3f}\n"
                f"Review: {review.get('text', '')[:250]}\n"
            )
    else:
        # Explicit marker, not empty string -- prompt instruction 3 branches on
        # this; an empty string let the model fabricate "similar reviews" content.
        rag_context = NO_SIMILAR_REVIEWS_MARKER

    log.info("Generating LLM business analysis")
    try:
        # result is already a validated ReviewAnalysisLLM instance (or raises) --
        # schema-constrained at generation time; see _get_chain().
        result = _get_chain().invoke(
        {
            "review": text[:600],
            "sentiment": sentiment,
            "category": category,
            "rag_context": rag_context,
        }
        )
        log.info("LLM analysis completed")
        return result.model_dump()

    except ValidationError:
        log.exception("LLM returned a malformed or out-of-enum analysis shape.")
        return EMPTY_ANALYSIS.copy()

    except Exception as exc:
        # openai/gpt-oss-20b (and others) write JSON as content instead of a tool
        # call, so Groq rejects it as tool_use_failed -- recover and validate it.
        recovered = _recover_from_failed_tool_call(exc)
        if recovered is not None:
            log.warning(
                "Model did not emit a tool call; recovered and validated the JSON "
                "from the error payload instead."
            )
            return recovered

        log.exception("LLM request failed.")
        return EMPTY_ANALYSIS.copy()


# Test

if __name__ == "__main__":

    demo = analyse_review_with_llm(
        text="The battery stopped charging after one week and customer support ignored my emails.",
        sentiment="negative",
        category="Product Performance Failures",
        similar_reviews=[],
    )

    print(json.dumps(demo, indent=4))
