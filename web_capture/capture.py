from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from web_capture.models import CaptureElement, Rect, Viewport, WebCapture


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _viewport(raw: Any) -> Viewport:
    source = raw if isinstance(raw, dict) else {}
    return {
        "width": max(1.0, _number(source.get("width"), 1)),
        "height": max(1.0, _number(source.get("height"), 1)),
        "scroll_x": _number(source.get("scroll_x")),
        "scroll_y": _number(source.get("scroll_y")),
        "document_width": max(1.0, _number(source.get("document_width"), 1)),
        "document_height": max(1.0, _number(source.get("document_height"), 1)),
    }


def clip_rect(
    raw: Any,
    viewport: Viewport,
    *,
    coord_space: str = "viewport",
) -> Rect | None:
    if not isinstance(raw, dict):
        return None
    x = _number(raw.get("x"))
    y = _number(raw.get("y"))
    width = max(0.0, _number(raw.get("width")))
    height = max(0.0, _number(raw.get("height")))
    left = max(0.0, x)
    top = max(0.0, y)
    if coord_space == "document":
        from web_capture.full_page import MAX_FULL_PAGE_HEIGHT_PX, document_canvas_height

        max_w = max(viewport["width"], float(viewport.get("document_width") or viewport["width"]))
        max_h = document_canvas_height(viewport, max_height=MAX_FULL_PAGE_HEIGHT_PX)
        right = min(max_w, x + width)
        bottom = min(max_h, y + height)
    else:
        right = min(viewport["width"], x + width)
        bottom = min(viewport["height"], y + height)
    if right <= left or bottom <= top:
        return None
    return {
        "x": round(left, 2),
        "y": round(top, 2),
        "width": round(right - left, 2),
        "height": round(bottom - top, 2),
    }


def _fingerprint(url: str, viewport: Viewport, elements: list[CaptureElement]) -> str:
    compact = {
        "url": url,
        "viewport": [viewport["width"], viewport["height"]],
        "elements": [
            [
                item.get("id"),
                item.get("kind"),
                item.get("text"),
                item.get("aria"),
                item.get("rect"),
                item.get("disabled"),
            ]
            for item in elements
        ],
    }
    payload = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_capture(
    state: dict[str, Any],
    *,
    context: str = "",
    elements: list[dict[str, Any]] | None = None,
    coord_space: str | None = None,
) -> WebCapture:
    viewport = _viewport(state.get("viewport"))
    space = coord_space or (
        "document"
        if str(state.get("screenshot_mode") or "") == "full_page"
        else "viewport"
    )
    source_elements = elements if elements is not None else list(state.get("interactables") or [])
    normalized: list[CaptureElement] = []
    seen: set[str] = set()

    for raw in source_elements:
        if not isinstance(raw, dict):
            continue
        element_id = str(raw.get("id") or f"element-{len(normalized)}")
        # Full-page maps: rects are viewport-relative at scrollY=0, including below-fold
        # elements with y > viewport.height — clip to the document canvas instead.
        rect_raw = raw.get("rect")
        if space == "document" and isinstance(rect_raw, dict):
            scroll_y = float(viewport.get("scroll_y") or 0)
            if abs(scroll_y) > 0.5:
                rect_raw = {
                    **rect_raw,
                    "y": float(rect_raw.get("y") or 0) + scroll_y,
                }
        rect = clip_rect(rect_raw, viewport, coord_space=space)
        issues: list[str] = []
        if not rect:
            issues.append("outside_document" if space == "document" else "outside_viewport")
        if raw.get("disabled"):
            issues.append("disabled")
        if element_id in seen:
            issues.append("duplicate_id")
        seen.add(element_id)
        if not rect:
            continue
        normalized.append(
            {
                **raw,
                "id": element_id,
                "index": len(normalized),
                "kind": str(raw.get("kind") or raw.get("role") or "element"),
                "text": raw.get("text"),
                "aria": raw.get("aria"),
                "rect": rect,
                "locator_candidates": list(raw.get("locator_candidates") or []),
                "locator_status": (
                    "synthetic"
                    if raw.get("inferred_from_overlay")
                    else "content"
                    if raw.get("map_layer") == "content"
                    else "unresolved"
                ),
                "locator": None,
                "ai_interactive": None,
                "ai_confidence": None,
                "ai_control_type": None,
                "ai_reason": None,
                "deterministic_issues": issues,
            }
        )

    url = str(state.get("url") or "")
    fingerprint = _fingerprint(url, viewport, normalized)
    return {
        "version": 1,
        "capture_id": f"cap_{uuid.uuid4().hex[:12]}",
        "fingerprint": fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "title": str(state.get("title") or ""),
        "context": context or str(state.get("context") or ""),
        "viewport": viewport,
        "elements": normalized,
        "summary": {
            "raw": len(source_elements),
            "visible": len(normalized),
            "unique": 0,
            "ambiguous": 0,
            "unresolved": len(normalized),
            "ai_kept": 0,
            "ai_rejected": 0,
        },
        "ai": {"status": "pending"},
    }
