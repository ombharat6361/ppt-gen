import os
import chainlit as cl
from openai import OpenAI
from dotenv import load_dotenv

from retriever import load_collection, retrieve_balanced, retrieve
from pptx_generator import generate_pptx

load_dotenv()

CLIENT = OpenAI(
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


@cl.on_chat_start
async def start():
    try:
        collection = load_collection()
        cl.user_session.set("collection", collection)
        cl.user_session.set("history", [])
        await cl.Message(
            content=(
                "Index loaded successfully. Ask me anything about the documents in **Sources/**.\n\n"
                "> Every answer is grounded in your indexed sources only — "
                "I will tell you explicitly if something isn't covered.\n\n"
                "**Tip:** Start your message with `/pptx` to generate a PowerPoint presentation "
                "on any topic covered by the sources."
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

    is_pptx = message.content.strip().lower().startswith("/pptx")
    query = message.content.strip()
    if is_pptx:
        query = query[5:].strip()
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

    if is_pptx:
        status_msg = cl.Message(content="Generating presentation — this may take a moment...")
        await status_msg.send()

        try:
            out_path = await cl.make_async(generate_pptx)(query, context)
            elements = [cl.File(name=out_path.name, path=str(out_path), display="inline")]
            await cl.Message(
                content=f"Here's your presentation on **{query}**.",
                elements=elements,
            ).send()
        except Exception as e:
            await cl.Message(content=f"Failed to generate presentation: `{e}`").send()
        return

    user_prompt = (
        f"Sources:\n\n{context}\n\n"
        f"---\n\n"
        f"Question: {query}\n\n"
        f"Answer using only the sources above. Cite each claim as [Source N]."
    )

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + history
        + [{"role": "user", "content": user_prompt}]
    )

    response_msg = cl.Message(content="")
    await response_msg.send()

    full_response = ""
    stream = CLIENT.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=1024,
        messages=messages,
        stream=True,
    )
    for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        full_response += text
        await response_msg.stream_token(text)

    await response_msg.update()

    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": full_response})
    cl.user_session.set("history", history)
