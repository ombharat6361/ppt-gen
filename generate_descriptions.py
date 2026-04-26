"""
Generate sources.json descriptions from the indexed ChromaDB content.

Usage:
    python generate_descriptions.py

Reads all indexed chunks, groups by category, sends a sample to the LLM
to produce a concise description, and writes the result to sources.json.
"""

import json
import os
from pathlib import Path
from collections import defaultdict

from openai import OpenAI
from dotenv import load_dotenv

from config import MODEL_NAME
from retriever import load_collection

load_dotenv()

SOURCES_DIR = Path(__file__).parent / "Sources"
SOURCES_CONFIG = Path(__file__).parent / "sources.json"

CLIENT = OpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

SUMMARIZE_PROMPT = """\
You are given text chunks from a document category in a knowledge base. \
Write a concise 1-3 sentence description of what this category contains. \
Focus on: the specific topics covered, organizations/people mentioned, \
and the types of information available (strategies, case studies, metrics, etc.).

Be specific — name companies, products, and key themes. \
Do not use generic filler like "various documents" or "multiple topics". \
This description will be used by an AI agent to decide whether to search this category."""

MAX_SAMPLE_CHARS = 3000


def get_category_chunks() -> dict[str, list[dict]]:
    """Pull all chunks from ChromaDB grouped by category."""
    collection = load_collection()
    results = collection.get(include=["documents", "metadatas"])

    cats: dict[str, list[dict]] = defaultdict(list)
    for doc, meta in zip(results["documents"], results["metadatas"]):
        cats[meta["category"]].append({"text": doc, "title": meta.get("title", "")})

    return dict(cats)


def build_sample(chunks: list[dict]) -> str:
    """Build a representative text sample from chunks, staying under the char limit."""
    titles = sorted(set(c["title"] for c in chunks if c["title"]))
    header = "Document titles in this category:\n" + "\n".join(f"- {t}" for t in titles)

    sample_texts = []
    char_budget = MAX_SAMPLE_CHARS - len(header) - 100
    for chunk in chunks:
        if char_budget <= 0:
            break
        snippet = chunk["text"][:500]
        sample_texts.append(snippet)
        char_budget -= len(snippet)

    return header + "\n\nSample excerpts:\n\n" + "\n---\n".join(sample_texts)


def summarize_category(category: str, sample: str) -> str:
    """Ask the LLM to produce a description for one category."""
    response = CLIENT.chat.completions.create(
        model=MODEL_NAME,
        max_tokens=256,
        temperature=0.3,
        messages=[
            {"role": "system", "content": SUMMARIZE_PROMPT},
            {
                "role": "user",
                "content": f"Category name: {category}\n\n{sample}",
            },
        ],
    )
    return response.choices[0].message.content.strip()


def get_unindexed_description(category: str) -> str:
    """For categories with no indexed chunks, describe from file listing."""
    cat_dir = SOURCES_DIR / category
    if not cat_dir.exists():
        return f"Documents in the {category} directory (not yet indexed)."

    files = [f.name for f in sorted(cat_dir.iterdir()) if f.is_file()]
    if not files:
        return f"Empty category — no documents in {category} yet."

    file_list = ", ".join(files[:10])
    return f"Contains files not yet indexed: {file_list}. Re-run indexer to include."


def main():
    print("Loading indexed chunks from ChromaDB...")
    category_chunks = get_category_chunks()

    # Also discover directories that may have no indexed content
    all_categories = set(category_chunks.keys())
    if SOURCES_DIR.exists():
        for child in sorted(SOURCES_DIR.iterdir()):
            if child.is_dir():
                all_categories.add(child.name)

    descriptions: dict[str, str] = {}

    for category in sorted(all_categories):
        chunks = category_chunks.get(category, [])

        if not chunks:
            print(f"\n{category}: no indexed chunks — using file listing")
            descriptions[category] = get_unindexed_description(category)
            continue

        print(f"\n{category}: {len(chunks)} chunks — summarizing with LLM...")
        sample = build_sample(chunks)
        description = summarize_category(category, sample)
        descriptions[category] = description
        print(f"  -> {description[:120]}...")

    SOURCES_CONFIG.write_text(
        json.dumps(descriptions, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {len(descriptions)} category descriptions to {SOURCES_CONFIG}")


if __name__ == "__main__":
    main()
