from __future__ import annotations

import hashlib
import re
from typing import Any

from ui_test.interactables import element_key

_STATE_FIELDS = ("disabled", "expanded", "collapsed", "selected", "checked", "value")
_PROGRESS_FIELDS = ("disabled", "expanded", "collapsed", "kind", "role")
_CLICK_BLOCK_ERROR_RE = re.compile(
    r"(intercept|not visible|not enabled|outside of the viewport|Timeout|"
    r"element is obscured|another element|pointer.?events|overlay)",
    re.I,
)
_DISMISS_CONTROL_RE = re.compile(
    r"\b(close|dismiss|no thanks|not now|accept|reject|agree|got it|continue|"
    r"allow|deny|subscribe|sign up|maybe later|keep watching|skip(?:\s+ad)?|"
    r"close video|not interested|no,? thanks)\b",
    re.I,
)


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


def progress_fingerprint(state: dict[str, Any]) -> str:
    """Stable page identity for loop detection — ignores transient form values."""
    url = str(state.get("url") or "").strip()
    blockers = sorted(
        f"{item.get('id') or ''}:{item.get('text') or item.get('label') or ''}"
        for item in (state.get("blocking_overlays") or [])
        if isinstance(item, dict)
    )
    controls: list[str] = []
    for item in state.get("interactables") or []:
        if not isinstance(item, dict):
            continue
        control_id = str(item.get("id") or "").strip()
        if not control_id:
            continue
        parts = [control_id]
        for field in _PROGRESS_FIELDS:
            parts.append(f"{field}={item.get(field)!s}")
        controls.append("|".join(parts))
    return _text_hash(f"{url}\n{'\n'.join(blockers)}\n{'\n'.join(sorted(controls))}")


def action_signature(action: dict[str, Any]) -> str:
    return "|".join(
        [
            str(action.get("action") or ""),
            str(action.get("target_id") or ""),
            str(action.get("url") or ""),
            str(action.get("value_key") or ""),
            str(action.get("value") or ""),
        ]
    )


def is_no_progress(
    before: dict[str, Any],
    after: dict[str, Any],
    delta: dict[str, Any],
) -> bool:
    """True only when the action changed nothing observable on the page."""
    return not bool(delta.get("meaningful_change"))


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


def _dismissish_controls(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        role = str(item.get("role") or "").lower()
        if kind not in {"button", "link", "blz-button", "div"} and role not in {
            "button",
            "link",
        }:
            continue
        label = " ".join(
            str(item.get(key) or "")
            for key in ("text", "aria", "label", "title")
        )
        if _DISMISS_CONTROL_RE.search(label):
            out.append(item)
    return out[:12]


def diagnose_action_stall(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    error: str | None = None,
    action: dict[str, Any] | None = None,
    delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain a failed/no-progress action by comparing the pre/post page scans.

    When a button "does nothing", the usual cause is a popup that appeared or was
    already covering the target — rescan diffs make that visible.
    """
    delta = delta if isinstance(delta, dict) else diff_page_states(before, after)
    err = str(error or "")
    action = action if isinstance(action, dict) else {}
    new_blockers = list(delta.get("new_blockers") or [])
    after_blockers = [
        item
        for item in (after.get("blocking_overlays") or [])
        if isinstance(item, dict)
    ]
    added = [
        item
        for item in (delta.get("interactables_added") or [])
        if isinstance(item, dict)
    ]
    dismiss_controls = _dismissish_controls(added) or _dismissish_controls(
        list(after.get("interactables") or [])
    )
    click_error = bool(err and _CLICK_BLOCK_ERROR_RE.search(err))
    url_stuck = not bool(delta.get("url_changed"))
    action_name = str(action.get("action") or "").lower()
    navigation_intent = action_name in {"click", "navigate", "press"}

    reasons: list[str] = []
    suspect_blocker = False
    if new_blockers:
        suspect_blocker = True
        reasons.append("new blocking overlay appeared after the action")
    elif after_blockers and (click_error or (navigation_intent and url_stuck and is_no_progress(before, after, delta))):
        suspect_blocker = True
        reasons.append("blocking overlay still present on rescan")
    if click_error:
        suspect_blocker = True
        reasons.append(f"playwright error suggests a cover/intercept: {err[:160]}")
    # Popup chrome (Close / No thanks) often counts as a "meaningful" interactable
    # delta even though navigation failed — treat newly appeared dismiss controls
    # as a blocker signal when the URL did not change.
    added_dismiss = _dismissish_controls(added)
    if navigation_intent and url_stuck and added_dismiss:
        if not suspect_blocker:
            suspect_blocker = True
            reasons.append("new dismiss/consent controls appeared while navigation stalled")
    elif (
        dismiss_controls
        and navigation_intent
        and url_stuck
        and is_no_progress(before, after, delta)
    ):
        if not suspect_blocker:
            suspect_blocker = True
            reasons.append("dismiss/consent controls present while navigation stalled")
    if not reasons and navigation_intent and url_stuck and is_no_progress(before, after, delta):
        reasons.append("page scan unchanged after action — possible invisible blocker or dead control")

    return {
        "suspect_blocker": suspect_blocker,
        "reasons": reasons,
        "reason": "; ".join(reasons) if reasons else "unknown stall",
        "new_blockers": new_blockers,
        "blocking_overlays": after_blockers,
        "dismiss_controls": [
            {
                "id": item.get("id"),
                "text": item.get("text") or item.get("aria") or item.get("label"),
                "kind": item.get("kind"),
            }
            for item in dismiss_controls[:8]
        ],
        "url_changed": bool(delta.get("url_changed")),
        "click_error": click_error,
        "recommended": (
            "clear_blocker"
            if suspect_blocker
            else "try_different_control"
        ),
    }
