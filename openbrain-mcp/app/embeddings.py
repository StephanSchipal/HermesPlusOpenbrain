# app/embeddings.py
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from app.config import MODEL_NAME

@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)

def embed_passage(text: str) -> list[float]:
    # e5 family requires the "passage:" prefix for stored documents
    vec = get_model().encode(f"passage: {text}", normalize_embeddings=True)
    return vec.tolist()

def embed_query(text: str) -> list[float]:
    # ...and "query:" for search queries
    vec = get_model().encode(f"query: {text}", normalize_embeddings=True)
    return vec.tolist()
