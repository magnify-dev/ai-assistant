from __future__ import annotations

import hashlib
from typing import Any

from ui_test.interactables import element_key

_STATE_FIELDS = ("disabled", "expanded", "selected", "checked", "value")


def _text_hash(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _elements(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in state.get("interactables") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip() or element_key(item)
        if key:
            result[key] = item
    return result


def diff_page_states(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Describe the meaningful semantic change caused by one browser action."""
    before_elements = _elements(before)
    after_elements = _elements(after)
    added_keys = [key for key in after_elements if key not in before_elements]
    removed_keys = [key for key in before_elements if key not in after_elements]
    changed: list[dict[str, Any]] = []
    for key in before_elements.keys() & after_elements.keys():
        old, new = before_elements[key], after_elements[key]
        fields = [field for field in _STATE_FIELDS if old.get(field) != new.get(field)]
        if fields:
            changed.append(
                {
                    "id": key,
                    "fields": fields,
                    "before": {field: old.get(field) for field in fields},
                    "after": {field: new.get(field) for field in fields},
                    "element": new,
                }
            )

    before_blockers = before.get("blocking_overlays") or []
    after_blockers = after.get("blocking_overlays") or []
    before_blocker_ids = {str(item.get("id") or item.get("text") or "") for item in before_blockers if isinstance(item, dict)}
    new_blockers = [
        item
        for item in after_blockers
        if isinstance(item, dict) and str(item.get("id") or item.get("text") or "") not in before_blocker_ids
    ]
    text_changed = _text_hash(before.get("visible_text")) != _text_hash(after.get("visible_text"))
    url_changed = str(before.get("url") or "") != str(after.get("url") or "")
    title_changed = str(before.get("title") or "") != str(after.get("title") or "")
    meaningful_change = bool(
        url_changed
        or title_changed
        or text_changed
        or added_keys
        or removed_keys
        or changed
        or new_blockers
    )
    return {
        "before_snapshot_id": before.get("snapshot_id"),
        "after_snapshot_id": after.get("snapshot_id"),
        "url_changed": url_changed,
        "title_changed": title_changed,
        "visible_text_changed": text_changed,
        "interactables_added": [after_elements[key] for key in added_keys],
        "interactables_removed": [before_elements[key] for key in removed_keys],
        "interactables_changed": changed,
        "new_blockers": new_blockers,
        "blocking_overlays": after_blockers,
        "meaningful_change": meaningful_change,
    }
