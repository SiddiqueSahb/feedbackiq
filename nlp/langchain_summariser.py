from __future__ import annotations

import json
import os
import sys

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from config import settings

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_groq import ChatGroq
from logger import get_logger

log = get_logger("nlp.summariser")

if not settings.GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not configured.")

llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model=settings.GROQ_MODEL,
    temperature=settings.LLM_TEMPERATURE,
)

parser = JsonOutputParser()

EMPTY_ANALYSIS = {
    "summary": "",
    "keywords": [],
    "business_insight": "",
    "severity": "",
    "priority": "",
    "department": "",
    "executive_summary": "",
}

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
2. Similar historical customer reviews retrieved from the review database.

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
- Use BOTH the review and retrieved reviews.

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

Use retrieved reviews ONLY as supporting evidence.

Never invent information.

Return ONLY JSON.
"""
)

# LCEL chain
chain = prompt | llm | parser


def analyse_review_with_llm(
    text: str,
    sentiment: str,
    category: str,
    similar_reviews: list[dict],
) -> dict:

    rag_context = ""

    for i, review in enumerate(similar_reviews, start=1):

        rag_context += (
            f"\nSimilar Review {i}\n"
            f"Platform: {review['platform']}\n"
            f"Similarity Score: {review['similarity_score']:.3f}\n"
            f"Platform: {review['platform']}\n"
            f"Rating: {review.get('rating','N/A')}\n"
            f"Sentiment: {review.get('sentiment_label','N/A')}\n"
            f"Similarity Score: {review['similarity_score']:.3f}\n"
            f"Review: {review['text'][:250]}\n"
        )

    log.info("Generating LLM business analysis")
    try:
        result = chain.invoke(
        {
            "review": text[:600],
            "sentiment": sentiment,
            "category": category,
            "rag_context": rag_context,
        }
        )
        log.info("LLM analysis completed")
        return result

    except Exception:
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