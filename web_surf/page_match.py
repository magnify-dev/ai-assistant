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
    "docs",
    "documentation",
    "support",
    "download",
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
    "forums.",
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


def is_secondary_host(url: str) -> bool:
    """True for aggregators, social, and forums that rarely hold primary-source data."""
    host = urlsplit(str(url or "").lower()).netloc
    return any(marker in host for marker in _LOW_VALUE_HOSTS)


def is_publisher_content_url(url: str) -> bool:
    """True for article/news/support pages on a publisher domain (not forums or homepages)."""
    if is_secondary_host(url):
        return False
    path = urlsplit(str(url or "").lower()).path
    return any(
        hint in path
        for hint in (
            "/article/",
            "/news/",
            "/patch",
            "/changelog",
            "/release",
            "/blue-tracker/",
            "/feed/",
        )
    )


def seed_url_priority(url: str, query: str) -> tuple[int, int]:
    """Rank candidate landing pages — higher is better."""
    parsed = urlsplit(str(url or "").lower())
    score = score_result_url(url, query)
    if is_secondary_host(url):
        score -= 80
    if is_publisher_content_url(url):
        score += 60
    if "patch" in parsed.path and "note" in parsed.path:
        score += 30
    return (score, score)


def registrable_domain(netloc: str) -> str:
    """Best-effort registrable domain (e.g. news.vendor.com -> vendor.com)."""
    host = str(netloc or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def official_registrable_domains(urls: list[str]) -> set[str]:
    return {
        registrable_domain(urlsplit(str(url or "")).netloc)
        for url in urls
        if str(url or "").strip()
    }


def url_on_publisher_domain(url: str, publisher_domains: set[str]) -> bool:
    if not publisher_domains:
        return False
    netloc = urlsplit(str(url or "").lower()).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return registrable_domain(netloc) in publisher_domains


def _host_matches_topic(host: str, query: str) -> bool:
    """True when the domain or subdomain names the research topic."""
    host_lower = host.lower()
    host_compact = host_lower.replace(".", "").replace("-", "")
    labels = [
        part
        for part in re.split(r"[.\-]", host_lower)
        if part and part not in {"www", "en", "us", "uk", "com", "org", "net", "io"}
    ]
    tokens = query_tokens(query, min_len=4) or query_tokens(query)
    for token in tokens:
        if token in host_compact:
            return True
        if any(len(label) >= 4 and (token in label or label in token) for label in labels):
            return True
    return False


def is_official_source(url: str, query: str) -> bool:
    """True for likely official / primary-source pages for the research topic."""
    if is_secondary_host(url):
        return False
    parsed = urlsplit(str(url or "").lower())
    if _host_matches_topic(parsed.netloc, query):
        return True
    tokens = query_tokens(query, min_len=4) or query_tokens(query)
    path_hints = sum(1 for hint in _CONTENT_PATH_HINTS if hint in parsed.path)
    token_hits_in_host = sum(1 for token in tokens if token in parsed.netloc)
    token_hits_in_path = sum(1 for token in tokens if token in parsed.path)
    path_parts = [part for part in parsed.path.split("/") if part]
    # Apex vendor domains (e.g. microsoft.com) often put topic content in the path.
    if path_hints and token_hits_in_path >= 1 and len(parsed.netloc.split(".")) <= 2:
        return True
    if path_hints and token_hits_in_host >= 1:
        return True
    # Publisher article URLs (e.g. news.vendor.com/en/article/topic-patch-notes).
    if path_hints and token_hits_in_path >= 2 and len(path_parts) >= 2:
        return True
    return score_result_url(url, query) >= 7


def partition_by_source_tier(
    results: list[Any],
    query: str,
) -> tuple[list[Any], list[Any]]:
    """Split results into (official, secondary) tiers."""
    goal = focus_query(query)
    official: list[Any] = []
    secondary: list[Any] = []
    for row in results:
        url = str(getattr(row, "url", "") or "")
        if is_official_source(url, goal):
            official.append(row)
        else:
            secondary.append(row)
    return official, secondary


def _rank_tier(results: list[Any], query: str, *, per_domain: int) -> list[Any]:
    scored = sorted(
        enumerate(results),
        key=lambda row: (
            -(score_search_result(row[1], query) + score_result_url(getattr(row[1], "url", ""), query)),
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
    if is_secondary_host(url):
        score -= 10
    return score


def rank_search_results(
    results: list[Any],
    query: str,
    *,
    per_domain: int = 2,
) -> list[Any]:
    """Order official sources first, then secondary, each tier by relevance + diversity."""
    goal = focus_query(query)
    official, secondary = partition_by_source_tier(results, goal)
    return _rank_tier(official, goal, per_domain=per_domain) + _rank_tier(
        secondary, goal, per_domain=per_domain
    )


def score_interactable(el: dict[str, Any], query: str) -> int:
    text = " ".join(str(el.get(key) or "") for key in ("text", "aria", "href")).lower()
    return score_blob_match(text, query)


def score_page_content(text: str, query: str) -> int:
    return score_blob_match(text.lower(), query)


_DATE_NUMERIC_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")
_DATE_NAMED_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{2,4})\b",
    re.I,
)
_DATE_NAMED_DAY_FIRST_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"(?:,)?\s+(\d{2,4})\b",
    re.I,
)
_MONTH_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_SECTION_HEADER_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)?\s+build\b", re.I)


