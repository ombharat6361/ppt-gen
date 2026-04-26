# LEXI Codebase Improvement Report

## 1. Current Architecture Overview

```
Sources/ (.url, .docx, .pdf)
    |
    v
indexer.py  -->  scraper.py (for .url files)
    |              pdfplumber (for .pdf)
    |              python-docx (for .docx)
    |
    v
ChromaDB (knowledge_base/chroma_db/)
    |   collection: "lexi_sources"
    |   chunked at 600 chars / 100 overlap
    |   default embedding: ChromaDB built-in (all-MiniLM-L6-v2)
    |
    v
Two query paths:
    |
    +-- Agentic path (agent.py + tools.py)
    |       LLM decides which category tools to call
    |       Up to 3 rounds of tool calls
    |       Final streamed synthesis
    |
    +-- /pptx path (app.py -> retriever.py -> pptx_generator.py)
            Flat top-k=10 retrieval
            LLM generates JSON slide plan
            python-pptx renders slides + optional Graphviz diagrams
```

---

## 2. Bug Fixes and Code Correctness

### 2.1 `_plan_slides` JSON parsing has no fallback — HIGH PRIORITY

**Current state** (`pptx_generator.py` lines 85-103): The LLM response is parsed with `json.loads(raw)` after stripping markdown fences. If the LLM returns malformed JSON, the entire PPTX generation crashes with an unhandled `json.JSONDecodeError`.

**Problem**: Llama 3.1 8B frequently produces malformed JSON (trailing commas, unescaped quotes, truncated output when hitting `max_tokens=2048`). There is no retry, no repair, and no user-friendly error.

**Fix**: (1) Add a retry loop (2-3 attempts). (2) Use `json-repair` library as fallback. (3) Increase `max_tokens` to 4096. (4) Wrap in try/except with a user-facing error.

### 2.2 `pyproject.toml` is missing `graphviz` and `python-pptx` dependencies — HIGH PRIORITY

**Current state**: `requirements.txt` includes `python-pptx` and `graphviz`, but `pyproject.toml` does not. Anyone installing via `pip install .` or `uv sync` gets import errors.

**Fix**: Add `"python-pptx>=4.0.0"` and `"graphviz>=0.20"` to pyproject.toml dependencies.

### 2.3 `retrieve_balanced` is defined but never used — MEDIUM

**Current state** (`retriever.py` lines 42-71): Implements balanced per-category retrieval but is never called. The `/pptx` path uses plain `retrieve(query, collection, top_k=10)` which can be dominated by one category.

**Fix**: Use `retrieve_balanced` in the PPTX path for more comprehensive cross-category coverage.

### 2.4 Temporary file leak in `_render_diagram` — LOW

Graphviz's `render()` creates a new file at `{tmp.name}.png`, leaving the original empty temp file from `NamedTemporaryFile` orphaned. Wrap in try/finally to clean up.

### 2.5 History trimming inconsistency — LOW

`app.py` lines 148-154: Before the trimming threshold, the code mutates the list from `cl.user_session.get("history")` via `.append()` (relying on reference semantics), but after trimming creates a new list and explicitly sets it. Always call `cl.user_session.set("history", history)` after mutations for consistency.

### 2.6 PPTX `CLIENT` created at import time — MEDIUM

`pptx_generator.py` creates a global `OpenAI` client at import time with `load_dotenv()`. If the env var is not set at import time, the client silently gets `api_key=None` and fails with an opaque error at runtime. Move client creation to function scope or validate the key.

---

## 3. Retrieval Quality Improvements

### 3.1 Character-based chunking loses semantic boundaries — HIGH

**Current state** (`indexer.py` lines 41-54): 600-char chunks split at whitespace. No paragraph, sentence, or heading awareness.

**Problem**: Fragments ideas mid-sentence. For structured .docx files, ignores headings entirely.

**Fix**: Use `langchain-text-splitters` `RecursiveCharacterTextSplitter` which tries `\n\n`, then `\n`, then `. `, then ` ` as split points. This is a standalone pip package, not full LangChain.

### 3.2 ChromaDB uses default all-MiniLM-L6-v2 embeddings — HIGH

**Current state**: Collection created with no explicit embedding function. Defaults to all-MiniLM-L6-v2 (384 dims, ~63 MTEB score).

**Problem**: This lightweight model has limited semantic understanding for domain-specific banking/AI content. `sentence-transformers` is already a dependency but not leveraged for a better model.

**Fix**: Use `BAAI/bge-base-en-v1.5` (768 dims, ~68 MTEB) via `chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction`. Must specify the same function at both index time (indexer.py) and query time (retriever.py).

### 3.3 No relevance threshold — LOW-quality chunks included — MEDIUM

**Current state**: `retrieve_by_category` returns top-k regardless of distance. When a category has few documents or the query is off-topic, the agent receives noise.

