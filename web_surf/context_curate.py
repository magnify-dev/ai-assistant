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
CONTENT_LABEL_RE = re.compile(
    r"\b(patch notes|changelog|release notes|updates?|what's new|whats new)\b",
    re.I,
)
MARKETING_LABEL_RE = re.compile(
    r"\b(purchase|buy now|shop now|subscribe|sign up|expansion|learn more|pre-order|preorder)\b",
    re.I,
)
SEMANTIC_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CONTROL_ID_RE = re.compile(r"^(el[-_]|input-|select-|btn-)")
# Mirrors browser_explore.ALLOWED_ACTIONS (kept local to avoid a circular import).
_ACTION_NAMES = {
    "click",
    "navigate",
    "swap_branch",
    "fill",
    "select",
    "press",
    "scroll",
    "back",
    "wait",
    "extract",
    "filter",
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


def _widget_type(item: dict[str, Any]) -> str:
    widget = str(item.get("widget") or "").strip().lower()
    if widget:
        return widget
    kind = str(item.get("kind") or "").lower()
    role = str(item.get("role") or "").lower()
    input_type = str(item.get("input_type") or "").lower()
    if kind == "select":
        return "select"
    if kind == "combobox" or role == "combobox":
        return "combobox"
    if kind == "textarea":
        return "textarea"
    if role == "spinbutton" or input_type == "number":
        return "number"
    if input_type in {"date", "email", "tel", "search", "password"}:
        return input_type
    if kind in {"input", "textbox"} or role in {"textbox", "searchbox"}:
        return "text"
    return kind or "text"


def _action_type(item: dict[str, Any]) -> str:
    widget = _widget_type(item)
    if widget in {"select", "combobox"}:
        return "select"
    hint = str(item.get("action_hint") or "").lower()
    kind = str(item.get("kind") or item.get("role") or "").lower()
    if kind in FORM_KINDS or widget in {"text", "number", "date", "email", "tel", "search", "textarea"} or "fill" in hint:
        return "fill"
    if kind == "link" or item.get("href"):
        return "navigate"
    return "click"


def _query_score(
    item: dict[str, Any],
    query_tokens: set[str],
    *,
    publisher_domains: set[str] | None = None,
    query: str = "",
) -> int:
    if not query_tokens:
        return 0
    blob = f"{_label(item)} {item.get('href') or ''}".lower()
    score = sum(2 for token in query_tokens if token in blob)
    if CONTENT_LABEL_RE.search(blob):
        score += 18
    if MARKETING_LABEL_RE.search(blob) and not CONTENT_LABEL_RE.search(blob):
        score -= 8
    href = str(item.get("href") or "").strip()
    if href:
        from web_surf.page_match import _CONTENT_PATH_HINTS, parse_content_date, query_implies_recency, url_on_publisher_domain

        path = urlsplit(href.lower()).path
        score += sum(1 for hint in _CONTENT_PATH_HINTS if hint in path)
        if url_on_publisher_domain(href, publisher_domains or set()):
            score += 12
        if query and query_implies_recency(query):
            published = parse_content_date(f"{_label(item)} {href}")
            if published:
                score += published.toordinal() // 10
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


def _control_priority(
    item: dict[str, Any],
    query_tokens: set[str],
    *,
    has_overlay: bool,
    publisher_domains: set[str] | None = None,
    query: str = "",
) -> int:
    if item.get("disabled"):
        return -1000
    return (
        _query_score(item, query_tokens, publisher_domains=publisher_domains, query=query)
        + _structural_score(item)
        + _overlay_score(item, has_overlay=has_overlay)
    )


def compact_control(item: dict[str, Any], *, query: str = "") -> dict[str, Any]:
    widget = _widget_type(item)
    row: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "action": _action_type(item),
        "widget": widget,
        "label": _label(item),
    }
    if query:
        from web_surf.page_match import parse_content_date, query_implies_recency

        if query_implies_recency(query):
            published = parse_content_date(f"{row['label']} {item.get('href') or ''}")
            if published:
                row["published"] = published.isoformat()
    href = str(item.get("href") or "").strip()
    if href:
        row["href"] = href
    name = str(item.get("name") or "").strip()
    if name:
        row["name"] = name[:40]
    input_type = str(item.get("input_type") or "").strip()
    if input_type:
        row["input_type"] = input_type
    value = str(item.get("value") or item.get("selected_label") or "").strip()
    if value:
        row["current"] = value[:40]
    if item.get("expands_section"):
        row["expandable"] = True
        if item.get("collapsed") is True:
            row["collapsed"] = True
        elif item.get("collapsed") is False:
            row["collapsed"] = False
    options = item.get("options")
    if isinstance(options, list) and options:
        row["options"] = [str(opt)[:80] for opt in options[:8]]
        if len(options) > 8:
            row["options_count"] = len(options)
    return row


