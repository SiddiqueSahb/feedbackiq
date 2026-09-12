from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer("all-MiniLM-L6-v2")

queries = [
    "battery stopped charging",
    "flight cancelled",
    "food was cold",
    "customer support ignored me",
]

embeddings = model.encode(
    queries,
    normalize_embeddings=True
)

print("Cosine Similarity Matrix")

similarity = embeddings @ embeddings.T

print(np.round(similarity, 3))