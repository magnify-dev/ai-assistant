from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from web_capture.context import get_active_project


def maps_dir(project: Path) -> Path:
    return project / ".agent" / "web-capture" / "maps"


def training_path(project: Path) -> Path:
    return project / ".agent" / "web-capture" / "training.jsonl"


def site_key(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "unknown").lower()
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}"


def _slug(value: str, limit: int = 64) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:limit] or "page"


def map_file_for_url(project: Path, url: str) -> Path:
    key = _slug(site_key(url))
    return maps_dir(project) / f"{key}.json"


def element_signature(element: dict[str, Any]) -> str:
    href = str(element.get("href") or "").strip()
    href_path = urlparse(href).path if href.startswith(("http://", "https://", "/")) else href
    parts = [
        str(element.get("kind") or "").lower(),
        str(element.get("role") or "").lower(),
        str(element.get("test_id") or "").lower(),
        str(element.get("name") or "").lower(),
        str(element.get("aria") or "").lower(),
        str(element.get("text") or "").strip().lower()[:80],
        href_path.lower()[:120],
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"sig_{digest}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_site_map(project: Path, url: str) -> dict[str, Any] | None:
    path = map_file_for_url(project, url)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_element_correction(
    project: Path,
    *,
    url: str,
    capture_id: str,
    element: dict[str, Any],
    interactive: bool,
    note: str = "",
) -> dict[str, Any]:
    signature = element_signature(element)
    site = site_key(url)
    path = map_file_for_url(project, url)
    existing = load_site_map(project, url) or {}
    entries = dict(existing.get("elements") or {})
    prior = entries.get(signature) if isinstance(entries.get(signature), dict) else {}
    entry = {
        "signature": signature,
        "interactive": interactive,
        "kind": element.get("kind"),
        "role": element.get("role"),
        "text": element.get("text"),
        "aria": element.get("aria"),
        "label": element.get("label"),
        "name": element.get("name"),
        "test_id": element.get("test_id"),
        "href": element.get("href"),
        "locator": element.get("locator"),
        "locator_status": element.get("locator_status"),
        "rect": element.get("rect"),
        "last_capture_id": capture_id,
        "last_element_id": element.get("id"),
        "note": note[:500] if note else prior.get("note"),
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "correction_count": int(prior.get("correction_count") or 0) + 1,
    }
    entries[signature] = entry
    payload = {
        "version": 1,
        "site_key": site,
        "url": url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "elements": entries,
    }
    _write_json(path, payload)
    append_training_record(
        project,
        {
            "kind": "element_correction",
            "capture_id": capture_id,
            "url": url,
            "site_key": site,
            "element_signature": signature,
            "element_id": element.get("id"),
            "raw_element": element,
            "ai_interactive": element.get("ai_interactive"),
            "user_interactive": interactive,
            "locator": element.get("locator"),
            "note": note[:500] if note else None,
        },
    )
    try:
        from web_capture.visual import stamp_element_correction_on_visual

        stamp_element_correction_on_visual(project, url, element, interactive=interactive)
    except Exception:
        pass
    return entry


def append_training_record(project: Path, record: dict[str, Any]) -> None:
    path = training_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def apply_site_map(capture: dict[str, Any], project: Path | None = None) -> dict[str, Any]:
    project = project or get_active_project()
    url = str(capture.get("url") or "")
    if not project or not url:
        capture["map"] = {"status": "none"}
        return capture

    saved = load_site_map(project, url)
    if not saved:
        capture["map"] = {"status": "missing", "site_key": site_key(url)}
        return capture

    entries = saved.get("elements") if isinstance(saved.get("elements"), dict) else {}
    matched = 0
    user_kept = 0
    user_rejected = 0
    for item in capture.get("elements") or []:
        if not isinstance(item, dict):
            continue
        signature = element_signature(item)
        row = entries.get(signature)
        if not isinstance(row, dict):
            item["user_interactive"] = None
            item["map_matched"] = False
            item["effective_interactive"] = _effective_interactive(item)
            continue
        matched += 1
        interactive = bool(row.get("interactive"))
        item["user_interactive"] = interactive
        item["map_matched"] = True
        item["map_signature"] = signature
        item["map_corrected_at"] = row.get("corrected_at")
        item["effective_interactive"] = interactive
        if interactive:
            user_kept += 1
        else:
            user_rejected += 1
        saved_locator = row.get("locator")
        if isinstance(saved_locator, dict) and saved_locator.get("value"):
            item["locator"] = saved_locator
            item["locator_status"] = str(row.get("locator_status") or item.get("locator_status") or "unique")

    for item in capture.get("elements") or []:
        if isinstance(item, dict) and "effective_interactive" not in item:
            item["effective_interactive"] = _effective_interactive(item)

    capture["map"] = {
        "status": "applied",
        "site_key": site_key(url),
        "matched": matched,
        "saved_entries": len(entries),
        "user_kept": user_kept,
        "user_rejected": user_rejected,
        "updated_at": saved.get("updated_at"),
    }
    capture.setdefault("summary", {}).update(
        {
            "user_kept": user_kept,
            "user_rejected": user_rejected,
            "map_matched": matched,
        }
    )
    return capture


def _effective_interactive(item: dict[str, Any]) -> bool:
    if item.get("user_interactive") is not None:
        return bool(item["user_interactive"])
    if item.get("ai_interactive") is not None:
        return bool(item["ai_interactive"])
    if item.get("disabled"):
        return False
    return item.get("locator_status") == "unique"


def sync_interactables_from_capture(state: dict[str, Any], capture: dict[str, Any]) -> None:
    """Mirror saved map decisions onto interactables used by Playwright agents."""
    by_id = {
        str(item.get("id")): item
        for item in (capture.get("elements") or [])
        if isinstance(item, dict) and item.get("id")
    }
    synced: list[dict[str, Any]] = []
    for raw in state.get("interactables") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        mapped = by_id.get(str(item.get("id") or ""))
        if mapped:
            item["user_interactive"] = mapped.get("user_interactive")
            item["map_matched"] = mapped.get("map_matched")
            item["ai_interactive"] = mapped.get("ai_interactive")
            item["effective_interactive"] = mapped.get("effective_interactive")
            if mapped.get("locator"):
                item["playwright_locator"] = mapped["locator"]
            if mapped.get("user_interactive") is False:
                continue
        synced.append(item)
    state["interactables"] = synced
    state["interactables_total"] = len(synced)
