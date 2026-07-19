"""Cross-run URL-keyed page maps — the only research artifact reused between runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web_capture.maps import _slug, site_key
from web_capture.storage import project_capture_dir


def by_url_dir(project: Path) -> Path:
    return project_capture_dir(project) / "by-url"


def capture_path_for_url(project: Path, url: str) -> Path:
    return by_url_dir(project) / f"{_slug(site_key(url))}.json"


def normalize_cache_url(url: str) -> str:
    return str(url or "").strip().split("#", 1)[0].rstrip("/") or str(url or "").strip()


def capture_is_reusable(capture: dict[str, Any] | None, *, project: Path | None = None) -> bool:
    """True only for a document-tall full-page/stitched map with a real screenshot.

    Viewport-only captures must never be reused — they starve the UI of a
    scrollable map and skip the full-page rebuild.
    """
    if not isinstance(capture, dict):
        return False
    elements = capture.get("elements")
    if not isinstance(elements, list) or len(elements) < 8:
        return False
    scroll_map = capture.get("scroll_map") if isinstance(capture.get("scroll_map"), dict) else {}
    mode = str(scroll_map.get("mode") or "")
    coords = str(scroll_map.get("coords") or "")
    stitched = bool(scroll_map.get("stitched"))
    canvas = float(scroll_map.get("canvas_height") or 0)
    viewport = capture.get("viewport") if isinstance(capture.get("viewport"), dict) else {}
    vp_h = float(viewport.get("height") or 0) or 720.0
    doc_h = float(viewport.get("document_height") or 0)
    tall_enough = canvas > vp_h * 1.05 or doc_h > vp_h * 1.05
    document_map = mode == "full_page" or (stitched and coords == "document")
    if not document_map or not tall_enough:
        return False
    if coords == "viewport":
        return False
    shot = str(capture.get("screenshot") or "").strip()
    if not shot:
        for slice_row in scroll_map.get("slices") or []:
            if isinstance(slice_row, dict) and str(slice_row.get("screenshot") or "").strip():
                shot = str(slice_row.get("screenshot") or "").strip()
                break
    if not shot:
        return False
    if project is not None:
        shot_path = project_capture_dir(project) / shot
        if not shot_path.is_file():
            return False
    return True


def capture_quality_score(capture: dict[str, Any] | None) -> float:
    """Higher is better — used so a worse rebuild cannot clobber a good cached map."""
    if not isinstance(capture, dict):
        return -1.0
    scroll_map = capture.get("scroll_map") if isinstance(capture.get("scroll_map"), dict) else {}
    canvas = float(scroll_map.get("canvas_height") or 0)
    els = len(capture.get("elements") or []) if isinstance(capture.get("elements"), list) else 0
    bonus = 1000.0 if capture_is_reusable(capture) else 0.0
    mode_bonus = 200.0 if scroll_map.get("mode") == "full_page" else 0.0
    return bonus + mode_bonus + canvas + els * 0.5


def _load_json_capture(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _capture_url_matches(capture: dict[str, Any], url: str) -> bool:
    left = normalize_cache_url(str(capture.get("url") or ""))
    right = normalize_cache_url(url)
    return bool(left and right and left == right)


def _newest_raw_capture_for_url(project: Path, url: str) -> dict[str, Any] | None:
    """Legacy fallback: pick the best matching capture from raw/ (quality first)."""
    raw_dir = project_capture_dir(project) / "raw"
    if not raw_dir.is_dir():
        return None
    best: dict[str, Any] | None = None
    best_score = -1.0
    for path in raw_dir.glob("*.json"):
        payload = _load_json_capture(path)
        if not payload or not _capture_url_matches(payload, url):
            continue
        if not capture_is_reusable(payload, project=project):
            continue
        score = capture_quality_score(payload)
        try:
            score += path.stat().st_mtime * 1e-12  # tiny tie-break for recency
        except OSError:
            pass
        if score >= best_score:
            best = payload
            best_score = score
    return best


def load_capture_for_url(project: Path | None, url: str) -> dict[str, Any] | None:
    """Load a previously saved map for this URL, or None."""
    if project is None or not str(url or "").strip():
        return None
    path = capture_path_for_url(project, url)
    if path.is_file():
        payload = _load_json_capture(path)
        if payload and capture_is_reusable(payload, project=project):
            return payload
    # Promote legacy raw/ captures into the by-url store on first hit.
    legacy = _newest_raw_capture_for_url(project, url)
    if legacy is not None:
        try:
            save_capture_for_url(project, legacy)
        except Exception:
            pass
        return legacy
    return None


def save_capture_for_url(project: Path | None, capture: dict[str, Any]) -> Path | None:
    """Persist a reusable map keyed by URL for future runs."""
    if project is None or not isinstance(capture, dict):
        return None
    url = str(capture.get("url") or "").strip()
    if not url or not capture_is_reusable(capture, project=project):
        return None
    target_dir = by_url_dir(project)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = capture_path_for_url(project, url)
    # Never let a weaker map overwrite a stronger cached one.
    if dest.is_file():
        existing = _load_json_capture(dest)
        if existing and capture_quality_score(existing) > capture_quality_score(capture) + 1.0:
            return dest
    # Strip huge ephemeral fields that aren't needed for reuse/display.
    payload = dict(capture)
    payload.pop("screenshot_b64", None)
    payload.pop("map_reuse", None)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return dest


def list_cached_captures(project: Path | None) -> list[dict[str, Any]]:
    """All reusable maps in the by-url store (newest-friendly order not guaranteed)."""
    if project is None:
        return []
    root = by_url_dir(project)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if capture_is_reusable(payload, project=project):
            out.append(payload)
    return out
