from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

# Sites users commonly name directly instead of by full domain.
_SITE_ALIASES: dict[str, str] = {
    "wowhead": "wowhead.com",
    "icy-veins": "icy-veins.com",
    "icy veins": "icy-veins.com",
    "icyveins": "icy-veins.com",
    "maxroll": "maxroll.gg",
    "reddit": "reddit.com",
    "wowpedia": "wowpedia.fandom.com",
    "fandom": "fandom.com",
}

_USER_SOURCE_RE = re.compile(
    r"\b(?:go\s+to|visit|use|open|check|start\s+(?:at|on)|from|on|at)\s+"
    r"([a-z][a-z0-9\-]{1,40})(?:\s+(?:and|to|for)|[.,]|$)",
    re.I,
)


def focus_query(query: str) -> str:
    """Strip this tool's collaboration wrapper so scoring sees only the user's task."""
    match = _TASK_WRAPPER_MARKER.search(query or "")
    if not match:
        return (query or "").strip()
    return query[match.end() :].strip() or query.strip()


def parse_user_preferred_domains(query: str) -> set[str]:
    """Domains the user explicitly asked to use — override official-source defaults."""
    text = focus_query(query)
    preferred: set[str] = set()
    for match in re.finditer(r"https?://([^\s/?#]+)", text, re.I):
        domain = registrable_domain(match.group(1).lower())
        if domain:
            preferred.add(domain)
    for match in re.finditer(
        r"\b([a-z0-9][a-z0-9\-]{0,40}\.(?:com|org|net|io|gg|co|tv))\b",
        text,
        re.I,
    ):
        domain = registrable_domain(match.group(1).lower())
        if domain:
            preferred.add(domain)
    for match in _USER_SOURCE_RE.finditer(text):
        token = match.group(1).lower().strip("-")
        if not token or token in _QUERY_STOPWORDS:
            continue
        if token in _SITE_ALIASES:
            preferred.add(_SITE_ALIASES[token])
            continue
        if "." in token:
            domain = registrable_domain(token)
            if domain:
                preferred.add(domain)
            continue
        if len(token) >= 4:
            preferred.add(registrable_domain(f"{token}.com"))
    return {domain for domain in preferred if domain and "." in domain}


def url_on_preferred_source(url: str, preferred_domains: set[str]) -> bool:
    """True when url belongs to a user-requested source site."""
    if not preferred_domains:
        return False
    host = registrable_domain(urlsplit(str(url or "").lower()).netloc)
    if not host:
        return False
    if host in preferred_domains:
        return True
    return any(
        host == registrable_domain(str(pref or ""))
        or host.endswith(f".{pref}")
        or pref in host
        for pref in preferred_domains
        if pref
    )


def user_directed_sources(query: str) -> bool:
    """True when the user explicitly named where to research."""
    return bool(parse_user_preferred_domains(query))


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


def seed_url_priority(
    url: str,
    query: str,
    *,
    preferred_domains: set[str] | None = None,
) -> tuple[int, int]:
    """Rank candidate landing pages — higher is better."""
    parsed = urlsplit(str(url or "").lower())
    score = score_result_url(url, query)
    preferred = preferred_domains or parse_user_preferred_domains(query)
    on_preferred = bool(preferred and url_on_preferred_source(url, preferred))
    if on_preferred:
        score += 1000
    if is_secondary_host(url) and not on_preferred:
        score -= 80
    if is_publisher_content_url(url) and not on_preferred:
        score += 60
    if "patch" in parsed.path and "note" in parsed.path:
        score += 30
    # Named site + "latest news": start on the listing hub and discover the article
    # in-browser. Search often returns a specific old/random article — demote those.
    if on_preferred and _wants_browse_discovery(query):
        if is_content_listing_url(url):
            score += 500
        elif is_deep_article_url(url):
            score -= 450
    return (score, score)


def _wants_browse_discovery(query: str) -> bool:
    """True when newest content must be found on-site, not picked from search memory."""
    focused = focus_query(query)
    if query_implies_recency(focused):
        return True
    return bool(re.search(r"\b(news|updates?|headlines|announcements?)\b", focused, re.I))


