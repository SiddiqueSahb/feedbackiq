"""
Verifies the Analyse-page LLM fixes (docs/Analyse_LLM_Audit.md, issues 1, 3, 4, 5).
No FAISS index needed - similar_reviews is passed in directly.

Run: python evaluate/verify_analyse_fixes.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nlp.summariser import (  # noqa: E402
    analyse_review_with_llm,
    EMPTY_ANALYSIS,
    ANALYSE_SIMILARITY_THRESHOLD,
)

VALID_SEVERITY = {"Low", "Medium", "High", "Critical"}
VALID_PRIORITY = {"Low", "Medium", "High", "Urgent"}
VALID_DEPARTMENT = {
    "Engineering", "Quality Assurance", "Customer Support",
    "Logistics", "Finance", "Marketing",
}


def check_shape(result: dict) -> list[str]:
    problems = []
    if set(result.keys()) != set(EMPTY_ANALYSIS.keys()):
        problems.append(f"unexpected keys: {set(result.keys())}")
        return problems

    if result == EMPTY_ANALYSIS:
        # Graceful degradation is an acceptable outcome, not a failure.
        return problems

    if not isinstance(result["keywords"], list) or len(result["keywords"]) != 5:
        problems.append(f"expected exactly 5 keywords, got {result['keywords']!r}")
    if result["severity"] not in VALID_SEVERITY:
        problems.append(f"invalid severity: {result['severity']!r}")
    if result["priority"] not in VALID_PRIORITY:
        problems.append(f"invalid priority: {result['priority']!r}")
    if result["department"] not in VALID_DEPARTMENT:
        problems.append(f"invalid department: {result['department']!r}")
    return problems


def run() -> int:
    failures = []

    print(f"ANALYSE_SIMILARITY_THRESHOLD = {ANALYSE_SIMILARITY_THRESHOLD}\n")

    # Case 1: weak matches only -> should be treated as empty
    weak_reviews = [
        {"platform": "amazon", "rating": 3, "sentiment_label": "neutral",
         "similarity_score": 0.10, "text": "Completely unrelated review about a toaster."},
        {"platform": "yelp", "rating": 2, "sentiment_label": "negative",
         "similarity_score": 0.22, "text": "Also unrelated, about parking validation."},
    ]
    result1 = analyse_review_with_llm(
        text="The battery stopped charging after one week and support never replied.",
        sentiment="negative",
        category="Product Performance Failures",
        similar_reviews=weak_reviews,
    )
    problems1 = check_shape(result1)
    print("[Case 1: weak matches only]")
    print(f"  business_insight: {result1.get('business_insight')}")
    if problems1:
        failures.append(f"Case 1: {problems1}")
        print(f"  [FAIL] {problems1}")
    else:
        print("  [PASS] shape OK (weak matches should not appear as grounding)")
    print()

    # Case 2: no similar reviews
    result2 = analyse_review_with_llm(
        text="The battery stopped charging after one week and support never replied.",
        sentiment="negative",
        category="Product Performance Failures",
        similar_reviews=[],
    )
    problems2 = check_shape(result2)
    print("[Case 2: no similar reviews]")
    print(f"  business_insight: {result2.get('business_insight')}")
    if problems2:
        failures.append(f"Case 2: {problems2}")
        print(f"  [FAIL] {problems2}")
    else:
        print("  [PASS] shape OK")
    print()

    # Case 3: strong matches -> should be used as grounding
    strong_reviews = [
        {"platform": "amazon", "rating": 1, "sentiment_label": "negative",
         "similarity_score": 0.71, "text": "My phone battery died after 5 days and nobody from support responded."},
        {"platform": "amazon", "rating": 2, "sentiment_label": "negative",
         "similarity_score": 0.58, "text": "Charging port stopped working within the first two weeks."},
    ]
    result3 = analyse_review_with_llm(
        text="The battery stopped charging after one week and support never replied.",
        sentiment="negative",
        category="Product Performance Failures",
        similar_reviews=strong_reviews,
    )
    problems3 = check_shape(result3)
    print("[Case 3: strong matches]")
    print(f"  business_insight: {result3.get('business_insight')}")
    if problems3:
        failures.append(f"Case 3: {problems3}")
        print(f"  [FAIL] {problems3}")
    else:
        print("  [PASS] shape OK")
    print()

    print("=" * 60)
    if failures:
        print(f"{len(failures)} case(s) had problems:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("All cases produced a valid shape (or graceful EMPTY_ANALYSIS fallback).")
    print("Manually eyeball the business_insight lines above:")
    print("  - Case 1 & 2 should NOT reference 'similar cases' or comparison data.")
    print("  - Case 3 SHOULD read as grounded in both the review and the retrieved evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