**Fix**: Add a configurable `max_distance` threshold to filter poor matches before returning results.

### 3.4 No hybrid search (keyword + semantic) — MEDIUM

**Problem**: Semantic search can miss exact-match acronyms like "ILL" (Internet Leased Line) or "GVPN" if the embedding does not capture them well.

**Fix**: Quick win — use ChromaDB's `where_document={"$contains": keyword}` filter. Better — add BM25 scoring via `rank-bm25` alongside vector search with reciprocal rank fusion.

### 3.5 No reranking of retrieved results — MEDIUM

**Problem**: Bi-encoder embeddings are less accurate at fine-grained relevance than cross-encoders.

**Fix**: Over-retrieve (top-20), rerank with `cross-encoder/ms-marco-MiniLM-L-6-v2` from `sentence-transformers` (already a dependency), return top-5. Adds ~50-200ms per query.

### 3.6 Scraper is basic and fragile — MEDIUM

**Current state** (`scraper.py`): `requests` + `BeautifulSoup` with simple heuristic content detection.

**Fix**: Replace internals with `trafilatura` which handles boilerplate removal and works on a much wider range of page structures. Same input/output contract.

### 3.7 DOCX extraction ignores tables and structure — MEDIUM

**Current state** (`indexer.py` line 66-68): Only `"\n".join(p.text for p in doc.paragraphs)`. Tables, heading hierarchy, and structured data are invisible.

**Fix**: Extract tables by iterating `doc.tables` rows. Preserve heading markers. Or use `unstructured`/`docling` for robust multi-format parsing.

---

## 4. Agent / LLM Improvements

### 4.1 Model too small for complex synthesis — HIGH

**Current state** (`config.py`): `llama-3.1-8b-instant` for ALL tasks.

**Problem**: 8B models struggle with multi-source synthesis, citation accuracy, and structured JSON generation.

**Fix**: Use tiered models. `llama-3.3-70b-versatile` (available on Groq) for synthesis and PPTX planning. Keep 8B for fast tasks like category descriptions.

### 4.2 Agent max_tokens=1024 for tool rounds is too low — MEDIUM

**Current state** (`agent.py` line 105): Can truncate when the model wants multiple tool calls in one round.

**Fix**: Increase to 2048 for tool-calling rounds.

### 4.3 No structured output for slide plans — HIGH

**Problem**: Free-form text-to-JSON is unreliable.

**Fix**: Use Groq's JSON mode (`response_format={"type": "json_object"}`) or `instructor` library with Pydantic models for validated, typed output. `instructor` patches the OpenAI client to return Pydantic objects with automatic retry on validation failure.

### 4.4 Dead code in app.py — LOW

`SYSTEM_PROMPT` (lines 19-30) and `build_context` (lines 33-45) are never used. Remove them.

### 4.5 PPTX path skips the agent — MEDIUM

The `/pptx` command uses flat `retrieve(query, collection, top_k=10)` instead of the agentic multi-category reasoning path. At minimum use `retrieve_balanced`; ideally route through the agent for initial research.

---

## 5. PowerPoint Generation Improvements

### 5.1 No slide layout variety — MEDIUM

Every content slide uses the identical layout. Implement 3-4 variants: full-width narrative, two-column, key stat highlight, quote/callout. Add a `layout` field to the slide plan JSON.

### 5.2 Long body text overflows the slide — MEDIUM

Body text is in a fixed 3.5-inch textbox. LLM may generate more text than fits, which becomes invisible. Fix: use `MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE`, or dynamically reduce font size based on length, or enforce hard character limits in the prompt.

### 5.3 No font fallback for cross-platform — LOW

"Segoe UI" in diagrams is Windows-only. Use platform detection or a cross-platform font.

### 5.4 No executive summary or agenda slide — LOW

Professional decks need an overview slide. Add this to the prompt and build a dedicated layout.

### 5.5 Source URLs not hyperlinked — LOW

Use python-pptx's hyperlink support to make source URLs clickable on the sources slide.

---

## 6. Architecture Improvements

### 6.1 No error handling for API failures in agent loop — HIGH

**Current state** (`agent.py` lines 101-108): No try/except around `client.chat.completions.create`. Groq's free tier has aggressive rate limits (30 req/min). Multi-tool-call conversations easily hit these.

**Fix**: Add retry with exponential backoff. Use `tenacity` library for declarative retry.

### 6.2 No test suite — HIGH

Zero tests. Any refactoring (chunking strategy, retrieval, prompts) has no safety net. Add `pytest` tests for: chunking edge cases, tool discovery, scraper HTML parsing, retriever integration with in-memory ChromaDB, PPTX file creation.

### 6.3 Tight coupling to ChromaDB and Groq — MEDIUM

Multiple modules hardcode paths and base URLs. Each module creates its own client. Create a central `clients.py` factory with lazy initialization and configurable provider URLs.

