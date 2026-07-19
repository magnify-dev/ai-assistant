from __future__ import annotations

from typing import Any


def _rect_area(rect: dict[str, Any]) -> float:
    return max(0.0, float(rect.get("width") or 0)) * max(0.0, float(rect.get("height") or 0))


def _rect_overlap_ratio(inner: dict[str, Any], outer: dict[str, Any]) -> float:
    """Share of inner rect area that lies inside outer rect."""
    ix1 = float(inner.get("x") or 0)
    iy1 = float(inner.get("y") or 0)
    ix2 = ix1 + float(inner.get("width") or 0)
    iy2 = iy1 + float(inner.get("height") or 0)
    ox1 = float(outer.get("x") or 0)
    oy1 = float(outer.get("y") or 0)
    ox2 = ox1 + float(outer.get("width") or 0)
    oy2 = oy1 + float(outer.get("height") or 0)
    overlap_w = max(0.0, min(ix2, ox2) - max(ix1, ox1))
    overlap_h = max(0.0, min(iy2, oy2) - max(iy1, oy1))
    inner_area = _rect_area(inner)
    if inner_area <= 0:
        return 0.0
    return (overlap_w * overlap_h) / inner_area


_WEAK_LABELS = frozenset(
    {
        "read more",
        "learn more",
        "more",
        "here",
        "click here",
        "link",
        "continue",
        "see more",
        "view",
        "view more",
        "details",
        "open",
        "→",
        "»",
        "…",
        "...",
    }
)


def _control_label(item: dict[str, Any]) -> str:
    return str(
        item.get("text") or item.get("title") or item.get("aria") or item.get("label") or ""
    ).strip()


def _is_weak_label(label: str) -> bool:
    text = str(label or "").strip()
    if not text or len(text) < 3:
        return True
    return text.lower() in _WEAK_LABELS


def _covering_controls(
    item: dict[str, Any],
    controls: list[dict[str, Any]],
    *,
    threshold: float = 0.72,
) -> list[dict[str, Any]]:
    rect = item.get("rect")
    if not isinstance(rect, dict):
        return []
    covering: list[dict[str, Any]] = []
    for control in controls:
        control_rect = control.get("rect")
        if not isinstance(control_rect, dict):
            continue
        if _rect_overlap_ratio(rect, control_rect) >= threshold:
            covering.append(control)
    return covering


def _transfer_content_label(control: dict[str, Any], content: dict[str, Any]) -> None:
    """Copy card/article display info onto a weak overlapping control (e.g. news link)."""
    content_label = str(content.get("title") or content.get("text") or "").strip()
    if content_label and not _is_weak_label(content_label) and _is_weak_label(_control_label(control)):
        control["text"] = content_label[:120]
        if not str(control.get("title") or "").strip():
            control["title"] = content_label[:120]
    # Always merge visible meta the user can see on the card (dates/authors/byline).
    if content.get("dates"):
        existing = [
            str(d).strip()
            for d in (control.get("dates") or [])
            if str(d).strip()
        ]
        for date in content.get("dates") or []:
            value = str(date).strip()
            if value and value.lower() not in {d.lower() for d in existing}:
                existing.append(value)
        if existing:
            control["dates"] = existing[:4]
    if content.get("authors") and not control.get("authors"):
        control["authors"] = content.get("authors")
    if content.get("byline") and not control.get("byline"):
        control["byline"] = content.get("byline")
    if content.get("content_role") or content.get("kind"):
        control.setdefault("content_role", content.get("content_role") or content.get("kind"))
    if content.get("href") and not control.get("href"):
        control["href"] = content.get("href")