def _normalize_year(year: int) -> int:
    if year < 100:
        return 2000 + year
    return year


def parse_target_dates(query: str) -> list[tuple[int, int, int]]:
    """Return (day, month, year) tuples explicitly mentioned in the user's query."""
    goal = focus_query(query)
    found: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()

    def add(day: int, month: int, year: int) -> None:
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return
        normalized = (day, month, _normalize_year(year))
        if normalized not in seen:
            seen.add(normalized)
            found.append(normalized)

    for match in _DATE_NUMERIC_RE.finditer(goal):
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if first > 12:
            add(first, second, year)
        elif second > 12:
            add(second, first, year)
        else:
            add(first, second, year)

    for match in _DATE_NAMED_RE.finditer(goal):
        month = _MONTH_TO_NUM[match.group(1).lower()]
        add(int(match.group(2)), month, int(match.group(3)))

    for match in _DATE_NAMED_DAY_FIRST_RE.finditer(goal):
        month = _MONTH_TO_NUM[match.group(2).lower()]
        add(int(match.group(1)), month, int(match.group(3)))

    return found


def _date_match_patterns(day: int, month: int, year: int) -> list[re.Pattern[str]]:
    month_name = next(
        (name for name, num in _MONTH_TO_NUM.items() if num == month),
        "",
    )
    patterns = [
        rf"\b{day}[./-]{month}[./-]{year}\b",
        rf"\b{month}[./-]{day}[./-]{year}\b",
        rf"\b{day}\s+{month_name}\s+{year}\b",
        rf"\b{month_name}\s+{day}(?:st|nd|rd|th)?(?:,)?\s+{year}\b",
    ]
    return [re.compile(pattern, re.I) for pattern in patterns]


def page_contains_target_date(text: str, query: str) -> bool:
    blob = str(text or "")
    if not blob.strip():
        return False
    for day, month, year in parse_target_dates(query):
        if any(pattern.search(blob) for pattern in _date_match_patterns(day, month, year)):
            return True
    return False


def filter_text_by_date(text: str, query: str, *, max_chars: int = 8000) -> str:
    """Keep only the section that matches a target date when a page lists many dated entries."""
    blob = str(text or "").strip()
    if not blob:
        return ""
    targets = parse_target_dates(query)
    if not targets:
        return blob[:max_chars]

    section_starts = [match.start() for match in _SECTION_HEADER_RE.finditer(blob)]
    if not section_starts:
        section_starts = [0]

    best_section = ""
    for day, month, year in targets:
        for pattern in _date_match_patterns(day, month, year):
            match = pattern.search(blob)
            if not match:
                continue
            start_index = 0
            for section_start in section_starts:
                if section_start <= match.start():
                    start_index = section_start
                else:
                    break
            end_index = len(blob)
            for section_start in section_starts:
                if section_start > match.start():
                    end_index = section_start
                    break
            section = blob[start_index:end_index].strip()
            if len(section) > len(best_section):
                best_section = section
            break

    if not best_section:
        return blob[:max_chars]
    return best_section[:max_chars]


def page_text_for_goal(text: str, query: str, *, max_chars: int = 12000) -> str:
    """Prefer a date-filtered slice when the goal names a specific date."""
    if parse_target_dates(query):
        filtered = filter_text_by_date(text, query, max_chars=max_chars)
        if len(filtered) >= 120:
            return filtered
    return str(text or "")[:max_chars]


_DETAIL_MARKERS = (
    "fixed issue",
    "fixed an issue",
    "bug fix",
    "balance update",
    "increased the",
    "decreased the",
    "reduced the",
    "added ",
    "removed ",
    "no longer",
    "now drops",
    "now rewards",
)
_BULLET_RE = re.compile(r"(?:^|\n)\s*[\*\-•]\s+\S", re.M)


