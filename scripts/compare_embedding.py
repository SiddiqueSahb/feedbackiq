"""
Compare TF-IDF and Semantic Search Retrieval Performance
"""

import os
import pandas as pd
import matplotlib.pyplot as plt

# Paths

SEMANTIC_FILE = "data/results/evaluation_semantic_search/overall_summary.csv"
TFIDF_FILE = "data/results/evaluation/tfidf/overall_summary.csv"

OUTPUT_DIR = "data/results/comparison_embedding_models"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Load summaries

semantic_summary = pd.read_csv(SEMANTIC_FILE)
tfidf_summary = pd.read_csv(TFIDF_FILE)


# Create comparison table

comparison = semantic_summary.rename(
    columns={"Value": "Semantic Search"}
)

comparison["TF-IDF"] = tfidf_summary["Value"]

print("\nComparison Results")
print(comparison)

comparison.to_csv(
    os.path.join(
        OUTPUT_DIR,
        "retrieval_comparison.csv"
    ),
    index=False
)

print("\n✓ retrieval_comparison.csv saved")



# Average Retrieval Time

retrieval_time = comparison.loc[
    comparison["Metric"] == "Average Retrieval Time (ms)"
]

semantic_time = retrieval_time["Semantic Search"].values[0]
tfidf_time = retrieval_time["TF-IDF"].values[0]

plt.figure(figsize=(6, 5))

bars = plt.bar(
    ["Semantic Search", "TF-IDF"],
    [semantic_time, tfidf_time]
)

plt.title("Average Retrieval Time")
plt.ylabel("Time (ms)")

for bar in bars:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.2f}",
        ha="center",
        va="bottom"
    )

plt.tight_layout()

plt.savefig(
    os.path.join(
        OUTPUT_DIR,
        "average_retrieval_time.png"
    )
)

plt.close()


# Average Similarity Score

similarity = comparison.loc[
    comparison["Metric"] == "Average Similarity Score"
]

semantic_similarity = similarity["Semantic Search"].values[0]
tfidf_similarity = similarity["TF-IDF"].values[0]

plt.figure(figsize=(6, 5))

bars = plt.bar(
    ["Semantic Search", "TF-IDF"],
    [semantic_similarity, tfidf_similarity]
)

plt.title("Average Similarity Score")
plt.ylabel("Similarity Score")

for bar in bars:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.3f}",
        ha="center",
        va="bottom"
    )

plt.tight_layout()

plt.savefig(
    os.path.join(
        OUTPUT_DIR,
        "average_similarity_score.png"
    )
)

plt.close()


# Average Precision@3

precision = comparison.loc[
    comparison["Metric"] == "Average Precision@3"
]

semantic_precision = precision["Semantic Search"].values[0]
tfidf_precision = precision["TF-IDF"].values[0]

plt.figure(figsize=(6, 5))

bars = plt.bar(
    ["Semantic Search", "TF-IDF"],
    [semantic_precision, tfidf_precision]
)

plt.title("Average Precision@3")
plt.ylabel("Precision@3")

for bar in bars:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.2f}",
        ha="center",
        va="bottom"
    )

plt.tight_layout()

plt.savefig(
    os.path.join(
        OUTPUT_DIR,
        "average_precision_at_3.png"
    )
)

plt.close()

print("\n✓ Comparison graphs saved successfully")