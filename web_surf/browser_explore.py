from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from web_surf import events
from web_surf.agent_memory import commit_agent_memory
from web_surf.context_curate import curate_browse_context, curate_overlay_context, normalize_decision
from web_surf.fetch import PageResult
from web_surf.explore_branches import (
    match_seed_url,
    resolve_active_branch_url,
    unexplored_seed_urls,
)
from web_surf.page_match import (
    focus_query,
    goal_is_satisfied,
    is_publisher_content_url,
    is_secondary_host,
    page_contains_target_date,
    page_extends_beyond_viewport,
    page_has_goal_links,
    page_looks_like_nav_shell,
    page_matches_query,
    page_text_for_goal,
    parse_target_dates,
    parse_user_preferred_domains,
    score_page_content,
    preferred_discovery_seeds,
    seed_url_priority,
    should_apply_date_filter,
    should_defer_collect_on_listing,
    snapshot_viewport,
    suggest_content_link_action,
    url_on_preferred_source,
    url_on_publisher_domain,
    viewport_has_content_below,
)
from web_surf.form_values import (
    _snapshot_blockers,
    ensure_form_values,
    is_verification_field,
    normalize_gate_select_value,
    overlay_blocks_collect,
    overlay_target_ids,
    report_is_negative,
    sanitize_form_values,
    snapshot_needs_overlay_action,
)
from web_surf.llm import ollama_chat
from web_surf.spec import _get_prompt
from web_surf.store import (
    content_hash,
    load_visit_graph,
    normalize_url,
    record_visit,
    save_session_state,
)
from ui_test.state_diff import action_signature, diff_page_states, is_no_progress, progress_fingerprint

logger = logging.getLogger(__name__)

DecisionProvider = Callable[[dict[str, Any]], dict[str, Any]]
HelpProvider = Callable[[dict[str, Any]], dict[str, Any]]
ALLOWED_ACTIONS = {
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
    "ask_helper",
    "provide_values",
}
KEY_NAMES = {
    "Enter",
    "Escape",
    "Tab",
    "ArrowDown",
    "ArrowUp",
    "ArrowLeft",
    "ArrowRight",
    "PageDown",
    "PageUp",
    "Home",
    "End",
    "Backspace",
    "Delete",
}
_OVERLAY_DEFERRED_ACTIONS = frozenset(
    {"navigate", "report", "extract", "filter", "scroll", "back", "wait", "help"}
)


def origin_url(url: str) -> str:
    parsed = urlsplit(normalize_url(url))
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _safe_normalize(url: str) -> str:
    try:
        return normalize_url(url)
    except (TypeError, ValueError):
        return ""