def is_deep_article_url(url: str) -> bool:
    """True for /news/<slug> style article URLs (not the listing itself)."""
    path = urlsplit(str(url or "")).path.lower().rstrip("/")
    return bool(_DEEP_ARTICLE_PATH_RE.search(path))


def listing_hub_url(url: str) -> str | None:
    """Collapse a deep article URL to its news/blog listing hub when possible."""
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path or "/"
    match = _LISTING_HUB_PREFIX_RE.search(path)
    if not match:
        return None
    hub_path = match.group(1).rstrip("/") or "/"
    return urlunsplit((parsed.scheme, parsed.netloc, hub_path, "", ""))


def preferred_discovery_seeds(query: str, candidate_urls: list[str]) -> list[str]:
    """
    When the user names a site and wants recent news, seed the listing hub(s)
    derived from search hits — not the specific article pages search returned.
    """
    preferred = parse_user_preferred_domains(query)
    if not preferred or not _wants_browse_discovery(query):
        return []
    seeds: list[str] = []
    for raw in candidate_urls:
        url = str(raw or "").strip()
        if not url or not url_on_preferred_source(url, preferred):
            continue
        hub = listing_hub_url(url)
        if hub and hub not in seeds:
            seeds.append(hub)
        elif is_content_listing_url(url) and url not in seeds:
            seeds.append(url.split("#", 1)[0].rstrip("/") or url)
    return seeds


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


def _extract_absolute_dates(blob: str) -> list[tuple[int, int, int]]:
    """Return (day, month, year) tuples found in arbitrary text."""
    found: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()

    def add(day: int, month: int, year: int) -> None:
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return
        normalized = (day, month, _normalize_year(year))
        if normalized not in seen:
            seen.add(normalized)
            found.append(normalized)

    for match in _DATE_NUMERIC_RE.finditer(blob):
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if first > 12:
            add(first, second, year)
        elif second > 12:
            add(second, first, year)
        else:
            add(first, second, year)

    for match in _DATE_NAMED_RE.finditer(blob):
        month = _MONTH_TO_NUM[match.group(1).lower()]
        add(int(match.group(2)), month, int(match.group(3)))

    for match in _DATE_NAMED_DAY_FIRST_RE.finditer(blob):
        month = _MONTH_TO_NUM[match.group(2).lower()]
        add(int(match.group(1)), month, int(match.group(3)))

    return found


def parse_target_dates(query: str) -> list[tuple[int, int, int]]:
    """Return (day, month, year) tuples explicitly mentioned in the user's query."""
    return _extract_absolute_dates(focus_query(query))


_RELATIVE_AGO_RE = re.compile(
    r"\b(?:(an?|\d+)\s+(minute|hour|day|week|month|year)s?\s+ago)\b",
    re.I,
)
_URL_DATE_RE = re.compile(r"/(\d{4})/(\d{1,2})(?:/(\d{1,2}))?(?:/|$|\?)")
_NEWS_ID_RE = re.compile(r"(?:news[=/]|/news/)(\d{5,})", re.I)


def parse_content_date(text: str, *, reference: date | None = None) -> date | None:
    """Best-effort parse of a publication date from link labels, URLs, or snippets."""
    blob = str(text or "").strip()
    if not blob:
        return None
    ref = reference or datetime.now(timezone.utc).date()

    if re.search(r"\b(?:just now|today)\b", blob, re.I):
        return ref
    if re.search(r"\byesterday\b", blob, re.I):
        return ref - timedelta(days=1)

    relative = _RELATIVE_AGO_RE.search(blob)
    if relative:
        amount_raw = str(relative.group(1) or "1").lower()
        amount = 1 if amount_raw in {"a", "an"} else int(amount_raw)
        unit = relative.group(2).lower()
        if unit.startswith("minute") or unit.startswith("hour"):
            return ref
        if unit.startswith("day"):
            return ref - timedelta(days=amount)
        if unit.startswith("week"):
            return ref - timedelta(weeks=amount)
        if unit.startswith("month"):
            return ref - timedelta(days=min(amount * 30, 365 * 3))
        if unit.startswith("year"):
            return ref - timedelta(days=min(amount * 365, 365 * 10))

    url_match = _URL_DATE_RE.search(blob)
    if url_match:
        year, month = int(url_match.group(1)), int(url_match.group(2))
        day = int(url_match.group(3) or 1)
        if 1 <= month <= 12 and 1 <= day <= 31 and 1990 <= year <= 2100:
            return date(year, month, day)

    absolute = _extract_absolute_dates(blob)
    if absolute:
        day, month, year = max(absolute, key=lambda item: (item[2], item[1], item[0]))
        return date(year, month, day)
    return None


