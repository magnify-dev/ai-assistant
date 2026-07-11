from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

WEB_STORE_VERSION = 1
WEB_DIR = "web"
INDEX_FILE = "index.yaml"
FACTS_FILE = "facts.yaml"
CACHE_DIR = "cache"
RUNS_DIR = "runs"
SESSIONS_DIR = "sessions"
VISIT_GRAPH_FILE = "visit-graph.yaml"


def web_store_dir(project: Path) -> Path:
    return project / ".agent" / WEB_DIR


def index_path(project: Path) -> Path:
    return web_store_dir(project) / INDEX_FILE


def facts_path(project: Path) -> Path:
    return web_store_dir(project) / FACTS_FILE


def cache_dir(project: Path) -> Path:
    return web_store_dir(project) / CACHE_DIR


def run_state_path(project: Path, run_id: str) -> Path:
    return web_store_dir(project) / RUNS_DIR / f"{run_id}.yaml"


def session_state_path(project: Path, session_id: str) -> Path:
    return web_store_dir(project) / SESSIONS_DIR / f"{session_id}.yaml"


def visit_graph_path(project: Path) -> Path:
    return web_store_dir(project) / VISIT_GRAPH_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (yaml.YAMLError, OSError):
        return None


def _save_yaml(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)
    return path


def save_run_state(project: Path, run_id: str, state: dict[str, Any]) -> Path:
    return _save_yaml(
        run_state_path(project, run_id),
        {"version": WEB_STORE_VERSION, "updated_at": _now_iso(), **state, "run_id": run_id},
    )


def save_session_state(project: Path, session_id: str, state: dict[str, Any]) -> Path:
    return _save_yaml(
        session_state_path(project, session_id),
        {"version": WEB_STORE_VERSION, "updated_at": _now_iso(), **state, "session_id": session_id},
    )


def load_visit_graph(project: Path) -> dict[str, Any]:
    data = _load_yaml(visit_graph_path(project)) or {}
    nodes = data.get("nodes")
    edges = data.get("edges")
    return {
        "version": data.get("version", WEB_STORE_VERSION),
        "nodes": nodes if isinstance(nodes, dict) else {},
        "edges": edges if isinstance(edges, list) else [],
    }


def record_visit(
    project: Path,
    *,
    url: str,
    title: str = "",
    source_url: str = "",
    action: str = "navigate",
    step_id: str = "",
) -> dict[str, Any]:
    graph = load_visit_graph(project)
    normalized = normalize_url(url)
    nodes = dict(graph["nodes"])
    existing = nodes.get(normalized) if isinstance(nodes.get(normalized), dict) else {}
    nodes[normalized] = {
        **existing,
        "url": normalized,
        "title": title or existing.get("title") or "",
        "last_visited_at": _now_iso(),
        "visit_count": int(existing.get("visit_count") or 0) + 1,
    }
    edges = list(graph["edges"])
    if source_url:
        try:
            source = normalize_url(source_url)
        except ValueError:
            source = ""
        edge = {
            "source": source,
            "target": normalized,
            "action": action,
            "step_id": step_id,
        }
        if source and edge not in edges:
            edges.append(edge)
    updated = {**graph, "nodes": nodes, "edges": edges[-1000:]}
    _save_yaml(visit_graph_path(project), {**updated, "updated_at": _now_iso()})
    return updated


def normalize_url(url: str) -> str:
    raw = url.strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must start with http:// or https://")
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def domain_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def empty_index() -> dict[str, Any]:
    return {"version": WEB_STORE_VERSION, "pages": {}}


def empty_facts() -> dict[str, Any]:
    return {"version": WEB_STORE_VERSION, "facts": []}


def load_index(project: Path) -> dict[str, Any]:
    data = _load_yaml(index_path(project))
    if not data:
        return empty_index()
    pages = data.get("pages")
    return {
        "version": data.get("version", WEB_STORE_VERSION),
        "updated_at": data.get("updated_at"),
        "pages": pages if isinstance(pages, dict) else {},
    }


def load_facts(project: Path) -> dict[str, Any]:
    data = _load_yaml(facts_path(project))
    if not data:
        return empty_facts()
    facts = data.get("facts")
    return {
        "version": data.get("version", WEB_STORE_VERSION),
        "updated_at": data.get("updated_at"),
        "facts": facts if isinstance(facts, list) else [],
    }


def save_index(project: Path, index: dict[str, Any]) -> Path:
    payload = empty_index()
    payload["updated_at"] = _now_iso()
    payload["pages"] = index.get("pages") if isinstance(index.get("pages"), dict) else {}
    return _save_yaml(index_path(project), payload)


