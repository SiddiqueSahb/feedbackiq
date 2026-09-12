"""
Evaluate Semantic Search Module

This script evaluates:

1. Retrieval latency
2. Similarity scores
3. Platform filtering
4. Sentiment filtering
5. Rating filtering
6. Query diversity
7. Overall retrieval performance

Results are exported as CSV files for dissertation analysis.
"""

import os
import time
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd


from feedbackiq.nlp.tf_idf_embedding import tfidf_search

# Create output directory

OUTPUT_DIR = "data/results/evaluation/tfidf"

os.makedirs(
    OUTPUT_DIR,
    exist_ok=True
)

TOP_K = 10

# Test Queries
QUERIES = [

    # Electronics
    "My phone won't stay charged for very long.",
    "My laptop battery runs out much faster than it should.",
    "The screen suddenly stopped working after a few days.",
    "The camera no longer takes clear pictures.",
    "The speaker sound is distorted and unclear.",

    # Delivery & Orders
    "My parcel arrived much later than expected.",
    "The package was damaged when it reached me.",
    "I received a completely different item than I ordered.",
    "Part of my order was missing from the box.",
    "My order was cancelled without any explanation.",

    # Customer Service
    "The customer support team was very unhelpful.",
    "I never got my money back after returning the product.",
    "Nobody responded to my complaint.",
    "The staff were rude during my visit.",
    "I couldn't get any help from customer service.",

    # Product Quality
    "The product stopped working after only one week.",
    "The quality is far worse than I expected.",
    "The colour looks completely different from the pictures.",
    "The item had already expired when it arrived.",
    "The product broke after only a few uses.",

    # Food
    "The food arrived cold and tasted terrible.",
    "My pizza was delivered very late and was cold.",

    # Airline
    "My flight was delayed for several hours.",
    "The airline lost my luggage.",
    "My flight was cancelled at the last minute.",

    # Internet & Technology
    "I keep losing my internet connection.",
    "The WiFi disconnects every few minutes.",
    "The internet speed is much slower than advertised.",

    # Payment
    "I was charged twice for the same purchase.",
    "My payment was successful but my order wasn't confirmed."

]
print("="*80)
print("Semantic Search Evaluation")
print("="*80)

# Retrieval Performance Evaluation

print("="*80)
print("Retrieval Performance Evaluation")
print("="*80)

# Warm-up
tfidf_search(
    query="warm up",
    top_k=1
)

retrieval_results = []

for query in QUERIES:

    start = time.perf_counter()

    results = tfidf_search(
        query=query,
        top_k=TOP_K
    )

    end = time.perf_counter()

    retrieval_time = round((end - start) * 1000, 2)

    if results:
        scores = [review["similarity_score"] for review in results]

        avg_similarity = round(np.mean(scores), 4)
        max_similarity = round(np.max(scores), 4)
        min_similarity = round(np.min(scores), 4)

    else:
        avg_similarity = 0
        max_similarity = 0
        min_similarity = 0

    retrieval_results.append({
        "Query": query,
        "Retrieval Time (ms)": retrieval_time,
        "Average Similarity": avg_similarity,
        "Maximum Similarity": max_similarity,
        "Minimum Similarity": min_similarity,
        "Retrieved Reviews": len(results)
    })


retrieval_df = pd.DataFrame(retrieval_results)

print("\nRetrieval Performance")
print(retrieval_df)

retrieval_df.to_csv(
    os.path.join(OUTPUT_DIR, "retrieval_performance.csv"),
    index=False
)

print("\n✓ retrieval_performance.csv saved")


# Summary Statistics

summary_df = pd.DataFrame({
    "Metric": [
        "Average Retrieval Time (ms)",
        "Minimum Retrieval Time (ms)",
        "Maximum Retrieval Time (ms)",
        "Average Similarity",
        "Average Retrieved Reviews"
    ],
    "Value": [
        round(retrieval_df["Retrieval Time (ms)"].mean(), 2),
        round(retrieval_df["Retrieval Time (ms)"].min(), 2),
        round(retrieval_df["Retrieval Time (ms)"].max(), 2),
        round(retrieval_df["Average Similarity"].mean(), 4),
        round(retrieval_df["Retrieved Reviews"].mean(), 2)
    ]
})

successful_queries = (
    retrieval_df["Retrieved Reviews"] > 0
).sum()

summary_df.loc[len(summary_df)] = [
    "Query Success Rate (%)",
    round(
        successful_queries /
        len(retrieval_df) * 100,
        2
    )
]

print("\nSummary Statistics")
print(summary_df)

summary_df.to_csv(
    os.path.join(OUTPUT_DIR, "summary_statistics.csv"),
    index=False
)

print("\n summary_statistics.csv saved")


# Query Diversity & Retrieval Quality Evaluation

print("\n" + "=" * 80)
print("Query Diversity & Retrieval Quality")
print("=" * 80)

retrieval_examples = []

for query in QUERIES:

    print(f"\nEvaluating: {query}")

    results = tfidf_search(
        query=query,
        top_k=3
    )

    if not results:
        retrieval_examples.append({
            "Query": query,
            "Rank": "-",
            "Similarity": "-",
            "Platform": "-",
            "Sentiment": "-",
            "Rating": "-",
            "Review": "No review retrieved"
        })
        continue

    for rank, review in enumerate(results, start=1):

        retrieval_examples.append({
            "Query": query,
            "Rank": rank,
            "Similarity": round(review["similarity_score"], 4),
            "Platform": review["platform"],
            "Sentiment": review["sentiment_label"],
            "Rating": review["rating"],
            "Review": review["text"][:250]
        })


