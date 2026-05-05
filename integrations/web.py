"""Web search (DuckDuckGo) and URL fetching integration."""

import asyncio
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _ddgs_text(query: str, max_results: int) -> list[dict]:
    """Synchronous DDG search — run in thread pool to avoid blocking."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # fallback to old name
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web using DuckDuckGo and return structured results."""
    try:
        raw = await asyncio.get_event_loop().run_in_executor(
            None, _ddgs_text, query, max_results
        )
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
        return {
            "success": True,
            "query": query,
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        logger.error("Web search failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "suggestion": "Try a more specific search query.",
        }


async def fetch_url(url: str, extract_text_only: bool = True) -> dict[str, Any]:
    """Fetch and return the content of a URL."""
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")

            if "text/html" in content_type and extract_text_only:
                soup = BeautifulSoup(response.text, "lxml")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                if len(text) > 12000:
                    text = text[:12000] + "\n\n[... content truncated ...]"
                return {"success": True, "url": url, "content": text, "content_type": content_type}
            else:
                content = response.text
                if len(content) > 12000:
                    content = content[:12000] + "\n\n[... truncated ...]"
                return {"success": True, "url": url, "content": content, "content_type": content_type}

    except Exception as e:
        logger.error("URL fetch failed for %s: %s", url, e)
        return {
            "success": False,
            "error": str(e),
            "suggestion": "Check the URL is accessible and try again.",
        }
