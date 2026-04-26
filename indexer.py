"""
Run this script manually to index the Sources/ folder into ChromaDB.

Usage:
    python indexer.py

Re-run whenever Sources/ changes. Each run wipes and rebuilds the index cleanly.
"""

import json
import sys
import tempfile
from pathlib import Path

import chromadb
import pdfplumber
import requests
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
    """Split text into overlapping chunks, breaking at whitespace boundaries."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            # Walk back to the nearest whitespace to avoid splitting mid-word
            space_pos = text.rfind(" ", start, end)
            if space_pos > start:
                end = space_pos + 1
        chunks.append(text[start:end])
        start = end - overlap
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


def extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF file."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def download_pdf_text(url: str) -> str:
    """Download a PDF from a URL and extract its text."""
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = Path(tmp.name)
    try:
        return extract_pdf_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


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

            is_pdf_url = url.lower().split("?")[0].endswith(".pdf")

            if is_pdf_url:
                print(f"  Downloading PDF: {rel_path}")
                print(f"    -> {url}")
                try:
                    text = download_pdf_text(url)
                except Exception as e:
                    print(f"    ERROR: {e}")
                    skipped += 1
                    continue

                if not text.strip():
                    print(f"    SKIP (empty PDF).")
                    skipped += 1
                    continue

                title = path.stem
                file_type = "url_pdf"
            else:
                print(f"  Scraping: {rel_path}")
                print(f"    -> {url}")
                result = scrape_url(url)

                if result["error"] or not result["text"].strip():
                    print(f"    ERROR: {result['error'] or 'empty response'}")
                    skipped += 1
                    continue

                text = result["text"]
                title = result["title"]
                file_type = "url"

            chunks = chunk_text(text)
            ids = [f"{rel_path}::{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source_path": rel_path,
                    "url": url,
                    "title": title,
                    "file_type": file_type,
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

        # ---- .pdf files ------------------------------------------------
        elif suffix == ".pdf":
            print(f"  Parsing PDF: {rel_path}")
            text = extract_pdf_text(path)

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
                    "file_type": "pdf",
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
