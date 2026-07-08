from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ui_test.exploration_registry import load_pages_registry, save_pages_registry
from ui_test.project_paths import agent_dir

SITE_MAP_FILE = "exploration.yaml"
REGISTRY_VERSION = 3


def site_map_path(project: Path) -> Path:
    from ui_test.exploration_registry import exploration_path

    return exploration_path(project)


def _path_key(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return path.rstrip("/") or "/"


def _build_contains(visible_content: dict[str, Any]) -> list[str]:
    """Semantic descriptions of what lives on a page — not current data values."""
    contains: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        text = line.strip()
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        contains.append(text)

    heading = str(visible_content.get("heading") or "").strip()
    if heading and not heading.startswith("http"):
        add(f"Page: {heading}")

    for h in visible_content.get("headings") or []:
        h_str = str(h).strip()
        if h_str and h_str != heading:
            add(f"Section: {h_str}")

    for section in visible_content.get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if title:
            add(f"Section: {title}")

    for table in visible_content.get("tables") or []:
        if not isinstance(table, dict):
            continue
        headers = [str(h).strip() for h in (table.get("headers") or []) if str(h).strip()]
        if headers:
            add(f"Table with columns: {', '.join(headers[:10])}")
        else:
            row_count = len(table.get("rows") or [])
            if row_count:
                add(f"Data table ({row_count} columns detected)")

    metrics = visible_content.get("metrics") or []
    if metrics:
        labels = [str(m.get("label") or "").strip() for m in metrics if isinstance(m, dict)]
        labels = [l for l in labels if l]
        if labels:
            add(f"Metrics displayed: {', '.join(labels[:8])}")

    lists = visible_content.get("lists") or []
    if lists:
        total = sum(len(lst.get("items") or []) for lst in lists if isinstance(lst, dict))
        if total:
            add(f"List content ({total} item(s))")

    if visible_content.get("empty_message"):
        add("May show an empty state when there is no data")

    return contains[:20]


def _structural_features(contains: list[str], visible_content: dict[str, Any]) -> list[str]:
    features: list[str] = []
    if any("table" in c.lower() for c in contains):
        features.append("table")
    if visible_content.get("metrics"):
        features.append("metrics")
    if visible_content.get("lists"):
        features.append("list")
    if visible_content.get("sections"):
        features.append("sections")
    if visible_content.get("empty_message"):
        features.append("empty_state")
    return features


def _content_summary(contains: list[str], features: list[str]) -> str:
    if contains:
        return " | ".join(contains[:4])
    if features:
        return f"Has: {', '.join(features)}"
    return ""


def _page_snapshot(visible_content: dict[str, Any]) -> dict[str, Any]:
    contains = _build_contains(visible_content)
    features = _structural_features(contains, visible_content)
    return {
        "contains": contains,
        "features": features,
        "summary": _content_summary(contains, features),
    }


def _content_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if not before:
        return bool(after)
    if str(before.get("summary") or "") != str(after.get("summary") or ""):
        return True
    return json.dumps(before.get("contains") or [], sort_keys=True) != json.dumps(
        after.get("contains") or [], sort_keys=True
    )


def semantic_summary_from_visible(visible_content: dict[str, Any]) -> str:
    return str(_page_snapshot(visible_content).get("summary") or "")


def load_site_map(project: Path) -> dict[str, Any]:
    data = load_pages_registry(project)
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("pages", {})
    return data


def save_site_map(project: Path, data: dict[str, Any]) -> Path:
    payload = dict(data)
    payload["version"] = REGISTRY_VERSION
    return save_pages_registry(project, payload)


def merge_page_discovery(
    registry: dict[str, Any],
    *,
    url: str,
    title: str,
    visible_content: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, int]:
    """Merge semantic page catalog entry — what lives here, not current values."""
    pages = dict(registry.get("pages") or {})
    key = _path_key(url)
    existing = dict(pages.get(key) or {})

    content_before = dict(existing.get("content") or {})
    content_after = content_before
    new_capabilities = 0
    if visible_content:
        content_after = _page_snapshot(visible_content)
        before_set = set(content_before.get("contains") or [])
        after_set = set(content_after.get("contains") or [])
        new_capabilities = len(after_set - before_set)

    changed = not existing or _content_changed(content_before, content_after)

    page_entry: dict[str, Any] = {
        "url": url,
        "path": key,
        "title": title or existing.get("title") or "",
        "discovered_at": existing.get("discovered_at") or datetime.now(timezone.utc).isoformat(),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "content": content_after,
    }
    features = content_after.get("features") or []
    if features:
        page_entry["features"] = features

    pages[key] = page_entry
    registry["pages"] = pages
    return registry, changed, new_capabilities


def registry_summary_for_agent(registry: dict[str, Any], *, max_pages: int = 30) -> str:
    pages = registry.get("pages") or {}
    lines: list[str] = [
        "What lives where (semantic catalog — describes page capabilities, not live data values):"
    ]
    for path, info in list(pages.items())[:max_pages]:
        if not isinstance(info, dict):
            continue
        content = info.get("content") or {}
        contains = content.get("contains") or []
        summary = str(content.get("summary") or "").strip()
        line = f"- {path}: {info.get('title') or '(no title)'}"
        if summary:
            line += f" — {summary}"
        elif contains:
            line += f" — {'; '.join(str(c) for c in contains[:3])}"
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else "(empty — first run will catalog pages)"


def site_map_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_pages = before.get("pages") or {}
    after_pages = after.get("pages") or {}
    new_pages: list[str] = []
    updated_pages: list[dict[str, Any]] = []
    for path, info in after_pages.items():
        if path not in before_pages:
            new_pages.append(str(path))
            continue
        if not isinstance(info, dict):
            continue
        before_info = before_pages.get(path) or {}
        if not isinstance(before_info, dict):
            continue
        content_changed = _content_changed(
            dict(before_info.get("content") or {}),
            dict(info.get("content") or {}),
        )
        if content_changed:
            before_contains = set((before_info.get("content") or {}).get("contains") or [])
            after_contains = set((info.get("content") or {}).get("contains") or [])
            updated_pages.append(
                {
                    "path": str(path),
                    "new_capabilities": len(after_contains - before_contains),
                    "content_updated": True,
                }
            )
    return {
        "new_pages": new_pages,
        "updated_pages": updated_pages,
        "total_pages": len(after_pages),
        "new_capabilities": sum(u.get("new_capabilities", 0) for u in updated_pages),
        "content_updates": len(updated_pages),
    }


def find_known_path(registry: dict[str, Any], keyword: str) -> str | None:
    """Find page path by matching keyword against semantic catalog."""
    kw = keyword.lower()
    if len(kw) < 3:
        return None

    pages = registry.get("pages") or {}
    best_path = ""
    best_score = 0

    for path in pages:
        if kw in str(path).lower():
            return str(path)

    for path, info in pages.items():
        if not isinstance(info, dict):
            continue
        score = 0
        content = info.get("content") or {}
        for item in content.get("contains") or []:
            if kw in str(item).lower():
                score += 4
        summary = str(content.get("summary") or "").lower()
        if kw in summary:
            score += 3
        title = str(info.get("title") or "").lower()
        if kw in title:
            score += 2
        if score > best_score:
            best_score = score
            best_path = str(path)

    return best_path or None