def page_has_substantive_content(text: str, query: str) -> bool:
    """True when scoped page text contains actual answer detail, not just a dated header."""
    scoped = page_text_for_goal(text, query, max_chars=12000)
    blob = scoped.lower().strip()
    if len(blob) < 120:
        return False
    detail_hits = sum(1 for marker in _DETAIL_MARKERS if marker in blob)
    bullet_count = len(_BULLET_RE.findall(scoped))
    fixed_hits = scoped.count("Fixed") + scoped.count("fixed")
    if detail_hits >= 2 or bullet_count >= 3 or fixed_hits >= 2:
        return True
    if len(blob) >= 500 and detail_hits >= 1:
        return True
    return len(blob) >= 900


def page_matches_query(
    text: str,
    query: str,
    *,
    min_chars: int = 200,
    min_token_ratio: float = 0.4,
) -> bool:
    """True when page body text looks like it answers the research query."""
    scoped = page_text_for_goal(text, query, max_chars=max(len(text or ""), min_chars * 4))
    blob = scoped.lower()
    dated_goal = bool(parse_target_dates(query))
    required_chars = 120 if dated_goal else min_chars
    if len(blob) < required_chars:
        return False
    if dated_goal and page_contains_target_date(scoped, query):
        return page_has_substantive_content(scoped, query)
    tokens = query_tokens(query, min_len=4) or query_tokens(query)
    if not tokens:
        return len(blob) >= min_chars
    hits = sum(1 for token in tokens if token in blob)
    required = max(2, int(len(tokens) * min_token_ratio + 0.5))
    return hits >= required


def goal_is_satisfied(
    text: str,
    query: str,
    *,
    source_url: str = "",
    publisher_domains: set[str] | None = None,
    publisher_routes: set[str] | None = None,
) -> bool:
    """True when collected text answers the goal from an acceptable source."""
    if not page_matches_query(text, query):
        return False
    domains = publisher_domains or set()
    if not domains:
        return True
    if url_on_publisher_domain(source_url, domains):
        if is_secondary_host(source_url):
            return page_has_substantive_content(text, query)
        if is_publisher_content_url(source_url):
            return True
        return page_has_substantive_content(text, query)
    routes = publisher_routes or set()
    if any(url_on_publisher_domain(str(route), domains) for route in routes):
        return False
    return page_has_substantive_content(text, query)


def suggest_expand_action(snapshot: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Click a collapsed accordion section that matches the research goal."""
    from ui_test.expandable import is_collapse_toggle, is_collapsed_section

    targets = parse_target_dates(query)
    date_patterns = [
        pattern
        for day, month, year in targets
        for pattern in _date_match_patterns(day, month, year)
    ]
    best: dict[str, Any] | None = None
    best_score = 0
    for raw in snapshot.get("interactables") or []:
        if not isinstance(raw, dict) or raw.get("disabled") or not raw.get("id"):
            continue
        if not is_collapse_toggle(raw) or not is_collapsed_section(raw):
            continue
        label = " ".join(
            str(raw.get(key) or "")
            for key in ("text", "aria", "label", "nearest_heading", "nearby_text")
        )
        score = score_interactable(raw, query)
        if date_patterns and any(pattern.search(label) for pattern in date_patterns):
            score += 24
        if score > best_score:
            best_score = score
            best = raw
    if best and best_score >= 6:
        return {
            "action": "click",
            "target_id": str(best["id"]),
            "reason": "Expand collapsed section matching the target date before collecting content",
        }
    return None


def page_has_goal_links(snapshot: dict[str, Any], query: str, *, min_score: int = 6) -> bool:
    """True when goal-relevant navigation exists but the page body likely lacks the full answer."""
    visible = str(snapshot.get("visible_text") or "")
    if page_matches_query(visible, query, min_chars=300):
        return False
    if page_contains_target_date(visible, query):
        filtered = filter_text_by_date(visible, query)
        if len(filtered) >= 300:
            return False
    from web_surf.context_curate import _label, _query_score, _tokens

    query_tokens = _tokens(focus_query(query))
    if not query_tokens:
        return False
    for raw in snapshot.get("interactables") or []:
        if not isinstance(raw, dict) or raw.get("disabled") or not raw.get("id"):
            continue
        if str(raw.get("kind") or "").lower() not in {"link", "button", "blz-button", "menuitem"}:
            continue
        if _query_score(raw, query_tokens) >= min_score:
            return True
    return False