def _json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.I | re.S)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def ollama_decision_provider(
    *,
    ollama_url: str,
    model: str,
    timeout_sec: float,
    publishers: list[str] | None = None,
    publisher_domains: set[str] | None = None,
) -> DecisionProvider:
    def _system_prompt(step_id: str) -> str:
        match = re.search(r"(\d+)", step_id or "")
        step_num = int(match.group(1)) if match else 1
        if step_num >= 3:
            compact = _get_prompt("web_research.browse_decide_compact")
            if compact.strip():
                return compact
        return _get_prompt("web_research.browse_decide")

    def decide(context: dict[str, Any]) -> dict[str, Any]:
        step_id = str(context.get("step_id") or "")
        raw = ollama_chat(
            prompt_key="web_research.browse_decide",
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=_system_prompt(step_id),
            user=json.dumps(
                curate_browse_context(
                    query=str(context.get("query") or ""),
                    step_id=str(context.get("step_id") or ""),
                    snapshot=context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {},
                    discovered_routes=set(context.get("routes") or context.get("discovered_routes") or []),
                    available_value_keys=list(context.get("keys") or context.get("available_value_keys") or []),
                    field_mapping=context.get("map") if isinstance(context.get("map"), dict) else context.get("field_mapping"),
                    recent_history=context.get("history") if isinstance(context.get("history"), list) else context.get("recent_history"),
                    agent_memory=list(context.get("agent_memory") or []),
                    last_transition=context.get("last") or context.get("last_transition"),
                    blocked_attempts=list(context.get("blocked_attempts") or []),
                    publishers=list(context.get("publishers") or publishers or []),
                    publisher_domains=set(context.get("publisher_domains") or publisher_domains or set()),
                    preferred_domains=set(context.get("preferred_domains") or []),
                    seed_urls=list(context.get("seed_urls") or []),
                    candidates=list(context.get("candidates") or []),
                    active_branch_url=str(context.get("active_branch_url") or ""),
                    branch_steps=int(context.get("branch_steps") or 0),
                    max_steps_per_branch=int(context.get("max_steps_per_branch") or 20),
                    helper_guidance=list(context.get("helper_guidance") or []),
                    collected_evidence=list(context.get("collected_evidence") or []),
                    accomplishment_steps=list(context.get("accomplishment_steps") or []),
                    data_needed=list(context.get("data_needed") or []),
                    success_criteria=list(context.get("success_criteria") or []),
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            format_json=True,
            session_id=str(context.get("session_id") or ""),
            step_id=str(context.get("step_id") or ""),
            snapshot_id=str((context.get("snapshot") or {}).get("snapshot_id") or ""),
            url=str((context.get("snapshot") or {}).get("url") or ""),
        )
        parsed = _json_object(raw)
        if parsed is None:
            raise ValueError("Ollama returned no valid decision JSON")
        normalized = normalize_decision(parsed)
        return normalized or parsed

    return decide


def ollama_overlay_decision_provider(
    *,
    ollama_url: str,
    model: str,
    timeout_sec: float,
) -> DecisionProvider:
    def decide(context: dict[str, Any]) -> dict[str, Any]:
        raw = ollama_chat(
            prompt_key="web_research.choose_overlay_dismiss",
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=_get_prompt("web_research.choose_overlay_dismiss"),
            user=json.dumps(
                curate_overlay_context(
                    step_id=str(context.get("step_id") or ""),
                    snapshot=context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {},
                    recent_history=context.get("history") if isinstance(context.get("history"), list) else context.get("recent_history"),
                    blocked_attempts=list(context.get("blocked_attempts") or []),
                    available_value_keys=list(context.get("keys") or context.get("available_value_keys") or []),
                    field_mapping=context.get("map") if isinstance(context.get("map"), dict) else context.get("field_mapping"),
                    agent_memory=list(context.get("agent_memory") or []),
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            format_json=True,
            session_id=str(context.get("session_id") or ""),
            step_id=str(context.get("step_id") or ""),
            snapshot_id=str((context.get("snapshot") or {}).get("snapshot_id") or ""),
            url=str((context.get("snapshot") or {}).get("url") or ""),
        )
        parsed = _json_object(raw)
        if parsed is None:
            raise ValueError("Ollama returned no valid overlay-dismiss JSON")
        normalized = normalize_decision(parsed)
        return normalized or parsed

    return decide


def validate_overlay_action(
    decision: Any,
    snapshot: dict[str, Any],
    form_values: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    allowed = overlay_target_ids(snapshot)
    if not allowed:
        return None, "no overlay controls mapped on this page"
    if not isinstance(decision, dict):
        return None, "decision must be an object"
    action = str(decision.get("action") or "").lower().strip()
    if action not in {"click", "fill", "select"}:
        return None, f"overlay dismiss only supports click/fill/select, not {action or '(missing)'}"
    target_id = str(decision.get("target_id") or "").strip()
    if not target_id or target_id not in allowed:
        return None, "target_id must be copied from overlay_map[]"
    validated, error = validate_action(
        decision,
        snapshot,
        set(),
        None,
        form_values,
    )
    if validated is None:
        return None, error
    if str(validated.get("target_id") or "") not in allowed:
        return None, "target_id must be copied from overlay_map[]"
    return validated, ""


def _matching_link_id(
    url: str,
    elements: dict[str, dict[str, Any]],
    current_url: str | None,
) -> str | None:
    """Return a snapshot control id when url matches a visible link (including same-page anchors)."""
    normalized = _safe_normalize(url)
    if not normalized:
        return None
    for el_id, item in elements.items():
        href = _safe_normalize(str(item.get("href") or ""))
        if href and href == normalized:
            return el_id
    current = _safe_normalize(str(current_url or ""))
    if not current:
        return None
    parsed_target = urlsplit(normalized)
    parsed_current = urlsplit(current)
    if (
        parsed_target.netloc == parsed_current.netloc
        and parsed_target.path.rstrip("/") == parsed_current.path.rstrip("/")
        and parsed_target.fragment
    ):
        fragment_suffix = f"#{parsed_target.fragment}"
        for el_id, item in elements.items():
            href = _safe_normalize(str(item.get("href") or ""))
            if href.endswith(fragment_suffix):
                return el_id
    return None


def validate_action(
    decision: Any,
    snapshot: dict[str, Any],
    discovered_routes: set[str],
    allowed_origins: set[str] | None = None,
    form_values: dict[str, str] | None = None,
    *,
    seed_urls: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(decision, dict):
        return None, "decision must be an object"
    action = str(decision.get("action") or "").lower().strip()
    if action not in ALLOWED_ACTIONS:
        return None, f"unsupported action: {action or '(missing)'}"
    if action == "ask_helper":
        action = "help"
    if action == "swap_branch":
        action = "navigate"
    validated = {
        "action": action,
        "reason": str(decision.get("reason") or "")[:500],
    }
    raw_action_name = str(decision.get("action") or "").lower().strip()
    elements = {
        str(item.get("id")): item
        for item in snapshot.get("interactables") or []
        if isinstance(item, dict) and item.get("id")
    }
    if action == "navigate":
        # Models often say navigate but point at an on-page link. Treat that as a click.
        target_id = str(decision.get("target_id") or "").strip()
        url = _safe_normalize(str(decision.get("url") or ""))
        target = elements.get(target_id)
        target_href = _safe_normalize(str((target or {}).get("href") or ""))
        if target is not None and (not url or url == target_href):
            return validate_action(
                {**decision, "action": "click"},
                snapshot,
                discovered_routes,
                allowed_origins,
                form_values,
                seed_urls=seed_urls,
            )
    element_actions = {"click", "fill", "select", "press"}
    if action in element_actions:
        target_id = str(decision.get("target_id") or "").strip()
        if not target_id or target_id not in elements:
            return None, f"{action} target_id is not in the current snapshot"
        if elements[target_id].get("disabled"):
            return None, f"{action} target is disabled"
        if elements[target_id].get("inferred_from_overlay"):
            return None, (
                f"{action} target is inferred from overlay text and has no reliable DOM locator — "
                "try a visible control inside the overlay instead"
            )
        target_kind = str(elements[target_id].get("kind") or "").lower()
        target_widget = str(elements[target_id].get("widget") or "").lower()
        if action == "fill" and (target_kind in {"select", "combobox"} or target_widget in {"select", "combobox"}):
            action = "select"
            validated["action"] = action
        target_href = _safe_normalize(str(elements[target_id].get("href") or ""))
        if target_href and allowed_origins and origin_url(target_href) not in allowed_origins:
            return None, f"{action} target leaves the allowed candidate origins"
        validated["target_id"] = target_id
        validated["target"] = elements[target_id]
    if action == "navigate":
        url = _safe_normalize(str(decision.get("url") or ""))
        if raw_action_name == "swap_branch" and seed_urls:
            seed_match = match_seed_url(url, seed_urls)
            if seed_match:
                url = seed_match
            elif url:
                for seed in seed_urls:
                    seed_norm = _safe_normalize(seed)
                    if seed_norm and (url in seed_norm or seed_norm in url):
                        url = seed_norm
                        break
        link_id = _matching_link_id(url, elements, str(snapshot.get("url") or ""))
        if link_id:
            return validate_action(
                {**decision, "action": "click", "target_id": link_id, "url": ""},
                snapshot,
                discovered_routes,
                allowed_origins,
                form_values,
                seed_urls=seed_urls,
            )
        allowed = {_safe_normalize(route) for route in discovered_routes}
        # Links visible on the current page are discovered by definition.
        allowed.update(
            _safe_normalize(str(item.get("href") or ""))
            for item in elements.values()
            if item.get("href")
        )
        allowed.discard("")
        if url and url not in allowed:
            for route in discovered_routes:
                normalized = _safe_normalize(str(route))
                if not normalized:
                    continue
                parsed = urlsplit(normalized)
                path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                if url == path or url == parsed.path or url.rstrip("/") == path.rstrip("/"):
                    url = normalized
                    break
        if not url or url not in allowed:
            return None, "navigate URL was not discovered from search or a page snapshot"
        if allowed_origins and origin_url(url) not in allowed_origins:
            return None, "navigate URL leaves the allowed candidate origins"
        validated["url"] = url
        if raw_action_name == "swap_branch":
            validated["swap_branch"] = True
    if action in {"fill", "select"}:
        value_key = str(decision.get("value_key") or "").strip()
        target = validated.get("target") if isinstance(validated.get("target"), dict) else {}
        verification_field = is_verification_field(target, snapshot)
        if value_key:
            value = (form_values or {}).get(value_key)
            if not value:
                return None, f"{action} value_key is not available in the generated form value store"
            validated["value_key"] = value_key
        else:
            value = decision.get("value")
            if not isinstance(value, str) or not value:
                return None, f"{action} requires a non-empty string value or value_key"
            if verification_field:
                return None, (
                    f"{action} on a verification field requires value_key from available_value_keys; "
                    "use provide_values first if keys are missing"
                )
        validated["value"] = normalize_gate_select_value(value[:2000], target)
    if action == "press":
        value = str(decision.get("value") or "")
        if value not in KEY_NAMES:
            return None, "press value is not an allowed keyboard key"
        validated["value"] = value
    if action == "wait":
        try:
            duration_ms = int(decision.get("duration_ms") or 750)
        except (TypeError, ValueError):
            return None, "wait duration_ms must be an integer"
        validated["duration_ms"] = max(100, min(duration_ms, 5000))
    if action == "scroll":
        try:
            amount = int(decision.get("amount") or 600)
        except (TypeError, ValueError):
            return None, "scroll amount must be an integer"
        validated["amount"] = max(-2000, min(amount, 2000))
    if action == "help":
        question = str(decision.get("question") or "").strip()
        if not question:
            return None, "help requires a question"
        validated["question"] = question[:2000]
    if action == "provide_values":
        sanitized = sanitize_form_values(decision.get("form_values"))
        if not sanitized:
            return None, "provide_values requires a non-empty form_values object"
        validated["form_values"] = sanitized
    if action in {"extract", "filter", "report"}:
        validated["note"] = str(decision.get("note") or "")[:2000]
    if action in {"extract", "filter"}:
        blocked, overlay_error = overlay_blocks_collect(snapshot)
        if blocked:
            visible = str(snapshot.get("visible_text") or "")
            events.extract_preview(
                {
                    "phase": "blocked",
                    "action": action,
                    "url": str(snapshot.get("url") or ""),
                    "step_id": str(snapshot.get("step_id") or ""),
                    "snapshot_id": str(snapshot.get("snapshot_id") or ""),
                    "visible_text_chars": len(visible.strip()),
                    "text_preview": visible[:1500],
                    "error": overlay_error,
                }
            )
            return None, overlay_error
    elif _snapshot_blockers(snapshot) and action in _OVERLAY_DEFERRED_ACTIONS:
        return None, (
            "clear blocking overlay first — dismiss consent or complete age verification"
        )
    return validated, ""


def _redact_form_values(snapshot: dict[str, Any], form_values: dict[str, str] | None) -> dict[str, Any]:
    """Keep explicit form values in the browser, but out of model-facing state."""
    values = [value for value in (form_values or {}).values() if value]
    if not values:
        return snapshot
    redacted = dict(snapshot)
    for field in ("visible_text", "semantic_snapshot"):
        value = str(redacted.get(field) or "")
        for secret in values:
            value = value.replace(secret, "[user-provided]")
        redacted[field] = value
    elements: list[dict[str, Any]] = []
    for raw in redacted.get("interactables") or []:
        item = dict(raw) if isinstance(raw, dict) else raw
        if isinstance(item, dict):
            for field in ("value", "text", "aria", "label", "nearby_text"):
                value = item.get(field)
                if isinstance(value, str):
                    for secret in values:
                        value = value.replace(secret, "[user-provided]")
                    item[field] = value
        elements.append(item)
    redacted["interactables"] = elements
    return redacted


def _snapshot(
    page: Any,
    *,
    session_id: str,
    step_id: str,
    context: str,
    form_values: dict[str, str] | None = None,
    analyze: bool = True,
    emit_progress: bool = True,
    screenshot_mode: str = "content",
    build_map: bool = True,
) -> dict[str, Any]:
    from ui_test.browser_state import attach_web_capture, collect_page_state

    state = _redact_form_values(
        collect_page_state(
            page,
            include_screenshot=True,
            screenshot_mode=screenshot_mode,
        ),
        form_values,
    )
    state["snapshot_id"] = f"snap_{uuid.uuid4().hex[:12]}"
    state["session_id"] = session_id
    state["step_id"] = step_id
    state["context"] = context
    # Push screenshot to the UI before the slower map rebuild finishes.
    if state.get("screenshot_b64") and emit_progress:
        events.snapshot(_compact_snapshot(state))
    if emit_progress and build_map:
        try:
            from web_capture.capture import build_capture
            from web_capture.progress import capture_progress_event

            draft = build_capture(state, context=context)
            capture_progress_event(
                phase="geometry",
                url=str(state.get("url") or ""),
                capture=draft,
                element_count=len(draft.get("elements") or []),
                screenshot_b64=state.get("screenshot_b64"),
                title=str(state.get("title") or ""),
                interactables=list(state.get("interactables") or []),
            )
        except Exception:
            pass
    if build_map:
        attach_web_capture(
            page,
            state,
            context=context,
            analyze=analyze,
            emit_progress=emit_progress,
        )
        if emit_progress:
            events.snapshot(_compact_snapshot(state))
    return state


def _clear_overlays_before_map(
    page: Any,
    snapshot: dict[str, Any],
    *,
    session_id: str,
    step_id: str,
    form_values: dict[str, str],
    field_mapping: dict[str, str],
    history: list[dict[str, Any]] | None = None,
    max_attempts: int = 5,
) -> tuple[dict[str, Any], bool]:
    """
    Dismiss cookie/age/consent blockers before the expensive full-page map build.

    Returns (snapshot, cleared_any). If blockers remain (e.g. need LLM), snapshot still
    has no full map — caller should skip map build until the page is clear.
    """
    from web_surf.form_values import suggest_overlay_action, snapshot_needs_overlay_action

    if not snapshot_needs_overlay_action(snapshot):
        return snapshot, False

    events.controller(
        {
            "session_id": session_id,
            "status": "overlay_dismiss",
            "current_url": str(page.url or ""),
            "reason": "Clearing blocking overlay before full-page map…",
        }
    )
    events.log("Blocking overlay detected — clearing before map build", level="info")

    cleared_any = False
    blocked: list[str] = []
    recent = list(history or [])

    for attempt in range(max_attempts):
        if not snapshot_needs_overlay_action(snapshot):
            events.log("Overlay cleared — ready for full-page map", level="info")
            return snapshot, cleared_any

        suggested = suggest_overlay_action(
            snapshot,
            form_values,
            field_mapping,
            recent_history=recent,
            blocked_attempts=blocked,
        )
        if not suggested:
            events.log(
                "Overlay still present but no deterministic dismiss — deferring to model",
                level="info",
            )
            break

        validated, error = validate_overlay_action(suggested, snapshot, form_values)
        if validated is None:
            validated, error = validate_action(
                suggested,
                snapshot,
                set(),
                None,
                form_values,
            )
        if validated is None:
            events.log(f"Pre-map overlay action rejected: {error}", level="info")
            break

        sig = action_signature(validated)
        if sig:
            blocked.append(sig)

        events.log(
            f"Pre-map overlay clear ({attempt + 1}/{max_attempts}): "
            f"{validated.get('action')} {validated.get('target_id') or ''} — "
            f"{str(validated.get('reason') or '')[:120]}",
            level="info",
        )
        try:
            _execute(page, validated)
            cleared_any = True
        except Exception as exc:
            events.log(f"Pre-map overlay clear failed: {exc}", level="info")
            recent.append(
                {
                    "action": validated.get("action"),
                    "target_id": validated.get("target_id"),
                    "ok": False,
                    "error": str(exc)[:200],
                    "reason": validated.get("reason"),
                }
            )
            break

        recent.append(
            {
                "action": validated.get("action"),
                "target_id": validated.get("target_id"),
                "ok": True,
                "reason": validated.get("reason"),
            }
        )
        snapshot = _snapshot(
            page,
            session_id=session_id,
            step_id=f"{step_id}_overlay{attempt + 1}",
            context="overlay_clear",
            form_values=form_values,
            analyze=False,
            emit_progress=True,
            screenshot_mode="content",
            build_map=False,
        )

    return snapshot, cleared_any


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Event-safe snapshot for the test-runner map and screenshot panes."""
    return dict(snapshot)


def _publish_web_capture(
    snapshot: dict[str, Any],
    *,
    session_dir: Path | None = None,
) -> None:
    """Push the (possibly stitched) capture to the live UI and persist latest.json."""
    capture = snapshot.get("web_capture")
    if not isinstance(capture, dict):
        return
    try:
        from web_capture.progress import capture_progress_event

        capture_progress_event(
            phase="complete",
            url=str(snapshot.get("url") or capture.get("url") or ""),
            capture=capture,
            element_count=len(capture.get("elements") or []),
            screenshot_b64=snapshot.get("screenshot_b64"),
            title=str(snapshot.get("title") or capture.get("title") or ""),
            interactables=list(snapshot.get("interactables") or []),
        )
    except Exception:
        pass
    events.snapshot(_compact_snapshot(snapshot))
    if session_dir is not None:
        try:
            from web_capture.storage import persist_capture

            persist_capture(session_dir, capture)
        except Exception:
            pass


def _frame_root(page: Any, item: dict[str, Any]) -> Any:
    """Resolve Playwright frame for iframe-hosted controls."""
    frame_index = item.get("frame_index")
    if frame_index is not None:
        try:
            frames = list(page.frames)
            idx = int(frame_index) + 1
            if 0 <= idx < len(frames):
                return frames[idx]
        except (TypeError, ValueError, IndexError):
            pass
    frame_url = str(item.get("frame_url") or "").strip()
    if frame_url:
        for frame in page.frames:
            if str(frame.url or "") == frame_url:
                return frame
    return page


def _target_label(item: dict[str, Any]) -> str:
    return str(item.get("text") or item.get("aria") or item.get("label") or item.get("name") or "").strip()


def _locator_for(page: Any, item: dict[str, Any]) -> Any:
    root = _frame_root(page, item)
    test_id = str(item.get("test_id") or "")
    kind = str(item.get("kind") or "").lower()
    widget = str(item.get("widget") or "").lower()
    role = str(item.get("role") or kind or "")
    name = str(item.get("name") or "").strip()
    aria = str(item.get("aria") or "").strip()
    label = str(item.get("label") or "").strip()
    text = str(item.get("text") or aria or label or "")
    href = str(item.get("href") or "")
    css_path = str(item.get("css_path") or "").strip()
    if test_id:
        return root.get_by_test_id(test_id).first
    if kind == "select" and name:
        if item.get("in_dialog"):
            return root.locator(
                f"[role='dialog'] select[name={json.dumps(name)}], "
                f"[aria-modal='true'] select[name={json.dumps(name)}], "
                f"select[name={json.dumps(name)}]"
            ).first
        return root.locator(f"select[name={json.dumps(name)}]").first
    if widget in {"combobox"} or role == "combobox":
        if aria:
            return root.get_by_role("combobox", name=aria, exact=False).first
        if label:
            return root.get_by_role("combobox", name=label, exact=False).first
        if name:
            return root.locator(f"[role='combobox'][name={json.dumps(name)}]").first
    if aria and kind in {"select", "input", "textarea", "textbox", "combobox", "spinbutton"}:
        return root.get_by_label(aria, exact=False).first
    if label and kind in {"input", "textarea", "textbox", "spinbutton"}:
        return root.get_by_label(label, exact=False).first
    if name and kind in {"select", "input", "textarea", "textbox", "spinbutton"}:
        tag = "input" if kind in {"textbox", "spinbutton"} else kind
        return root.locator(f"{tag}[name={json.dumps(name)}]").first
    if href and (not text or len(text) > 100):
        return root.locator(f"a[href={json.dumps(href)}]").first
    if role in {"link", "button", "menuitem", "textbox", "checkbox", "radio", "combobox", "spinbutton", "searchbox"} and text:
        if len(text) > 80:
            return root.get_by_role(role, name=text[:80], exact=False).first
        return root.get_by_role(role, name=text, exact=True).first
    if href:
        return root.locator(f"a[href={json.dumps(href)}]").first
    if text and len(text) <= 80:
        return root.get_by_text(text, exact=True).first
    placeholder = str(item.get("placeholder") or "")
    if placeholder:
        return root.get_by_placeholder(placeholder, exact=True).first
    if css_path:
        loc = root.locator(css_path)
        if loc.count() >= 1:
            return loc.first
    raise ValueError("target has no usable semantic locator")


def _selection_matches(current: str, expected: str, item: dict[str, Any]) -> bool:
    current = str(current or "").strip()
    expected = str(expected or "").strip()
    if not expected:
        return False
    if current == expected:
        return True
    if expected in current or current in expected:
        return True
    options = item.get("options") if isinstance(item.get("options"), list) else []
    option_values = item.get("option_values") if isinstance(item.get("option_values"), list) else []
    for opt in options:
        opt_text = str(opt).strip()
        if opt_text == expected and (opt_text == current or expected in opt_text):
            return True
    for opt_val in option_values:
        if str(opt_val).strip() == expected and str(opt_val).strip() == current:
            return True
    return False


def _fill_stable(page: Any, item: dict[str, Any], value: str) -> None:
    loc = _locator_for(page, item)
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        loc.click(timeout=3000)
    except Exception:
        pass
    label = str(item.get("text") or item.get("name") or item.get("id") or "field")
    for attempt in range(3):
        loc.fill(value, timeout=8000)
        try:
            current = loc.input_value(timeout=2000)
            if current == value:
                return
        except Exception:
            pass
        page.wait_for_timeout(250 * (attempt + 1))
    raise RuntimeError(f"Could not set {label} — input value did not stick")


def _select_stable(page: Any, item: dict[str, Any], value: str) -> None:
    loc = _locator_for(page, item)
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    label = str(item.get("text") or item.get("name") or item.get("id") or "menu")
    strategies = [
        lambda: loc.select_option(value=value, timeout=8000),
        lambda: loc.select_option(label=value, timeout=8000),
    ]
    last_exc: Exception | None = None
    for attempt in range(3):
        for strategy in strategies:
            try:
                strategy()
                current = loc.input_value(timeout=2000)
                if _selection_matches(current, value, item):
                    return
            except Exception as exc:
                last_exc = exc
        page.wait_for_timeout(250 * (attempt + 1))
    raise RuntimeError(f"Could not select {value!r} in {label}") from last_exc


def _discover_official_outbound(
    snapshot: dict[str, Any],
    publisher_domains: set[str],
    allowed_origins: set[str],
    discovered_routes: set[str],
    *,
    preferred_domains: set[str] | None = None,
) -> list[str]:
    """Promote outbound links to publisher domains identified for this research task."""
    from web_surf.page_match import url_on_publisher_domain

    if not publisher_domains or preferred_domains:
        return []

    promoted: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    for item in snapshot.get("interactables") or []:
        if not isinstance(item, dict):
            continue
        href = _safe_normalize(str(item.get("href") or ""))
        if href:
            candidates.append(href)
    for route in snapshot.get("discovered_routes") or []:
        href = _safe_normalize(str(route))
        if href:
            candidates.append(href)

    for href in candidates:
        if not href or href in seen or not url_on_publisher_domain(href, publisher_domains):
            continue
        seen.add(href)
        origin = origin_url(href)
        if origin not in allowed_origins:
            allowed_origins.add(origin)
            promoted.append(href)
        discovered_routes.add(href)
        discovered_routes.add(origin)
    return promoted


def _sync_branch_navigation(
    *,
    page_url: str,
    snapshot: dict[str, Any],
    allowed_origins: set[str],
    discovered_routes: set[str],
) -> bool:
    """Allow in-branch exploration after redirects (e.g. age-gate handoffs)."""
    current = _safe_normalize(page_url)
    if not current:
        return False
    expanded = False
    origin = origin_url(current)
    if origin and origin not in allowed_origins:
        allowed_origins.add(origin)
        expanded = True
    discovered_routes.add(current)
    if origin:
        discovered_routes.add(origin)
    hrefs: list[str] = []
    for route in snapshot.get("discovered_routes") or []:
        href = _safe_normalize(str(route))
        if href:
            hrefs.append(href)
    for item in snapshot.get("interactables") or []:
        if not isinstance(item, dict):
            continue
        href = _safe_normalize(str(item.get("href") or ""))
        if href:
            hrefs.append(href)
    for href in hrefs:
        discovered_routes.add(href)
        route_origin = origin_url(href)
        if route_origin and route_origin not in allowed_origins:
            allowed_origins.add(route_origin)
            expanded = True
    return expanded


def _wait_for_page_change(
    page: Any,
    *,
    before_url: str,
    before_title: str,
    timeout_ms: int = 1800,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            if str(page.url or "") != before_url:
                return True
            current_title = str(page.title() or "")
            if current_title and current_title != before_title:
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False


def _settle_after_action(page: Any, action: dict[str, Any], *, before_url: str, before_title: str) -> None:
    from ui_test.expandable import is_collapse_toggle

    kind = str(action.get("action") or "")
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    if kind in {"navigate", "click", "back", "press"}:
        if _wait_for_page_change(page, before_url=before_url, before_title=before_title, timeout_ms=1800):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(200)
            return
    if kind == "click" and is_collapse_toggle(target):
        return
    page.wait_for_timeout(350)


def _execute(page: Any, action: dict[str, Any]) -> None:
    from ui_test.expandable import is_collapse_toggle, wait_for_section_expand

    kind = action["action"]
    before_url = str(page.url or "")
    try:
        before_title = str(page.title() or "")
    except Exception:
        before_title = ""
    # Short element timeouts: a click blocked by an overlay should fail fast so the
    # next snapshot (which lists the overlay) reaches the model quickly.
    if kind == "navigate":
        page.goto(action["url"], wait_until="domcontentloaded", timeout=45000)
    elif kind == "click":
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        loc = _locator_for(page, action["target"])
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        loc.click(timeout=5000)
        if is_collapse_toggle(target):
            wait_for_section_expand(page, target, timeout_ms=5000)
    elif kind == "fill":
        _fill_stable(page, action["target"], action["value"])
    elif kind == "select":
        _select_stable(page, action["target"], action["value"])
    elif kind == "press":
        _locator_for(page, action["target"]).press(action["value"], timeout=5000)
    elif kind == "scroll":
        page.mouse.wheel(0, action["amount"])
    elif kind == "back":
        page.go_back(wait_until="domcontentloaded", timeout=45000)
    elif kind == "wait":
        page.wait_for_timeout(action["duration_ms"])
    if kind in {"navigate", "click", "fill", "select", "press", "scroll", "back"}:
        _settle_after_action(page, action, before_url=before_url, before_title=before_title)


def stdin_help_provider(request: dict[str, Any]) -> dict[str, Any]:
    """Wait for the test-runner's matching NDJSON helper response on stdin."""
    request_id = str(request.get("request_id") or "")
    while True:
        line = sys.stdin.readline()
        if not line:
            return {"status": "transport_closed", "answer": ""}
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") != "web_help_response":
            continue
        if request_id and str(payload.get("request_id") or "") != request_id:
            continue
        return {
            "status": "ok" if payload.get("ok") else "error",
            "answer": str(payload.get("content") or payload.get("response") or ""),
            "error": str(payload.get("error") or ""),
        }


def _read_scroll_y(page: Any) -> float:
    try:
        return float(page.evaluate("() => window.scrollY || window.pageYOffset || 0"))
    except Exception:
        return 0.0


def _scroll_to_top(page: Any) -> float:
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    return _read_scroll_y(page)


def _scroll_by_measured(page: Any, *, delta_y: float) -> tuple[float, float]:
    """Scroll by delta_y and return (before, after) measured scroll positions."""
    before = _read_scroll_y(page)
    page.evaluate("(dy) => window.scrollBy(0, dy)", float(delta_y))
    page.wait_for_timeout(400)
    after = _read_scroll_y(page)
    return before, after


def _scroll_to_next_view(page: Any, *, viewport_height: float | None = None) -> tuple[float, float]:
    """Scroll roughly one viewport; return measured before/after scrollY."""
    step = float(viewport_height or 720) * 0.9
    return _scroll_by_measured(page, delta_y=step)


def _viewport_snapshot_score(snapshot: dict[str, Any], goal: str) -> int:
    from web_surf.page_match import page_has_substantive_content

    text = str(snapshot.get("visible_text") or "")
    scoped = page_text_for_goal(text, goal, max_chars=8000)
    score = score_page_content(scoped, goal)
    if page_has_substantive_content(scoped, goal):
        score += 40
    if page_looks_like_nav_shell(text, goal):
        score -= 30
    return score


def _paging_view_budget(snapshot: dict[str, Any], *, max_views: int = 16) -> int:
    """Cover the full document height (with small overlap), capped for safety."""
    vp = snapshot_viewport(snapshot)
    view_height = max(1.0, vp["height"])
    doc_height = max(view_height, vp["document_height"])
    step = view_height * 0.9
    needed = int((doc_height + step - 1) // step) if step else 1
    return max(1, min(max_views, needed))


def _merge_paged_page_text(views: list[dict[str, Any]], *, max_chars: int = 16000) -> str:
    """Concatenate unique text from scroll views so below-fold content is not lost."""
    chunks: list[str] = []
    seen: set[str] = set()
    for snap in views:
        text = str(snap.get("visible_text") or snap.get("semantic_snapshot") or "").strip()
        if len(text) < 40:
            continue
        key = text[:240]
        if key in seen:
            continue
        seen.add(key)
        chunks.append(text)
    merged = "\n\n".join(chunks)
    return merged[:max_chars]


def _wake_lazy_content(
    page: Any,
    *,
    viewport_height: float,
    max_steps: int = 14,
) -> list[str]:
    """Scroll through the page once so lazy media/DOM mounts, collecting text chunks."""
    texts: list[str] = []
    _scroll_to_top(page)
    for step in range(max_steps):
        try:
            chunk = str(
                page.evaluate(
                    """() => (document.body && (document.body.innerText || "")) || "" """
                )
                or ""
            ).strip()
        except Exception:
            chunk = ""
        if len(chunk) >= 40:
            texts.append(chunk[:8000])
        before = _read_scroll_y(page)
        _, after = _scroll_to_next_view(page, viewport_height=viewport_height)
        if after <= before + 1.0:
            break
        try:
            doc_h = float(
                page.evaluate(
                    """() => Math.max(
                      document.documentElement ? document.documentElement.scrollHeight : 0,
                      document.body ? document.body.scrollHeight : 0,
                      1
                    )"""
                )
                or 1
            )
        except Exception:
            doc_h = after + viewport_height
        if after + viewport_height >= doc_h - 2:
            break
        if step >= max_steps - 1:
            break
    _scroll_to_top(page)
    return texts


def _enhance_snapshot_with_viewport_paging(
    page: Any,
    snapshot: dict[str, Any],
    goal: str,
    *,
    session_id: str,
    step_id: str,
    form_values: dict[str, str] | None,
    views_explored: dict[str, int],
    scroll_stitch_cache: dict[str, Any] | None = None,
    session_dir: Path | None = None,
    max_views: int = 16,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a full-page map with one screenshot (no multi-slice stitch)."""
    url_key = str(snapshot.get("url") or "")

    # Already captured this URL as a full-page map — reuse.
    if scroll_stitch_cache is not None:
        cached = (scroll_stitch_cache.get(url_key) or {}).get("merged")
        if isinstance(cached, dict):
            from web_capture.full_page import is_full_page_capture

            if is_full_page_capture(cached) or (
                isinstance(cached.get("scroll_map"), dict)
                and cached["scroll_map"].get("coords") == "document"
            ):
                result = dict(snapshot)
                result["web_capture"] = cached
                views_explored[url_key] = max(views_explored.get(url_key, 0), 1)
                _publish_web_capture(result, session_dir=session_dir)
                return result, []

    vp = snapshot_viewport(snapshot)
    events.log(
        "Capturing full-page screenshot for map (single image, no scroll stitch)",
        level="info",
    )
    wake_texts = _wake_lazy_content(
        page,
        viewport_height=float(vp.get("height") or 720),
        max_steps=min(max_views, 14),
    )

    full_snap = _snapshot(
        page,
        session_id=session_id,
        step_id=f"{step_id}_full",
        context="full_page",
        form_values=form_values,
        analyze=True,
        emit_progress=True,
        screenshot_mode="full_page",
    )
    views_explored[url_key] = max(views_explored.get(url_key, 0), 1)

    capture = full_snap.get("web_capture")
    if isinstance(capture, dict):
        from web_capture.full_page import annotate_full_page_capture, is_full_page_capture

        if not is_full_page_capture(capture):
            capture = annotate_full_page_capture(capture)
            full_snap["web_capture"] = capture
        if scroll_stitch_cache is not None:
            entry = scroll_stitch_cache.setdefault(url_key, {"by_scroll": {}, "merged": None})
            entry["merged"] = capture

    # Prefer wake-pass text when it found more below-fold content.
    merged_text = _merge_paged_page_text(
        [{"visible_text": t} for t in wake_texts]
        + [full_snap, snapshot],
    )
    result = dict(full_snap)
    if merged_text:
        result["visible_text"] = merged_text
        result["semantic_snapshot"] = merged_text
        result["page_text_stitched"] = True
    # Keep goal-scoring: if original snapshot text scored higher, still use full map image.
    if _viewport_snapshot_score(snapshot, goal) > _viewport_snapshot_score(result, goal):
        result["interactables"] = snapshot.get("interactables") or result.get("interactables")
    extra = [_compact_snapshot(result)]
    _publish_web_capture(result, session_dir=session_dir)
    return result, extra


def _apply_scroll_stitch(
    snapshot: dict[str, Any],
    scroll_stitch_cache: dict[str, Any],
    *,
    session_dir: Path | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Publish / cache the page map. Full-page captures skip multi-slice stitching."""
    capture = snapshot.get("web_capture")
    if not isinstance(capture, dict):
        return snapshot
    url = str(snapshot.get("url") or "")
    from web_capture.full_page import is_full_page_capture
    from web_capture.stitch import accumulate_scroll_capture, is_stitched_capture

    if is_full_page_capture(capture) or is_stitched_capture(capture):
        if scroll_stitch_cache is not None and url:
            entry = scroll_stitch_cache.setdefault(url, {"by_scroll": {}, "merged": None})
            entry["merged"] = capture
        try:
            from ui_test.browser_state import remember_web_capture

            remember_web_capture(capture)
        except Exception:
            pass
        if publish:
            _publish_web_capture(snapshot, session_dir=session_dir)
        return snapshot

    # Legacy path: accumulate viewport slices if anything still emits them.
    merged = accumulate_scroll_capture(scroll_stitch_cache, url=url, capture=capture)
    if not merged:
        return snapshot
    updated = dict(snapshot)
    updated["web_capture"] = merged
    try:
        from ui_test.browser_state import remember_web_capture

        remember_web_capture(merged)
    except Exception:
        pass
    if publish:
        _publish_web_capture(updated, session_dir=session_dir)
    return updated


def _content_collect_key(
    snapshot: dict[str, Any],
    goal: str,
    *,
    apply_date_filter: bool = False,
) -> str:
    """Stable identity for extract/filter dedup — same URL + same clipped text."""
    raw_text = str(snapshot.get("visible_text") or "").strip()
    if apply_date_filter and goal:
        text = page_text_for_goal(raw_text, goal, max_chars=12000)
    else:
        text = raw_text[:12000]
    url = _safe_normalize(str(snapshot.get("url") or ""))
    return f"{url}|{content_hash(text)}"


def _content_collect_signature(action_name: str, collect_key: str) -> str:
    return f"{action_name}|{collect_key}"


def _page_result(
    page: Any,
    snapshot: dict[str, Any],
    step_id: str,
    *,
    goal: str = "",
    apply_date_filter: bool = False,
) -> PageResult:
    raw_text = str(snapshot.get("visible_text") or snapshot.get("semantic_snapshot") or "").strip()
    if apply_date_filter and goal:
        text = page_text_for_goal(raw_text, goal, max_chars=12000)
    else:
        text = raw_text[:12000]
    return PageResult(
        url=_safe_normalize(str(snapshot.get("url") or page.url)),
        title=str(snapshot.get("title") or ""),
        text=text,
        markdown=text,
        content_hash=content_hash(text),
        fetch_tier=2,
        evidence_context={
            "source_session_id": str(snapshot.get("session_id") or ""),
            "source_step_id": step_id,
            "source_snapshot_id": str(snapshot.get("snapshot_id") or ""),
        },
    )


def _publisher_routes(discovered_routes: set[str], publisher_domains: set[str]) -> set[str]:
    if not publisher_domains:
        return set()
    return {
        str(route)
        for route in discovered_routes
        if url_on_publisher_domain(str(route), publisher_domains)
    }


def _goal_satisfied_for_page(
    text: str,
    goal: str,
    *,
    source_url: str,
    publisher_domains: set[str],
    discovered_routes: set[str],
    preferred_domains: set[str] | None = None,
) -> bool:
    return goal_is_satisfied(
        text,
        goal,
        source_url=source_url,
        publisher_domains=publisher_domains,
        publisher_routes=_publisher_routes(discovered_routes, publisher_domains),
        preferred_domains=preferred_domains,
    )


def _should_collect_on_page(
    snapshot: dict[str, Any],
    goal: str,
    *,
    publisher_domains: set[str] | None = None,
    discovered_routes: set[str] | None = None,
    preferred_domains: set[str] | None = None,
) -> bool:
    visible = str(snapshot.get("visible_text") or "").strip()
    if len(visible) < 120:
        return False
    if not page_matches_query(visible, goal):
        return False
    if should_defer_collect_on_listing(snapshot, goal):
        return False
    scoped = page_text_for_goal(visible, goal, max_chars=12000)
    preferred = set(preferred_domains or set()) or parse_user_preferred_domains(goal)
    return _goal_satisfied_for_page(
        scoped,
        goal,
        source_url=str(snapshot.get("url") or ""),
        publisher_domains=set(publisher_domains or set()),
        discovered_routes=set(discovered_routes or set()),
        preferred_domains=preferred,
    )


def _try_auto_collect_on_snapshot(
    *,
    page: Any,
    snapshot: dict[str, Any],
    step_id: str,
    goal: str,
    collected_content_keys: set[str],
    publisher_domains: set[str] | None = None,
    discovered_routes: set[str] | None = None,
) -> tuple[PageResult | None, dict[str, Any]]:
    """Capture answer text when the visible page already satisfies the goal."""
    apply_date_filter = should_apply_date_filter(goal)
    collect_key = _content_collect_key(snapshot, goal, apply_date_filter=apply_date_filter)
    if collect_key in collected_content_keys:
        return None, {}
    if not _should_collect_on_page(
        snapshot,
        goal,
        publisher_domains=publisher_domains,
        discovered_routes=discovered_routes,
        preferred_domains=parse_user_preferred_domains(goal),
    ):
        return None, {}
    return _auto_collect_from_page(
        page=page,
        snapshot=snapshot,
        step_id=step_id,
        goal=goal,
        reason="Auto-collected visible page text that matches the research goal",
        publisher_domains=publisher_domains,
        discovered_routes=discovered_routes,
    )


def _auto_collect_from_page(
    *,
    page: Any,
    snapshot: dict[str, Any],
    step_id: str,
    goal: str,
    reason: str,
    publisher_domains: set[str] | None = None,
    discovered_routes: set[str] | None = None,
) -> tuple[PageResult | None, dict[str, Any]]:
    if not _should_collect_on_page(
        snapshot,
        goal,
        publisher_domains=publisher_domains,
        discovered_routes=discovered_routes,
        preferred_domains=parse_user_preferred_domains(goal),
    ):
        return None, {}
    page_result = _page_result(
        page,
        snapshot,
        step_id,
        goal=goal,
        apply_date_filter=True,
    )
    if not page_result.ok or len(page_result.text) < 200:
        return None, {}
    return page_result, {
        "step_id": step_id,
        "snapshot_id": snapshot["snapshot_id"],
        "action": "filter",
        "reason": reason,
        "ok": True,
        "auto_collected": True,
    }


def explore_candidates_in_browser(
    *,
    query: str,
    candidates: list[Any],
    project_path: Path,
    max_visits: int = 5,
    max_steps: int = 20,
    max_steps_per_branch: int = 20,
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5:14b",
    timeout_sec: float = 120.0,
    decision_provider: DecisionProvider | None = None,
    help_provider: HelpProvider | None = None,
    success_criteria: list[str] | None = None,
    data_needed: list[str] | None = None,
    accomplishment_steps: list[dict[str, Any]] | None = None,
    form_values: dict[str, str] | None = None,
    form_values_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    publisher_domains: set[str] | None = None,
    publishers: list[str] | None = None,
) -> tuple[list[PageResult], str, bool, dict[str, Any]]:
    """Explore from result origins, executing exactly one validated model action per step."""
    from playwright.sync_api import sync_playwright
    from ui_test.playwright_session import PlaywrightSessionRecorder, session_manifest_paths
    from web_surf.plan import (
        infer_step_completion,
        mark_step_done,
        normalize_accomplishment_steps,
        plan_progress,
    )

    session_id = f"web_{uuid.uuid4().hex}"
    # Strip the collaboration wrapper so decisions and scoring see the user's task.
    goal = focus_query(query)
    plan_steps = normalize_accomplishment_steps(accomplishment_steps, query=goal)
    recorder = PlaywrightSessionRecorder(
        project_path / ".agent" / "current" / "web-artifacts" / "playwright-session"
    )
    recorder.prepare()
    decide = decision_provider or ollama_decision_provider(
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
        publishers=list(publishers or []),
        publisher_domains=set(publisher_domains or set()),
    )
    overlay_decide = ollama_overlay_decision_provider(
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
    )
    publisher_domain_set = set(publisher_domains or set())
    publisher_names = list(publishers or [])
    preferred_domain_set = parse_user_preferred_domains(goal)
    session_form_values: dict[str, str] = {
        str(key): str(value)
        for key, value in (form_values or {}).items()
        if str(value)
    }
    planned_form_fingerprints: set[str] = set()
    field_mapping: dict[str, str] = {}
    form_value_reasoning = ""
    raw_candidate_urls: list[str] = []
    for row in candidates[: max(max_visits * 3, max_visits)]:
        url = _safe_normalize(str(getattr(row, "url", "")))
        if url and url not in raw_candidate_urls:
            raw_candidate_urls.append(url)
    # Named site + latest/news: start on the listing hub and discover the article
    # on-page. Do not land on a specific search-result article first.
    discovery_hubs = [
        _safe_normalize(url)
        for url in preferred_discovery_seeds(goal, raw_candidate_urls)
        if _safe_normalize(url)
    ]
    seed_urls: list[str] = []
    for url in discovery_hubs + raw_candidate_urls:
        if url and url not in seed_urls:
            seed_urls.append(url)
    seed_urls.sort(
        key=lambda url: seed_url_priority(url, goal, preferred_domains=preferred_domain_set),
        reverse=True,
    )
    # Keep exploration focused: hubs first, then a few search hits as fallbacks.
    seed_urls = seed_urls[: max(max_visits, len(discovery_hubs) + 1)]
    origins = list(dict.fromkeys(origin_url(url) for url in seed_urls))
    allowed_origins = set(origins)
    # Listing hubs + search results are trusted starting points.
    discovered_routes = set(origins) | set(seed_urls)
    start_urls = seed_urls or origins
    if discovery_hubs:
        events.log(
            f"Preferred-site discovery: start on listing hub(s) {', '.join(discovery_hubs[:3])}",
            level="info",
        )
    max_steps = max(max_steps, len(start_urls) * max(1, max_steps_per_branch))
    history: list[dict[str, Any]] = []
    agent_memory: list[dict[str, Any]] = []
    active_branch_url = ""
    stall_break_threshold = 5
    state_attempts: dict[str, set[str]] = {}
    collected_content_keys: set[str] = set()
    report_rejected = False
    helper_guidance: list[dict[str, Any]] = []
    viewport_views_explored: dict[str, int] = {}
    scroll_stitch_cache: dict[str, Any] = {}
    snapshots: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    pages: list[PageResult] = []
    found_content = ""
    goal_met = False
    session_state: dict[str, Any] = {
        "query": query,
        "goal": goal,
        "status": "starting",
        "seed_urls": seed_urls,
        "origins": origins,
        "history": history,
        "agent_memory": agent_memory,
        "snapshots": snapshots,
        "transitions": transitions,
        "discovered_routes": sorted(discovered_routes),
        "accomplishment_steps": plan_steps,
        "data_needed": list(data_needed or []),
        "success_criteria": list(success_criteria or []),
    }
    save_session_state(project_path, session_id, session_state)
    events.candidates(
        {
            "session_id": session_id,
            "candidates": [
                {
                    "url": url,
                    "origin": origin_url(url),
                    "title": str(getattr(row, "title", "") or ""),
                    "snippet": str(getattr(row, "snippet", "") or ""),
                }
                for row, url in zip(candidates[:max_visits], seed_urls)
            ],
        }
    )
    events.criteria(
        {
            "session_id": session_id,
            "criteria": [
                {"criterion": criterion, "met": False}
                for criterion in (success_criteria or [])
            ],
            "unmet_criteria": list(success_criteria or []),
            "accomplishment_steps": plan_steps,
            "data_needed": list(data_needed or []),
            "goal": goal,
        }
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="JarvisWebResearch/2.0 (+local research agent)",
            **recorder.context_options(),
        )
        from web_surf.adblock import install_adblock

        install_adblock(context)
        recorder.attach(context)
        page = context.new_page()

        def _record_agent_memory(
            *,
            outcome: dict[str, Any],
            decision: dict[str, Any] | None = None,
            page_url: str = "",
            snapshot: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            entry = commit_agent_memory(
                step_id=str(outcome.get("step_id") or ""),
                decision=decision if isinstance(decision, dict) else outcome,
                outcome=outcome,
                page_url=page_url or str(page.url or ""),
                snapshot=snapshot,
            )
            agent_memory.append(entry)
            events.agent_memory(
                {
                    "session_id": session_id,
                    "entry": entry,
                    "memory": agent_memory,
                    "total": len(agent_memory),
                }
            )
            return entry

        def _finalize_step(
            item: dict[str, Any],
            *,
            decision: dict[str, Any] | None = None,
            page_url: str = "",
            snapshot: dict[str, Any] | None = None,
        ) -> None:
            history.append(item)
            _record_agent_memory(
                outcome=item,
                decision=decision,
                page_url=page_url,
                snapshot=snapshot,
            )
            events.action({"session_id": session_id, **item})

        try:
            for start_url in start_urls:
                if len(history) >= max_steps or goal_met:
                    break
                active_branch_url = start_url
                previous_url = str(page.url or "")
                bootstrap_id = f"step_{len(history) + 1:03d}"
                try:
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "origin_loaded",
                            "current_url": start_url,
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                        }
                    )
                    page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(750)
                    graph = record_visit(
                        project_path,
                        url=str(page.url),
                        source_url=previous_url,
                        action="origin",
                        step_id=bootstrap_id,
                    )
                    events.visit_graph({"session_id": session_id, "graph": graph})
                    # Already on the requested site — don't force a homepage detour for s1.
                    if preferred_domain_set and url_on_preferred_source(
                        str(page.url or ""),
                        preferred_domain_set,
                    ):
                        marked = infer_step_completion(
                            plan_steps,
                            action="navigate",
                            on_preferred_source=True,
                            page_relevant=False,
                        )
                        if marked:
                            session_state["accomplishment_steps"] = plan_steps
                except Exception as exc:
                    item = {
                        "step_id": bootstrap_id,
                        "action": "origin",
                        "url": start_url,
                        "branch_url": active_branch_url,
                        "ok": False,
                        "error": str(exc),
                    }
                    _finalize_step(item, page_url=start_url)
                    continue

                while (
                    len(history) < max_steps
                    and sum(
                        1
                        for row in history
                        if _safe_normalize(str(row.get("branch_url") or active_branch_url))
                        == _safe_normalize(active_branch_url)
                        and str(row.get("action") or "") not in {"", "pending"}
                    )
                    < max_steps_per_branch
                    and not goal_met
                ):
                    step_id = f"step_{len(history) + 1:03d}"
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "observing",
                            "current_url": str(page.url or ""),
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                        }
                    )
                    # Light observe first — do not build a full-page map under a blocker.
                    snapshot = _snapshot(
                        page,
                        session_id=session_id,
                        step_id=step_id,
                        context="decision",
                        form_values=session_form_values,
                        analyze=False,
                        build_map=False,
                        screenshot_mode="content",
                    )
                    # Age/cookie gates often need form values before deterministic dismiss.
                    if snapshot_needs_overlay_action(snapshot):
                        plan_result = ensure_form_values(
                            query=goal,
                            snapshot=snapshot,
                            form_values=session_form_values,
                            planned_fingerprints=planned_form_fingerprints,
                            ollama_url=ollama_url,
                            model=model,
                            timeout_sec=timeout_sec,
                            provider=form_values_provider,
                        )
                        if plan_result:
                            session_form_values.update(plan_result.get("form_values") or {})
                            field_mapping.update(plan_result.get("field_mapping") or {})
                            events.form_values_plan(
                                {
                                    "session_id": session_id,
                                    "step_id": step_id,
                                    "snapshot_id": snapshot["snapshot_id"],
                                    "available_value_keys": sorted(session_form_values.keys()),
                                    "field_mapping": field_mapping,
                                    "reasoning": str(plan_result.get("reasoning") or ""),
                                    "new_keys": sorted((plan_result.get("form_values") or {}).keys()),
                                    "trigger": "pre_map_overlay",
                                }
                            )
                        snapshot, _cleared = _clear_overlays_before_map(
                            page,
                            snapshot,
                            session_id=session_id,
                            step_id=step_id,
                            form_values=session_form_values,
                            field_mapping=field_mapping,
                            history=history,
                        )
                    # Full-page map only when the page is clear — avoids rebuild after dismiss.
                    if not snapshot_needs_overlay_action(snapshot):
                        snapshot, paged_snapshots = _enhance_snapshot_with_viewport_paging(
                            page,
                            snapshot,
                            goal,
                            session_id=session_id,
                            step_id=step_id,
                            form_values=session_form_values,
                            views_explored=viewport_views_explored,
                            scroll_stitch_cache=scroll_stitch_cache,
                            session_dir=recorder.session_dir,
                        )
                        snapshots.extend(paged_snapshots)
                        if paged_snapshots:
                            events.log(
                                "Built full-page map after page was clear of blockers",
                                level="info",
                            )
                    snapshot = _apply_scroll_stitch(
                        snapshot,
                        scroll_stitch_cache,
                        session_dir=recorder.session_dir,
                    )
                    # Record after map so history frames show the full scrollable page.
                    recorder.record_frame(
                        page,
                        label=step_id,
                        context="decision",
                        snapshot=snapshot,
                    )
                    snapshots.append(_compact_snapshot(snapshot))
                    active_branch_url = resolve_active_branch_url(
                        page_url=str(page.url or ""),
                        seed_urls=seed_urls,
                        active_branch_url=active_branch_url,
                    )
                    if _sync_branch_navigation(
                        page_url=str(page.url or ""),
                        snapshot=snapshot,
                        allowed_origins=allowed_origins,
                        discovered_routes=discovered_routes,
                    ):
                        events.log(
                            "Expanded branch navigation after redirect — continuing on current path",
                            level="info",
                        )
                    promoted = _discover_official_outbound(
                        snapshot,
                        publisher_domain_set,
                        allowed_origins,
                        discovered_routes,
                        preferred_domains=preferred_domain_set,
                    )
                    if promoted:
                        events.log(
                            f"Discovered {len(promoted)} official outbound link(s) — navigation allowed",
                            level="info",
                        )
                        events.candidates(
                            {
                                "session_id": session_id,
                                "tier": "discovered_official",
                                "candidates": [{"url": url} for url in promoted[:12]],
                            }
                        )
                    discovered_routes.update(
                        route
                        for route in snapshot.get("discovered_routes") or []
                        if _safe_normalize(str(route))
                        and origin_url(str(route)) in allowed_origins
                    )
                    map_content_link: dict[str, Any] | None = None
                    if should_defer_collect_on_listing(snapshot, goal):
                        map_content_link = suggest_content_link_action(snapshot, goal)
                        if map_content_link:
                            helper_guidance.append(
                                {
                                    "step_id": step_id,
                                    "kind": "listing_page",
                                    "instruction": (
                                        "This looks like a news/article listing page. "
                                        "Use the built page map — open the newest feed item "
                                        f"({map_content_link.get('target_id')}"
                                        + (
                                            f", {map_content_link.get('feed_title')}"
                                            if map_content_link.get("feed_title")
                                            else ""
                                        )
                                        + (
                                            f", {map_content_link.get('feed_date')}"
                                            if map_content_link.get("feed_date")
                                            else ""
                                        )
                                        + "). Do not settle for an older item."
                                    ),
                                }
                            )
                        elif viewport_has_content_below(snapshot):
                            helper_guidance.append(
                                {
                                    "step_id": step_id,
                                    "kind": "scroll_needed",
                                    "instruction": (
                                        "The page continues below the current view. "
                                        "Use action=scroll to reveal more content before collecting."
                                    ),
                                }
                            )
                    auto_collected = None
                    auto_collect_item: dict[str, Any] = {}
                    if not snapshot_needs_overlay_action(snapshot):
                        auto_collected, auto_collect_item = _try_auto_collect_on_snapshot(
                            page=page,
                            snapshot=snapshot,
                            step_id=step_id,
                            goal=goal,
                            collected_content_keys=collected_content_keys,
                            publisher_domains=publisher_domain_set,
                            discovered_routes=discovered_routes,
                        )
                    if auto_collected:
                        collect_key = _content_collect_key(
                            snapshot,
                            goal,
                            apply_date_filter=should_apply_date_filter(goal),
                        )
                        collected_content_keys.add(collect_key)
                        pages.append(auto_collected)
                        found_content = auto_collected.text
                        events.evidence(
                            {
                                "session_id": session_id,
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "source_url": auto_collected.url,
                                "title": auto_collected.title,
                                "text": auto_collected.text,
                                "note": auto_collect_item.get("reason", ""),
                                "auto_collected": True,
                            }
                        )
                        _finalize_step(
                            {
                                **auto_collect_item,
                                "snapshot_id": snapshot["snapshot_id"],
                            },
                            page_url=str(page.url or ""),
                            snapshot=snapshot,
                        )
                        helper_guidance.append(
                            {
                                "step_id": step_id,
                                "kind": "auto_collected",
                                "instruction": (
                                    "Visible page text matches the goal and was captured automatically. "
                                    "Use action=report now unless key details are still hidden."
                                ),
                            }
                        )
                        if _goal_satisfied_for_page(
                            auto_collected.text,
                            goal,
                            source_url=auto_collected.url,
                            publisher_domains=publisher_domain_set,
                            discovered_routes=discovered_routes,
                            preferred_domains=preferred_domain_set,
                        ):
                            goal_met = True
                    plan_result = ensure_form_values(
                        query=goal,
                        snapshot=snapshot,
                        form_values=session_form_values,
                        planned_fingerprints=planned_form_fingerprints,
                        ollama_url=ollama_url,
                        model=model,
                        timeout_sec=timeout_sec,
                        provider=form_values_provider,
                    )
                    if plan_result:
                        session_form_values.update(plan_result.get("form_values") or {})
                        field_mapping.update(plan_result.get("field_mapping") or {})
                        form_value_reasoning = str(plan_result.get("reasoning") or "")
                        events.form_values_plan(
                            {
                                "session_id": session_id,
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "available_value_keys": sorted(session_form_values.keys()),
                                "field_mapping": field_mapping,
                                "reasoning": form_value_reasoning,
                                "new_keys": sorted((plan_result.get("form_values") or {}).keys()),
                            }
                        )
                    model_context = {
                        "query": goal,
                        "session_id": session_id,
                        "step_id": step_id,
                        "snapshot": _compact_snapshot(snapshot),
                        "discovered_routes": discovered_routes,
                        "seed_urls": seed_urls,
                        "candidates": candidates[:max_visits],
                        "available_value_keys": sorted(session_form_values.keys()),
                        "field_mapping": field_mapping,
                        "recent_history": history,
                        "agent_memory": agent_memory,
                        "last_transition": transitions[-1] if transitions else None,
                        "publishers": publisher_names,
                        "publisher_domains": publisher_domain_set,
                        "preferred_domains": preferred_domain_set,
                        "active_branch_url": active_branch_url,
                        "branch_steps": sum(
                            1
                            for row in history
                            if _safe_normalize(str(row.get("branch_url") or active_branch_url))
                            == _safe_normalize(active_branch_url)
                            and str(row.get("action") or "") not in {"", "pending"}
                        ),
                        "max_steps_per_branch": max_steps_per_branch,
                        "helper_guidance": helper_guidance[-4:],
                        "collected_evidence": [
                            {
                                "url": page_result.url,
                                "title": page_result.title[:120],
                                "chars": len(page_result.text),
                                "step_id": str(
                                    (page_result.evidence_context or {}).get("source_step_id") or ""
                                ),
                            }
                            for page_result in pages[-5:]
                        ],
                        "accomplishment_steps": plan_steps,
                        "data_needed": list(data_needed or []),
                        "success_criteria": list(success_criteria or []),
                    }
                    current_fp = progress_fingerprint(snapshot)
                    blocked_attempts = sorted(state_attempts.get(current_fp, set()))
                    if blocked_attempts:
                        model_context["blocked_attempts"] = blocked_attempts
                    overlay_mode = snapshot_needs_overlay_action(snapshot)
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "overlay_dismiss" if overlay_mode else "deciding",
                            "current_url": str(page.url or ""),
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                            "reason": (
                                "Choosing overlay control from overlay_map…"
                                if overlay_mode
                                else "Waiting for local AI to choose the next action…"
                            ),
                        }
                    )
                    if blocked_attempts:
                        helper_guidance.append(
                            {
                                "step_id": step_id,
                                "kind": "repeat_blocked",
                                "instruction": (
                                    "Blocked actions on this page: try a different control "
                                    "from overlay_map[] — fill age-gate fields, swap_branch, or report."
                                    if overlay_mode
                                    else "Blocked actions on this page: try a different control "
                                    "from menu[] — fill age-gate fields, swap_branch, or report."
                                ),
                            }
                        )
                    try:
                        # When the page map already identified the newest feed item, act on
                        # that map instead of letting the LLM re-pick from contaminated labels.
                        if (
                            not overlay_mode
                            and isinstance(map_content_link, dict)
                            and map_content_link.get("from_page_map")
                            and map_content_link.get("target_id")
                            and should_defer_collect_on_listing(snapshot, goal)
                        ):
                            raw_decision = {
                                "action": "click",
                                "target_id": str(map_content_link["target_id"]),
                                "reason": str(
                                    map_content_link.get("reason")
                                    or "Open newest item from page map."
                                ),
                            }
                            events.log(
                                f"Using page map feed item for next click: {map_content_link.get('target_id')}",
                                level="info",
                            )
                        else:
                            raw_decision = (
                                overlay_decide(model_context) if overlay_mode else decide(model_context)
                            )
                    except Exception as exc:
                        raw_decision = {"action": "help", "question": f"Decision model unavailable: {exc}"}
                    coerced = normalize_decision(raw_decision)
                    if coerced is not None:
                        raw_decision = coerced
                    progress_now = plan_progress(plan_steps)
                    current_plan = progress_now.get("current") if isinstance(progress_now, dict) else None
                    decision_process = {
                        "goal": goal,
                        "current_step": (
                            {
                                "id": current_plan.get("id"),
                                "description": current_plan.get("description"),
                                "done_when": current_plan.get("done_when"),
                            }
                            if isinstance(current_plan, dict)
                            else None
                        ),
                        "action": str(raw_decision.get("action") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "target_id": str(raw_decision.get("target_id") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "url": str(raw_decision.get("url") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "reason": str(raw_decision.get("reason") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "completed_step_id": str(raw_decision.get("completed_step_id") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "page_url": str(snapshot.get("url") or ""),
                        "overlay_dismiss": overlay_mode,
                    }
                    events.decision(
                        {
                            "session_id": session_id,
                            "step_id": step_id,
                            "snapshot_id": snapshot["snapshot_id"],
                            "decision": raw_decision,
                            "overlay_dismiss": overlay_mode,
                            "process": decision_process,
                        }
                    )
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "next_action",
                            "current_url": str(snapshot.get("url") or ""),
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                            "reason": decision_process.get("reason") or "",
                            "process": decision_process,
                        }
                    )
                    if snapshot.get("screenshot_b64"):
                        events.snapshot(_compact_snapshot({**snapshot, "context": "decision"}))
                    recorder.record_decision(raw_decision)
                    if overlay_mode:
                        action, validation_error = validate_overlay_action(
                            raw_decision,
                            snapshot,
                            session_form_values,
                        )
                    else:
                        action, validation_error = validate_action(
                            raw_decision,
                            snapshot,
                            discovered_routes,
                            allowed_origins,
                            session_form_values,
                            seed_urls=seed_urls,
                        )
                    raw_action = (
                        str(raw_decision.get("action") or "").lower().strip()
                        if isinstance(raw_decision, dict)
                        else ""
                    )
                    pending_item = {
                        "step_id": step_id,
                        "snapshot_id": snapshot["snapshot_id"],
                        "action": raw_action or "pending",
                        "reason": str(raw_decision.get("reason") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "target_id": str(raw_decision.get("target_id") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "branch_url": active_branch_url,
                        "ok": None,
                    }
                    session_state.update(
                        {
                            "status": "deciding",
                            "session_id": session_id,
                            "history": [*history, pending_item],
                            "agent_memory": agent_memory,
                            "snapshots": snapshots[-20:],
                            "discovered_routes": sorted(discovered_routes),
                            "current_url": str(page.url or ""),
                        }
                    )
                    save_session_state(project_path, session_id, session_state)
                    if isinstance(raw_decision, dict) and raw_decision.get("completed_step_id"):
                        if mark_step_done(plan_steps, str(raw_decision.get("completed_step_id") or "")):
                            session_state["accomplishment_steps"] = plan_steps
                            events.criteria(
                                {
                                    "session_id": session_id,
                                    "accomplishment_steps": plan_steps,
                                    "goal": goal,
                                    "plan_progress": plan_progress(plan_steps),
                                }
                            )
                    if action is None:
                        if (
                            "not available in the generated form value store" in validation_error
                            or "use provide_values first" in validation_error
                        ):
                            retry_plan = ensure_form_values(
                                query=goal,
                                snapshot=snapshot,
                                form_values=session_form_values,
                                planned_fingerprints=set(),
                                ollama_url=ollama_url,
                                model=model,
                                timeout_sec=timeout_sec,
                                provider=form_values_provider,
                            )
                            if retry_plan:
                                session_form_values.update(retry_plan.get("form_values") or {})
                                field_mapping.update(retry_plan.get("field_mapping") or {})
                                form_value_reasoning = str(retry_plan.get("reasoning") or "")
                                events.form_values_plan(
                                    {
                                        "session_id": session_id,
                                        "step_id": step_id,
                                        "snapshot_id": snapshot["snapshot_id"],
                                        "available_value_keys": sorted(session_form_values.keys()),
                                        "field_mapping": field_mapping,
                                        "reasoning": form_value_reasoning,
                                        "new_keys": sorted((retry_plan.get("form_values") or {}).keys()),
                                        "trigger": "missing_value_key",
                                    }
                                )
                                action, validation_error = validate_action(
                                    raw_decision,
                                    snapshot,
                                    discovered_routes,
                                    allowed_origins,
                                    session_form_values,
                                    seed_urls=seed_urls,
                                )
                        if action is None:
                            item = {
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "action": str(raw_decision.get("action") or "invalid")
                                if isinstance(raw_decision, dict)
                                else "invalid",
                                "reason": pending_item["reason"],
                                "target_id": pending_item["target_id"],
                                "branch_url": active_branch_url,
                                "ok": False,
                                "error": validation_error,
                            }
                            _finalize_step(
                                item,
                                decision=raw_decision if isinstance(raw_decision, dict) else None,
                                snapshot=snapshot,
                            )
                            if sum(1 for row in history[-stall_break_threshold:] if not row.get("ok")) >= stall_break_threshold:
                                events.controller(
                                    {
                                        "session_id": session_id,
                                        "status": "blocked",
                                        "current_url": str(page.url or ""),
                                        "reason": validation_error,
                                    }
                                )
                                break
                            continue

                    attempt_signature = action_signature(action)
                    if action["action"] in {"extract", "filter"}:
                        collect_key = _content_collect_key(
                            snapshot,
                            goal,
                            apply_date_filter=action["action"] == "filter",
                        )
                        attempt_signature = _content_collect_signature(
                            action["action"],
                            collect_key,
                        )
                        if collect_key in collected_content_keys:
                            item = {
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "action": action["action"],
                                "reason": action.get("reason", ""),
                                "target_id": action.get("target_id", pending_item["target_id"]),
                                "branch_url": active_branch_url,
                                "ok": False,
                                "progress": False,
                                "error": (
                                    f"{action['action']} rejected: this page content was already "
                                    "collected — use action=report"
                                ),
                            }
                            _finalize_step(
                                item,
                                decision=raw_decision if isinstance(raw_decision, dict) else None,
                                snapshot=snapshot,
                            )
                            helper_guidance.append(
                                {
                                    "step_id": step_id,
                                    "kind": "duplicate_collect",
                                    "instruction": (
                                        "This page was already extracted. Use action=report now — "
                                        "do not repeat extract or filter on the same content."
                                    ),
                                }
                            )
                            if sum(
                                1
                                for row in history[-stall_break_threshold:]
                                if row.get("progress") is False
                                or "already collected" in str(row.get("error") or "").lower()
                            ) >= stall_break_threshold:
                                events.controller(
                                    {
                                        "session_id": session_id,
                                        "status": "blocked",
                                        "current_url": str(page.url or ""),
                                        "reason": item["error"],
                                    }
                                )
                                break
                            continue
                    if attempt_signature in state_attempts.get(current_fp, set()):
                        item = {
                            "step_id": step_id,
                            "snapshot_id": snapshot["snapshot_id"],
                            "action": action["action"],
                            "reason": action.get("reason", ""),
                            "target_id": action.get("target_id", pending_item["target_id"]),
                            "branch_url": active_branch_url,
                            "ok": False,
                            "progress": False,
                            "error": (
                                "action already tried without progress — "
                                "pick a different control or route"
                            ),
                        }
                        _finalize_step(
                            item,
                            decision=raw_decision if isinstance(raw_decision, dict) else None,
                            snapshot=snapshot,
                        )
                        if sum(
                            1
                            for row in history[-stall_break_threshold:]
                            if row.get("progress") is False or "no progress" in str(row.get("error") or "").lower()
                        ) >= stall_break_threshold:
                            events.controller(
                                {
                                    "session_id": session_id,
                                    "status": "blocked",
                                    "current_url": str(page.url or ""),
                                    "reason": item["error"],
                                }
                            )
                            break
                        session_state.update(
                            {
                                "status": "running",
                                "history": history,
                                "agent_memory": agent_memory,
                                "snapshots": snapshots[-20:],
                                "transitions": transitions[-20:],
                                "discovered_routes": sorted(discovered_routes),
                                "current_url": str(page.url or ""),
                            }
                        )
                        save_session_state(project_path, session_id, session_state)
                        continue

                    item = {
                        "step_id": step_id,
                        "snapshot_id": snapshot["snapshot_id"],
                        "action": raw_action or action["action"],
                        "reason": action.get("reason", ""),
                        "target_id": action.get("target_id", pending_item["target_id"]),
                        "branch_url": active_branch_url,
                    }
                    target_info = action.get("target") if isinstance(action.get("target"), dict) else {}
                    target_label = str(
                        target_info.get("text")
                        or target_info.get("aria")
                        or target_info.get("label")
                        or ""
                    ).strip()
                    if target_label:
                        item["target_label"] = target_label[:80]
                    target_href = str(target_info.get("href") or "").strip()
                    if target_href:
                        item["target_href"] = target_href
                    if action.get("value_key"):
                        item["value_key"] = str(action["value_key"])
                    if action["action"] == "provide_values":
                        session_form_values.update(action["form_values"])
                        events.form_values_plan(
                            {
                                "session_id": session_id,
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "available_value_keys": sorted(session_form_values.keys()),
                                "new_keys": sorted(action["form_values"].keys()),
                                "reasoning": action.get("reason", ""),
                                "trigger": "provide_values_action",
                            }
                        )
                        _finalize_step(
                            {
                                **item,
                                "ok": True,
                                "form_values_added": sorted(action["form_values"].keys()),
                            },
                            decision=raw_decision if isinstance(raw_decision, dict) else None,
                        )
                        continue
                    if action["action"] == "help":
                        events.controller(
                            {
                                "session_id": session_id,
                                "status": "waiting_for_helper",
                                "current_url": str(page.url or ""),
                                "reason": action["question"],
                            }
                        )
                        request_id = f"help_{uuid.uuid4().hex[:12]}"
                        request = {
                            "session_id": session_id,
                            "step_id": step_id,
                            "request_id": request_id,
                            "question": action["question"],
                            "context": _compact_snapshot(snapshot),
                        }
                        events.help_request(request)
                        supplied = help_provider(request) if help_provider else {
                            "status": "transport_unavailable",
                            "answer": "",
                        }
                        result = {
                            "session_id": session_id,
                            "step_id": step_id,
                            "request_id": request_id,
                            **supplied,
                        }
                        events.help_result(result)
                        answer = str(result.get("answer") or "").strip()
                        _finalize_step(
                            {**item, "ok": bool(answer), "help_result": result},
                            decision=raw_decision if isinstance(raw_decision, dict) else None,
                        )
                        if answer:
                            helper_guidance.append(
                                {"step_id": step_id, "question": action["question"], "answer": answer}
                            )
                            continue
                        break
                    if action["action"] in {"extract", "filter", "report"}:
                        events.controller(
                            {
                                "session_id": session_id,
                                "status": "extracting",
                                "current_url": str(page.url or ""),
                                "step": len(history) + 1,
                                "max_steps": max_steps,
                            }
                        )
                        page_result = _page_result(
                            page,
                            snapshot,
                            step_id,
                            goal=goal,
                            apply_date_filter=(
                                action["action"] == "filter"
                                or (
                                    action["action"] == "report"
                                    and should_apply_date_filter(goal)
                                )
                            ),
                        )
                        report_reason = str(action.get("reason") or "")
                        report_note = str(action.get("note") or "")
                        reject_report = False
                        reject_error = ""
                        if action["action"] == "report":
                            # Credit extract steps if we already collected page text earlier.
                            if pages:
                                infer_step_completion(
                                    plan_steps,
                                    action="extract",
                                    page_relevant=True,
                                    evidence_collected=True,
                                )
                            progress = plan_progress(plan_steps)
                            if progress["blocking"]:
                                reject_report = True
                                blocking_desc = "; ".join(
                                    str(step.get("description") or step.get("id") or "")
                                    for step in progress["blocking"][:3]
                                )
                                reject_error = (
                                    "report rejected: accomplishment plan still has unfinished steps — "
                                    f"{blocking_desc}. Advance current_step before reporting."
                                )
                            elif report_is_negative(report_reason, report_note):
                                reject_report = True
                                reject_error = (
                                    "report rejected: reason indicates the answer was not found — "
                                    "click a goal-relevant link or extract more evidence first"
                                )
                            else:
                                on_preferred = bool(
                                    preferred_domain_set
                                    and url_on_preferred_source(
                                        str(snapshot.get("url") or ""),
                                        preferred_domain_set,
                                    )
                                )
                                pending_seeds = unexplored_seed_urls(
                                    seed_urls,
                                    history,
                                    active_branch_url=active_branch_url,
                                    current_page_url=str(snapshot.get("url") or ""),
                                )
                                if on_preferred and page_matches_query(
                                    str(snapshot.get("visible_text") or ""),
                                    goal,
                                ):
                                    pending_seeds = []
                                elif preferred_domain_set:
                                    pending_seeds = [
                                        seed
                                        for seed in pending_seeds
                                        if url_on_preferred_source(seed, preferred_domain_set)
                                    ]
                                if pending_seeds:
                                    auto_swap_url = pending_seeds[0]
                                    events.log(
                                        f"Report blocked — auto swap_branch to {auto_swap_url[:120]}",
                                        level="info",
                                    )
                                    try:
                                        page.goto(
                                            auto_swap_url,
                                            wait_until="domcontentloaded",
                                            timeout=45000,
                                        )
                                        page.wait_for_timeout(500)
                                        active_branch_url = auto_swap_url
                                        state_attempts = {}
                                        auto_item = {
                                            **item,
                                            "action": "swap_branch",
                                            "url": auto_swap_url,
                                            "branch_url": auto_swap_url,
                                            "ok": True,
                                            "reason": (
                                                "Opened unexplored search candidate instead of reporting "
                                                "from current branch"
                                            ),
                                            "auto_swap": True,
                                        }
                                        _finalize_step(
                                            auto_item,
                                            decision=raw_decision
                                            if isinstance(raw_decision, dict)
                                            else None,
                                            page_url=auto_swap_url,
                                        )
                                        helper_guidance.append(
                                            {
                                                "step_id": step_id,
                                                "kind": "auto_swap_branch",
                                                "instruction": (
                                                    f"Exploring search candidate: {auto_swap_url[:120]}. "
                                                    "Use extract/report here if the goal is satisfied."
                                                ),
                                            }
                                        )
                                        swap_snapshot = _snapshot(
                                            page,
                                            session_id=session_id,
                                            step_id=f"{step_id}_swap",
                                            context="post_swap",
                                            form_values=session_form_values,
                                        )
                                        snapshots.append(_compact_snapshot(swap_snapshot))
                                        auto_collected, auto_collect_item = _try_auto_collect_on_snapshot(
                                            page=page,
                                            snapshot=swap_snapshot,
                                            step_id=f"{step_id}_swap",
                                            goal=goal,
                                            collected_content_keys=collected_content_keys,
                                            publisher_domains=publisher_domain_set,
                                            discovered_routes=discovered_routes,
                                        )
                                        if auto_collected:
                                            collect_key = _content_collect_key(
                                                swap_snapshot,
                                                goal,
                                                apply_date_filter=should_apply_date_filter(goal),
                                            )
                                            collected_content_keys.add(collect_key)
                                            pages.append(auto_collected)
                                            found_content = auto_collected.text
                                            events.evidence(
                                                {
                                                    "session_id": session_id,
                                                    "step_id": f"{step_id}_swap",
                                                    "snapshot_id": swap_snapshot["snapshot_id"],
                                                    "source_url": auto_collected.url,
                                                    "title": auto_collected.title,
                                                    "text": auto_collected.text,
                                                    "note": auto_collect_item.get("reason", ""),
                                                    "auto_collected": True,
                                                }
                                            )
                                            _finalize_step(
                                                {
                                                    **auto_collect_item,
                                                    "snapshot_id": swap_snapshot["snapshot_id"],
                                                },
                                                page_url=str(page.url or ""),
                                                snapshot=swap_snapshot,
                                            )
                                            helper_guidance.append(
                                                {
                                                    "step_id": f"{step_id}_swap",
                                                    "kind": "auto_collected",
                                                    "instruction": (
                                                        "New branch page text matches the goal and was captured. "
                                                        "Use action=report."
                                                    ),
                                                }
                                            )
                                        continue
                                    except Exception as exc:
                                        reject_report = True
                                        reject_error = (
                                            "report rejected: other search candidates were not tried yet — "
                                            f"use swap_branch to explore {auto_swap_url[:120]} "
                                            f"(auto-swap failed: {exc})"
                                        )
                            if not reject_report and page_has_goal_links(snapshot, goal):
                                reject_report = True
                                reject_error = (
                                    "report rejected: goal-relevant links are still available — "
                                    "click one of them before reporting"
                                )
                            elif (
                                not reject_report
                                and not preferred_domain_set
                                and publisher_domain_set
                                and is_secondary_host(str(snapshot.get("url") or ""))
                                and any(is_publisher_content_url(str(route)) for route in discovered_routes)
                            ):
                                reject_report = True
                                reject_error = (
                                    "report rejected: official publisher article pages are still available — "
                                    "swap_branch or follow a news/article link before reporting from forums"
                                )
                            elif (
                                not reject_report
                                and not preferred_domain_set
                                and publisher_domain_set
                                and not url_on_publisher_domain(
                                    str(snapshot.get("url") or ""),
                                    publisher_domain_set,
                                )
                                and _publisher_routes(discovered_routes, publisher_domain_set)
                            ):
                                reject_report = True
                                reject_error = (
                                    "report rejected: official publisher sources are still available — "
                                    "follow a publisher link before reporting from a secondary site"
                                )
                            elif (
                                not reject_report
                                and not report_rejected
                                and not _goal_satisfied_for_page(
                                    page_result.text,
                                    goal,
                                    source_url=page_result.url,
                                    publisher_domains=publisher_domain_set,
                                    discovered_routes=discovered_routes,
                                    preferred_domains=preferred_domain_set,
                                )
                            ):
                                reject_report = True
                                report_rejected = True
                                reject_error = (
                                    "report rejected: current page text does not answer the goal — "
                                    "navigate to a page that does, or report again if you are certain"
                                )
                        if reject_report:
                            item = {
                                **item,
                                "ok": False,
                                "error": reject_error,
                            }
                            _finalize_step(item, decision=raw_decision if isinstance(raw_decision, dict) else None)
                            continue
                        events.extract_preview(
                            {
                                "phase": "collected",
                                "action": action["action"],
                                "url": page_result.url,
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "visible_text_chars": len(page_result.text),
                                "text_preview": page_result.text[:1500],
                                "ok": page_result.ok,
                            }
                        )
                        if page_result.ok:
                            pages.append(page_result)
                            found_content = page_result.text
                            goal_met = action["action"] == "report" and page_result.ok
                            page_relevant = page_matches_query(page_result.text, goal)
                            on_preferred = bool(
                                preferred_domain_set
                                and url_on_preferred_source(page_result.url, preferred_domain_set)
                            )
                            marked = infer_step_completion(
                                plan_steps,
                                action=str(action["action"]),
                                page_relevant=page_relevant,
                                evidence_collected=action["action"] in {"extract", "filter", "report"},
                                reported=action["action"] == "report",
                                on_preferred_source=on_preferred,
                            )
                            if marked:
                                session_state["accomplishment_steps"] = plan_steps
                                events.criteria(
                                    {
                                        "session_id": session_id,
                                        "accomplishment_steps": plan_steps,
                                        "goal": goal,
                                        "plan_progress": plan_progress(plan_steps),
                                        "marked_steps": marked,
                                    }
                                )
                            evidence_payload = {
                                "session_id": session_id,
                                "step_id": step_id,
                                "snapshot_id": snapshot["snapshot_id"],
                                "source_url": page_result.url,
                                "title": page_result.title,
                                "text": page_result.text,
                                "note": action.get("note", ""),
                            }
                            events.evidence(evidence_payload)
                            if action["action"] in {"extract", "filter"}:
                                collect_key = _content_collect_key(
                                    snapshot,
                                    goal,
                                    apply_date_filter=action["action"] == "filter",
                                )
                                collected_content_keys.add(collect_key)
                                state_attempts.setdefault(current_fp, set()).add(
                                    _content_collect_signature(action["action"], collect_key)
                                )
                                instruction = (
                                    "Page content was collected. Use action=report now — "
                                    "do not extract or filter the same page again."
                                )
                                if _goal_satisfied_for_page(
                                    page_result.text,
                                    goal,
                                    source_url=page_result.url,
                                    publisher_domains=publisher_domain_set,
                                    discovered_routes=discovered_routes,
                                    preferred_domains=preferred_domain_set,
                                ):
                                    instruction = (
                                        "Collected text answers the goal. Use action=report now — "
                                        "do not extract the same page again."
                                    )
                                helper_guidance.append(
                                    {
                                        "step_id": step_id,
                                        "kind": "content_collected",
                                        "instruction": instruction,
                                    }
                                )
                        _finalize_step({**item, "ok": page_result.ok}, decision=raw_decision if isinstance(raw_decision, dict) else None)
                        if action["action"] == "report" and page_result.ok:
                            events.criteria(
                                {
                                    "session_id": session_id,
                                    "criteria": [
                                        {
                                            "criterion": criterion,
                                            "met": True,
                                            "note": action.get("note", ""),
                                        }
                                        for criterion in (success_criteria or [])
                                    ],
                                    "unmet_criteria": [],
                                }
                            )
                            break
                        continue

                    before = str(page.url or "")
                    target_info = action.get("target") if isinstance(action.get("target"), dict) else {}
                    target_label = _target_label(target_info)
                    acting_reason = f"{action['action']}"
                    if target_label:
                        acting_reason = f"{action['action']} → {target_label[:80]}"
                    elif action.get("target_id"):
                        acting_reason = f"{action['action']} → #{action['target_id']}"
                    elif action.get("url"):
                        acting_reason = f"{action['action']} → {action['url']}"
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "acting",
                            "current_url": before,
                            "action": action["action"],
                            "target_id": action.get("target_id"),
                            "target_label": target_label[:80] if target_label else "",
                            "reason": acting_reason,
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                        }
                    )
                    try:
                        _execute(page, action)
                        after = str(page.url or "")
                        graph = record_visit(
                            project_path,
                            url=after,
                            title=str(snapshot.get("title") or ""),
                            source_url=before,
                            action=action["action"],
                            step_id=step_id,
                        )
                        events.visit_graph({"session_id": session_id, "graph": graph})
                        item = {**item, "ok": True, "url": after}
                    except Exception as exc:
                        item = {**item, "ok": False, "error": str(exc)}
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "observing",
                            "current_url": str(page.url or ""),
                            "reason": "Checking whether the action changed the page…",
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                        }
                    )
                    # After an action: observe lightly; only rebuild the full-page map
                    # when blockers are gone (so overlay dismiss does not map twice).
                    post_snapshot = _snapshot(
                        page,
                        session_id=session_id,
                        step_id=step_id,
                        context="post_action",
                        form_values=session_form_values,
                        analyze=False,
                        build_map=False,
                        screenshot_mode="content",
                    )
                    if not snapshot_needs_overlay_action(post_snapshot):
                        post_snapshot, _ = _enhance_snapshot_with_viewport_paging(
                            page,
                            post_snapshot,
                            goal,
                            session_id=session_id,
                            step_id=f"{step_id}_after",
                            form_values=session_form_values,
                            views_explored=viewport_views_explored,
                            scroll_stitch_cache=scroll_stitch_cache,
                            session_dir=recorder.session_dir,
                        )
                    post_snapshot = _apply_scroll_stitch(
                        post_snapshot,
                        scroll_stitch_cache,
                        session_dir=recorder.session_dir,
                    )
                    recorder.record_frame(
                        page,
                        label=f"{step_id}_after",
                        context="post_action",
                        snapshot=post_snapshot,
                    )
                    snapshots.append(_compact_snapshot(post_snapshot))
                    _sync_branch_navigation(
                        page_url=str(page.url or ""),
                        snapshot=post_snapshot,
                        allowed_origins=allowed_origins,
                        discovered_routes=discovered_routes,
                    )
                    if raw_action == "swap_branch" or action.get("swap_branch"):
                        item["action"] = "swap_branch"
                        swap_target = match_seed_url(
                            str(action.get("url") or raw_decision.get("url") or ""),
                            seed_urls,
                        ) or _safe_normalize(
                            str(action.get("url") or raw_decision.get("url") or "")
                        )
                        if swap_target:
                            active_branch_url = swap_target
                            item["branch_url"] = swap_target
                            item["url"] = str(page.url or "")
                            state_attempts = {}

                    delta = diff_page_states(snapshot, post_snapshot)
                    transition = {
                        "session_id": session_id,
                        "step_id": step_id,
                        "action": action["action"],
                        "before_snapshot_id": snapshot["snapshot_id"],
                        "after_snapshot_id": post_snapshot["snapshot_id"],
                        "delta": delta,
                    }
                    transitions.append(transition)
                    item["transition"] = delta
                    events.transition(transition)
                    state_attempts.setdefault(current_fp, set()).add(attempt_signature)
                    if is_no_progress(snapshot, post_snapshot, delta):
                        item["ok"] = False
                        item["progress"] = False
                        item["error"] = (
                            "no progress — page state unchanged; "
                            "try a different control or route"
                        )
                    elif not item.get("ok"):
                        item["progress"] = False
                    if delta["new_blockers"] or (
                        not item["ok"] and "intercept" in str(item.get("error") or "").lower()
                    ):
                        blocker = delta["new_blockers"] or delta["blocking_overlays"]
                        from web_surf.form_values import AGE_GATE_AGENT_NOTE, looks_like_age_gate

                        instruction = (
                            "Resolve this blocker using available_value_keys and field_mapping. "
                            "Use provide_values if new semantic keys are needed."
                        )
                        if looks_like_age_gate(post_snapshot):
                            instruction = f"{instruction} {AGE_GATE_AGENT_NOTE}"
                        helper_guidance.append(
                            {
                                "step_id": step_id,
                                "kind": "blocking_overlay",
                                "error": item.get("error", ""),
                                "blockers": blocker,
                                "instruction": instruction,
                            }
                        )
                    _finalize_step(
                        item,
                        decision=raw_decision if isinstance(raw_decision, dict) else None,
                        snapshot=post_snapshot,
                    )
                    if item.get("progress") is False:
                        from ui_test.expandable import (
                            is_collapse_toggle,
                            is_collapsed_section,
                            section_text_growth,
                            wait_for_section_expand,
                        )

                        target = action.get("target") if isinstance(action.get("target"), dict) else {}
                        if (
                            action.get("action") == "click"
                            and is_collapse_toggle(target)
                            and is_collapsed_section(target)
                        ):
                            if wait_for_section_expand(page, target, timeout_ms=5000):
                                post_snapshot = _snapshot(
                                    page,
                                    session_id=session_id,
                                    step_id=step_id,
                                    context="post_expand",
                                    form_values=session_form_values,
                                )
                                snapshots.append(_compact_snapshot(post_snapshot))
                                if section_text_growth(snapshot, post_snapshot):
                                    item["ok"] = True
                                    item["progress"] = True
                                    item.pop("error", None)
                                    history[-1] = item
                                    if agent_memory:
                                        agent_memory[-1] = commit_agent_memory(
                                            step_id=str(item.get("step_id") or ""),
                                            decision=raw_decision if isinstance(raw_decision, dict) else item,
                                            outcome=item,
                                            page_url=str(page.url or ""),
                                        )
                                        events.agent_memory(
                                            {
                                                "session_id": session_id,
                                                "entry": agent_memory[-1],
                                                "memory": agent_memory,
                                                "total": len(agent_memory),
                                                "updated": True,
                                            }
                                        )
                                    events.action({"session_id": session_id, **item})
                                    helper_guidance.append(
                                        {
                                            "step_id": step_id,
                                            "kind": "section_expanded",
                                            "instruction": (
                                                "Collapsed section expanded successfully. "
                                                "Use filter/extract on the visible content, then report."
                                            ),
                                        }
                                    )
                                    session_state.update(
                                        {
                                            "status": "running",
                                            "history": history,
                                            "agent_memory": agent_memory,
                                            "snapshots": snapshots[-20:],
                                            "transitions": transitions[-20:],
                                            "discovered_routes": sorted(discovered_routes),
                                            "current_url": str(page.url or ""),
                                        }
                                    )
                                    save_session_state(project_path, session_id, session_state)
                                    continue
                        elif action.get("action") == "click" and isinstance(action.get("target"), dict):
                            try:
                                _locator_for(page, action["target"]).scroll_into_view_if_needed(
                                    timeout=3000
                                )
                                page.wait_for_timeout(500)
                                post_snapshot = _snapshot(
                                    page,
                                    session_id=session_id,
                                    step_id=step_id,
                                    context="post_scroll",
                                    form_values=session_form_values,
                                )
                            except Exception:
                                pass
                        collected, collect_item = _auto_collect_from_page(
                            page=page,
                            snapshot=post_snapshot,
                            step_id=step_id,
                            goal=goal,
                            reason=(
                                "Collect target-date section already present on this page "
                                "instead of repeating ineffective navigation"
                            ),
                            publisher_domains=publisher_domain_set,
                            discovered_routes=discovered_routes,
                        )
                        if collected:
                            pages.append(collected)
                            found_content = collected.text
                            events.evidence(
                                {
                                    "session_id": session_id,
                                    "step_id": step_id,
                                    "snapshot_id": post_snapshot["snapshot_id"],
                                    "source_url": collected.url,
                                    "title": collected.title,
                                    "text": collected.text,
                                    "note": collect_item.get("reason", ""),
                                    "auto_collected": True,
                                }
                            )
                            _finalize_step(
                                {
                                    **collect_item,
                                    "snapshot_id": post_snapshot["snapshot_id"],
                                }
                            )
                            if _goal_satisfied_for_page(
                                collected.text,
                                goal,
                                source_url=collected.url,
                                publisher_domains=publisher_domain_set,
                                discovered_routes=discovered_routes,
                                preferred_domains=preferred_domain_set,
                            ):
                                goal_met = True
                                events.criteria(
                                    {
                                        "session_id": session_id,
                                        "criteria": [
                                            {
                                                "criterion": criterion,
                                                "met": True,
                                                "note": collect_item.get("reason", ""),
                                            }
                                            for criterion in (success_criteria or [])
                                        ],
                                        "unmet_criteria": [],
                                    }
                                )
                                break
                            helper_guidance.append(
                                {
                                    "step_id": step_id,
                                    "kind": "content_collected",
                                    "instruction": (
                                        "Target-date content was collected from the current page. "
                                        "Use action=report now instead of navigating elsewhere."
                                    ),
                                }
                            )
                            session_state.update(
                                {
                                    "status": "running",
                                    "history": history,
                                    "agent_memory": agent_memory,
                                    "snapshots": snapshots[-20:],
                                    "transitions": transitions[-20:],
                                    "discovered_routes": sorted(discovered_routes),
                                    "current_url": str(page.url or ""),
                                }
                            )
                            save_session_state(project_path, session_id, session_state)
                            continue
                        helper_guidance.append(
                            {
                                "step_id": step_id,
                                "kind": "no_progress",
                                "error": item.get("error", ""),
                                "instruction": (
                                    "The last action left the page in the same state. "
                                    "Use action=filter or action=extract on the current page text, "
                                    "or scroll to reveal more content — do not repeat the same click."
                                ),
                            }
                        )
                        if sum(
                            1
                            for row in history[-stall_break_threshold:]
                            if row.get("progress") is False
                            or "no progress" in str(row.get("error") or "").lower()
                        ) >= stall_break_threshold:
                            stuck_collected, stuck_item = _auto_collect_from_page(
                                page=page,
                                snapshot=post_snapshot,
                                step_id=step_id,
                                goal=goal,
                                reason=(
                                    "Stopped repeating navigation; collected target-date section "
                                    "from the current canonical page"
                                ),
                                publisher_domains=publisher_domain_set,
                                discovered_routes=discovered_routes,
                            )
                            if stuck_collected and _goal_satisfied_for_page(
                                stuck_collected.text,
                                goal,
                                source_url=stuck_collected.url,
                                publisher_domains=publisher_domain_set,
                                discovered_routes=discovered_routes,
                                preferred_domains=preferred_domain_set,
                            ):
                                pages.append(stuck_collected)
                                found_content = stuck_collected.text
                                events.evidence(
                                    {
                                        "session_id": session_id,
                                        "step_id": step_id,
                                        "snapshot_id": post_snapshot["snapshot_id"],
                                        "source_url": stuck_collected.url,
                                        "title": stuck_collected.title,
                                        "text": stuck_collected.text,
                                        "note": stuck_item.get("reason", ""),
                                        "auto_collected": True,
                                    }
                                )
                                _finalize_step(
                                    {
                                        **stuck_item,
                                        "snapshot_id": post_snapshot["snapshot_id"],
                                    }
                                )
                                goal_met = True
                                events.criteria(
                                    {
                                        "session_id": session_id,
                                        "criteria": [
                                            {
                                                "criterion": criterion,
                                                "met": True,
                                                "note": stuck_item.get("reason", ""),
                                            }
                                            for criterion in (success_criteria or [])
                                        ],
                                        "unmet_criteria": [],
                                    }
                                )
                                break
                            events.controller(
                                {
                                    "session_id": session_id,
                                    "status": "blocked",
                                    "current_url": str(page.url or ""),
                                    "reason": item.get("error") or "stuck repeating ineffective actions",
                                }
                            )
                            break
                        session_state.update(
                            {
                                "status": "running",
                                "history": history,
                                "agent_memory": agent_memory,
                                "snapshots": snapshots[-20:],
                                "transitions": transitions[-20:],
                                "discovered_routes": sorted(discovered_routes),
                                "current_url": str(page.url or ""),
                            }
                        )
                        save_session_state(project_path, session_id, session_state)
                        continue
                    session_state.update(
                        {
                            "status": "running",
                            "history": history,
                            "agent_memory": agent_memory,
                            "snapshots": snapshots[-20:],
                            "transitions": transitions[-20:],
                            "discovered_routes": sorted(discovered_routes),
                            "current_url": str(page.url or ""),
                        }
                    )
                    save_session_state(project_path, session_id, session_state)
        finally:
            session_state.update(
                {
                    "status": "completed" if goal_met else "incomplete",
                    "history": history,
                    "agent_memory": agent_memory,
                    "snapshots": snapshots[-20:],
                    "transitions": transitions[-20:],
                    "discovered_routes": sorted(discovered_routes),
                    "current_url": str(page.url or ""),
                    "goal_met": goal_met,
                    "goal": goal,
                    "accomplishment_steps": plan_steps,
                    "data_needed": list(data_needed or []),
                    "success_criteria": list(success_criteria or []),
                }
            )
            save_session_state(project_path, session_id, session_state)
            events.controller(
                {
                    "session_id": session_id,
                    "status": "complete" if goal_met else "incomplete",
                    "current_url": str(page.url or ""),
                    "step": len(history),
                    "max_steps": max_steps,
                    "goal_met": goal_met,
                }
            )
            try:
                browser.close()
            except Exception:
                pass
            try:
                session_manifest = session_manifest_paths(
                    recorder.finalize(),
                    base="web-artifacts/playwright-session",
                )
                events.playwright_session(
                    {
                        "source": "web",
                        "session_id": session_id,
                        "session": session_manifest,
                    }
                )
            except Exception as exc:
                events.log(f"Failed to persist web session replay: {exc}", level="warn")

    graph = load_visit_graph(project_path)
    return pages, found_content, goal_met, {
        "session_id": session_id,
        "steps": history,
        "visited_pages": list((graph.get("nodes") or {}).values()),
        "unmet_criteria": [] if goal_met else list(success_criteria or []),
        "helper_history": helper_guidance,
        "agent_memory": agent_memory,
        "transitions": transitions,
        "goal": goal,
        "accomplishment_steps": plan_steps,
        "data_needed": list(data_needed or []),
        "plan_progress": plan_progress(plan_steps),
    }