def compact_form_field(
    item: dict[str, Any],
    *,
    field_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Structured description of one form input for the decision model."""
    widget = _widget_type(item)
    row: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "widget": widget,
        "action": "select" if widget in {"select", "combobox"} else "fill",
        "label": _label(item),
    }
    name = str(item.get("name") or "").strip()
    if name:
        row["name"] = name[:40]
    input_type = str(item.get("input_type") or "").strip()
    if input_type:
        row["input_type"] = input_type
    value = str(item.get("value") or item.get("selected_label") or "").strip()
    if value:
        row["current"] = value[:40]
    options = item.get("options")
    if isinstance(options, list) and options:
        row["options"] = [str(opt)[:60] for opt in options[:10]]
        if len(options) > 10:
            row["options_count"] = len(options)
    mapping = field_mapping or {}
    field_id = str(item.get("id") or "")
    if field_id and field_id in mapping:
        row["value_key"] = mapping[field_id]
    return row


def curate_form_fields(
    interactables: list[dict[str, Any]] | None,
    *,
    field_mapping: dict[str, str] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    from web_surf.form_values import is_form_interactable

    fields: list[dict[str, Any]] = []
    for raw in interactables or []:
        if not isinstance(raw, dict) or not raw.get("id") or raw.get("disabled"):
            continue
        if not is_form_interactable(raw):
            continue
        fields.append(compact_form_field(raw, field_mapping=field_mapping))
        if len(fields) >= limit:
            break
    return fields


def curate_controls(
    interactables: list[dict[str, Any]] | None,
    *,
    query: str = "",
    has_overlay: bool = False,
    limit: int = 40,
    publisher_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Keep a balanced mix of overlay, goal-relevant, and structural controls."""
    from web_surf.page_match import focus_query

    focused = focus_query(query)
    query_tokens = _tokens(focused)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, raw in enumerate(interactables or []):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        ranked.append(
            (
                _control_priority(
                    raw,
                    query_tokens,
                    has_overlay=has_overlay,
                    publisher_domains=publisher_domains,
                    query=focused,
                ),
                index,
                raw,
            )
        )
    ranked.sort(key=lambda row: (row[0], -row[1]), reverse=True)

    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        control_id = str(item.get("id") or "")
        if not control_id or control_id in seen:
            return
        seen.add(control_id)
        chosen.append(compact_control(item, query=focused))

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
        if query_tokens and _query_score(item, query_tokens, publisher_domains=publisher_domains, query=focused) > 0:
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
        error = str(item.get("error") or "").strip()
        status = "ok" if item.get("ok") else "fail"
        if item.get("progress") is False or (
            not item.get("ok") and "no progress" in error.lower()
        ):
            status = "no_change"
        value_key = str(item.get("value_key") or "").strip()
        line = f"{action}:{target or '-'}"
        if value_key:
            line = f"{line}[{value_key}]"
        if label:
            line = f'{line} "{label[:50]}"'
        if url:
            line = f"{line} -> {url[:90]}"
        transition = item.get("transition") if isinstance(item.get("transition"), dict) else {}
        for change in transition.get("interactables_changed") or []:
            if not isinstance(change, dict):
                continue
            after = change.get("after") if isinstance(change.get("after"), dict) else {}
            value = str(after.get("value") or after.get("selected") or "").strip()
            if value:
                line = f"{line} set={value[:30]}"
                break
        line = f"{line} {status}"
        if error:
            line = f"{line} ({error[:120]})"
        lines.append(line)
    return lines


def _compact_field_changes(delta: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for change in delta.get("interactables_changed") or []:
        if not isinstance(change, dict):
            continue
        fields = [str(field) for field in (change.get("fields") or []) if str(field)]
        if not fields:
            continue
        after = change.get("after") if isinstance(change.get("after"), dict) else {}
        values = {
            field: str(after.get(field) or "")[:40]
            for field in fields
            if field in {"value", "selected", "checked"} and str(after.get(field) or "").strip()
        }
        if not values:
            continue
        rows.append({"id": str(change.get("id") or ""), "set": values})
    return rows[:6]


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
    field_changes = _compact_field_changes(delta)
    if not (
        delta.get("url_changed")
        or delta.get("visible_text_changed")
        or new_blockers
        or new_controls
        or field_changes
    ):
        return None
    payload: dict[str, Any] = {
        "url_changed": bool(delta.get("url_changed")),
        "text_changed": bool(delta.get("visible_text_changed")),
        "blockers": new_blockers,
        "new_controls": new_controls,
    }
    if field_changes:
        payload["fields_set"] = field_changes
    return payload


def curate_browse_context(
    *,
    query: str,
    step_id: str,
    snapshot: dict[str, Any],
    discovered_routes: set[str] | list[str],
    available_value_keys: list[str] | None = None,
    field_mapping: dict[str, str] | None = None,
    recent_history: list[dict[str, Any]] | None = None,
    agent_memory: list[dict[str, Any]] | None = None,
    last_transition: dict[str, Any] | None = None,
    blocked_attempts: list[str] | None = None,
    publishers: list[str] | None = None,
    publisher_domains: set[str] | None = None,
    preferred_domains: set[str] | None = None,
    seed_urls: list[str] | None = None,
    candidates: list[Any] | None = None,
    active_branch_url: str | None = None,
    branch_steps: int = 0,
    max_steps_per_branch: int = 20,
    helper_guidance: list[dict[str, Any]] | None = None,
    collected_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from web_surf.form_values import _snapshot_blockers

    overlays = _snapshot_blockers(snapshot)
    from web_surf.page_match import (
        filter_text_by_date,
        focus_query,
        is_content_listing_url,
        page_contains_target_date,
        page_matches_query,
        parse_target_dates,
        parse_user_preferred_domains,
        query_implies_recency,
        url_on_publisher_domain,
        viewport_explored_fraction,
        viewport_has_content_below,
        page_extends_beyond_viewport,
        snapshot_viewport,
    )

    focused_goal = focus_query(query)
    domain_set = set(publisher_domains or set())
    publisher_names = [str(item).strip() for item in (publishers or []) if str(item).strip()]
    preferred_set = set(preferred_domains or set()) or parse_user_preferred_domains(focused_goal)
    visible_text = str(snapshot.get("visible_text") or "")
    target_dates = parse_target_dates(focused_goal)
    curated_page_text = curate_text(visible_text, query=focused_goal, max_chars=4500)
    if target_dates and page_contains_target_date(visible_text, focused_goal):
        filtered = filter_text_by_date(visible_text, focused_goal, max_chars=8000)
        if len(filtered) >= 120:
            curated_page_text = curate_text(filtered, query=focused_goal, max_chars=4500)
    elif query_implies_recency(focused_goal):
        from web_surf.page_match import filter_text_by_recency

        filtered = filter_text_by_recency(visible_text, focused_goal, max_chars=8000)
        if len(filtered) >= 120:
            curated_page_text = curate_text(filtered, query=focused_goal, max_chars=4500)
    payload: dict[str, Any] = {
        "goal": focused_goal.strip(),
        "step": step_id,
        "page": {
            "url": str(snapshot.get("url") or ""),
            "title": str(snapshot.get("title") or "")[:140],
            "text": curated_page_text,
            "text_chars": len(visible_text.strip()),
        },
        "overlays": compact_blockers(overlays),
        "controls": curate_controls(
            snapshot.get("interactables"),
            query=query,
            has_overlay=bool(overlays),
            publisher_domains=domain_set,
        ),
        "routes": compact_routes(list(discovered_routes)),
    }
    if page_extends_beyond_viewport(snapshot):
        vp = snapshot_viewport(snapshot)
        payload["viewport"] = {
            "scroll_y": int(vp["scroll_y"]),
            "page_height": int(vp["document_height"]),
            "view_height": int(vp["height"]),
            "explored_pct": round(viewport_explored_fraction(snapshot) * 100),
            "more_below": viewport_has_content_below(snapshot),
        }
        if query_implies_recency(focused_goal) or is_content_listing_url(str(snapshot.get("url") or "")):
            payload["scroll_note"] = (
                "This page extends beyond the current view. Scroll down or open the top "
                "news/article link to reach the newest content before extract/filter/report."
            )
    if query_implies_recency(focused_goal):
        payload["recency_requirement"] = True
        payload["recency_note"] = (
            "Timing is part of the user's request — pick the NEWEST matching item, not just any "
            "related article. On listing pages prefer links with the latest published date "
            "(usually first/top). When several dated sections appear on one page, collect only "
            "the newest dated section. Do not report older content when newer content is visible "
            "or one click away."
        )

    publisher_routes = [
        route
        for route in discovered_routes
        if url_on_publisher_domain(str(route), domain_set)
    ]
    if publisher_names:
        payload["publishers"] = publisher_names[:8]
    if preferred_set:
        payload["preferred_sources"] = sorted(preferred_set)
        payload["user_directive"] = (
            "The user explicitly requested these sources: "
            + ", ".join(sorted(preferred_set))
            + ". Follow the user's site preference over official/publisher defaults. "
            "Stay on these sites and extract/report there — do not swap to official sources "
            "unless the requested site has no answer."
        )
    if publisher_routes:
        payload["publisher_routes"] = compact_routes(publisher_routes)
    if target_dates:
        payload["target_dates"] = [
            f"{day:02d}.{month:02d}.{year}" for day, month, year in target_dates
        ]
        if page_contains_target_date(visible_text, query):
            payload["content_on_page"] = (
                "The current page already contains text for the target date. "
                "Use action=filter or action=extract once to collect that section, "
                "then action=report. Do not navigate away to other patch-note pages."
            )
        elif page_matches_query(visible_text, focused_goal):
            payload["content_on_page"] = (
                "The visible page text already looks relevant to the goal. "
                "Use action=extract or action=filter once to capture it, then action=report. "
                "Read page.text before clicking away."
            )
    current_url = str(snapshot.get("url") or "").strip().rstrip("/")
    prior_collects = [
        item
        for item in (collected_evidence or [])
        if isinstance(item, dict)
        and str(item.get("url") or "").strip().rstrip("/") == current_url
    ]
    if prior_collects and not overlays:
        latest = prior_collects[-1]
        step_ref = str(latest.get("step_id") or "").strip()
        payload["evidence_collected"] = (
            "Content from this page was already collected"
            + (f" in {step_ref}" if step_ref else "")
            + ". Use action=report now — do not repeat extract or filter."
        )
        payload["report_ready"] = True
    elif prior_collects and overlays:
        payload["evidence_collected"] = (
            "Content was collected but a blocking overlay is still up. "
            "Dismiss the overlay from overlay_map[] first, then use action=report."
        )
    collapsed = [
        control
        for control in payload.get("controls") or []
        if control.get("expandable") and control.get("collapsed")
    ]
    if collapsed:
        payload["collapsed_sections"] = collapsed[:6]
        payload["expand_note"] = (
            "Some controls are collapsed accordion/section headers. "
            "Click a collapsed control whose label matches the goal to expand it "
            "and reveal the hidden content before filter/extract/report."
        )
    keys = [str(key) for key in (available_value_keys or []) if str(key)]
    if keys:
        payload["form_keys"] = keys
    mapping = {str(k): str(v) for k, v in (field_mapping or {}).items() if k and v}
    if mapping:
        payload["form_map"] = mapping
    form_fields = curate_form_fields(
        snapshot.get("interactables"),
        field_mapping=mapping,
    )
    if form_fields:
        payload["form_fields"] = form_fields
        payload["form_note"] = "select→action=select, text→action=fill; use value_key from form_keys"
    history = compact_history(recent_history)
    if history:
        payload["history"] = history[-4:]
    transition = compact_transition(last_transition)
    if transition:
        payload["last_change"] = transition
    from web_surf.explore_branches import build_exploration_menu, summarize_exploration_branches
    from web_surf.form_values import AGE_GATE_AGENT_NOTE, looks_like_age_gate, summarize_overlay_actions

    branch_info = summarize_exploration_branches(
        current_url=str(snapshot.get("url") or ""),
        seed_urls=list(seed_urls or []),
        candidates=list(candidates or []),
        history=list(recent_history or []),
        active_branch_url=str(active_branch_url or snapshot.get("url") or ""),
        branch_steps=branch_steps,
        max_steps_per_branch=max_steps_per_branch,
    )
    from web_surf.agent_memory import (
        compact_agent_memory_for_prompt,
        compact_avoid,
        compact_branch_note,
        compact_failed_steps,
        stuck_reason,
    )

    memory_rows, memory_note = compact_agent_memory_for_prompt(agent_memory, limit=12)
    failed_steps = compact_failed_steps(agent_memory, limit=8)
    avoid = compact_avoid(
        blocked_signatures=blocked_attempts,
        history=list(recent_history or []),
        agent_memory=list(agent_memory or []),
        snapshot=snapshot,
        limit=10,
    )
    branch_note = compact_branch_note(branch_info, agent_memory)
    stuck = stuck_reason(
        snapshot=snapshot,
        branch_info=branch_info,
        failed_steps=failed_steps,
    )

    if stuck:
        payload["stuck"] = stuck
    if branch_note:
        payload["branch_note"] = branch_note
    if failed_steps:
        payload["failed"] = failed_steps
    if avoid:
        payload["avoid"] = avoid
    if memory_rows:
        payload["steps"] = memory_rows
    if memory_note:
        payload["steps_note"] = memory_note
    guidance_lines = [
        str(item.get("instruction") or item.get("error") or "").strip()
        for item in (helper_guidance or [])
        if isinstance(item, dict)
    ]
    guidance_lines = [line for line in guidance_lines if line]
    if guidance_lines:
        payload["guidance"] = guidance_lines[-3:]
    overlay_actions = summarize_overlay_actions(snapshot) if overlays else []
    menu = build_exploration_menu(
        controls=payload.get("controls") or [],
        overlay_actions=overlay_actions,
        branch_info=branch_info,
    )

    payload["branch"] = {
        "current": branch_info.get("current"),
        "alternatives": branch_info.get("alternatives") or [],
        "stall_count": branch_info.get("stall_count", 0),
        "branch_steps": branch_info.get("branch_steps", 0),
        "max_steps_per_branch": branch_info.get("max_steps_per_branch", max_steps_per_branch),
        "can_back": bool(branch_info.get("can_back")),
    }
    payload["menu"] = menu
    payload["explore_note"] = (
        "Pick ONE action from menu[]. Read user_directive, stuck, branch_note, failed, avoid, guidance, "
        "evidence_collected before choosing. "
        "Clear overlays before extract/report. Swap branch only when stalled."
    )

    if looks_like_age_gate(snapshot):
        payload["age_gate_note"] = AGE_GATE_AGENT_NOTE
    if overlays:
        payload["overlay_required"] = True
        if overlay_actions:
            payload["overlay_actions"] = overlay_actions
        from web_surf.form_values import build_overlay_map

        overlay_map = build_overlay_map(snapshot)
        if overlay_map.get("elements"):
            payload["overlay_map"] = overlay_map["elements"]
    return payload


def curate_overlay_context(
    *,
    step_id: str,
    snapshot: dict[str, Any],
    recent_history: list[dict[str, Any]] | None = None,
    blocked_attempts: list[str] | None = None,
    available_value_keys: list[str] | None = None,
    field_mapping: dict[str, str] | None = None,
    agent_memory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Focused context for choosing which overlay control to interact with."""
    from web_surf.form_values import (
        AGE_GATE_AGENT_NOTE,
        build_overlay_map,
        looks_like_age_gate,
        _snapshot_blockers,
    )
    from web_surf.agent_memory import compact_avoid, compact_failed_steps

    overlays = _snapshot_blockers(snapshot)
    overlay_map = build_overlay_map(snapshot)
    payload: dict[str, Any] = {
        "step": step_id,
        "page": {
            "url": str(snapshot.get("url") or ""),
            "title": str(snapshot.get("title") or "")[:140],
        },
        "overlays": compact_blockers(overlays),
        "overlay_map": overlay_map.get("elements") or [],
        "menu": overlay_map.get("menu") or [],
        "overlay_note": (
            "A blocking overlay covers the page. Pick ONE element from overlay_map[] or menu[]. "
            "Copy target_id exactly. Prefer reject/decline for cookie banners when available; "
            "otherwise accept/agree/OK. For age gates fill year/month/day before confirm."
        ),
    }
    failed_steps = compact_failed_steps(agent_memory, limit=6)
    avoid = compact_avoid(
        blocked_signatures=blocked_attempts,
        history=list(recent_history or []),
        agent_memory=list(agent_memory or []),
        snapshot=snapshot,
        limit=8,
    )
    if failed_steps:
        payload["failed"] = failed_steps
    if avoid:
        payload["avoid"] = avoid
    history = compact_history(recent_history, limit=4)
    if history:
        payload["history"] = history
    keys = [str(key) for key in (available_value_keys or []) if str(key)]
    if keys:
        payload["form_keys"] = keys
    mapping = {str(k): str(v) for k, v in (field_mapping or {}).items() if k and v}
    if mapping:
        payload["form_map"] = mapping
    form_fields = curate_form_fields(snapshot.get("interactables"), field_mapping=mapping)
    gate_fields = [field for field in form_fields if field.get("id") in overlay_target_ids_from_map(overlay_map)]
    if gate_fields:
        payload["form_fields"] = gate_fields
        payload["form_note"] = "select→action=select, text→action=fill; use value_key from form_keys"
    if looks_like_age_gate(snapshot):
        payload["age_gate_note"] = AGE_GATE_AGENT_NOTE
    if not payload["overlay_map"]:
        payload["overlay_note"] = (
            "Overlay detected but no controls mapped — try menu[] if present or report stuck."
        )
    return payload


def overlay_target_ids_from_map(overlay_map: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id") or "")
        for item in (overlay_map.get("elements") or [])
        if isinstance(item, dict) and item.get("id")
    }


def curate_form_plan_context(
    *,
    query: str,
    snapshot: dict[str, Any],
    existing_keys: list[str] | None = None,
) -> dict[str, Any]:
    from web_surf.form_values import collect_form_fields, collect_gate_fields, looks_like_age_gate, _snapshot_blockers

    fields = collect_gate_fields(snapshot) if looks_like_age_gate(snapshot) else collect_form_fields(snapshot)
    compact_fields: list[dict[str, Any]] = []
    for field in fields[:14]:
        row: dict[str, Any] = {
            "id": field["id"],
            "label": field["label"][:100],
            "widget": field.get("widget") or field.get("kind") or "text",
            "action": "select"
            if str(field.get("widget") or field.get("kind") or "").lower() in {"select", "combobox"}
            else "fill",
        }
        if field.get("name"):
            row["name"] = field["name"][:40]
        if field.get("placeholder"):
            row["placeholder"] = field["placeholder"][:80]
        options = field.get("options")
        if isinstance(options, list) and options:
            row["options"] = [str(opt)[:60] for opt in options[:8]]
        compact_fields.append(row)
    payload: dict[str, Any] = {
        "goal": query.strip(),
        "overlays": compact_blockers(_snapshot_blockers(snapshot)),
        "fields": compact_fields,
    }
    keys = [str(key) for key in (existing_keys or []) if str(key)]
    if keys:
        payload["existing_keys"] = keys
    from web_surf.form_values import AGE_GATE_AGENT_NOTE, looks_like_age_gate

    if looks_like_age_gate(snapshot):
        payload["age_gate_note"] = AGE_GATE_AGENT_NOTE
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
    from web_surf.page_match import focus_query, query_implies_recency

    focused = focus_query(query)
    curated = curate_text(page_text, query=focused, max_chars=max_chars)
    lines = [
        f"goal: {research_spec.get('summary') or query}",
        f"need: {', '.join(needed) if needed else 'relevant facts'}",
    ]
    if query_implies_recency(focused):
        lines.append(
            "timing: user wants the newest/most recent item — extract facts only from the "
            "latest dated section or article, not older entries on the same page"
        )
    lines.extend(
        [
            f"title: {page_title}",
            f"url: {page_url}",
            "content:",
            curated,
        ]
    )
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
