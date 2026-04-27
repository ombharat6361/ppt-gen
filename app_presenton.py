import os
from pathlib import Path

import chainlit as cl
from openai import AsyncOpenAI
from dotenv import load_dotenv

from config import MAX_HISTORY_TURNS
from retriever import load_collection, retrieve
from presenton_client import OUTPUT_DIR, generate_presentation
from tools import discover_categories, build_tools
from agent import run_agent

load_dotenv()

CLIENT = AsyncOpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

SYSTEM_PROMPT = """You are LEXI, a research assistant. Your job is to answer questions \
using ONLY the source excerpts provided to you in each message.

Rules you must follow:
- Cite every factual claim using [Source N] notation, where N matches the source number given.
- If the sources do not contain enough information to answer the question, say so explicitly. \
Do not fill gaps with general knowledge.
- If two sources contradict each other, surface the conflict rather than silently picking one.
- Use hedged language when sources are ambiguous: "according to [Source N]", "as of [date]".
- Do not add filler, background context, or conclusions not supported by the sources.
- End your response with a 'Sources used' list showing the title and URL (if any) for each \
[Source N] you cited."""

OUTLINE_PROMPT = """\
You are a presentation storyteller. Given source excerpts and a user topic, produce a \
Markdown outline for a slide deck that reads like a coherent narrative.

Format rules:
- Start with a top-level heading for the deck title.
- Use second-level headings (##) for each slide title (4-8 content slides).
- Under each slide heading write 2-4 sentences of narrative prose. Cite sources as [Source N].
- After the prose you may add an optional "**Key point:** …" line for a punchy takeaway.
- End with a ## Sources slide that lists every [Source N] cited as a numbered list with title \
and URL (if available).
- Do NOT invent facts — use ONLY the provided sources.
- If sources are insufficient, include a slide noting the gaps."""


def build_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a numbered source block for the prompt."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk["metadata"]
        category_desc = meta.get("category_description", "")
        header = f"[Source {i}] {meta['title']} | Category: {meta['category']}"
        if category_desc:
            header += f" ({category_desc})"
        if meta.get("url"):
            header += f"\nURL: {meta['url']}"
        parts.append(f"{header}\n\n{chunk['text']}")
    return "\n\n---\n\n".join(parts)


async def _build_outline(topic: str, context: str) -> str:
    """Ask the LLM for a Markdown outline and return the raw string."""
    resp = await CLIENT.chat.completions.create(
        model=os.environ.get("LEXI_MODEL", "llama-3.1-8b-instant"),
        max_tokens=2048,
        temperature=0.3,
        messages=[
            {"role": "system", "content": OUTLINE_PROMPT},
            {"role": "user", "content": f"Sources:\n\n{context}\n\n---\n\nTopic: {topic}"},
        ],
    )
    return resp.choices[0].message.content.strip()


@cl.on_chat_start
async def start():
    try:
        collection = load_collection()
        categories = discover_categories()
        tools, name_map = build_tools(categories)

        cl.user_session.set("collection", collection)
        cl.user_session.set("tools", tools)
        cl.user_session.set("name_map", name_map)
        cl.user_session.set("history", [])

        category_list = ", ".join(f"**{c}**" for c in categories)
        await cl.Message(
            content=(
                "Index loaded successfully. Ask me anything about the documents in **Sources/**.\n\n"
                f"> I have access to these source categories: {category_list}\n\n"
                "> Every answer is grounded in your indexed sources only — "
                "I will tell you explicitly if something isn't covered.\n\n"
                "**Tip:** Start your message with `/pptx` to generate a PowerPoint presentation "
                "on any topic covered by the sources (powered by Presenton)."
            )
        ).send()
    except Exception as e:
        await cl.Message(
            content=(
                "**Could not load the index.**\n\n"
                "Please run `python indexer.py` first to index your Sources folder, "
                f"then restart the app.\n\n`{e}`"
            )
        ).send()


@cl.on_message
async def main(message: cl.Message):
    collection = cl.user_session.get("collection")

    if not collection:
        await cl.Message(
            content="Index not loaded. Please restart after running `python indexer.py`."
        ).send()
        return

    history: list[dict] = cl.user_session.get("history")

    # --- /pptx command (non-agentic, uses flat retrieval) ---
    is_pptx = message.content.strip().lower().startswith("/pptx")
    if is_pptx:
        query = message.content.strip()[5:].strip()
        if not query:
            await cl.Message(
                content="Please provide a topic after `/pptx`. Example: `/pptx AI in banking`"
            ).send()
            return

        chunks = retrieve(query, collection, top_k=10)
        if not chunks:
            await cl.Message(
                content="No relevant sources found for your query. Try rephrasing or check that the index is up to date."
            ).send()
            return

        context = build_context(chunks)
        status_msg = cl.Message(content="Generating presentation via Presenton — this may take a moment...")
        await status_msg.send()

        try:
            outline = await _build_outline(query, context)

            safe_name = "".join(c if c.isalnum() or c in " _-" else "" for c in query)[:60].strip()
            out_path = OUTPUT_DIR / f"{safe_name}.pptx"

            await generate_presentation(
                content=outline,
                n_slides=6,
                theme="default",
                output_path=out_path,
            )

            elements = [cl.File(name=out_path.name, path=str(out_path), display="inline")]
            await cl.Message(
                content=f"Here's your presentation on **{query}**.",
                elements=elements,
            ).send()
        except Exception as e:
            await cl.Message(content=f"Failed to generate presentation: `{e}`").send()
        return

    # --- Agentic query path ---
    query = message.content.strip()
    tools = cl.user_session.get("tools")
    name_map = cl.user_session.get("name_map")

    response_msg = cl.Message(content="")
    await response_msg.send()

    async def stream_token(token: str):
        await response_msg.stream_token(token)

    full_response = await run_agent(
        client=CLIENT,
        user_query=query,
        collection=collection,
        history=history,
        tools=tools,
        name_map=name_map,
        stream_callback=stream_token,
    )

    await response_msg.update()

    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": full_response})
    max_messages = MAX_HISTORY_TURNS * 2
    if len(history) > max_messages:
        history = history[-max_messages:]
    cl.user_session.set("history", history)
