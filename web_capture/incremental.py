"""Incremental same-URL map updates — patch instead of full redraw."""

from __future__ import annotations

import copy
from typing import Any

from web_capture.maps import element_signature
from web_capture.stitch import document_rect, is_stitched_capture

# Geometry within this px band counts as unchanged for UI stability.
_RECT_TOLERANCE = 3.0
# Reuse prior AI/user labels when signature overlap is at least this high.
_REUSE_OVERLAP = 0.55
# Fields carried from the previous map onto matched elements.
_PRESERVE_FIELDS = (
    "ai_interactive",
    "ai_confidence",
    "ai_control_type",
    "ai_reason",
    "user_interactive",
    "user_note",
    "map_matched",
    "map_signature",
)


def _norm_url(url: str) -> str:
    return str(url or "").strip().split("#", 1)[0].rstrip("/")


def same_page_url(a: str, b: str) -> bool:
    return bool(a and b and _norm_url(a) == _norm_url(b))


def _rect_tuple(rect: dict[str, Any] | None) -> tuple[float, float, float, float]:
    if not isinstance(rect, dict):
        return (0.0, 0.0, 0.0, 0.0)
    return (
        float(rect.get("x") or 0),
        float(rect.get("y") or 0),
        float(rect.get("width") or 0),
        float(rect.get("height") or 0),
    )


def rects_close(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
    *,
    tol: float = _RECT_TOLERANCE,
) -> bool:
    ax, ay, aw, ah = _rect_tuple(a)
    bx, by, bw, bh = _rect_tuple(b)
    return (
        abs(ax - bx) <= tol
        and abs(ay - by) <= tol
        and abs(aw - bw) <= tol
        and abs(ah - bh) <= tol
    )