def newest_date_in_text(text: str, *, reference: date | None = None) -> date | None:
    """Return the newest date mentioned anywhere in a page or listing blob."""
    blob = str(text or "")
    if not blob.strip():
        return None
    ref = reference or datetime.now(timezone.utc).date()
    candidates: list[date] = []
    for match in _RELATIVE_AGO_RE.finditer(blob):
        parsed = parse_content_date(match.group(0), reference=ref)
        if parsed:
            candidates.append(parsed)
    for day, month, year in _extract_absolute_dates(blob):
        candidates.append(date(year, month, day))
    if re.search(r"\b(?:just now|today)\b", blob, re.I):
        candidates.append(ref)
    if re.search(r"\byesterday\b", blob, re.I):
        candidates.append(ref - timedelta(days=1))
    for match in _URL_DATE_RE.finditer(blob):
        parsed = parse_content_date(match.group(0), reference=ref)
        if parsed:
            candidates.append(parsed)
    return max(candidates) if candidates else None


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


def _section_around_date_match(blob: str, match_start: int) -> str:
    section_starts = [match.start() for match in _SECTION_HEADER_RE.finditer(blob)]
    if not section_starts:
        section_starts = [0]
    start_index = 0
    for section_start in section_starts:
        if section_start <= match_start:
            start_index = section_start
        else:
            break
    end_index = len(blob)
    for section_start in section_starts:
        if section_start > match_start:
            end_index = section_start
            break
    return blob[start_index:end_index].strip()


def filter_text_by_date(text: str, query: str, *, max_chars: int = 8000) -> str:
    """Keep only the section that matches a target date when a page lists many dated entries."""
    blob = str(text or "").strip()
    if not blob:
        return ""
    targets = parse_target_dates(query)
    if not targets:
        return blob[:max_chars]

    best_section = ""
    for day, month, year in targets:
        for pattern in _date_match_patterns(day, month, year):
            match = pattern.search(blob)
            if not match:
                continue
            section = _section_around_date_match(blob, match.start())
            if len(section) > len(best_section):
                best_section = section
            break

    if not best_section:
        return blob[:max_chars]
    return best_section[:max_chars]


def filter_text_by_recency(text: str, query: str, *, max_chars: int = 8000) -> str:
    """Keep only the newest dated section when the user asked for latest/recent content."""
    blob = str(text or "").strip()
    if not blob or not query_implies_recency(query):
        return blob[:max_chars]

    dated_positions: list[tuple[date, int]] = []
    for day, month, year in _extract_absolute_dates(blob):
        parsed = date(year, month, day)
        for pattern in _date_match_patterns(day, month, year):
            match = pattern.search(blob)
            if match:
                dated_positions.append((parsed, match.start()))
                break

    if not dated_positions:
        return blob[:max_chars]

    dated_positions.sort(key=lambda row: row[1])
    newest_date = max(row[0] for row in dated_positions)
    anchor_pos = next(pos for when, pos in dated_positions if when == newest_date)

    para_start = blob.rfind(". ", 0, anchor_pos)
    start = para_start + 2 if para_start >= 0 else max(0, anchor_pos - 48)

    end = len(blob)
    for _when, pos in dated_positions:
        if pos > anchor_pos:
            end = pos
            break

    section = blob[start:end].strip()
    if len(section) >= 80:
        return section[:max_chars]
    return blob[:max_chars]