retrieval_examples_df = pd.DataFrame(retrieval_examples)

print("\nTop Retrieved Reviews")
print(retrieval_examples_df.head(20))

retrieval_examples_df.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "retrieval_examples.csv"
    ),
    index=False
)

print("\n✓ retrieval_examples.csv saved")

# Precision@3 Evaluation

print("\n" + "=" * 80)
print("Precision@3 Evaluation")
print("=" * 80)

precision_results = []

print(
    "\nOpen 'retrieval_examples.csv' and manually mark "
    "whether each retrieved review is relevant."
)

for query in QUERIES:

    print(f"\nQuery: {query}")

    results = tfidf_search(
        query=query,
        top_k=3
    )

    relevant = 0

    for i, review in enumerate(results, start=1):

        print("-" * 60)
        print(f"Rank {i}")
        print(review["text"])
        print("-" * 60)

        answer = input(
            "Relevant? (y/n): "
        ).strip().lower()

        if answer == "y":
            relevant += 1

    precision = relevant / len(results)

    precision_results.append({
        "Query": query,
        "Relevant Reviews": relevant,
        "Retrieved Reviews": len(results),
        "Precision@3": round(precision, 2)
    })


precision_df = pd.DataFrame(precision_results)

print("\nPrecision Results")
print(precision_df)

precision_df.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "precision_at_3.csv"
    ),
    index=False
)

print("\n✓ precision_at_3.csv saved")


# Overall Evaluation Summary

print("\n" + "=" * 80)
print("Overall Evaluation Summary")
print("=" * 80)

overall_metrics = [
    {
        "Metric": "Number of Queries",
        "Value": len(QUERIES)
    },
    {
        "Metric": "Average Retrieval Time (ms)",
        "Value": round(retrieval_df["Retrieval Time (ms)"].mean(), 2)
    },
    {
        "Metric": "Minimum Retrieval Time (ms)",
        "Value": round(retrieval_df["Retrieval Time (ms)"].min(), 2)
    },
    {
        "Metric": "Maximum Retrieval Time (ms)",
        "Value": round(retrieval_df["Retrieval Time (ms)"].max(), 2)
    },
    {
        "Metric": "Average Similarity Score",
        "Value": round(retrieval_df["Average Similarity"].mean(), 4)
    },
    {
        "Metric": "Average Reviews Retrieved",
        "Value": round(retrieval_df["Retrieved Reviews"].mean(), 2)
    },
    {
        "Metric": "Average Precision@3",
        "Value": round(precision_df["Precision@3"].mean(), 2)
    }
]

successful_queries = (
    retrieval_df["Retrieved Reviews"] > 0
).sum()

overall_metrics.append({
    "Metric": "Query Success Rate (%)",
    "Value": round(
        successful_queries /
        len(retrieval_df) * 100,
        2
    )
})

overall_summary = pd.DataFrame(overall_metrics)

print("\nOverall Evaluation Results")
print(overall_summary)

output_file = os.path.join(OUTPUT_DIR, "overall_summary.csv")
overall_summary.to_csv(output_file, index=False)

print(f"\n✓ Overall summary saved to {output_file}")

# Dataset Information

dataset_information = pd.DataFrame([
    {
        "Metric": "Total Reviews Indexed",
        "Value": 642692
    },
    {
        "Metric": "Retrieval Method",
        "Value": "TF-IDF"
    },
    {
        "Metric": "Similarity Measure",
        "Value": "Cosine Similarity"
    },
    {
        "Metric": "Maximum Features",
        "Value": 50000
    },
    {
        "Metric": "Top-K Retrieved",
        "Value": TOP_K
    }
])

print("\nDataset Information")
print(dataset_information)

dataset_information.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "dataset_information.csv"
    ),
    index=False
)

print("\n✓ dataset_information.csv saved")


# Visualisations

plt.figure(figsize=(12, 5))

plt.bar(
    retrieval_df["Query"],
    retrieval_df["Retrieval Time (ms)"]
)

plt.xticks(rotation=90)

plt.ylabel("Retrieval Time (ms)")

plt.title("Retrieval Time per Query")

plt.tight_layout()

plt.savefig(
    os.path.join(
        OUTPUT_DIR,
        "retrieval_latency.png"
    )
)

plt.close()

print("✓ retrieval_latency.png saved")


plt.figure(figsize=(12, 5))

plt.bar(
    retrieval_df["Query"],
    retrieval_df["Average Similarity"]
)

plt.xticks(rotation=90)

plt.ylabel("Average Similarity")

plt.title("Average Similarity per Query")

plt.tight_layout()

plt.savefig(
    os.path.join(
        OUTPUT_DIR,
        "average_similarity.png"
    )
)

plt.close()

print("✓ average_similarity.png saved")


plt.figure(figsize=(10, 5))

plt.bar(
    precision_df["Query"],
    precision_df["Precision@3"]
)

plt.xticks(rotation=90)

plt.ylabel("Precision@3")

plt.title("Precision@3 by Query")

plt.tight_layout()

plt.savefig(
    os.path.join(
        OUTPUT_DIR,
        "precision_at_3.png"
    )
)

plt.close()

print("✓ precision_at_3.png saved")