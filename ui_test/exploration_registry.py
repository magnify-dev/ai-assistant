from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ui_test.project_paths import agent_dir

EXPLORATION_FILE = "exploration.yaml"
EXPLORATION_VERSION = 1
LEGACY_NAV_FILE = "cheatsheet-navigation.yaml"
LEGACY_SITE_MAP_FILE = "site-map.yaml"


def exploration_path(project: Path) -> Path:
    return agent_dir(project) / EXPLORATION_FILE


def empty_exploration() -> dict[str, Any]:
    return {
        "version": EXPLORATION_VERSION,
        "navigation": {
            "tree": [],
            "routes": {},
            "edges": [],
            "global_nav": [],
        },
        "pages": {},
    }


def _load_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (yaml.YAMLError, OSError):
        return None


def _migrate_legacy(project: Path) -> dict[str, Any] | None:
    base = agent_dir(project)
    nav_data = _load_yaml(base / LEGACY_NAV_FILE)
    site_data = _load_yaml(base / LEGACY_SITE_MAP_FILE)
    if not nav_data and not site_data:
        return None

    doc = empty_exploration()
    if nav_data:
        doc["navigation"] = {
            "tree": nav_data.get("tree") or [],
            "routes": nav_data.get("routes") or {},
            "edges": nav_data.get("edges") or [],
            "global_nav": nav_data.get("global_nav") or [],
        }
    if site_data:
        doc["pages"] = site_data.get("pages") or {}
    return doc


def build_simple_tree(navigation: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a simple hierarchical tree for UI display from routes and edges."""
    routes = navigation.get("routes") or {}
    if not isinstance(routes, dict):
        routes = {}

    children_map: dict[str, list[dict[str, str]]] = {}
    for edge in navigation.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if not src or not dst:
            continue
        route_info = routes.get(dst) if isinstance(routes.get(dst), dict) else {}
        via = edge.get("via") if isinstance(edge.get("via"), dict) else {}
        title = str(route_info.get("title") or via.get("text") or dst)
        bucket = children_map.setdefault(src, [])
        if not any(item["path"] == dst for item in bucket):
            bucket.append({"path": dst, "title": title})

    for path, info in routes.items():
        if not isinstance(info, dict):
            continue
        for el in info.get("interactables") or []:
            if not isinstance(el, dict) or el.get("kind") != "link":
                continue
            href = str(el.get("href") or el.get("reaches") or "")
            if not href.startswith("/"):
                continue
            bucket = children_map.setdefault(str(path), [])
            if any(item["path"] == href for item in bucket):
                continue
            bucket.append({"path": href, "title": str(el.get("text") or href)})

    def dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for item in sorted(items, key=lambda row: row["path"]):
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            out.append(item)
        return out

    def build_node(path: str, visited: set[str]) -> dict[str, Any]:
        info = routes.get(path) if isinstance(routes.get(path), dict) else {}
        title = str(info.get("title") or path)
        node: dict[str, Any] = {"path": path, "title": title}
        if path in visited:
            return node
        next_visited = set(visited)
        next_visited.add(path)
        children = dedupe(children_map.get(path, []))
        if children:
            node["children"] = [build_node(child["path"], next_visited) for child in children[:24]]
        return node

    roots: list[str] = []
    if isinstance(routes.get("/"), dict):
        roots.append("/")
    elif routes:
        roots.append(sorted(str(key) for key in routes.keys())[0])
    else:
        roots.append("/")
    return [build_node(root, set()) for root in roots[:4]]


def load_exploration(project: Path) -> dict[str, Any]:
    path = exploration_path(project)
    data = _load_yaml(path)
    if data and isinstance(data.get("navigation"), dict) and isinstance(data.get("pages"), dict):
        doc = empty_exploration()
        doc["version"] = data.get("version", EXPLORATION_VERSION)
        doc["updated_at"] = data.get("updated_at")
        doc["navigation"] = {
            "tree": data["navigation"].get("tree") or [],
            "routes": data["navigation"].get("routes") or {},
            "edges": data["navigation"].get("edges") or [],
            "global_nav": data["navigation"].get("global_nav") or [],
        }
        doc["pages"] = data.get("pages") or {}
        return doc

    migrated = _migrate_legacy(project)
    if migrated:
        save_exploration(project, migrated)
        return migrated
    return empty_exploration()


def save_exploration(project: Path, data: dict[str, Any]) -> Path:
    path = exploration_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = empty_exploration()
    payload["version"] = EXPLORATION_VERSION
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    navigation = data.get("navigation") if isinstance(data.get("navigation"), dict) else {}
    payload["navigation"] = {
        "tree": navigation.get("tree") or build_simple_tree(navigation),
        "routes": navigation.get("routes") or {},
        "edges": navigation.get("edges") or [],
        "global_nav": navigation.get("global_nav") or [],
    }
    payload["pages"] = data.get("pages") if isinstance(data.get("pages"), dict) else {}
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def load_navigation(project: Path) -> dict[str, Any]:
    navigation = load_exploration(project)["navigation"]
    return {
        "version": 1,
        "routes": navigation.get("routes") or {},
        "edges": navigation.get("edges") or [],
        "global_nav": navigation.get("global_nav") or [],
        "tree": navigation.get("tree") or [],
    }


def load_pages_registry(project: Path) -> dict[str, Any]:
    pages = load_exploration(project).get("pages") or {}
    return {"version": 3, "pages": pages}


def save_navigation(project: Path, navigation: dict[str, Any]) -> Path:
    doc = load_exploration(project)
    doc["navigation"] = {
        "tree": navigation.get("tree") or build_simple_tree(navigation),
        "routes": navigation.get("routes") or {},
        "edges": navigation.get("edges") or [],
        "global_nav": navigation.get("global_nav") or [],
    }
    return save_exploration(project, doc)


def save_pages_registry(project: Path, registry: dict[str, Any]) -> Path:
    doc = load_exploration(project)
    doc["pages"] = registry.get("pages") if isinstance(registry.get("pages"), dict) else {}
    return save_exploration(project, doc)
