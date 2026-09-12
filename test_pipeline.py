import os

# Prevent thread conflicts (important on macOS)
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import torch

torch.set_num_threads(1)
torch.set_num_interop_threads(1)

import time

from feedbackiq.nlp.sentiment import get_finetuned
from feedbackiq.nlp.categoriser import categorise
from feedbackiq.nlp.embedding_service import semantic_search
from feedbackiq.nlp.summariser import analyse_review_with_llm


def print_stage(stage):
    print("\n" + "=" * 80)
    print(stage)
    print("=" * 80)


review = """
The battery stopped charging after one week.
Customer support ignored my emails and refused to replace the product.
"""

print_stage("STARTING COMPLETE NLP PIPELINE")

# Step 1 - Sentiment
print_stage("STEP 1 - SENTIMENT")

start = time.time()

sentiment = get_finetuned().predict(review)

print("✓ Sentiment Completed")
print(sentiment)

print(f"Time: {time.time()-start:.2f}s")

# Step 2 - Complaint Category
print_stage("STEP 2 - COMPLAINT CATEGORY")

start = time.time()

category = categorise(review)[0]["category"]

print("✓ Category Completed")
print(category)

print(f"Time: {time.time()-start:.2f}s")

# Step 3 - Semantic Search (RAG)
print_stage("STEP 3 - RAG RETRIEVAL")

start = time.time()

similar = semantic_search(
    review,
    top_k=5
)

print("✓ Semantic Search Completed")

for i, r in enumerate(similar, start=1):
    print(f"\nResult {i}")
    print(f"Similarity : {r['similarity_score']:.3f}")
    print(f"Platform   : {r['platform']}")
    print(f"Review     : {r['text'][:120]}")

print(f"\nTime: {time.time()-start:.2f}s")

# Step 4 - LLM
print_stage("STEP 4 - LLM ANALYSIS")

start = time.time()

analysis = analyse_review_with_llm(
    text=review,
    sentiment=sentiment["label"],
    category=category,
    similar_reviews=similar,
)

print("✓ LLM Completed")

print(f"Time: {time.time()-start:.2f}s")

# Final Output
print_stage("FINAL OUTPUT")

print("\nSentiment")
print(sentiment)

print("\nCategory")
print(category)

print("\nLLM Output")

for key, value in analysis.items():
    print(f"{key}: {value}")