"""Scroll-slice stitching: measure real scroll deltas and append only new bands."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from web_capture.maps import element_signature

# Viewport-relative Y within this band → same sticky/fixed chrome across scroll slices.
_PERSISTENT_Y_TOLERANCE = 8.0
# How close document Y must be to treat two elements as the same after scroll.
_DOC_Y_TOLERANCE = 12.0


def document_rect(rect: dict[str, Any], scroll_y: float) -> dict[str, float]:
    return {
        "x": round(float(rect.get("x") or 0), 2),
        "y": round(float(rect.get("y") or 0) + scroll_y, 2),
        "width": round(max(0.0, float(rect.get("width") or 0)), 2),
        "height": round(max(0.0, float(rect.get("height") or 0)), 2),
    }


def _slice_scroll_y(capture: dict[str, Any]) -> float:
    viewport = capture.get("viewport") if isinstance(capture.get("viewport"), dict) else {}
    return float(viewport.get("scroll_y") or 0)


def is_stitched_capture(capture: dict[str, Any] | None) -> bool:
    """True when capture already uses document-space coords from a prior merge."""
    if not isinstance(capture, dict):
        return False
    scroll_map = capture.get("scroll_map")
    if not isinstance(scroll_map, dict):
        return False
    if scroll_map.get("stitched") or scroll_map.get("coords") == "document":
        return True
    return int(scroll_map.get("slice_count") or 0) > 1


def _is_persistent_duplicate(
    signature: str,
    viewport_y: float,
    seen: dict[str, float],
) -> bool:
    """True when the same control reappears at the same viewport position after scrolling."""
    prior = seen.get(signature)
    if prior is None:
        return False
    return abs(prior - viewport_y) <= _PERSISTENT_Y_TOLERANCE


def compute_unique_bands(slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    For ordered viewport slices, compute the newly revealed band of each screenshot.

    First slice draws its full viewport. Later slices only draw the strip that was not
    already covered by the previous slice — matching the measured scroll delta.
    """
    ordered = sorted(
        [s for s in slices if isinstance(s, dict)],
        key=lambda row: float(row.get("scroll_y") or 0),
    )
    bands: list[dict[str, Any]] = []
    covered_bottom = 0.0
    for row in ordered:
        scroll_y = float(row.get("scroll_y") or 0)
        height = float(row.get("height") or 720)
        # How far into this screenshot the previously covered content ends.
        content_top = max(0.0, covered_bottom - scroll_y)
        if content_top >= height - 1:
            # Entirely overlapped by prior coverage — skip drawing, keep for bookkeeping.
            content_top = height
            content_height = 0.0
        else:
            content_height = height - content_top
        draw_top = scroll_y + content_top
        bands.append(
            {
                **row,
                "scroll_y": round(scroll_y, 2),
                "height": round(height, 2),
                "content_top": round(content_top, 2),
                "content_height": round(content_height, 2),
                "draw_top": round(draw_top, 2),
                "delta_from_prev": round(max(0.0, scroll_y - (bands[-1]["scroll_y"] if bands else 0.0)), 2),
            }
        )
        covered_bottom = max(covered_bottom, scroll_y + height)
    return bands


