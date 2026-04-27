import asyncio
from pathlib import Path

import httpx

from config import PRESENTON_API_KEY, PRESENTON_BASE_URL

OUTPUT_DIR = Path(__file__).parent / "output"

_POLL_INTERVAL = 5
_TIMEOUT = 180


async def generate_presentation(
    content: str,
    n_slides: int,
    theme: str,
    output_path: Path,
) -> Path:
    """Submit a presentation job to Presenton, poll until done, and save the PPTX.

    Args:
        content: Markdown outline to pass as the prompt.
        n_slides: Suggested slide count.
        theme: Presenton theme name (e.g. "default").
        output_path: Where to write the downloaded PPTX file.

    Returns:
        The resolved output_path after the file has been written.

    Raises:
        RuntimeError: On API errors, unexpected status values, or timeout.
    """
    headers = {}
    if PRESENTON_API_KEY:
        headers["Authorization"] = f"Bearer {PRESENTON_API_KEY}"

    async with httpx.AsyncClient(base_url=PRESENTON_BASE_URL, timeout=30) as client:
        # --- Submit ---
        resp = await client.post(
            "/api/v1/ppt/presentation/generate/async",
            json={"prompt": content, "n_slides": n_slides, "theme": theme},
            headers=headers,
        )
        resp.raise_for_status()
        task_id = resp.json()["task_id"]

        # --- Poll ---
        deadline = asyncio.get_event_loop().time() + _TIMEOUT
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError(
                    f"Presenton job {task_id} did not complete within {_TIMEOUT}s"
                )

            poll = await client.get(
                f"/api/v1/ppt/presentation/status/{task_id}",
                headers=headers,
            )
            poll.raise_for_status()
            body = poll.json()
            status = body.get("status", "")

            if status == "completed":
                rel_path = body["data"]["path"]
                break
            if status == "failed":
                raise RuntimeError(
                    f"Presenton job {task_id} failed: {body.get('message', 'unknown error')}"
                )

            await asyncio.sleep(_POLL_INTERVAL)

        # --- Download ---
        dl = await client.get(rel_path, headers=headers)
        dl.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(dl.content)
    return output_path