def merge_capture_elements(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Combine interactable controls with spatial content blocks for the page map."""
    controls = [dict(item) for item in (state.get("interactables") or []) if isinstance(item, dict)]
    for item in controls:
        item.setdefault("map_layer", "control")
    content_items = [
        dict(item)
        for item in (state.get("page_content_map") or [])
        if isinstance(item, dict)
    ]
    if not content_items:
        return controls

    merged = list(controls)
    seen_content_keys: set[str] = set()

    for raw in content_items:
        item = dict(raw)
        item.setdefault("map_layer", "content")
        key = _content_dedupe_key(item)
        if key in seen_content_keys:
            continue
        rect = item.get("rect")
        if not isinstance(rect, dict) or _rect_area(rect) <= 0:
            continue
        covering = _covering_controls(item, controls)
        if covering:
            # Prefer one labeled control over a duplicate content box under the same hit target.
            for control in covering:
                _transfer_content_label(control, item)
            continue
        merged.append(item)
        if key:
            seen_content_keys.add(key)
    for index, item in enumerate(merged):
        item["index"] = index
    apply_merged_labels_to_interactables(state, merged)
    return merged


def apply_merged_labels_to_interactables(
    state: dict[str, Any],
    merged: list[dict[str, Any]],
) -> None:
    """Push enriched display labels from the map back onto agent interactables."""
    by_id = {
        str(item.get("id")): item
        for item in merged
        if isinstance(item, dict)
        and item.get("id")
        and item.get("map_layer") != "content"
    }
    if not by_id:
        return
    for raw in state.get("interactables") or []:
        if not isinstance(raw, dict):
            continue
        enriched = by_id.get(str(raw.get("id") or ""))
        if not enriched:
            continue
        if enriched.get("text") and _is_weak_label(_control_label(raw)):
            raw["text"] = enriched.get("text")
        if enriched.get("title") and not str(raw.get("title") or "").strip():
            raw["title"] = enriched.get("title")
        if enriched.get("dates"):
            existing = [str(d).strip() for d in (raw.get("dates") or []) if str(d).strip()]
            for date in enriched.get("dates") or []:
                value = str(date).strip()
                if value and value.lower() not in {d.lower() for d in existing}:
                    existing.append(value)
            if existing:
                raw["dates"] = existing[:4]
        if enriched.get("authors") and not raw.get("authors"):
            raw["authors"] = enriched.get("authors")
        if enriched.get("byline") and not raw.get("byline"):
            raw["byline"] = enriched.get("byline")
        if enriched.get("content_role"):
            raw.setdefault("content_role", enriched.get("content_role"))
        if enriched.get("href") and not raw.get("href"):
            raw["href"] = enriched.get("href")


def _content_dedupe_key(item: dict[str, Any]) -> str:
    rect = item.get("rect") if isinstance(item.get("rect"), dict) else {}
    text = str(item.get("text") or item.get("title") or "").strip().lower()[:96]
    return (
        f"{item.get('map_layer') or 'content'}|{item.get('kind') or ''}|{text}|"
        f"{round(float(rect.get('x') or 0))}|{round(float(rect.get('y') or 0))}|"
        f"{round(float(rect.get('width') or 0))}|{round(float(rect.get('height') or 0))}"
    )


def apply_content_defaults(capture: dict[str, Any]) -> None:
    """Deterministic interactive defaults for content-layer elements when AI is off."""
    kept = 0
    rejected = 0
    for item in capture.get("elements") or []:
        if not isinstance(item, dict):
            continue
        if item.get("map_layer") != "content":
            continue
        if item.get("ai_interactive") is not None:
            continue
        interactive = bool(item.get("likely_clickable"))
        item["ai_interactive"] = interactive
        item["ai_confidence"] = 0.85 if interactive else 0.9
        item["ai_control_type"] = "card" if interactive else "content"
        item["ai_reason"] = (
            "Clickable content block (pointer cursor or card pattern)."
            if interactive
            else "Read-only page content block."
        )
        if interactive:
            kept += 1
        else:
            rejected += 1
    summary = capture.setdefault("summary", {})
    summary["ai_kept"] = int(summary.get("ai_kept") or 0) + kept
    summary["ai_rejected"] = int(summary.get("ai_rejected") or 0) + rejected


def promote_clickable_content(state: dict[str, Any], capture: dict[str, Any]) -> None:
    """Expose clickable content blocks as interactables for browser agents."""
    existing_ids = {
        str(item.get("id"))
        for item in (state.get("interactables") or [])
        if isinstance(item, dict) and item.get("id")
    }
    promoted: list[dict[str, Any]] = []
    for raw in capture.get("elements") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("map_layer") != "content" or not raw.get("likely_clickable"):
            continue
        if raw.get("effective_interactive") is False or raw.get("user_interactive") is False:
            continue
        if raw.get("ai_interactive") is False and raw.get("user_interactive") is None:
            continue
        element_id = str(raw.get("id") or "")
        if not element_id or element_id in existing_ids:
            continue
        label = str(raw.get("title") or raw.get("text") or raw.get("aria") or "content").strip()
        item = {
            "id": element_id,
            "index": len(state.get("interactables") or []) + len(promoted),
            "kind": "card" if raw.get("likely_clickable") else str(raw.get("kind") or "content"),
            "role": raw.get("role"),
            "text": label[:120] or None,
            "aria": raw.get("aria"),
            "href": raw.get("href"),
            "rect": raw.get("rect"),
            "css_path": raw.get("css_path"),
            "tag": raw.get("tag"),
            "map_layer": "content",
            "content_role": raw.get("content_role") or raw.get("kind"),
            "dates": raw.get("dates"),
            "authors": raw.get("authors"),
            "byline": raw.get("byline"),
            "title": raw.get("title") or label[:120] or None,
            "likely_clickable": True,
            "from_content_map": True,
            "widget": "click",
            "action_hint": f'Click this card: "{label[:80]}".' if label else "Click this content card.",
            "ai_interactive": raw.get("ai_interactive"),
            "user_interactive": raw.get("user_interactive"),
            "effective_interactive": raw.get("effective_interactive"),
            "map_matched": raw.get("map_matched"),
            "playwright_locator": raw.get("locator"),
        }
        promoted.append(item)
        existing_ids.add(element_id)
    if promoted:
        state["interactables"] = list(state.get("interactables") or []) + promoted
        state["interactables_total"] = len(state["interactables"])


def summarize_map_layers(elements: list[dict[str, Any]]) -> dict[str, int]:
    controls = 0
    content = 0
    clickable_content = 0
    for item in elements:
        if not isinstance(item, dict):
            continue
        if item.get("map_layer") == "content":
            content += 1
            if item.get("likely_clickable"):
                clickable_content += 1
        else:
            controls += 1
    return {
        "controls": controls,
        "content": content,
        "clickable_content": clickable_content,
    }
