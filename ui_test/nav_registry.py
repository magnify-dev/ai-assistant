from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ui_test.interactables import (
    element_key,
    normalize_href,
    normalize_interactable,
    should_store_interactable,
)
from ui_test.exploration_registry import load_navigation, save_navigation
from ui_test.project_paths import agent_dir

NAV_FILE = "exploration.yaml"
NAV_VERSION = 1
MAX_INTERACTABLES_PER_ROUTE = 60
GLOBAL_NAV_MIN_ROUTES = 2


def nav_tree_path(project: Path) -> Path:
    from ui_test.exploration_registry import exploration_path

    return exploration_path(project)


def _path_key(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.path or "/").rstrip("/") or "/"


def _via_key(via: dict[str, Any]) -> str:
    return "|".join(
        [
            str(via.get("kind") or ""),
            str(via.get("text") or "").strip().lower()[:80],
            normalize_href(str(via.get("href") or "")),
        ]
    )


def load_nav_tree(project: Path) -> dict[str, Any]:
    data = load_navigation(project)
    data.setdefault("version", NAV_VERSION)
    data.setdefault("edges", [])
    data.setdefault("global_nav", [])
    data.setdefault("routes", {})
    return data


def save_nav_tree(project: Path, data: dict[str, Any]) -> Path:
    payload = dict(data)
    payload["version"] = NAV_VERSION
    _recompute_global_nav(payload)
    return save_navigation(project, payload)


def _recompute_global_nav(tree: dict[str, Any]) -> None:
    """Links that appear on multiple routes become global shell navigation."""
    link_counts: dict[str, dict[str, Any]] = {}
    routes = tree.get("routes") or {}
    for route_info in routes.values():
        if not isinstance(route_info, dict):
            continue
        for el in route_info.get("interactables") or []:
            if not isinstance(el, dict) or el.get("kind") != "link":
                continue
            href = normalize_href(str(el.get("href") or ""))
            if not href:
                continue
            key = element_key(el)
            if key not in link_counts:
                link_counts[key] = {"count": 0, "el": el}
            link_counts[key]["count"] += 1

    global_nav: list[dict[str, Any]] = []
    for entry in link_counts.values():
        if entry["count"] >= GLOBAL_NAV_MIN_ROUTES:
            global_nav.append(dict(entry["el"]))
    global_nav.sort(key=lambda e: str(e.get("text") or e.get("href") or ""))
    tree["global_nav"] = global_nav[:30]


def merge_nav_discovery(
    tree: dict[str, Any],
    *,
    path: str,
    title: str,
    interactables: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool, int]:
    """Merge interactables discovered on a route into the navigation tree."""
    routes = dict(tree.get("routes") or {})
    key = _path_key(path)
    existing = dict(routes.get(key) or {})
    known = {element_key(e) for e in (existing.get("interactables") or []) if isinstance(e, dict)}
    merged = list(existing.get("interactables") or [])
    new_count = 0

    for el in interactables:
        if not isinstance(el, dict) or not should_store_interactable(el):
            continue
        normalized = normalize_interactable(el)
        ek = element_key(normalized)
        if ek in known:
            continue
        known.add(ek)
        href = str(normalized.get("href") or "")
        if normalized.get("kind") == "link" and href:
            normalized["reaches"] = normalize_href(href)
        merged.append(normalized)
        new_count += 1

    if len(merged) > MAX_INTERACTABLES_PER_ROUTE:
        merged = merged[:MAX_INTERACTABLES_PER_ROUTE]

    changed = new_count > 0 or not existing
    routes[key] = {
        "path": key,
        "title": title or existing.get("title") or "",
        "discovered_at": existing.get("discovered_at") or datetime.now(timezone.utc).isoformat(),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "interactables": merged,
    }
    tree["routes"] = routes
    return tree, changed, new_count


