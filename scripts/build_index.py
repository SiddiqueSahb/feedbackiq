"""
Generates sentence embeddings and builds FAISS + LangChain indexes.
Run once after preprocessing. Re-run only when dataset changes.

Usage:
    python scripts/build_index.py
"""
import sys, os
import os, sys, json
import numpy as np
import pandas as pd

from feedbackiq.core.config import settings


def main():
    # Load data
    if not os.path.exists(settings.DATA_PATH):
        print(f"  Data not found at {settings.DATA_PATH}")
        print("    Run: python scripts/preprocess.py first")
        sys.exit(1)

    print(f"  Loading data from {settings.DATA_PATH}...")
    df = pd.read_parquet(settings.DATA_PATH)
    texts    = df["cleaned_text"].tolist()
    meta_cols = ["platform", "rating", "sentiment_label", "review_id"]
    if "product_category" in df.columns:
        meta_cols.append("product_category")
    metadata = df[meta_cols].to_dict("records")
    print(f"    Loaded {len(texts):,} reviews")

    # Generate embeddings
    print(f"\n  Generating embeddings with {settings.EMBEDDING_MODEL}...")
    print("    This may take a few minutes...")
    from sentence_transformers import SentenceTransformer
    model      = SentenceTransformer(settings.EMBEDDING_MODEL)
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,   # L2 normalise for cosine similarity
    )
    print(f"    Embedding shape: {embeddings.shape}")

    os.makedirs("data/embeddings", exist_ok=True)

    # Save embeddings
    np.save(settings.EMBED_PATH, embeddings)
    print(f"\n  Embeddings saved → {settings.EMBED_PATH}")

     

    # Build LangChain FAISS index
    print("\n Building LangChain FAISS index...")
    try:
        from langchain_community.vectorstores import FAISS as LangFAISS
        from langchain_community.embeddings  import HuggingFaceEmbeddings

        lc_embeddings = HuggingFaceEmbeddings(
            model_name=settings.EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

        # Build with texts + metadata for filtered search
        lc_index = LangFAISS.from_texts(
            texts=texts,
            embedding=lc_embeddings,
            metadatas=metadata,
        )
        os.makedirs(settings.LANGCHAIN_INDEX_PATH, exist_ok=True)
        lc_index.save_local(settings.LANGCHAIN_INDEX_PATH)
        print(f"  LangChain index  → {settings.LANGCHAIN_INDEX_PATH}/")

    except Exception as e:
        print(f"  LangChain index skipped ({e}). RAG will fall back to direct FAISS.")

    print(f"\n  Index build complete!")
    print("\nNext step → start the backend: uvicorn feedbackiq.api.main:app --reload")
    print(f"Then start frontend:            streamlit run frontend/app.py")


if __name__ == "__main__":
    main()