def save_facts(project: Path, facts_doc: dict[str, Any]) -> Path:
    payload = empty_facts()
    payload["updated_at"] = _now_iso()
    payload["facts"] = facts_doc.get("facts") if isinstance(facts_doc.get("facts"), list) else []
    return _save_yaml(facts_path(project), payload)


def cache_page_markdown(project: Path, page_hash: str, markdown: str) -> Path:
    target = cache_dir(project) / f"{page_hash}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def read_cached_markdown(project: Path, page_hash: str) -> str:
    path = cache_dir(project) / f"{page_hash}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def merge_page_index(
    index: dict[str, Any],
    *,
    url: str,
    title: str,
    summary: str,
    fetch_tier: int,
    page_hash: str,
    search_query: str = "",
) -> tuple[dict[str, Any], bool]:
    key = normalize_url(url)
    pages = dict(index.get("pages") or {})
    existing = pages.get(key) if isinstance(pages.get(key), dict) else {}
    changed = (
        existing.get("content_hash") != page_hash
        or existing.get("summary") != summary
        or existing.get("title") != title
    )
    pages[key] = {
        "url": key,
        "domain": domain_from_url(key),
        "title": title.strip() or existing.get("title") or key,
        "summary": summary.strip() or existing.get("summary") or "",
        "fetch_tier": fetch_tier,
        "content_hash": page_hash,
        "fetched_at": _now_iso(),
        "search_query": search_query or existing.get("search_query") or "",
        "visit_count": int(existing.get("visit_count") or 0) + 1,
    }
    return {**index, "pages": pages}, changed


def merge_facts(
    facts_doc: dict[str, Any],
    new_facts: list[dict[str, Any]],
    *,
    research_query: str,
) -> tuple[dict[str, Any], int]:
    existing = list(facts_doc.get("facts") or [])
    seen = {
        (
            str(item.get("field") or ""),
            str(item.get("value") or ""),
            str(item.get("source_url") or ""),
        )
        for item in existing
        if isinstance(item, dict)
    }
    added = 0
    for fact in new_facts:
        if not isinstance(fact, dict):
            continue
        key = (
            str(fact.get("field") or ""),
            str(fact.get("value") or ""),
            str(fact.get("source_url") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        existing.append(
            {
                "id": fact.get("id") or content_hash(f"{key[0]}|{key[1]}|{key[2]}"),
                "field": key[0],
                "value": key[1],
                "source_url": normalize_url(key[2]) if key[2] else "",
                "quote": str(fact.get("quote") or "").strip(),
                "source_session_id": str(fact.get("source_session_id") or ""),
                "source_step_id": str(fact.get("source_step_id") or ""),
                "source_snapshot_id": str(fact.get("source_snapshot_id") or ""),
                "extracted_at": _now_iso(),
                "research_query": research_query,
            }
        )
        added += 1
    return {**facts_doc, "facts": existing[-500:]}, added


def index_summary_for_agent(index: dict[str, Any], *, max_pages: int = 20) -> str:
    pages = index.get("pages") or {}
    lines = ["Known web pages (semantic catalog):"]
    for url, info in list(pages.items())[:max_pages]:
        if not isinstance(info, dict):
            continue
        title = str(info.get("title") or url)
        summary = str(info.get("summary") or "").strip()
        lines.append(f"- {title} ({url})")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines) if len(lines) > 1 else "(no pages indexed yet)"


def facts_summary_for_agent(
    facts_doc: dict[str, Any],
    *,
    query: str = "",
    max_facts: int = 30,
) -> str:
    facts = facts_doc.get("facts") or []
    if not facts:
        return "(no extracted facts yet)"

    query_tokens = {t for t in re.split(r"\W+", query.lower()) if len(t) > 2}
    scored: list[tuple[int, dict[str, Any]]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        blob = " ".join(
            str(fact.get(key) or "")
            for key in ("field", "value", "quote", "source_url", "research_query")
        ).lower()
        score = sum(1 for token in query_tokens if token in blob) if query_tokens else 0
        scored.append((score, fact))

    scored.sort(key=lambda row: row[0], reverse=True)
    lines = ["Extracted facts:"]
    for score, fact in scored[:max_facts]:
        if query_tokens and score == 0:
            continue
        field = str(fact.get("field") or "fact")
        value = str(fact.get("value") or "")
        source = str(fact.get("source_url") or "")
        lines.append(f"- {field}: {value} (source: {source})")
    return "\n".join(lines) if len(lines) > 1 else "(no matching facts)"