def page_text_for_goal(text: str, query: str, *, max_chars: int = 12000) -> str:
    """Prefer a date-filtered slice when the goal names a specific date or newest content."""
    if parse_target_dates(query):
        filtered = filter_text_by_date(text, query, max_chars=max_chars)
        if len(filtered) >= 120:
            return filtered
    if query_implies_recency(query):
        filtered = filter_text_by_recency(text, query, max_chars=max_chars)
        if len(filtered) >= 120:
            return filtered
    return str(text or "")[:max_chars]


_RECENCY_RE = re.compile(
    r"\b("
    r"latest|newest|most recent|recent|"
    r"today|yesterday|this week|this month|"
    r"breaking|just (?:announced|released|updated|posted)|"
    r"up[- ]to[- ]date|current|what'?s new"
    r")\b",
    re.I,
)
# Listing endpoints only — NOT deep articles under /news/<slug>.
_LISTING_PATH_RE = re.compile(r"/(news|updates?|blog|articles?)/?$", re.I)
_DEEP_ARTICLE_PATH_RE = re.compile(r"/(news|updates?|blog|articles?)/[^/]+", re.I)
_LISTING_HUB_PREFIX_RE = re.compile(
    r"^(.*?/(?:news|updates?|blog|articles?))(?:/|$)",
    re.I,
)
_NAV_SHELL_MARKERS = (
    "log in",
    "sign in",
    "cookie",
    "skip to",
    "subscribe",
    "trending topics",
    "create account",
    "register",
)


def query_implies_recency(query: str) -> bool:
    return bool(_RECENCY_RE.search(focus_query(query)))


def should_apply_date_filter(query: str) -> bool:
    """True when collected page text should be clipped to a target or newest dated section."""
    focused = focus_query(query)
    return bool(parse_target_dates(focused) or query_implies_recency(focused))


def is_content_listing_url(url: str) -> bool:
    """True for news/blog index pages, not individual articles under them."""
    path = urlsplit(str(url or "")).path.lower().rstrip("/")
    return bool(_LISTING_PATH_RE.search(path))


def snapshot_viewport(snapshot: dict[str, Any]) -> dict[str, float]:
    vp = snapshot.get("viewport") if isinstance(snapshot.get("viewport"), dict) else {}
    width = float(vp.get("width") or 1280)
    height = float(vp.get("height") or 720)
    return {
        "width": width,
        "height": height,
        "scroll_x": float(vp.get("scroll_x") or 0),
        "scroll_y": float(vp.get("scroll_y") or 0),
        "document_width": float(vp.get("document_width") or width),
        "document_height": float(vp.get("document_height") or height),
    }


def viewport_has_content_below(snapshot: dict[str, Any], *, threshold: float = 0.12) -> bool:
    vp = snapshot_viewport(snapshot)
    bottom = vp["scroll_y"] + vp["height"]
    return bottom < vp["document_height"] - vp["height"] * threshold


def page_extends_beyond_viewport(snapshot: dict[str, Any], *, ratio: float = 1.25) -> bool:
    vp = snapshot_viewport(snapshot)
    return vp["document_height"] > vp["height"] * ratio


def viewport_explored_fraction(snapshot: dict[str, Any]) -> float:
    vp = snapshot_viewport(snapshot)
    if vp["document_height"] <= vp["height"]:
        return 1.0
    visible_bottom = min(vp["scroll_y"] + vp["height"], vp["document_height"])
    return visible_bottom / max(vp["document_height"], 1.0)


def page_looks_like_nav_shell(text: str, query: str) -> bool:
    """True when visible text is mostly site chrome, not article content."""
    scoped = page_text_for_goal(text, query, max_chars=4000)
    if page_has_substantive_content(scoped, query):
        return False
    blob = scoped.lower()
    nav_hits = sum(1 for marker in _NAV_SHELL_MARKERS if marker in blob)
    return nav_hits >= 2


