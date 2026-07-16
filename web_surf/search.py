from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_TIMEOUT_SEC = 30.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def web_search(query: str, *, max_results: int = 8) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []

    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("ddgs is not installed — run: pip install ddgs") from exc

    rows: list[SearchResult] = []
    seen: set[str] = set()
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("href") or item.get("url") or "").strip()
                if not _is_http_url(url):
                    continue
                key = url.lower()
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    SearchResult(
                        title=str(item.get("title") or url).strip(),
                        url=url,
                        snippet=str(item.get("body") or item.get("snippet") or "").strip(),
                        query=query,
                    )
                )
    except Exception as exc:
        logger.warning("Web search failed for %r: %s", query, exc)
        raise RuntimeError(f"Web search failed: {exc}") from exc

    return rows
