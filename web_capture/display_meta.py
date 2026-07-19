"""Parse dates, authors, and bylines from visible page copy.

Site-agnostic: grounded in text a user would read (e.g. "Posted 1 day ago by Ada"),
not CSS class names or site-specific selectors.
"""

from __future__ import annotations

import re
from typing import Any

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

_RELATIVE_DATE = re.compile(
    r"\b(?:(?:an?|\d+)\s+(?:minute|hour|day|week|month|year)s?\s+ago|today|yesterday|just now)\b",
    re.IGNORECASE,
)
_ABSOLUTE_DATES = [
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
]
_BYLINE = re.compile(
    r"\b(?:posted|published|updated|written)\b"
    r"(?:\s+(?P<date>"
    r"(?:an?|\d+)\s+(?:minute|hour|day|week|month|year)s?\s+ago|"
    r"today|yesterday|just now|"
    rf"{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}|"
    r"\d{4}-\d{2}-\d{2}"
    r"))?"
    r"(?:\s+by\s+(?P<author>[^\n|,;]{2,60}))?",
    re.IGNORECASE,
)
_BY_AUTHOR = re.compile(
    r"\bby\s+([A-Z][\w.'\-]{1,40}(?:\s+[A-Z][\w.'\-]{1,40}){0,2})\b"
)
def looks_like_date(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 48:
        return False
    if _RELATIVE_DATE.fullmatch(text):
        return True
    # Short phrases that are essentially just the relative date.
    if _RELATIVE_DATE.search(text) and len(text) <= 40 and len(text.split()) <= 6:
        return True
    for pattern in _ABSOLUTE_DATES:
        if pattern.fullmatch(text):
            return True
        if pattern.search(text) and len(text) <= 40 and len(text.split()) <= 6:
            return True
    return False


def is_meta_line(value: str) -> bool:
    """True when the line is primarily a byline/date, not a headline that mentions a date."""
    text = str(value or "").strip()
    if not text:
        return True
    if re.match(r"^(?:posted|published|updated|written)\b", text, re.IGNORECASE):
        return True
    if re.match(r"^by\s+\S+", text, re.IGNORECASE):
        return True
    if looks_like_date(text) and len(text.split()) <= 6:
        return True
    byline = _BYLINE.fullmatch(text) or (
        _BYLINE.match(text) and len(text) <= 90 and len(text.split()) <= 12
    )
    return bool(byline)


def strip_meta_noise(text: str) -> str:
    """Remove visible byline/date fragments so a headline can be recovered."""
    cleaned = str(text or "")
    cleaned = _BYLINE.sub(" ", cleaned)
    cleaned = _RELATIVE_DATE.sub(" ", cleaned)
    for pattern in _ABSOLUTE_DATES:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\bby\s+[A-Z][\w.'\-]{1,40}(?:\s+[A-Z][\w.'\-]{1,40}){0,2}\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -–—|•")


def extract_dates(text: str, *, limit: int = 4) -> list[str]:
    raw = str(text or "")
    out: list[str] = []
    seen: set[str] = set()

    def _add(match: str) -> None:
        value = re.sub(r"\s+", " ", match).strip()
        if not value or not looks_like_date(value):
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(value[:48])

    for match in _RELATIVE_DATE.finditer(raw):
        _add(match.group(0))
        if len(out) >= limit:
            return out
    for pattern in _ABSOLUTE_DATES:
        for match in pattern.finditer(raw):
            _add(match.group(0))
            if len(out) >= limit:
                return out
    return out


def extract_authors(text: str, *, limit: int = 3) -> list[str]:
    raw = str(text or "")
    out: list[str] = []
    seen: set[str] = set()
    for match in _BYLINE.finditer(raw):
        author = re.sub(r"\s+", " ", str(match.group("author") or "")).strip(" .,-")
        if not author or looks_like_date(author):
            continue
        key = author.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(author[:60])
        if len(out) >= limit:
            return out
    for match in _BY_AUTHOR.finditer(raw):
        author = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
        if not author or looks_like_date(author) or len(author) < 2:
            continue
        key = author.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(author[:60])
        if len(out) >= limit:
            return out
    return out


