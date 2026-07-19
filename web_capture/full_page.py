"""Full-page capture: one Playwright screenshot + document-space element map."""

from __future__ import annotations

import copy
from typing import Any

# Cap absurdly tall pages (infinite scroll / catalogs) so JPEG stays manageable.
MAX_FULL_PAGE_HEIGHT_PX = 14000.0


def clip_rect_to_document(raw: Any, *, width: float, height: float) -> dict[str, float] | None:
    """Keep element boxes that intersect the document canvas (not just the viewport)."""
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw.get("x") or 0)
        y = float(raw.get("y") or 0)
        w = max(0.0, float(raw.get("width") or 0))
        h = max(0.0, float(raw.get("height") or 0))
    except (TypeError, ValueError):
        return None
    left = max(0.0, x)
    top = max(0.0, y)
    right = min(width, x + w)
    bottom = min(height, y + h)
    if right <= left or bottom <= top:
        return None
    return {
        "x": round(left, 2),
        "y": round(top, 2),
        "width": round(right - left, 2),
        "height": round(bottom - top, 2),
    }


def document_canvas_height(viewport: dict[str, Any] | None, *, max_height: float = MAX_FULL_PAGE_HEIGHT_PX) -> float:
    source = viewport if isinstance(viewport, dict) else {}
    view_h = max(1.0, float(source.get("height") or 720))
    doc_h = max(view_h, float(source.get("document_height") or view_h))
    return round(min(doc_h, max_height), 2)


def promote_elements_to_document(
    elements: list[dict[str, Any]],
    *,
    scroll_y: float,
    canvas_width: float,
    canvas_height: float,
) -> list[dict[str, Any]]:
    """Shift viewport-relative rects by scroll_y and keep those on the document canvas."""
    out: list[dict[str, Any]] = []
    for raw in elements:
        if not isinstance(raw, dict):
            continue
        rect = raw.get("rect")
        if not isinstance(rect, dict):
            continue
        shifted = {
            "x": float(rect.get("x") or 0),
            "y": float(rect.get("y") or 0) + scroll_y,
            "width": float(rect.get("width") or 0),
            "height": float(rect.get("height") or 0),
        }
        clipped = clip_rect_to_document(shifted, width=canvas_width, height=canvas_height)
        if not clipped:
            continue
        item = copy.deepcopy(raw)
        item["rect"] = clipped
        item["source_scroll_y"] = round(scroll_y, 2)
        out.append(item)
    return out


def annotate_full_page_capture(
    capture: dict[str, Any],
    *,
    max_height: float = MAX_FULL_PAGE_HEIGHT_PX,
) -> dict[str, Any]:
    """
    Mark a capture as a single full-page document map.

    Expects elements already in document coordinates (scroll origin at top).
    """
    result = copy.deepcopy(capture) if isinstance(capture, dict) else {}
    viewport = result.get("viewport") if isinstance(result.get("viewport"), dict) else {}
    width = max(1.0, float(viewport.get("width") or 1))
    view_height = max(1.0, float(viewport.get("height") or 720))
    canvas_height = document_canvas_height(viewport, max_height=max_height)

    # Re-clip elements to the capped canvas (drops anything past the screenshot).
    elements: list[dict[str, Any]] = []
    for raw in result.get("elements") or []:
        if not isinstance(raw, dict):
            continue
        clipped = clip_rect_to_document(raw.get("rect"), width=width, height=canvas_height)
        if not clipped:
            continue
        item = dict(raw)
        item["rect"] = clipped
        item["index"] = len(elements)
        elements.append(item)
    result["elements"] = elements

    result["viewport"] = {
        **viewport,
        "width": width,
        "height": view_height,
        "scroll_x": 0.0,
        "scroll_y": 0.0,
        "document_width": max(float(viewport.get("document_width") or width), width),
        "document_height": max(float(viewport.get("document_height") or canvas_height), canvas_height),
    }
    result["scroll_map"] = {
        "stitched": True,
        "coords": "document",
        "mode": "full_page",
        "canvas_height": canvas_height,
        "slice_count": 1,
        "explored_height": canvas_height,
        "persistent_skipped": 0,
        "slices": [
            {
                "scroll_y": 0.0,
                "height": canvas_height,
                "content_top": 0.0,
                "content_height": canvas_height,
                "draw_top": 0.0,
                "delta_from_prev": 0.0,
                "screenshot": result.get("screenshot"),
                "capture_id": result.get("capture_id"),
            }
        ],
    }
    result["context"] = str(result.get("context") or "full_page")
    result.setdefault("summary", {})
    result["summary"]["full_page"] = True
    result["summary"]["visible"] = len(elements)
    return result


def is_full_page_capture(capture: dict[str, Any] | None) -> bool:
    if not isinstance(capture, dict):
        return False
    scroll_map = capture.get("scroll_map")
    if isinstance(scroll_map, dict) and scroll_map.get("mode") == "full_page":
        return True
    summary = capture.get("summary")
    return isinstance(summary, dict) and bool(summary.get("full_page"))