def record_nav_transition(
    tree: dict[str, Any],
    *,
    from_path: str,
    to_path: str,
    via: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Record a verified transition between routes (click or navigate)."""
    src = _path_key(from_path)
    dst = normalize_href(to_path) if to_path.startswith("/") else _path_key(to_path)
    if src == dst:
        return tree, False

    via_entry = {
        "kind": via.get("kind"),
        "text": via.get("text"),
        "href": normalize_href(str(via.get("href") or "")) or via.get("href"),
        "test_id": via.get("test_id"),
    }
    via_entry = {k: v for k, v in via_entry.items() if v}

    edges = list(tree.get("edges") or [])
    edge_key = f"{src}|{dst}|{_via_key(via_entry)}"
    existing_keys = {
        f"{e.get('from')}|{e.get('to')}|{_via_key(e.get('via') or {})}"
        for e in edges
        if isinstance(e, dict)
    }
    if edge_key in existing_keys:
        return tree, False

    edges.append(
        {
            "from": src,
            "to": dst,
            "via": via_entry,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    tree["edges"] = edges[-200:]

    routes = dict(tree.get("routes") or {})
    route = dict(routes.get(src) or {"path": src})
    reaches = dict(route.get("verified_reaches") or {})
    reaches[dst] = {"via": via_entry}
    route["verified_reaches"] = reaches
    routes[src] = route
    tree["routes"] = routes
    return tree, True


def record_interactable_click(
    tree: dict[str, Any],
    *,
    path: str,
    via: dict[str, Any],
) -> dict[str, Any]:
    """Track which controls were clicked on a route (including same-URL reveals)."""
    key = _path_key(path)
    routes = dict(tree.get("routes") or {})
    route = dict(routes.get(key) or {"path": key})
    clicked = list(route.get("clicked") or [])
    ek = _via_key(via)
    if ek and ek not in clicked:
        clicked.append(ek)
    route["clicked"] = clicked[-80:]
    routes[key] = route
    tree["routes"] = routes
    return tree


def nav_summary_for_agent(tree: dict[str, Any], *, max_routes: int = 25) -> str:
    lines: list[str] = []
    global_nav = tree.get("global_nav") or []
    if global_nav:
        nav_bits = []
        for el in global_nav[:12]:
            if not isinstance(el, dict):
                continue
            text = str(el.get("text") or "").strip()
            href = str(el.get("href") or "").strip()
            if text and href:
                nav_bits.append(f"{text}→{href}")
        if nav_bits:
            lines.append(f"Global nav: {', '.join(nav_bits)}")

    routes = tree.get("routes") or {}
    for path, info in list(routes.items())[:max_routes]:
        if not isinstance(info, dict):
            continue
        links: list[str] = []
        actions: list[str] = []
        for el in info.get("interactables") or []:
            if not isinstance(el, dict):
                continue
            text = str(el.get("text") or el.get("aria") or "").strip()
            kind = str(el.get("kind") or "")
            href = str(el.get("href") or el.get("reaches") or "").strip()
            if kind == "link" and text and href:
                links.append(f"{text}→{href}")
            elif text and kind in ("button", "input"):
                actions.append(text)
        reaches = info.get("verified_reaches") or {}
        reach_bits = [f"{dst} via {v.get('via', {}).get('text', '?')}" for dst, v in list(reaches.items())[:4] if isinstance(v, dict)]
        line = f"- {path}: {info.get('title') or '(no title)'}"
        if links:
            line += f" | links: {', '.join(links[:8])}"
        if actions:
            line += f" | actions: {', '.join(actions[:6])}"
        if reach_bits:
            line += f" | verified: {', '.join(reach_bits)}"
        lines.append(line)

    edges = tree.get("edges") or []
    if edges and not routes:
        for edge in edges[-10:]:
            if isinstance(edge, dict):
                via = edge.get("via") or {}
                lines.append(
                    f"- {edge.get('from')} → {edge.get('to')} via {via.get('text') or via.get('href') or '?'}"
                )

    return "\n".join(lines) if lines else "(empty — exploration builds the navigation tree)"


def nav_tree_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_routes = before.get("routes") or {}
    after_routes = after.get("routes") or {}
    new_routes: list[str] = []
    updated_routes: list[dict[str, Any]] = []
    for path, info in after_routes.items():
        if path not in before_routes:
            new_routes.append(str(path))
            continue
        if not isinstance(info, dict):
            continue
        before_info = before_routes.get(path) or {}
        before_n = len(before_info.get("interactables") or []) if isinstance(before_info, dict) else 0
        after_n = len(info.get("interactables") or [])
        if after_n > before_n:
            updated_routes.append({"path": str(path), "new_interactables": after_n - before_n})
    new_edges = max(0, len(after.get("edges") or []) - len(before.get("edges") or []))
    return {
        "new_routes": new_routes,
        "updated_routes": updated_routes,
        "total_routes": len(after_routes),
        "new_interactables": sum(u.get("new_interactables", 0) for u in updated_routes),
        "new_edges": new_edges,
    }


def find_nav_path(tree: dict[str, Any], keyword: str) -> str | None:
    """Find route path by matching keyword against nav labels and hrefs."""
    kw = keyword.lower()
    if len(kw) < 3:
        return None
    for el in tree.get("global_nav") or []:
        if not isinstance(el, dict):
            continue
        text = str(el.get("text") or "").lower()
        href = normalize_href(str(el.get("href") or ""))
        if kw in text or kw in href.lower():
            return href or None
    for path, info in (tree.get("routes") or {}).items():
        if kw in str(path).lower():
            return str(path)
        if not isinstance(info, dict):
            continue
        for el in info.get("interactables") or []:
            if not isinstance(el, dict):
                continue
            text = str(el.get("text") or "").lower()
            href = normalize_href(str(el.get("href") or ""))
            if kw in text and href:
                return href
    return None
