from openai import AsyncOpenAI

from tools import execute_tool_call

MAX_TOOL_ROUNDS = 3

SUPERVISOR_SYSTEM_PROMPT = """\
You are LEXI, an agentic research assistant with access to multiple knowledge base tools. \
Each tool searches a different category of indexed documents.

Category guidance:
- "Our Solutions" contains the products and services WE offer. When the user asks about \
"our offerings", "what we can do", "what solutions we have", or anything about presenting \
to a customer, ALWAYS search Our Solutions. This is the only category that represents \
our own capabilities.
- Other categories (Articles, Case Studies, etc.) contain external research, third-party \
case studies, and industry context. Use them for background and evidence, but never \
present their content as something we offer.

Your workflow:
1. Analyze the user's question to determine which source categories are relevant.
2. Call the appropriate search tools. You may call multiple tools if the question \
spans multiple categories. You may also call zero tools if the question is a \
greeting or meta-question about your capabilities.
3. Review the retrieved chunks from each tool. Decide which chunks are relevant \
to the question and which are noise. When presenting solutions to a customer, \
use Our Solutions for what we offer and other categories only as supporting evidence.
4. Synthesize a grounded answer using ONLY the relevant retrieved chunks.

Citation rules:
- Cite every factual claim using [Source N] notation.
- Number sources sequentially across all tool results (first chunk from any tool is [Source 1], etc.).
- If the retrieved chunks do not contain enough information, say so explicitly. \
Do not fill gaps with general knowledge.
- If two sources contradict each other, surface the conflict rather than silently picking one.
- Use hedged language when sources are ambiguous: "according to [Source N]", "as of [date]".
- Do not add filler, background context, or conclusions not supported by the sources.
- End your response with a 'Sources used' list showing the title and URL (if any) for each \
[Source N] you cited.

Important: Only use information from the tool results. Never use your training data to answer \
factual questions about the documents."""


def _serialize_message(msg) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a plain dict."""
    d: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d


async def run_agent(
    client: AsyncOpenAI,
    user_query: str,
    collection,
    history: list[dict],
    tools: list[dict],
    name_map: dict[str, str],
    stream_callback=None,
) -> str:
    """Run the agentic loop. Returns the final assistant response text."""
    messages = [
        {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_query},
    ]

    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            max_tokens=1024,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )

        msg = response.choices[0].message

        if not msg.tool_calls:
            break

        messages.append(_serialize_message(msg))

        for tc in msg.tool_calls:
            result = execute_tool_call(
                tc.function.name,
                tc.function.arguments,
                collection,
                name_map,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    # Final streamed synthesis (no tools — forces text response)
    full_response = ""
    stream = await client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=1024,
        messages=messages,
        stream=True,
    )

    async for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        full_response += token
        if stream_callback:
            await stream_callback(token)

    return full_response