def extract_byline(text: str) -> str | None:
    raw = str(text or "")
    match = _BYLINE.search(raw)
    if not match:
        return None
    snippet = re.sub(r"\s+", " ", match.group(0)).strip()
    return snippet[:160] or None


def preferred_display_title(text: str, *, limit: int = 120) -> str | None:
    """Pick a headline-like line from visible card copy, skipping bylines/dates."""
    raw = str(text or "").strip()
    if not raw:
        return None
    parts = re.split(r"[\n•|]+", raw)
    for part in parts:
        candidate = re.sub(r"\s+", " ", part).strip()
        if not candidate:
            continue
        if is_meta_line(candidate):
            continue
        # Headline and byline often share one visual line — strip the meta tail.
        candidate = strip_meta_noise(candidate) or candidate
        if len(candidate) < 8 or is_meta_line(candidate) or looks_like_date(candidate):
            continue
        # Bare author names are not titles.
        if len(candidate.split()) == 1 and extract_authors(f"by {candidate}"):
            continue
        return candidate[:limit]
    cleaned = strip_meta_noise(raw) or re.sub(r"\s+", " ", raw).strip()
    if cleaned and not is_meta_line(cleaned) and len(cleaned) >= 8:
        return cleaned[:limit]
    return None


def parse_display_meta(text: str) -> dict[str, Any]:
    raw = str(text or "")
    dates = extract_dates(raw)
    authors = extract_authors(raw)
    byline = extract_byline(raw)
    return {
        "dates": dates or None,
        "authors": authors or None,
        "byline": byline,
    }


def enrich_item_display_meta(item: dict[str, Any]) -> dict[str, Any]:
    """Fill dates/authors/byline from whatever visible text the item already carries."""
    row = dict(item)
    nearby = str(row.get("nearby_text") or "").strip()
    # Long nearby_text often spans sibling cards and bleeds other articles' dates.
    local_nearby = nearby if len(nearby) <= 160 else ""
    chunks = [
        str(row.get("title") or ""),
        str(row.get("text") or ""),
        str(row.get("byline") or ""),
        local_nearby,
        " ".join(str(d) for d in (row.get("dates") or []) if d),
    ]
    blob = " ".join(chunk for chunk in chunks if chunk.strip())
    meta = parse_display_meta(blob)

    existing_dates = [
        str(d).strip()
        for d in (row.get("dates") or [])
        if str(d).strip() and looks_like_date(str(d))
    ]
    merged_dates = existing_dates[:]
    for date in meta.get("dates") or []:
        if date.lower() not in {d.lower() for d in merged_dates}:
            merged_dates.append(date)
    if merged_dates:
        row["dates"] = merged_dates[:4]
    elif row.get("dates"):
        # Drop non-date values that slipped in (e.g. author names).
        row["dates"] = None

    if meta.get("authors") and not row.get("authors"):
        row["authors"] = meta["authors"]
    if meta.get("byline") and not row.get("byline"):
        row["byline"] = meta["byline"]

    title = str(row.get("title") or "").strip()
    text = str(row.get("text") or "").strip()
    if title and (is_meta_line(title) or (looks_like_date(title) is False and extract_authors(f"by {title}") and len(title.split()) == 1)):
        better = preferred_display_title(blob)
        if better and better.lower() != title.lower():
            row["title"] = better
            if not text or text == title:
                row["text"] = better
    elif text and is_meta_line(text) and len(text.split()) <= 6:
        better = preferred_display_title(blob)
        if better:
            row["text"] = better
            row.setdefault("title", better)

    # Author-only labels are not useful primary names when a byline/date exists.
    label = str(row.get("text") or row.get("title") or "").strip()
    authors = row.get("authors") if isinstance(row.get("authors"), list) else []
    if (
        label
        and authors
        and label.lower() == str(authors[0]).lower()
        and (row.get("byline") or row.get("dates"))
    ):
        better = preferred_display_title(blob)
        if better and better.lower() != label.lower():
            row["text"] = better
            row["title"] = better

    return row
