from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
import trafilatura

from web_surf.store import content_hash, normalize_url

logger = logging.getLogger(__name__)

USER_AGENT = "JarvisWebResearch/1.0 (+local research agent)"


@dataclass
class PageResult:
    url: str
    title: str
    text: str
    markdown: str
    content_hash: str
    fetch_tier: int
    error: str = ""
    evidence_context: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text.strip())


def fetch_page_tier1(
    url: str,
    *,
    timeout_sec: float = 20.0,
    max_chars: int = 12000,
) -> PageResult:
    safe_url = normalize_url(url)
    try:
        with httpx.Client(
            timeout=timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(safe_url)
            response.raise_for_status()
            raw_html = response.text
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower() and "<html" not in raw_html.lower():
                return PageResult(
                    url=safe_url,
                    title="",
                    text="",
                    markdown="",
                    content_hash="",
                    fetch_tier=1,
                    error=f"Unsupported content type: {content_type or 'unknown'}",
                )
    except Exception as exc:
        logger.warning("Tier-1 fetch failed for %s: %s", safe_url, exc)
        return PageResult(
            url=safe_url,
            title="",
            text="",
            markdown="",
            content_hash="",
            fetch_tier=1,
            error=str(exc),
        )

    metadata: dict[str, Any] = {}
    try:
        metadata = trafilatura.extract_metadata(raw_html) or {}
    except Exception:
        metadata = {}

    markdown = ""
    try:
        markdown = trafilatura.extract(
            raw_html,
            url=safe_url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
        ) or ""
    except Exception as exc:
        logger.warning("Trafilatura markdown extract failed for %s: %s", safe_url, exc)

    text = ""
    try:
        text = trafilatura.extract(
            raw_html,
            url=safe_url,
            output_format="txt",
            include_tables=True,
        ) or ""
    except Exception as exc:
        logger.warning("Trafilatura text extract failed for %s: %s", safe_url, exc)

    body = (markdown or text).strip()
    if not body:
        return PageResult(
            url=safe_url,
            title=str(getattr(metadata, "title", "") or ""),
            text="",
            markdown="",
            content_hash="",
            fetch_tier=1,
            error="No readable content extracted",
        )

    clipped = body[:max_chars]
    title = str(getattr(metadata, "title", "") or "").strip()
    return PageResult(
        url=safe_url,
        title=title,
        text=clipped,
        markdown=clipped,
        content_hash=content_hash(clipped),
        fetch_tier=1,
    )
