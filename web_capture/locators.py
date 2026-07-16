from __future__ import annotations

import json
from typing import Any


def _candidate_locator(page: Any, candidate: dict[str, Any]) -> Any:
    kind = str(candidate.get("kind") or "")
    value = str(candidate.get("value") or "")
    if kind == "test_id":
        return page.get_by_test_id(value)
    if kind == "role":
        return page.get_by_role(
            str(candidate.get("role") or "button"),
            name=str(candidate.get("name") or value),
            exact=True,
        )
    if kind == "label":
        return page.get_by_label(value, exact=True)
    if kind == "placeholder":
        return page.get_by_placeholder(value, exact=True)
    if kind in {"css", "name", "href"}:
        return page.locator(value)
    raise ValueError(f"Unsupported locator candidate: {kind}")


def generated_candidates(item: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    test_id = str(item.get("test_id") or "").strip()
    role = str(item.get("role") or item.get("kind") or "").strip()
    text = str(item.get("aria") or item.get("label") or item.get("text") or "").strip()
    name = str(item.get("name") or "").strip()
    placeholder = str(item.get("placeholder") or "").strip()
    href = str(item.get("href") or "").strip()
    tag = str(item.get("tag") or "").strip()
    if test_id:
        candidates.append({"kind": "test_id", "value": test_id})
    if role and text and role in {
        "button", "link", "menuitem", "textbox", "combobox", "spinbutton",
        "checkbox", "radio", "tab", "switch",
    }:
        candidates.append({"kind": "role", "value": text, "role": role, "name": text})
    if item.get("label"):
        candidates.append({"kind": "label", "value": str(item["label"])})
    if placeholder:
        candidates.append({"kind": "placeholder", "value": placeholder})
    if name:
        selector_tag = tag or ("input" if item.get("kind") == "input" else str(item.get("kind") or "*"))
        candidates.append({"kind": "name", "value": f"{selector_tag}[name={json.dumps(name)}]"})
    if href and item.get("kind") == "link":
        candidates.append({"kind": "href", "value": f"a[href={json.dumps(href)}]"})
    if item.get("css_path"):
        candidates.append({"kind": "css", "value": str(item["css_path"])})
    return candidates


def validate_capture_locators(page: Any, capture: dict[str, Any]) -> dict[str, Any]:
    summary = capture.setdefault("summary", {})
    counts = {"unique": 0, "ambiguous": 0, "unresolved": 0}
    for item in capture.get("elements") or []:
        if not isinstance(item, dict):
            continue
        if item.get("locator_status") == "synthetic":
            counts["unresolved"] += 1
            continue
        candidates = list(item.get("locator_candidates") or generated_candidates(item))
        frame_index = item.get("frame_index")
        root = page
        if frame_index is not None:
            try:
                root = list(page.frames)[int(frame_index) + 1]
            except (IndexError, TypeError, ValueError):
                root = page
        for candidate in candidates:
            if frame_index is not None:
                candidate["frame_index"] = int(frame_index)
                candidate["frame_url"] = str(item.get("frame_url") or "")
        item["locator_candidates"] = candidates
        selected = None
        has_ambiguous = False
        for candidate in candidates:
            try:
                count = int(_candidate_locator(root, candidate).count())
            except Exception:
                count = 0
            candidate["count"] = count
            if count == 1:
                selected = candidate
                break
            if count > 1:
                has_ambiguous = True
        if selected:
            item["locator_status"] = "unique"
            item["locator"] = selected
            counts["unique"] += 1
        elif has_ambiguous:
            item["locator_status"] = "ambiguous"
            item["deterministic_issues"] = [
                *list(item.get("deterministic_issues") or []),
                "ambiguous_locator",
            ]
            counts["ambiguous"] += 1
        else:
            item["locator_status"] = "unresolved"
            item["deterministic_issues"] = [
                *list(item.get("deterministic_issues") or []),
                "unresolved_locator",
            ]
            counts["unresolved"] += 1
    summary.update(counts)
    return capture