def should_defer_collect_on_listing(snapshot: dict[str, Any], query: str) -> bool:
    """Defer auto-collect when a listing page still needs scrolling or a deeper article link."""
    url = str(snapshot.get("url") or "")
    visible = str(snapshot.get("visible_text") or "")
    recency = query_implies_recency(query)
    listing = is_content_listing_url(url)
    if not recency and not listing:
        return False
    if listing and (
        page_looks_like_nav_shell(visible, query)
        or page_has_goal_links(snapshot, query, min_score=4)
    ):
        return True
    if recency and page_extends_beyond_viewport(snapshot) and page_looks_like_nav_shell(visible, query):
        return True
    if recency and listing and not page_has_substantive_content(page_text_for_goal(visible, query), query):
        return page_has_goal_links(snapshot, query, min_score=4)
    return False


def element_publication_date(el: dict[str, Any], *, reference: date | None = None) -> date | None:
    """Publication date from element-local fields only — never nearby_text (avoids bleed)."""
    chunks: list[str] = []
    dates = el.get("dates") if isinstance(el.get("dates"), list) else []
    for value in dates[:2]:
        chunks.append(str(value))
    for key in ("byline", "title", "text", "aria", "label"):
        value = str(el.get(key) or "").strip()
        if value:
            chunks.append(value)
    href = str(el.get("href") or "").strip()
    if href:
        chunks.append(href)
    for chunk in chunks:
        parsed = parse_content_date(chunk, reference=reference)
        if parsed:
            return parsed
    return None


def _title_tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(token) >= 3}


def page_understanding_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    understanding = snapshot.get("page_understanding")
    if isinstance(understanding, dict):
        return understanding
    capture = snapshot.get("web_capture")
    if isinstance(capture, dict):
        nested = capture.get("page_understanding")
        if isinstance(nested, dict):
            return nested
    return None