def _element_keys(element: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    signature = element_signature(element)
    if signature:
        keys.append(f"sig:{signature}")
    element_id = str(element.get("id") or "").strip()
    if element_id:
        keys.append(f"id:{element_id}")
    return keys


def index_elements(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in elements:
        if not isinstance(item, dict):
            continue
        for key in _element_keys(item):
            indexed.setdefault(key, item)
    return indexed


def signature_overlap(
    previous_elements: list[dict[str, Any]],
    current_elements: list[dict[str, Any]],
) -> float:
    prev_sigs = {
        element_signature(item)
        for item in previous_elements
        if isinstance(item, dict) and item.get("rect")
    }
    curr_sigs = {
        element_signature(item)
        for item in current_elements
        if isinstance(item, dict) and item.get("rect")
    }
    if not prev_sigs and not curr_sigs:
        return 1.0
    if not prev_sigs or not curr_sigs:
        return 0.0
    shared = len(prev_sigs & curr_sigs)
    return shared / max(len(prev_sigs), len(curr_sigs))


def diff_capture_elements(
    previous_elements: list[dict[str, Any]],
    current_elements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fast compare of two element lists by signature/id."""
    prev_by_sig: dict[str, dict[str, Any]] = {}
    for item in previous_elements:
        if isinstance(item, dict) and item.get("rect"):
            prev_by_sig[element_signature(item)] = item

    matched: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    seen_prev: set[str] = set()

    for item in current_elements:
        if not isinstance(item, dict) or not item.get("rect"):
            continue
        sig = element_signature(item)
        prior = prev_by_sig.get(sig)
        if prior is None:
            added.append(item)
            continue
        seen_prev.add(sig)
        moved = not rects_close(prior.get("rect"), item.get("rect"))
        matched.append({"signature": sig, "previous": prior, "current": item, "moved": moved})

    removed = [
        item
        for sig, item in prev_by_sig.items()
        if sig not in seen_prev
    ]
    return {
        "matched": matched,
        "added": added,
        "removed": removed,
        "moved": [row for row in matched if row["moved"]],
        "unchanged": [row for row in matched if not row["moved"]],
        "overlap": signature_overlap(previous_elements, current_elements),
    }


def _copy_preserved(src: dict[str, Any], dest: dict[str, Any]) -> None:
    for field in _PRESERVE_FIELDS:
        if field in src and src[field] is not None:
            dest[field] = src[field]
    if src.get("map_signature"):
        dest["map_signature"] = src["map_signature"]
    else:
        dest.setdefault("map_signature", element_signature(dest))


def _viewport_band(capture: dict[str, Any]) -> tuple[float, float]:
    viewport = capture.get("viewport") if isinstance(capture.get("viewport"), dict) else {}
    scroll_y = float(viewport.get("scroll_y") or 0)
    height = float(viewport.get("height") or 720)
    if is_stitched_capture(capture):
        # Stitched captures are already document-space; band is the explored canvas.
        canvas = float((capture.get("scroll_map") or {}).get("canvas_height") or height)
        return 0.0, canvas
    return scroll_y, scroll_y + height


def _in_band(rect: dict[str, Any] | None, top: float, bottom: float) -> bool:
    if not isinstance(rect, dict):
        return False
    y = float(rect.get("y") or 0)
    h = float(rect.get("height") or 0)
    mid = y + h / 2.0
    return top - _RECT_TOLERANCE <= mid <= bottom + _RECT_TOLERANCE


def apply_preserved_labels(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Copy AI/user labels from previous capture onto matching current elements."""
    if not isinstance(previous, dict):
        return current
    prev_elements = [e for e in (previous.get("elements") or []) if isinstance(e, dict)]
    curr_elements = [e for e in (current.get("elements") or []) if isinstance(e, dict)]
    if not prev_elements or not curr_elements:
        return current
    overlap = signature_overlap(prev_elements, curr_elements)
    if overlap < _REUSE_OVERLAP:
        return current

    prev_index = index_elements(prev_elements)
    reused = 0
    for item in curr_elements:
        prior = None
        for key in _element_keys(item):
            prior = prev_index.get(key)
            if prior is not None:
                break
        if prior is None:
            continue
        _copy_preserved(prior, item)
        reused += 1

    current.setdefault("summary", {})
    current["summary"]["incremental_reused"] = reused
    current["summary"]["incremental_overlap"] = round(overlap, 3)
    current["map_update"] = {
        "mode": "label_reuse",
        "overlap": round(overlap, 3),
        "reused": reused,
        "added": max(0, len(curr_elements) - reused),
    }
    return current


def patch_map_with_viewport(
    base: dict[str, Any],
    viewport_capture: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Patch a same-URL map with a fresh viewport capture.

    Keeps elements outside the observed scroll band, updates/adds elements
    inside the band from the new capture, and preserves AI/user labels.
    """
    if not isinstance(base, dict) or not isinstance(viewport_capture, dict):
        return None
    if not same_page_url(str(base.get("url") or ""), str(viewport_capture.get("url") or "")):
        return None
    if is_stitched_capture(viewport_capture):
        # Fresh stitched maps replace the base for that URL.
        return copy.deepcopy(viewport_capture)

    result = copy.deepcopy(base)
    base_stitched = is_stitched_capture(result)
    vp = viewport_capture.get("viewport") if isinstance(viewport_capture.get("viewport"), dict) else {}
    scroll_y = float(vp.get("scroll_y") or 0)
    view_height = float(vp.get("height") or 720)
    band_top = scroll_y if base_stitched else 0.0
    band_bottom = (scroll_y + view_height) if base_stitched else view_height

    incoming: list[dict[str, Any]] = []
    for raw in viewport_capture.get("elements") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("rect"), dict):
            continue
        item = copy.deepcopy(raw)
        if base_stitched:
            item["rect"] = document_rect(item["rect"], scroll_y)
            item["source_scroll_y"] = round(scroll_y, 2)
        item.setdefault("map_signature", element_signature(item))
        if item["rect"]["width"] <= 0 or item["rect"]["height"] <= 0:
            continue
        incoming.append(item)

    prev_elements = [e for e in (result.get("elements") or []) if isinstance(e, dict)]
    prev_index = index_elements(prev_elements)
    kept: list[dict[str, Any]] = []
    removed = 0

    for item in prev_elements:
        rect = item.get("rect") if isinstance(item.get("rect"), dict) else None
        if rect is None:
            continue
        # Sticky chrome near the top of the viewport band is replaced by incoming.
        if _in_band(rect, band_top, band_bottom):
            removed += 1
            continue
        kept.append(item)

    added = 0
    moved = 0
    reused = 0
    seen_ids: set[str] = {str(e.get("id") or "") for e in kept if e.get("id")}
    for item in incoming:
        element_id = str(item.get("id") or "")
        prior = None
        for key in _element_keys(item):
            prior = prev_index.get(key)
            if prior is not None:
                break
        if prior is not None:
            _copy_preserved(prior, item)
            reused += 1
            if not rects_close(prior.get("rect"), item.get("rect")):
                moved += 1
        else:
            added += 1
        if element_id and element_id in seen_ids:
            # Prefer the fresher geometry for the same id.
            kept = [e for e in kept if str(e.get("id") or "") != element_id]
        kept.append(item)
        if element_id:
            seen_ids.add(element_id)

    for index, item in enumerate(kept):
        item["index"] = index
    result["elements"] = kept

    # Refresh viewport metadata from the live capture; keep document canvas when stitched.
    result["viewport"] = {
        **dict(result.get("viewport") or {}),
        **{k: vp[k] for k in ("width", "document_width", "document_height") if k in vp},
        "height": float((result.get("viewport") or {}).get("height") or view_height)
        if base_stitched
        else view_height,
        "scroll_x": 0.0 if base_stitched else float(vp.get("scroll_x") or 0),
        "scroll_y": 0.0 if base_stitched else scroll_y,
    }
    if viewport_capture.get("screenshot"):
        result["screenshot"] = viewport_capture["screenshot"]
    if viewport_capture.get("title"):
        result["title"] = viewport_capture["title"]

    scroll_map = dict(result.get("scroll_map") or {})
    if base_stitched:
        slices = list(scroll_map.get("slices") or [])
        replaced = False
        for row in slices:
            if abs(float(row.get("scroll_y") or 0) - scroll_y) <= 1.0:
                row["screenshot"] = viewport_capture.get("screenshot") or row.get("screenshot")
                row["capture_id"] = viewport_capture.get("capture_id") or row.get("capture_id")
                row["height"] = round(view_height, 2)
                replaced = True
                break
        if not replaced:
            slices.append(
                {
                    "scroll_y": round(scroll_y, 2),
                    "height": round(view_height, 2),
                    "screenshot": viewport_capture.get("screenshot"),
                    "capture_id": viewport_capture.get("capture_id"),
                }
            )
            slices.sort(key=lambda row: float(row.get("scroll_y") or 0))
        max_bottom = max(
            (float(e["rect"]["y"]) + float(e["rect"]["height"]) for e in kept if isinstance(e.get("rect"), dict)),
            default=view_height,
        )
        explored = max(
            float(scroll_map.get("canvas_height") or 0),
            max_bottom,
            max((float(s.get("scroll_y") or 0) + float(s.get("height") or 0) for s in slices), default=view_height),
        )
        scroll_map.update(
            {
                "stitched": True,
                "coords": "document",
                "canvas_height": round(explored, 2),
                "explored_height": round(explored, 2),
                "slice_count": len(slices),
                "slices": slices,
            }
        )
        result["scroll_map"] = scroll_map
        # Keep a stable stitch id so the UI can patch rather than flash a new map.
        result["capture_id"] = str(base.get("capture_id") or result.get("capture_id"))
        result["context"] = "scroll_stitch_patch"
    else:
        result["capture_id"] = str(viewport_capture.get("capture_id") or result.get("capture_id"))
        result["context"] = str(viewport_capture.get("context") or result.get("context") or "incremental")

    result.setdefault("summary", {})
    result["summary"].update(
        {
            "visible": len(kept),
            "incremental_reused": reused,
            "incremental_added": added,
            "incremental_removed": removed,
            "incremental_moved": moved,
        }
    )
    result["map_update"] = {
        "mode": "patch",
        "overlap": round(signature_overlap(prev_elements, incoming), 3),
        "reused": reused,
        "added": added,
        "removed": removed,
        "moved": moved,
        "band": [round(band_top, 2), round(band_bottom, 2)],
    }
    result["fingerprint"] = str(viewport_capture.get("fingerprint") or result.get("fingerprint") or "")
    return result


def _capture_scroll_y(capture: dict[str, Any]) -> float:
    viewport = capture.get("viewport") if isinstance(capture.get("viewport"), dict) else {}
    return float(viewport.get("scroll_y") or 0)


def maybe_incremental_capture(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """
    Same URL → patch prior map / reuse labels.
    URL change → return current unchanged (full redraw).
    """
    if not isinstance(current, dict):
        return current
    if not isinstance(previous, dict):
        current["map_update"] = {"mode": "full", "reason": "no_previous"}
        return current
    if not same_page_url(str(previous.get("url") or ""), str(current.get("url") or "")):
        current["map_update"] = {"mode": "full", "reason": "url_changed"}
        return current

    # Document maps can absorb any viewport slice.
    if is_stitched_capture(previous):
        patched = patch_map_with_viewport(previous, current)
        if patched is not None:
            return patched

    # Unstitched maps: only patch when we're looking at the same scroll band.
    # Different scroll positions are merged later by scroll stitch.
    if abs(_capture_scroll_y(previous) - _capture_scroll_y(current)) <= 1.0:
        patched = patch_map_with_viewport(previous, current)
        if patched is not None:
            return patched

    current["map_update"] = {"mode": "full", "reason": "scroll_changed"}
    return apply_preserved_labels(previous, current)
