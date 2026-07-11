from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ui_test.interactables import element_key
from ui_test.page_registry import find_known_path

_STOP_WORDS = frozenset(
    {
        "find",
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "which",
        "what",
        "when",
        "where",
        "report",
        "me",
        "most",
        "about",
        "using",
        "through",
        "your",
        "need",
        "discover",
        "write",
        "channel",
        "channels",
    }
)

_CONTENT_HINTS = re.compile(
    r"\b(patch|changelog|release\s*notes?|notes?|version|update)\b",
    re.IGNORECASE,
)


def task_keywords(task_text: str) -> list[str]:
    words = re.findall(r"[a-z]{4,}", task_text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in _STOP_WORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def task_mentions_hidden_content(task_text: str) -> bool:
    return bool(_CONTENT_HINTS.search(task_text))


def find_task_path_in_site_map(registry: dict[str, Any], task_text: str) -> tuple[str | None, str]:
    """Return (path, reason) if site map catalog suggests task data lives on a page."""
    kws = task_keywords(task_text)
    if not kws:
        return None, ""

    pages = registry.get("pages") or {}
    best_path = ""
    best_score = 0
    best_reason = ""

    for path, info in pages.items():
        if not isinstance(info, dict):
            continue
        content = info.get("content") or {}
        score = 0
        hits: list[str] = []
        for item in content.get("contains") or []:
            item_l = str(item).lower()
            for kw in kws:
                if kw in item_l:
                    score += 4
                    hits.append(kw)
        summary = str(content.get("summary") or "").lower()
        for kw in kws:
            if kw in summary:
                score += 2
                hits.append(kw)
        if score > best_score:
            best_score = score
            best_path = str(path)
            best_reason = f"site map mentions: {', '.join(sorted(set(hits)))}"

    if best_score >= 4 and best_path:
        return best_path, best_reason

    for kw in kws:
        known = find_known_path(registry, kw)
        if known:
            return known, f"site map keyword match: {kw}"
    return None, ""


def current_page_has_task_data(
    *,
    task_text: str,
    semantic_summary: str,
    page_snippet: str,
    visible_content: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Heuristic: does the current page likely contain data needed for the task?"""
    kws = task_keywords(task_text)
    if not kws:
        return False, ""

    blob_parts = [semantic_summary.lower(), page_snippet.lower()]
    visible = visible_content or {}
    for table in visible.get("tables") or []:
        if isinstance(table, dict):
            blob_parts.extend(str(h).lower() for h in (table.get("headers") or []))
            if table.get("rows"):
                blob_parts.append("table_data")
    blob = " ".join(blob_parts)
    hits = [kw for kw in kws if kw in blob]

    wants_metrics = any(w in task_text.lower() for w in ("view", "count", "most", "report", "analytic"))
    has_table = bool(visible.get("tables"))
    has_rows = any(
        isinstance(t, dict) and (t.get("rows") or [])
        for t in (visible.get("tables") or [])
    )

    if wants_metrics and has_table and has_rows:
        return True, "table with data visible on current page"

    if len(hits) >= 2 or (len(hits) >= 1 and has_rows):
        return True, f"current page matches: {', '.join(hits)}"

    return False, ""


_INTERACTION_PATTERN = re.compile(
    r"\b(click|press|toggle|drag|hover|submit|escape|modal|dialog|dismiss|re-open|reopen)\b",
    re.IGNORECASE,
)


def task_requires_interaction(task_text: str) -> bool:
    """True when the task asks for UI interactions (clicking, key presses, modals)
    rather than just reading data off a page."""
    return bool(_INTERACTION_PATTERN.search(task_text))


def history_has_interaction(step_history: list[str]) -> bool:
    """True if the exploration loop already performed at least one interaction step."""
    return any(
        s.lstrip().lower().startswith(("click", "fill", "press"))
        for s in step_history
    )


def path_key(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.path or "/").rstrip("/") or "/"


def _route_clicked_keys(nav_tree: dict[str, Any], current_path: str) -> set[str]:
    routes = nav_tree.get("routes") or {}
    route = routes.get(path_key(current_path)) if isinstance(routes.get(path_key(current_path)), dict) else {}
    return {str(k) for k in (route.get("clicked") or []) if k}


def pick_button_for_task(
    task_text: str,
    interactables: list[dict[str, Any]],
    nav_tree: dict[str, Any],
    current_path: str,
) -> dict[str, Any] | None:
    """Pick an unexplored button/tab that might reveal task content on the current page."""
    clicked = _route_clicked_keys(nav_tree, current_path)
    kws = task_keywords(task_text)
    candidates: list[tuple[int, int, str]] = []

    for i, el in enumerate(interactables):
        if not isinstance(el, dict):
            continue
        kind = str(el.get("kind") or "").lower()
        if kind not in ("button", "menuitem", "tab", "input", "link"):
            continue
        text = str(el.get("text") or el.get("aria") or "").strip()
        if not text:
            continue
        if element_key(el) in clicked:
            continue
        text_l = text.lower()
        score = 0
        for kw in kws:
            if kw in text_l:
                score += 5
        if _CONTENT_HINTS.search(text):
            score += 10
        if score > 0:
            candidates.append((score, i, text))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    _, index, text = candidates[0]
    return {
        "action": "click",
        "target": {"index": index, "text": text},
        "reason": f"Explore '{text}' on current page — may reveal task content",
    }


def pick_unexplored_button(
    interactables: list[dict[str, Any]],
    nav_tree: dict[str, Any],
    current_path: str,
) -> dict[str, Any] | None:
    """Click the next unexplored button/menu on the current page."""
    clicked = _route_clicked_keys(nav_tree, current_path)
    for i, el in enumerate(interactables):
        if not isinstance(el, dict):
            continue
        kind = str(el.get("kind") or "").lower()
        if kind not in ("button", "menuitem", "tab"):
            continue
        text = str(el.get("text") or el.get("aria") or "").strip()
        if not text:
            continue
        if element_key(el) in clicked:
            continue
        return {
            "action": "click",
            "target": {"index": i, "text": text},
            "reason": f"Try unexplored control '{text}' on this page",
        }
    return None


def pick_nav_action_to_path(
    target_path: str,
    current_path: str,
    interactables: list[dict[str, Any]],
    nav_tree: dict[str, Any],
) -> dict[str, Any] | None:
    """Pick a click action to reach target_path — never invent a URL."""
    target = path_key(target_path)
    current = path_key(current_path)
    if target == current:
        return None

    for el in interactables:
        if not isinstance(el, dict):
            continue
        href = str(el.get("href") or el.get("reaches") or "")
        if not href:
            continue
        el_path = path_key(href)
        if el_path == target:
            return {
                "action": "click",
                "target": {
                    "index": interactables.index(el),
                    "text": el.get("text"),
                    "href": el.get("href"),
                },
                "reason": f"Click link on page to reach {target}",
            }

    routes = nav_tree.get("routes") or {}
    route = routes.get(current) if isinstance(routes.get(current), dict) else None
    if isinstance(route, dict):
        reaches = route.get("verified_reaches") or {}
        if target in reaches:
            via = (reaches[target] or {}).get("via") or {}
            text = via.get("text")
            if text:
                for i, el in enumerate(interactables):
                    if str(el.get("text") or "") == str(text):
                        return {
                            "action": "click",
                            "target": {"index": i, "text": text, "href": el.get("href")},
                            "reason": f"Verified route {current} → {target}",
                        }

    return None
