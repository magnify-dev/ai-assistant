from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_QUERY_STOPWORDS = frozenset(
    {
        "find",
        "give",
        "have",
        "help",
        "how",
        "into",
        "just",
        "latest",
        "like",
        "many",
        "more",
        "most",
        "much",
        "need",
        "over",
        "please",
        "show",
        "some",
        "tell",
        "that",
        "them",
        "they",
        "this",
        "under",
        "very",
        "want",
        "what",
        "when",
        "where",
        "which",
        "with",
        "your",
    }
)


# Path words that mark primary-source content pages (release/update announcements).
_CONTENT_PATH_HINTS = (
    "patch",
    "notes",
    "changelog",
    "release",
    "news",
    "update",
    "blog",
    "announcement",
    "version",
)
# Aggregator / social hosts rarely hold the primary source.
_LOW_VALUE_HOSTS = (
    "reddit.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.",
    "quora.com",
)

_TASK_WRAPPER_MARKER = re.compile(r"original user task:\s*", re.I)


def focus_query(query: str) -> str:
    """Strip this tool's collaboration wrapper so scoring sees only the user's task."""
    match = _TASK_WRAPPER_MARKER.search(query or "")
    if not match:
        return (query or "").strip()
    return query[match.end() :].strip() or query.strip()


def query_tokens(query: str, *, min_len: int = 3) -> list[str]:
    """Meaningful tokens from the user's research query."""
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if len(token) < min_len or token in _QUERY_STOPWORDS:
            continue
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def score_blob_match(blob: str, query: str) -> int:
    """Score how well a text blob matches the query (higher = more relevant)."""
    tokens = query_tokens(query)
    if not tokens:
        return 0
    score = 0
    for token in tokens:
        if token in blob:
            score += 8
    return score


def score_search_result(row: Any, query: str) -> int:
    blob = (
        f"{getattr(row, 'title', '')} {getattr(row, 'snippet', '')} "
        f"{getattr(row, 'url', '')}"
    ).lower()
    return score_blob_match(blob, query)


def score_result_url(url: str, query: str) -> int:
    """Rank likely primary sources higher — generic signals, no hardcoded site names."""
    parsed = urlsplit(str(url or "").lower())
    host = parsed.netloc
    path = parsed.path
    score = 0
    for hint in _CONTENT_PATH_HINTS:
        if hint in path or hint in host:
            score += 3
    host_compact = host.replace(".", "").replace("-", "")
    # A domain named after the topic is usually the official source.
    for token in query_tokens(query):
        if token in host_compact:
            score += 4
    if any(marker in host for marker in _LOW_VALUE_HOSTS):
        score -= 10
    return score


def rank_search_results(
    results: list[Any],
    query: str,
    *,
    per_domain: int = 2,
) -> list[Any]:
    """Order results by relevance + source quality, keeping domain diversity."""
    goal = focus_query(query)
    scored = sorted(
        enumerate(results),
        key=lambda row: (
            -(score_search_result(row[1], goal) + score_result_url(getattr(row[1], "url", ""), goal)),
            row[0],
        ),
    )
    picked: list[Any] = []
    overflow: list[Any] = []
    domain_counts: dict[str, int] = {}
    for _, row in scored:
        host = urlsplit(str(getattr(row, "url", "")).lower()).netloc
        if domain_counts.get(host, 0) >= per_domain:
            overflow.append(row)
            continue
        domain_counts[host] = domain_counts.get(host, 0) + 1
        picked.append(row)
    return picked + overflow


def score_interactable(el: dict[str, Any], query: str) -> int:
    text = " ".join(str(el.get(key) or "") for key in ("text", "aria", "href")).lower()
    return score_blob_match(text, query)


def score_page_content(text: str, query: str) -> int:
    return score_blob_match(text.lower(), query)


def page_matches_query(
    text: str,
    query: str,
    *,
    min_chars: int = 200,
    min_token_ratio: float = 0.4,
) -> bool:
    """True when page body text looks like it answers the research query."""
    blob = text.lower()
    if len(blob) < min_chars:
        return False
    tokens = query_tokens(query, min_len=4) or query_tokens(query)
    if not tokens:
        return len(blob) >= min_chars
    hits = sum(1 for token in tokens if token in blob)
    required = max(2, int(len(tokens) * min_token_ratio + 0.5))
    return hits >= required
