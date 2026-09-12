import faiss
import numpy as np

index = faiss.read_index("data/embeddings/reviews.faiss")

print("Index loaded")
print("Dimension:", index.d)
print("Vectors:", index.ntotal)

embeddings = np.load("data/embeddings/review_embeddings.npy")

print("Embeddings:", embeddings.shape)

query = embeddings[0:1].astype(np.float32)

print("Searching...")

scores, ids = index.search(query, 5)

print(scores)
print(ids)
print(query.dtype)
print(query.flags)