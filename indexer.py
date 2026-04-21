"""
Run this script manually to index the Sources/ folder into ChromaDB.

Usage:
    python indexer.py

Re-run whenever Sources/ changes. Each run wipes and rebuilds the index cleanly.
"""

import json
import sys
from pathlib import Path

import chromadb
from docx import Document

from scraper import scrape_url

SOURCES_DIR = Path(__file__).parent / "Sources"
CHROMA_DIR = Path(__file__).parent / "knowledge_base" / "chroma_db"
COLLECTION_NAME = "lexi_sources"
SOURCES_CONFIG = Path(__file__).parent / "sources.json"


def load_category_descriptions() -> dict[str, str]:
    if SOURCES_CONFIG.exists():
        return json.loads(SOURCES_CONFIG.read_text(encoding="utf-8"))
    return {}

CHUNK_SIZE = 600
CHUNK_OVERLAP = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return [c for c in chunks if c.strip()]


def parse_url_file(path: Path) -> str:
    """Extract the URL= line from a Windows .url shortcut file."""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.upper().startswith("URL="):
            return line[4:].strip()
    return ""


def extract_docx_text(path: Path) -> str:
    """Extract plain text from a .docx file."""
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

def index_sources():
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Wipe the existing collection so re-indexing is clean
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}'.")
    except Exception:
        pass

    collection = client.create_collection(COLLECTION_NAME)
    print(f"Created collection '{COLLECTION_NAME}'.\n")

    category_descriptions = load_category_descriptions()

    files = sorted(p for p in SOURCES_DIR.rglob("*") if p.is_file())
    indexed = 0
    skipped = 0

    for path in files:
        suffix = path.suffix.lower()
        category = path.parent.name
        category_description = category_descriptions.get(category, "")
        rel_path = str(path.relative_to(SOURCES_DIR.parent))  # relative to LEXI/

        # ---- .url files ------------------------------------------------
        if suffix == ".url":
            url = parse_url_file(path)
            if not url:
                print(f"  SKIP (no URL found): {rel_path}")
                skipped += 1
                continue

            print(f"  Scraping: {rel_path}")
            print(f"    -> {url}")
            result = scrape_url(url)

            if result["error"] or not result["text"].strip():
                print(f"    ERROR: {result['error'] or 'empty response'}")
                skipped += 1
                continue

            chunks = chunk_text(result["text"])
            ids = [f"{rel_path}::{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source_path": rel_path,
                    "url": url,
                    "title": result["title"],
                    "file_type": "url",
                    "category": category,
                    "category_description": category_description,
                }
                for _ in chunks
            ]
            collection.add(ids=ids, documents=chunks, metadatas=metadatas)
            print(f"    Indexed {len(chunks)} chunk(s).")
            indexed += 1

        # ---- .docx files -----------------------------------------------
        elif suffix == ".docx":
            print(f"  Parsing DOCX: {rel_path}")
            text = extract_docx_text(path)

            if not text.strip():
                print(f"    SKIP (empty document).")
                skipped += 1
                continue

            chunks = chunk_text(text)
            ids = [f"{rel_path}::{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source_path": rel_path,
                    "url": "",
                    "title": path.stem,
                    "file_type": "docx",
                    "category": category,
                    "category_description": category_description,
                }
                for _ in chunks
            ]
            collection.add(ids=ids, documents=chunks, metadatas=metadatas)
            print(f"    Indexed {len(chunks)} chunk(s).")
            indexed += 1

        else:
            print(f"  SKIP (unsupported type '{suffix}'): {rel_path}")
            skipped += 1

    print(f"\nIndexing complete: {indexed} document(s) indexed, {skipped} skipped.")


if __name__ == "__main__":
    if not SOURCES_DIR.exists():
        print(f"ERROR: Sources directory not found: {SOURCES_DIR}", file=sys.stderr)
        sys.exit(1)

    index_sources()