### 6.4 Inconsistent logging — MEDIUM

`agent.py` uses file logging. Everything else uses `print()`. Centralize with Python `logging` module. Replace all `print()` with logger calls.

### 6.5 No input validation — MEDIUM

No query length limit. No protection against prompt injection or excessive requests. Add `MAX_QUERY_LENGTH` check and consider rate limiting.

### 6.6 No environment variable validation at startup — HIGH

`GROQ_API_KEY` via `os.environ.get()` returns `None` if unset. App starts but fails cryptically on first LLM call. Validate at startup in `config.py` with a clear error message and `sys.exit(1)`.

### 6.7 Graphviz binary not validated at startup — MEDIUM

The `graphviz` Python package requires the `dot` binary. If missing, diagram rendering crashes. Add `shutil.which("dot")` check and gracefully degrade.

---

## 7. Security Considerations

### 7.1 `allow_origins = ["*"]` in Chainlit config — HIGH for deployment

`.chainlit/config.toml` line 22: CORS is wide open. Lock down for any deployment.

### 7.2 No rate limiting on chat endpoint — MEDIUM

Any user can send unlimited messages, exhausting the Groq API quota. Add throttling or authentication.

---

## 8. Open-Source Tool Recommendations

| Tool | Purpose | Replaces/Enhances | Priority |
|---|---|---|---|
| `langchain-text-splitters` | Sentence/paragraph-aware chunking | Custom `chunk_text` in indexer.py | High |
| `instructor` | Structured LLM output with Pydantic validation | Manual JSON parsing in pptx_generator.py | High |
| `json-repair` | Fix common LLM JSON errors | Fallback for json.loads failures | High |
| `tenacity` | Retry logic with exponential backoff | Manual retry for API calls | High |
| `pytest` | Testing framework | No tests currently | High |
| `trafilatura` | Robust web content extraction | scraper.py BeautifulSoup logic | Medium |
| `rank-bm25` | Keyword search for hybrid retrieval | Enhances semantic-only retrieval | Medium |
| `sentence-transformers` CrossEncoder | Reranking retrieved results | Improves retrieval precision (already installed) | Medium |
| `unstructured` or `docling` | Multi-format document parsing | Custom extract functions | Medium |

---

## 9. Generalization Roadmap

### Phase 1: Foundation (1-2 days)
1. Fix pyproject.toml dependencies
2. Add env var validation at startup
3. Add JSON repair for slide plans
4. Remove dead code in app.py
5. Use `retrieve_balanced` for PPTX

### Phase 2: Retrieval Quality (3-5 days)
6. Upgrade embedding model (requires re-indexing)
7. Implement sentence-aware chunking (requires re-indexing)
8. Add distance threshold filtering
9. Improve DOCX extraction with table support
10. Replace scraper with trafilatura

### Phase 3: LLM Quality (2-3 days)
11. Use larger model for synthesis/PPTX
12. Add structured output for slide plans (instructor)
13. Add API retry logic (tenacity)
14. Increase tool-calling max_tokens

### Phase 4: Production Hardening (3-5 days)
15. Add test suite (pytest)
16. Centralize logging
17. Add input validation
18. Lock down CORS
19. Add Dockerfile

### Phase 5: Advanced Features (5-10 days)
20. Cross-encoder reranking
21. Hybrid search (BM25 + semantic)
22. Slide layout variety
23. LLM response caching
24. Client factory for dependency injection

---

## 10. Hardcoded Values to Parameterize

| Value | Location | Suggestion |
|---|---|---|
| `CHUNK_SIZE=600, OVERLAP=100` | indexer.py:33-34 | config.py env vars |
| `TOP_K_PER_TOOL=5` | tools.py:9 | config.py |
| `MAX_TOOL_ROUNDS=3` | agent.py:12 | config.py |
| max_tokens (1024, 2048, 4096) | agent.py, pptx_generator.py | config.py |
| BRAND colors | pptx_generator.py:76-82 | theme config file |
| Groq base URL | app.py:16, pptx_generator.py:20 | config.py `LLM_BASE_URL` |
| Sources dir, ChromaDB dir | indexer.py:23-24, retriever.py:4 | config.py |
| Slide count "4-8" | pptx_generator.py:61 | configurable per request |
| Output directory | pptx_generator.py:24 | config.py |

---

## 11. Edge Cases to Handle

1. **Empty Sources/ directory** — currently crashes, should show helpful message
2. **Very large documents (>100 pages)** — hundreds of chunks, consider summarization
3. **Non-English content** — use multilingual embedding model
4. **Duplicate content across sources** — hash-based dedup at indexing
5. **Category with zero indexed documents** — agent may still search it
6. **Concurrent PPTX generation** — filename collisions, add UUID to names
7. **Stale index** — file hash comparison to detect when re-indexing needed