def match_feed_item_interactable(
    feed_item: dict[str, Any],
    interactables: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Map a page-understanding feed row onto a real clickable interactable."""
    best: dict[str, Any] | None = None
    best_score = 0
    feed_href = str(feed_item.get("href") or "").strip().lower()
    feed_title = str(feed_item.get("title") or "").strip()
    feed_tokens = _title_tokens(feed_title)
    feed_news_id = ""
    news_match = _NEWS_ID_RE.search(feed_href)
    if news_match:
        feed_news_id = news_match.group(1)
    feed_path = urlsplit(feed_href).path.rstrip("/") if feed_href else ""
    for raw in interactables or []:
        if not isinstance(raw, dict) or raw.get("disabled") or not raw.get("id"):
            continue
        kind = str(raw.get("kind") or "").lower()
        if kind not in {"link", "button", "menuitem", "blz-button", "card"}:
            continue
        href = str(raw.get("href") or "").strip().lower()
        if not href:
            continue
        el_path = urlsplit(href).path.rstrip("/")
        # Skip bare origins / section hubs — they falsely substring-match article hrefs.
        if not el_path or el_path == "/" or (
            len(el_path) < 12 and not any(hint in el_path for hint in _CONTENT_PATH_HINTS)
        ):
            if not (feed_news_id and feed_news_id in href):
                continue
        score = 0
        if feed_news_id and feed_news_id in href:
            score += 100
        if feed_href and href and feed_href == href:
            score += 50
        elif feed_path and el_path and len(el_path) >= 12:
            if feed_path in el_path or el_path in feed_path:
                score += 40
        label = str(raw.get("text") or raw.get("title") or raw.get("aria") or "")
        overlap = len(feed_tokens & _title_tokens(label))
        if overlap:
            score += 8 * overlap
        if score > best_score:
            best_score = score
            best = raw
    # Require a real article match: news-id hit or solid title overlap (not nav crumbs).
    if best and best_score >= 24:
        return best
    return None


def newest_feed_item(understanding: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(understanding, dict):
        return None
    best: dict[str, Any] | None = None
    best_date: date | None = None
    for raw in understanding.get("feed_items") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        published = parse_content_date(
            " ".join(
                str(raw.get(key) or "")
                for key in ("date", "byline", "title")
            )
        )
        if best is None or (published and (best_date is None or published > best_date)):
            best = raw
            best_date = published
    return best


def score_content_link(el: dict[str, Any], query: str, *, dom_index: int = 0) -> int:
    href = str(el.get("href") or "").lower()
    label = " ".join(
        str(el.get(key) or "")
        for key in ("text", "title", "aria", "label")
    ).strip()
    blob = f"{label} {href}".lower()
    score = score_interactable(el, query)
    path = urlsplit(href).path
    score += sum(3 for hint in _CONTENT_PATH_HINTS if hint in path)
    if re.search(r"\b(patch notes|changelog|release notes|updates?|what's new|whats new)\b", blob, re.I):
        score += 12
    parsed_date = element_publication_date(el)
    if parsed_date:
        score += parsed_date.toordinal()
    elif query_implies_recency(query):
        # Listing pages usually show newest items first when dates are absent.
        score += max(0, 24 - min(dom_index, 24))
    return score


def suggest_content_link_action(
    snapshot: dict[str, Any],
    query: str,
    *,
    min_score: int = 6,
) -> dict[str, Any] | None:
    """Click the newest article using the page map first, then dated interactables."""
    url = str(snapshot.get("url") or "")
    if not (is_content_listing_url(url) or query_implies_recency(query)):
        return None

    understanding = page_understanding_from_snapshot(snapshot)
    feed = newest_feed_item(understanding)
    if feed:
        matched = match_feed_item_interactable(feed, snapshot.get("interactables") or [])
        if matched:
            label = str(feed.get("title") or matched.get("text") or matched.get("aria") or "")[:80]
            published = parse_content_date(
                " ".join(str(feed.get(key) or "") for key in ("date", "byline", "title"))
            )
            date_note = f" dated {published.isoformat()}" if published else ""
            shown = str(feed.get("date") or "").strip()
            if shown:
                date_note = f" ({shown})"
            return {
                "action": "click",
                "target_id": str(matched["id"]),
                "reason": (
                    f'Open the newest mapped feed item "{label}"{date_note} '
                    "(from page_understanding.feed_items)."
                ),
                "from_page_map": True,
                "feed_title": label,
                "feed_date": shown or None,
            }

    best: dict[str, Any] | None = None
    best_score = 0
    current_path = urlsplit(url).path.rstrip("/")
    for dom_index, raw in enumerate(snapshot.get("interactables") or []):
        if not isinstance(raw, dict) or raw.get("disabled") or not raw.get("id"):
            continue
        kind = str(raw.get("kind") or "").lower()
        if kind not in {"link", "button", "menuitem", "blz-button"}:
            continue
        href = str(raw.get("href") or "").strip()
        if not href:
            continue
        href_path = urlsplit(href).path.rstrip("/")
        if href_path == current_path:
            continue
        if not any(hint in href_path for hint in _CONTENT_PATH_HINTS):
            continue
        score = score_content_link(raw, query, dom_index=dom_index)
        if score > best_score:
            best_score = score
            best = raw
    if best and best_score >= min_score:
        label = str(best.get("text") or best.get("title") or best.get("aria") or "")[:60]
        published = element_publication_date(best)
        date_note = f" dated {published.isoformat()}" if published else ""
        return {
            "action": "click",
            "target_id": str(best["id"]),
            "reason": f"Open the newest relevant article link ({label}{date_note})",
            "from_page_map": False,
        }
    return None


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
    nav_hits = sum(1 for marker in _NAV_SHELL_MARKERS if marker in blob)
    if nav_hits >= 2 and detail_hits < 1 and bullet_count < 2 and fixed_hits < 2:
        return False
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
    preferred_domains: set[str] | None = None,
) -> bool:
    """True when collected text answers the goal from an acceptable source."""
    if not page_matches_query(text, query):
        return False
    preferred = preferred_domains or parse_user_preferred_domains(query)
    if preferred and url_on_preferred_source(source_url, preferred):
        return page_has_substantive_content(text, query)
    domains = publisher_domains or set()
    if not domains:
        return True
    if url_on_publisher_domain(source_url, domains):
        if is_secondary_host(source_url):
            return page_has_substantive_content(text, query)
        if is_publisher_content_url(source_url):
            if is_content_listing_url(source_url):
                return page_has_substantive_content(text, query)
            return True
        return page_has_substantive_content(text, query)
    routes = publisher_routes or set()
    if preferred:
        return page_has_substantive_content(text, query)
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
