import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15


def scrape_url(url: str) -> dict:
    """
    Fetch a URL and extract its title and main text content.

    Returns:
        {
            "url": str,
            "title": str,
            "text": str,
            "error": str | None
        }
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Strip non-content tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else url

        # Prefer semantic content containers, fall back to body
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id="content")
            or soup.find(class_="content")
            or soup.find("body")
        )
        text = main.get_text(separator="\n", strip=True) if main else ""

        return {"url": url, "title": title, "text": text, "error": None}

    except Exception as e:
        return {"url": url, "title": url, "text": "", "error": str(e)}
