from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

# Generic modal / consent button language — not site-specific.
OVERLAY_ACTION_RE = re.compile(
    r"\b(accept|agree|allow|ok|okay|close|continue|confirm|dismiss|got it|understood|reject|decline|save)\b",
    re.I,
)
FORM_KINDS = {"textbox", "combobox", "input", "select", "textarea", "spinbutton"}
STRUCTURAL_LANDMARKS = {"main", "nav", "navigation", "banner", "content", "search"}
SEMANTIC_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CONTROL_ID_RE = re.compile(r"^(el[-_]|input-|select-|btn-)")
# Mirrors browser_explore.ALLOWED_ACTIONS (kept local to avoid a circular import).
_ACTION_NAMES = {
    "click",
    "navigate",
    "fill",
    "select",
    "press",
    "scroll",
    "back",
    "wait",
    "extract",
    "report",
    "help",
    "provide_values",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"\W+", text.lower()) if len(t) > 2}


def _label(item: dict[str, Any]) -> str:
    for key in ("text", "aria", "label", "placeholder", "name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:120]
    return str(item.get("id") or "control")[:80]


def _action_type(item: dict[str, Any]) -> str:
    hint = str(item.get("action_hint") or "").lower()
    kind = str(item.get("kind") or item.get("role") or "").lower()
    if kind in FORM_KINDS or "fill" in hint:
        return "fill"
    if kind in {"select", "combobox"} or "select" in hint:
        return "select"
    if kind == "link" or item.get("href"):
        return "navigate"
    return "click"


def _query_score(item: dict[str, Any], query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    blob = f"{_label(item)} {item.get('href') or ''}".lower()
    score = sum(2 for token in query_tokens if token in blob)
    if score:
        # Content-page links (patch notes, changelogs, news articles) beat nav links.
        from web_surf.page_match import _CONTENT_PATH_HINTS

        path = urlsplit(str(item.get("href") or "").lower()).path
        score += sum(1 for hint in _CONTENT_PATH_HINTS if hint in path)
    return score


def _structural_score(item: dict[str, Any]) -> int:
    landmark = str(item.get("landmark") or "").lower()
    kind = str(item.get("kind") or item.get("role") or "").lower()
    score = 0
    if landmark in STRUCTURAL_LANDMARKS:
        score += 2
    if kind in {"link", "button", "menuitem"}:
        score += 1
    if str(item.get("placeholder") or "").strip():
        score += 2
    return score


def _overlay_score(item: dict[str, Any], *, has_overlay: bool) -> int:
    if not has_overlay:
        return 0
    label = _label(item).lower()
    score = 0
    if OVERLAY_ACTION_RE.search(label):
        score += 8
    if _action_type(item) in {"fill", "select"}:
        score += 4
    return score


def _control_priority(item: dict[str, Any], query_tokens: set[str], *, has_overlay: bool) -> int:
    if item.get("disabled"):
        return -1000
    return (
        _query_score(item, query_tokens)
        + _structural_score(item)
        + _overlay_score(item, has_overlay=has_overlay)
    )


def compact_control(item: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "action": _action_type(item),
        "label": _label(item),
    }
    href = str(item.get("href") or "").strip()
    if href:
        row["href"] = href
    options = item.get("options")
    if isinstance(options, list) and options:
        row["options"] = [str(opt)[:80] for opt in options[:8]]
    return row


def curate_controls(
    interactables: list[dict[str, Any]] | None,
    *,
    query: str = "",
    has_overlay: bool = False,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Keep a balanced mix of overlay, goal-relevant, and structural controls."""
    query_tokens = _tokens(query)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, raw in enumerate(interactables or []):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        ranked.append((_control_priority(raw, query_tokens, has_overlay=has_overlay), index, raw))
    ranked.sort(key=lambda row: (row[0], -row[1]), reverse=True)

    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        control_id = str(item.get("id") or "")
        if not control_id or control_id in seen:
            return
        seen.add(control_id)
        chosen.append(compact_control(item))

    # Overlay dismiss / form controls first when a modal is present.
    if has_overlay:
        for score, _, item in ranked:
            if score < 0:
                continue
            if _overlay_score(item, has_overlay=True) > 0:
                add(item)
            if len(chosen) >= min(12, limit):
                break

    # Goal-relevant controls.
    for score, _, item in ranked:
        if score < 0:
            continue
        if query_tokens and _query_score(item, query_tokens) > 0:
            add(item)
        if len(chosen) >= limit - 8:
            break

    # Structural navigation / search so unknown sites stay explorable.
    for score, _, item in ranked:
        if score < 0:
            continue
        if _structural_score(item) > 0:
            add(item)
        if len(chosen) >= limit:
            break

    # Stable fill from original DOM order if we still have room.
    if len(chosen) < limit:
        for raw in interactables or []:
            if isinstance(raw, dict):
                add(raw)
            if len(chosen) >= limit:
                break

    return chosen[:limit]


def curate_text(text: str, *, query: str = "", max_chars: int = 1800) -> str:
    """Keep page lead + goal-relevant lines — works when the page topic != query."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    lead = raw[: min(500, max_chars // 3)]
    query_tokens = _tokens(query)
    chunks: list[tuple[int, str]] = []
    for line in re.split(r"(?<=[.!?])\s+|\n+", raw):
        line = line.strip()
        if not line or len(line) < 8 or line in lead:
            continue
        score = sum(1 for token in query_tokens if token in line.lower())
        if len(line) < 100 and line[:1].isupper():
            score += 1
        chunks.append((score, line))
    chunks.sort(key=lambda row: row[0], reverse=True)

    parts = [lead]
    size = len(lead)
    generic_added = 0
    for score, line in chunks:
        if size + len(line) > max_chars:
            break
        if query_tokens and score == 0 and generic_added >= 4:
            continue
        parts.append(line)
        size += len(line) + 1
        if score == 0:
            generic_added += 1
    return "\n".join(parts)[:max_chars]


def compact_routes(routes: list[str] | set[str], *, limit: int = 30) -> list[str]:
    """Keep full URLs so navigation works across multiple origins."""
    compact: list[str] = []
    for route in sorted({str(item).strip() for item in routes if str(item).strip()}):
        if route not in compact:
            compact.append(route)
        if len(compact) >= limit:
            break
    return compact


def compact_blockers(blockers: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in blockers or []:
        if not isinstance(raw, dict):
            continue
        rows.append(
            {
                "id": str(raw.get("id") or ""),
                "text": str(raw.get("text") or raw.get("label") or "")[:200],
            }
        )
    return rows[:5]


def compact_history(history: list[dict[str, Any]] | None, *, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for item in (history or [])[-limit:]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "?")
        target = str(item.get("target_id") or "").strip()
        label = str(item.get("target_label") or "").strip()
        url = str(item.get("target_href") or item.get("url") or "").strip()
        status = "ok" if item.get("ok") else "fail"
        error = str(item.get("error") or "").strip()
        line = f"{action}:{target or '-'}"
        if label:
            line = f'{line} "{label[:50]}"'
        if url:
            line = f"{line} -> {url[:90]}"
        line = f"{line} {status}"
        if error:
            line = f"{line} ({error[:120]})"
        lines.append(line)
    return lines


def compact_transition(transition: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(transition, dict):
        return None
    delta = transition.get("delta")
    if not isinstance(delta, dict):
        delta = transition
    added = delta.get("interactables_added") or []
    new_blockers = compact_blockers(delta.get("new_blockers"))
    new_controls = [
        compact_control(item)
        for item in added[:8]
        if isinstance(item, dict) and item.get("id")
    ]
    if not (
        delta.get("url_changed")
        or delta.get("visible_text_changed")
        or new_blockers
        or new_controls
    ):
        return None
    return {
        "url_changed": bool(delta.get("url_changed")),
        "text_changed": bool(delta.get("visible_text_changed")),
        "blockers": new_blockers,
        "new_controls": new_controls,
    }


def curate_browse_context(
    *,
    query: str,
    step_id: str,
    snapshot: dict[str, Any],
    discovered_routes: set[str] | list[str],
    available_value_keys: list[str] | None = None,
    field_mapping: dict[str, str] | None = None,
    recent_history: list[dict[str, Any]] | None = None,
    last_transition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overlays = snapshot.get("blocking_overlays") or []
    payload: dict[str, Any] = {
        "goal": query.strip(),
        "step": step_id,
        "page": {
            "url": str(snapshot.get("url") or ""),
            "title": str(snapshot.get("title") or "")[:140],
            "text": curate_text(str(snapshot.get("visible_text") or ""), query=query),
        },
        "overlays": compact_blockers(overlays),
        "controls": curate_controls(
            snapshot.get("interactables"),
            query=query,
            has_overlay=bool(overlays),
        ),
        "routes": compact_routes(list(discovered_routes)),
    }
    keys = [str(key) for key in (available_value_keys or []) if str(key)]
    if keys:
        payload["form_keys"] = keys
    mapping = {str(k): str(v) for k, v in (field_mapping or {}).items() if k and v}
    if mapping:
        payload["form_map"] = mapping
    history = compact_history(recent_history)
    if history:
        payload["history"] = history
    transition = compact_transition(last_transition)
    if transition:
        payload["last_change"] = transition
    return payload


def curate_form_plan_context(
    *,
    query: str,
    snapshot: dict[str, Any],
    existing_keys: list[str] | None = None,
) -> dict[str, Any]:
    from web_surf.form_values import collect_form_fields

    fields = collect_form_fields(snapshot)
    compact_fields: list[dict[str, Any]] = []
    for field in fields[:14]:
        row: dict[str, Any] = {
            "id": field["id"],
            "label": field["label"][:100],
            "action": field.get("action_hint") or field.get("kind") or "fill",
        }
        if field.get("placeholder"):
            row["placeholder"] = field["placeholder"][:80]
        options = field.get("options")
        if isinstance(options, list) and options:
            row["options"] = [str(opt)[:60] for opt in options[:8]]
        compact_fields.append(row)
    payload: dict[str, Any] = {
        "goal": query.strip(),
        "overlays": compact_blockers(snapshot.get("blocking_overlays")),
        "fields": compact_fields,
    }
    keys = [str(key) for key in (existing_keys or []) if str(key)]
    if keys:
        payload["existing_keys"] = keys
    return payload


def curate_extract_context(
    *,
    page_text: str,
    page_url: str,
    page_title: str,
    research_spec: dict[str, Any],
    max_chars: int = 5000,
) -> str:
    needed = [
        str(item).strip()
        for item in (research_spec.get("data_needed") or [])
        if str(item).strip()
    ]
    query = " ".join(
        [
            str(research_spec.get("summary") or ""),
            *needed,
            str(research_spec.get("source_query") or ""),
        ]
    ).strip()
    curated = curate_text(page_text, query=query, max_chars=max_chars)
    lines = [
        f"goal: {research_spec.get('summary') or query}",
        f"need: {', '.join(needed) if needed else 'relevant facts'}",
        f"title: {page_title}",
        f"url: {page_url}",
        "content:",
        curated,
    ]
    return "\n".join(lines)


def _looks_like_control_id(key: str) -> bool:
    text = str(key).strip()
    if not text:
        return False
    if CONTROL_ID_RE.match(text):
        return True
    return text.startswith("el") or ("-" in text and "_" not in text)


def _dict_to_fill_or_values(data: dict[str, Any], *, reason: str = "") -> dict[str, Any] | None:
    pairs = [(str(k), str(v)) for k, v in data.items() if str(k).strip() and str(v).strip()]
    if not pairs:
        return None
    if len(pairs) == 1 and _looks_like_control_id(pairs[0][0]):
        return {
            "action": "fill",
            "target_id": pairs[0][0],
            "value": pairs[0][1],
            "reason": reason,
        }
    if all(not _looks_like_control_id(key) for key, _ in pairs):
        return {"action": "provide_values", "form_values": dict(pairs), "reason": reason}
    return None


def normalize_decision(raw: Any) -> dict[str, Any] | None:
    """Coerce alternate JSON shapes into one action — format-agnostic, not site-specific."""
    if not isinstance(raw, dict):
        return None
    action_field = raw.get("action")
    if isinstance(action_field, str) and action_field.strip():
        return raw
    # {"action": {"type": "click", "target_id": ...}} → flatten the nested object.
    if isinstance(action_field, dict):
        flattened = {
            **{k: v for k, v in raw.items() if k != "action"},
            **action_field,
            "action": str(
                action_field.get("action")
                or action_field.get("type")
                or action_field.get("name")
                or ""
            ),
        }
        if str(flattened.get("action") or "").strip():
            return flattened

    next_actions = None
    for key in ("next_action", "actions", "steps"):
        value = raw.get(key)
        if isinstance(value, list) and value:
            next_actions = value
            break
    if isinstance(next_actions, list) and next_actions:
        first = next_actions[0]
        if isinstance(first, dict):
            normalized = normalize_decision(first)
            if normalized:
                return normalized
            action_type = str(first.get("type") or first.get("name") or "").lower()
            target = first.get("target") if isinstance(first.get("target"), dict) else {}
            target_id = str(
                first.get("target_id")
                or first.get("button_id")
                or target.get("id")
                or ""
            ).strip()
            reason = str(first.get("reason") or "")
            if action_type in {"click", "click_button", "press"} and target_id:
                return {"action": "click", "target_id": target_id, "reason": reason}
            if action_type in {"fill", "set_value"} and target_id:
                value = first.get("value")
                if isinstance(value, str):
                    return {"action": "fill", "target_id": target_id, "value": value, "reason": reason}
                if isinstance(value, dict):
                    converted = _dict_to_fill_or_values(value, reason=reason)
                    if converted:
                        return converted
            value = first.get("value") or first.get("form_values")
            if isinstance(value, dict):
                converted = _dict_to_fill_or_values(value, reason=reason)
                if converted:
                    return converted

    # Top-level "type"/"name" instead of "action".
    action_type = str(raw.get("type") or raw.get("name") or "").strip().lower()
    if action_type in _ACTION_NAMES:
        if action_type in {"provide_values", "fill"} and not isinstance(raw.get("form_values"), dict):
            value = raw.get("value")
            if isinstance(value, dict):
                converted = _dict_to_fill_or_values(value, reason=str(raw.get("reason") or ""))
                if converted:
                    return converted
        coerced = {**raw, "action": action_type}
        if not str(coerced.get("target_id") or "").strip():
            target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
            target_id = str(raw.get("button_id") or target.get("id") or "").strip()
            if target_id:
                coerced["target_id"] = target_id
        return coerced

    button_id = str(raw.get("button_id") or raw.get("target_id") or "").strip()
    action_type = str(raw.get("type") or raw.get("action") or "").lower()
    if button_id and "click" in action_type:
        return {"action": "click", "target_id": button_id, "reason": str(raw.get("reason") or "")}

    # Action-name-as-key shape: {"click": {"target_id": "x"}} or {"navigate": "https://…"}.
    for key, value in raw.items():
        name = str(key).strip().lower()
        if name not in _ACTION_NAMES:
            continue
        if isinstance(value, dict):
            return {**value, "action": name, "reason": str(raw.get("reason") or value.get("reason") or "")}
        if isinstance(value, str) and value.strip():
            field = "url" if name == "navigate" else "target_id"
            return {"action": name, field: value.strip(), "reason": str(raw.get("reason") or "")}
    return None
