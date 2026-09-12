import sys
import os

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


from nlp.embedding_service import semantic_search

queries = [
    "battery stopped charging after one week",
    "customer support ignored my emails",
    "flight was cancelled",
    "food was cold and tasteless",
    "package arrived late and damaged",
]

for query in queries:

    print("=" * 100)
    print("QUERY:")
    print(query)

    results = semantic_search(query, top_k=5)

    print(f"\nRetrieved {len(results)} reviews\n")

    for i, review in enumerate(results, start=1):
        print(f"Result {i}")
        print(f"Similarity : {review['similarity_score']}")
        print(f"Platform   : {review['platform']}")
        print(f"Rating     : {review['rating']}")
        print(f"Sentiment  : {review['sentiment_label']}")
        print(f"Review     : {review['text'][:150]}")
        print("-" * 80)