def merge_scroll_captures(captures: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Merge viewport captures into one document-tall map, skipping sticky duplicates."""
    # Never re-merge already-stitched captures (rects would get double scroll offsets).
    slices = [
        item
        for item in captures
        if isinstance(item, dict)
        and item.get("elements") is not None
        and not is_stitched_capture(item)
    ]
    prior_stitched = [
        item for item in captures if isinstance(item, dict) and is_stitched_capture(item)
    ]
    if not slices:
        if not prior_stitched:
            return None
        best = max(
            prior_stitched,
            key=lambda item: int((item.get("scroll_map") or {}).get("slice_count") or 0),
        )
        return copy.deepcopy(best)
    if len(slices) == 1:
        # Prefer an existing multi-slice stitch over downgrading to one viewport.
        if prior_stitched:
            best = max(
                prior_stitched,
                key=lambda item: int((item.get("scroll_map") or {}).get("slice_count") or 0),
            )
            if int((best.get("scroll_map") or {}).get("slice_count") or 0) > 1:
                return copy.deepcopy(best)
        return annotate_scroll_map(copy.deepcopy(slices[0]))

    ordered = sorted(slices, key=_slice_scroll_y)
    base = copy.deepcopy(ordered[-1])
    viewport = dict(base.get("viewport") or {})
    width = float(viewport.get("width") or 1280)
    view_height = float(viewport.get("height") or 720)

    raw_slice_meta = [
        {
            "scroll_y": round(_slice_scroll_y(capture), 2),
            "height": round(
                float((capture.get("viewport") or {}).get("height") or view_height),
                2,
            ),
            "screenshot": capture.get("screenshot"),
            "capture_id": capture.get("capture_id"),
        }
        for capture in ordered
    ]
    scroll_slices = compute_unique_bands(raw_slice_meta)

    merged_elements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_sig_doc: dict[str, float] = {}
    persistent_y: dict[str, float] = {}
    skipped_overlap = 0
    max_bottom = 0.0

    for capture, band in zip(ordered, scroll_slices):
        vp = capture.get("viewport") if isinstance(capture.get("viewport"), dict) else {}
        scroll_y = float(vp.get("scroll_y") or 0)
        content_top = float(band.get("content_top") or 0)
        # New plane starts here in viewport space — skip already-covered content above it.
        new_plane_y = content_top

        for raw in capture.get("elements") or []:
            if not isinstance(raw, dict):
                continue
            element_id = str(raw.get("id") or "")
            rect = raw.get("rect") if isinstance(raw.get("rect"), dict) else None
            if not element_id or not rect:
                continue
            viewport_y = float(rect.get("y") or 0)
            viewport_h = float(rect.get("height") or 0)
            mid_y = viewport_y + viewport_h / 2.0
            signature = element_signature(raw)

            if _is_persistent_duplicate(signature, viewport_y, persistent_y):
                skipped_overlap += 1
                continue
            # Later slices: only ingest elements whose center is in the newly revealed plane.
            if new_plane_y > 0 and mid_y < new_plane_y:
                skipped_overlap += 1
                continue
            if element_id in seen_ids:
                skipped_overlap += 1
                continue

            doc_rect = document_rect(rect, scroll_y)
            if doc_rect["width"] <= 0 or doc_rect["height"] <= 0:
                continue

            prior_doc_y = seen_sig_doc.get(signature)
            if prior_doc_y is not None and abs(prior_doc_y - doc_rect["y"]) <= _DOC_Y_TOLERANCE:
                skipped_overlap += 1
                continue

            item = {
                **raw,
                "id": element_id,
                "rect": doc_rect,
                "source_scroll_y": round(scroll_y, 2),
                "map_signature": signature,
            }
            merged_elements.append(item)
            seen_ids.add(element_id)
            seen_sig_doc[signature] = doc_rect["y"]
            if signature not in persistent_y:
                persistent_y[signature] = viewport_y
            max_bottom = max(max_bottom, doc_rect["y"] + doc_rect["height"])

    explored_height = max(
        view_height,
        max_bottom,
        max(
            (float(row["draw_top"]) + float(row["content_height"]) for row in scroll_slices),
            default=view_height,
        ),
        max((float(row["scroll_y"]) + float(row["height"]) for row in scroll_slices), default=view_height),
    )
    canvas_height = round(explored_height, 2)

    for index, item in enumerate(merged_elements):
        item["index"] = index

    base["elements"] = merged_elements
    base["viewport"] = {
        **viewport,
        "width": width,
        "height": view_height,
        "scroll_x": 0.0,
        "scroll_y": 0.0,
        "document_width": max(float(viewport.get("document_width") or width), width),
        "document_height": max(
            float(viewport.get("document_height") or canvas_height),
            canvas_height,
        ),
    }
    drawable = [row for row in scroll_slices if float(row.get("content_height") or 0) > 1]
    base["scroll_map"] = {
        "stitched": True,
        "coords": "document",
        "canvas_height": canvas_height,
        "slice_count": len(drawable) or len(scroll_slices),
        "explored_height": canvas_height,
        "persistent_skipped": skipped_overlap,
        "slices": scroll_slices,
    }
    base["capture_id"] = f"cap_stitch_{uuid.uuid4().hex[:10]}"
    base["created_at"] = datetime.now(timezone.utc).isoformat()
    base["context"] = "scroll_stitch"
    base.setdefault("summary", {})
    base["summary"].update(
        {
            "raw": sum(int((c.get("summary") or {}).get("raw") or 0) for c in ordered),
            "visible": len(merged_elements),
            "overlap_skipped": skipped_overlap,
        }
    )
    top_shot = next(
        (row.get("screenshot") for row in scroll_slices if row.get("screenshot")),
        base.get("screenshot"),
    )
    if top_shot:
        base["screenshot"] = top_shot
    return base


def annotate_scroll_map(capture: dict[str, Any]) -> dict[str, Any]:
    """Mark a single-viewport capture — keep viewport coords and viewport-sized canvas."""
    result = copy.deepcopy(capture)
    if is_stitched_capture(result):
        return result
    viewport = result.get("viewport") if isinstance(result.get("viewport"), dict) else {}
    height = float(viewport.get("height") or 720)
    scroll_y = float(viewport.get("scroll_y") or 0)
    result["scroll_map"] = {
        "stitched": False,
        "coords": "viewport",
        "canvas_height": round(height, 2),
        "slice_count": 1,
        "explored_height": round(scroll_y + height, 2),
        "persistent_skipped": 0,
        "slices": [
            {
                "scroll_y": round(scroll_y, 2),
                "height": round(height, 2),
                "content_top": 0.0,
                "content_height": round(height, 2),
                "draw_top": round(scroll_y, 2),
                "delta_from_prev": 0.0,
                "screenshot": result.get("screenshot"),
                "capture_id": result.get("capture_id"),
            }
        ],
    }
    return result


def accumulate_scroll_capture(
    cache: dict[str, Any],
    *,
    url: str,
    capture: dict[str, Any],
) -> dict[str, Any] | None:
    """Append a viewport capture slice for url; return merged map when possible."""
    if not url or not isinstance(capture, dict):
        return None
    # Already merged — return as-is; never treat document-space rects as a new slice.
    if is_stitched_capture(capture):
        entry = cache.setdefault(url, {"by_scroll": {}, "merged": None})
        entry["merged"] = capture
        return capture

    entry = cache.setdefault(url, {"by_scroll": {}, "merged": None})
    scroll_y = round(_slice_scroll_y(capture), 2)
    by_scroll: dict[float, dict[str, Any]] = entry.setdefault("by_scroll", {})
    # Skip reprocessing an identical scroll position unless the new capture has more elements.
    prior = by_scroll.get(scroll_y)
    if prior is not None and len(capture.get("elements") or []) <= len(prior.get("elements") or []):
        return entry.get("merged") or merge_scroll_captures(list(by_scroll.values()))
    by_scroll[scroll_y] = capture
    merged = merge_scroll_captures(list(by_scroll.values()))
    entry["merged"] = merged
    return merged
