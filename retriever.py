import chromadb
from pathlib import Path

CHROMA_DIR = Path(__file__).parent / "knowledge_base" / "chroma_db"
COLLECTION_NAME = "lexi_sources"


def load_collection():
    """Load the ChromaDB collection. Raises if the index hasn't been built yet."""
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_collection(COLLECTION_NAME)


def retrieve(query: str, collection, top_k: int = 5) -> list[dict]:
    """
    Run a semantic search against the indexed sources.

    Returns a list of dicts:
        {
            "text": str,          # the chunk content
            "metadata": dict,     # source_path, url, title, file_type, category
            "distance": float     # lower = more similar
        }
    """
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({"text": doc, "metadata": meta, "distance": dist})

    return chunks


def retrieve_balanced(
    query: str, collection, categories: list[str], per_category: int = 2
) -> list[dict]:
    """
    Retrieve `per_category` chunks from each category, then sort all results
    by distance so the most relevant chunks appear first regardless of source.
    This ensures no category is crowded out by a globally dominant topic.
    """
    seen_ids: set[str] = set()
    chunks: list[dict] = []

    for category in categories:
        results = collection.query(
            query_texts=[query],
            n_results=per_category,
            where={"category": category},
            include=["documents", "metadatas", "distances"],
        )

        for doc_id, doc, meta, dist in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                chunks.append({"text": doc, "metadata": meta, "distance": dist})

    return sorted(chunks, key=lambda c: c["distance"])


def retrieve_by_category(
    query: str, collection, category: str, top_k: int = 5
) -> list[dict]:
    """Retrieve chunks filtered to a single source category."""
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where={"category": category},
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"][0]:
        return []

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({"text": doc, "metadata": meta, "distance": dist})

    return chunks
