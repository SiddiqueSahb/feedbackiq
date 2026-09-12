"""
Test script for the RAG pipeline.

Run:
    python tests/test_rag.py
"""


from feedbackiq.rag.pipeline import ask


def print_response(response: dict) -> None:
    """Display the generated answer and retrieved sources."""

    print("\n" + "=" * 80)
    print("Generated Answer")
    print("=" * 80)
    print(response.get("answer", "No answer returned."))

    print(f"\nRetrieved Documents : {response.get('retrieval_count', 0)}")
    print(f"Use Case            : {response.get('use_case', 'N/A')}")

    print("\n" + "=" * 80)
    print("Retrieved Sources")
    print("=" * 80)

    sources = response.get("sources", [])

    if not sources:
        print("No sources retrieved.")
        return

    for i, source in enumerate(sources, start=1):

        print(f"\nSource {i}")
        print("-" * 60)
        print(f"Platform   : {source.get('platform', 'Unknown')}")
        print(f"Sentiment  : {source.get('sentiment_label', 'Unknown')}")
        print(f"Rating     : {source.get('rating', 'N/A')}")
        print(f"Review ID  : {source.get('review_id', 'N/A')}")
        print("Review:")
        print(source.get("text", ""))
        print("-" * 60)


def main():
    """Interactive RAG testing."""

    print("=" * 80)
    print("FeedbackIQ RAG Pipeline Test")
    print("=" * 80)

    chat_history = []

    while True:

        question = input("\nEnter your question (type 'exit' to quit): ").strip()

        if question.lower() == "exit":
            print("\nExiting RAG test...")
            break

        if not question:
            print("Please enter a valid question.")
            continue

        try:
            response = ask(
                question=question,
                chat_history=chat_history,
            )

            print_response(response)

            # Store conversation for follow-up questions
            chat_history.extend([
                {
                    "role": "user",
                    "content": question,
                },
                {
                    "role": "assistant",
                    "content": response.get("answer", ""),
                },
            ])

        except Exception as e:
            print(f"\nError while running RAG pipeline:\n{e}")


if __name__ == "__main__":
    main()