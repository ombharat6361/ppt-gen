import json
from pathlib import Path

from retriever import retrieve_by_category

SOURCES_DIR = Path(__file__).parent / "Sources"
SOURCES_CONFIG = Path(__file__).parent / "sources.json"

TOP_K_PER_TOOL = 5


def discover_categories() -> dict[str, str]:
    """Return {category_name: description} by merging sources.json with Sources/ subdirectories."""
    descriptions: dict[str, str] = {}
    if SOURCES_CONFIG.exists():
        descriptions = json.loads(SOURCES_CONFIG.read_text(encoding="utf-8"))

    if SOURCES_DIR.exists():
        for child in sorted(SOURCES_DIR.iterdir()):
            if child.is_dir() and child.name not in descriptions:
                descriptions[child.name] = f"Documents in the {child.name} directory."

    return descriptions


def _category_to_function_name(category: str) -> str:
    return "search_" + category.lower().replace(" ", "_")


def build_tools(
    categories: dict[str, str],
) -> tuple[list[dict], dict[str, str]]:
    """Build OpenAI tool definitions and a function-name-to-category lookup."""
    tools = []
    name_map: dict[str, str] = {}

    for category, description in categories.items():
        func_name = _category_to_function_name(category)
        name_map[func_name] = category

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": func_name,
                    "description": (
                        f"Search the '{category}' knowledge base. {description} "
                        f"Call this tool when the user's question relates to {category.lower()}."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query to find relevant document chunks.",
                            }
                        },
                        "required": ["query"],
                    },
                },
            }
        )

    return tools, name_map


def execute_tool_call(
    function_name: str,
    arguments_json: str,
    collection,
    name_map: dict[str, str],
) -> str:
    """Execute a tool call and return a JSON string for the messages list."""
    category = name_map.get(function_name)
    if category is None:
        return json.dumps({"error": f"Unknown tool: {function_name}"})

    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError:
        return json.dumps({"error": f"Malformed arguments: {arguments_json}"})

    query = args.get("query", "")
    chunks = retrieve_by_category(query, collection, category, top_k=TOP_K_PER_TOOL)

    if not chunks:
        return json.dumps(
            {
                "category": category,
                "results": [],
                "message": f"No relevant chunks found in '{category}' for this query.",
            }
        )

    results = []
    for chunk in chunks:
        meta = chunk["metadata"]
        results.append(
            {
                "text": chunk["text"],
                "title": meta.get("title", ""),
                "url": meta.get("url", ""),
                "source_path": meta.get("source_path", ""),
                "distance": chunk["distance"],
            }
        )

    return json.dumps({"category": category, "results": results})
