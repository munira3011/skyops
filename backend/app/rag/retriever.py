from functools import lru_cache

import chromadb

from app.rag.ingest import CHROMA_DIR, COLLECTION_NAME, PolicyChunk

# Empirically: on-topic queries score ~0.6-1.5, off-topic/gibberish score ~1.8+.
_MAX_DISTANCE = 1.6


@lru_cache(maxsize=1)
def _collection():
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_collection(COLLECTION_NAME)


def retrieve(query: str, k: int = 1) -> list[PolicyChunk]:
    """Runs a semantic query against the Chroma `policies` collection; returns the top-k matching chunks below the relevance-distance cutoff."""
    result = _collection().query(query_texts=[query], n_results=k, include=["documents", "metadatas", "distances"])
    documents, metadatas, distances = result["documents"][0], result["metadatas"][0], result["distances"][0]
    return [
        PolicyChunk(source=metadata["source"], heading=metadata["heading"], text=text)
        for text, metadata, distance in zip(documents, metadatas, distances)
        if distance <= _MAX_DISTANCE
    ]
