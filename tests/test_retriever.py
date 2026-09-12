
from __future__ import annotations
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Optional, Any
from logger import get_logger
from functools import lru_cache
from config import settings
from rag.prompts import RAG_PROMPT, CONDENSE_QUESTION_PROMPT, is_complaint_question
from langchain_classic.memory import ConversationBufferMemory


from rag.pipeline import _get_vectorstore
import faiss
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

from config import settings

index = faiss.read_index(settings.INDEX_PATH)
print("FAISS index loaded")

df = pd.read_parquet(settings.DATA_PATH)
print(f"Loaded {len(df)} reviews")

model = SentenceTransformer(settings.EMBEDDING_MODEL)

query = model.encode(
    ["What are customers complaining about?"],
    normalize_embeddings=True,
).astype(np.float32)

print("Searching...")

scores, indices = index.search(query, k=5)

print("\nTop 5 Results\n")

for rank, (score, idx) in enumerate(zip(scores[0], indices[0]), start=1):
    review = df.iloc[idx]

    print(f"Result {rank}")
    print(f"Similarity : {score:.4f}")
    print(f"Platform   : {review['platform']}")
    print(f"Sentiment  : {review['sentiment_label']}")
    print(f"Rating     : {review['rating']}")
    print(f"Review ID  : {review['review_id']}")
    print(f"Review     : {review['cleaned_text'][:300]}")
    print("-" * 80)