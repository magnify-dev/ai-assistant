from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from web_surf import events
from web_surf.context_curate import curate_browse_context, normalize_decision
from web_surf.fetch import PageResult
from web_surf.page_match import focus_query, page_matches_query
from web_surf.form_values import (
    ensure_form_values,
    is_verification_field,
    sanitize_form_values,
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

logger = logging.getLogger(__name__)

DecisionProvider = Callable[[dict[str, Any]], dict[str, Any]]
HelpProvider = Callable[[dict[str, Any]], dict[str, Any]]
ALLOWED_ACTIONS = {
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
) -> DecisionProvider:
    def decide(context: dict[str, Any]) -> dict[str, Any]:
        raw = ollama_chat(
            prompt_key="web_research.browse_decide",
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=_get_prompt("web_research.browse_decide"),
            user=json.dumps(
                curate_browse_context(
                    query=str(context.get("query") or ""),
                    step_id=str(context.get("step_id") or ""),
                    snapshot=context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {},
                    discovered_routes=set(context.get("routes") or context.get("discovered_routes") or []),
                    available_value_keys=list(context.get("keys") or context.get("available_value_keys") or []),
                    field_mapping=context.get("map") if isinstance(context.get("map"), dict) else context.get("field_mapping"),
                    recent_history=context.get("history") if isinstance(context.get("history"), list) else context.get("recent_history"),
                    last_transition=context.get("last") or context.get("last_transition"),
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


def validate_action(
    decision: Any,
    snapshot: dict[str, Any],
    discovered_routes: set[str],
    allowed_origins: set[str] | None = None,
    form_values: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(decision, dict):
        return None, "decision must be an object"
    action = str(decision.get("action") or "").lower().strip()
    if action not in ALLOWED_ACTIONS:
        return None, f"unsupported action: {action or '(missing)'}"
    if action == "ask_helper":
        action = "help"
    validated = {
        "action": action,
        "reason": str(decision.get("reason") or "")[:500],
    }
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
            )
    element_actions = {"click", "fill", "select", "press"}
    if action in element_actions:
        target_id = str(decision.get("target_id") or "").strip()
        if not target_id or target_id not in elements:
            return None, f"{action} target_id is not in the current snapshot"
        if elements[target_id].get("disabled"):
            return None, f"{action} target is disabled"
        target_href = _safe_normalize(str(elements[target_id].get("href") or ""))
        if target_href and allowed_origins and origin_url(target_href) not in allowed_origins:
            return None, f"{action} target leaves the allowed candidate origins"
        validated["target_id"] = target_id
        validated["target"] = elements[target_id]
    if action == "navigate":
        url = _safe_normalize(str(decision.get("url") or ""))
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
        validated["value"] = value[:2000]
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
    if action in {"extract", "report"}:
        validated["note"] = str(decision.get("note") or "")[:2000]
    return validated, ""


def _redact_form_values(snapshot: dict[str, Any], form_values: dict[str, str] | None) -> dict[str, Any]:
    """Keep explicit form values in the browser, but out of model-facing state."""
    values = [value for value in (form_values or {}).values() if value]
    if not values:
        return snapshot
    redacted = dict(snapshot)
    for field in ("visible_text",):
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
) -> dict[str, Any]:
    from ui_test.browser_state import collect_page_state

    state = _redact_form_values(collect_page_state(page, include_screenshot=True), form_values)
    state["snapshot_id"] = f"snap_{uuid.uuid4().hex[:12]}"
    state["session_id"] = session_id
    state["step_id"] = step_id
    state["context"] = context
    events.snapshot(state)
    return state


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "screenshot_b64"}


def _locator_for(page: Any, item: dict[str, Any]) -> Any:
    test_id = str(item.get("test_id") or "")
    role = str(item.get("role") or item.get("kind") or "")
    text = str(item.get("text") or item.get("aria") or "")
    href = str(item.get("href") or "")
    if test_id:
        return page.get_by_test_id(test_id).first
    if role in {"link", "button", "menuitem", "textbox", "checkbox", "radio", "combobox"} and text:
        return page.get_by_role(role, name=text, exact=True).first
    if href:
        return page.locator(f"a[href={json.dumps(href)}]").first
    if text:
        return page.get_by_text(text, exact=True).first
    placeholder = str(item.get("placeholder") or "")
    if placeholder:
        return page.get_by_placeholder(placeholder, exact=True).first
    raise ValueError("target has no usable semantic locator")


def _execute(page: Any, action: dict[str, Any]) -> None:
    kind = action["action"]
    # Short element timeouts: a click blocked by an overlay should fail fast so the
    # next snapshot (which lists the overlay) reaches the model quickly.
    if kind == "navigate":
        page.goto(action["url"], wait_until="domcontentloaded", timeout=45000)
    elif kind == "click":
        _locator_for(page, action["target"]).click(timeout=8000)
    elif kind == "fill":
        _locator_for(page, action["target"]).fill(action["value"], timeout=8000)
    elif kind == "select":
        _locator_for(page, action["target"]).select_option(label=action["value"], timeout=8000)
    elif kind == "press":
        _locator_for(page, action["target"]).press(action["value"], timeout=8000)
    elif kind == "scroll":
        page.mouse.wheel(0, action["amount"])
    elif kind == "back":
        page.go_back(wait_until="domcontentloaded", timeout=45000)
    elif kind == "wait":
        page.wait_for_timeout(action["duration_ms"])
    if kind in {"navigate", "click", "fill", "select", "press", "scroll", "back"}:
        page.wait_for_timeout(750)


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


def _page_result(page: Any, snapshot: dict[str, Any], step_id: str) -> PageResult:
    text = str(snapshot.get("visible_text") or "").strip()[:12000]
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


def explore_candidates_in_browser(
    *,
    query: str,
    candidates: list[Any],
    project_path: Path,
    max_visits: int = 5,
    max_steps: int = 20,
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5:14b",
    timeout_sec: float = 120.0,
    decision_provider: DecisionProvider | None = None,
    help_provider: HelpProvider | None = None,
    success_criteria: list[str] | None = None,
    form_values: dict[str, str] | None = None,
    form_values_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[list[PageResult], str, bool, dict[str, Any]]:
    """Explore from result origins, executing exactly one validated model action per step."""
    from playwright.sync_api import sync_playwright
    from ui_test.playwright_session import PlaywrightSessionRecorder, session_manifest_paths

    session_id = f"web_{uuid.uuid4().hex}"
    # Strip the collaboration wrapper so decisions and scoring see the user's task.
    goal = focus_query(query)
    recorder = PlaywrightSessionRecorder(
        project_path / ".agent" / "current" / "web-artifacts" / "playwright-session"
    )
    recorder.prepare()
    decide = decision_provider or ollama_decision_provider(
        ollama_url=ollama_url,
        model=model,
        timeout_sec=timeout_sec,
    )
    session_form_values: dict[str, str] = {
        str(key): str(value)
        for key, value in (form_values or {}).items()
        if str(value)
    }
    planned_form_fingerprints: set[str] = set()
    field_mapping: dict[str, str] = {}
    form_value_reasoning = ""
    seed_urls: list[str] = []
    for row in candidates[:max_visits]:
        url = _safe_normalize(str(getattr(row, "url", "")))
        if url and url not in seed_urls:
            seed_urls.append(url)
    origins = list(dict.fromkeys(origin_url(url) for url in seed_urls))
    allowed_origins = set(origins)
    # Search results are trusted starting points: land on them directly and let
    # the model navigate back to them at any time.
    discovered_routes = set(origins) | set(seed_urls)
    start_urls = seed_urls or origins
    history: list[dict[str, Any]] = []
    report_rejected = False
    helper_guidance: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    pages: list[PageResult] = []
    found_content = ""
    goal_met = False
    session_state: dict[str, Any] = {
        "query": query,
        "status": "starting",
        "seed_urls": seed_urls,
        "origins": origins,
        "history": history,
        "snapshots": snapshots,
        "transitions": transitions,
        "discovered_routes": sorted(discovered_routes),
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
        }
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="JarvisWebResearch/2.0 (+local research agent)",
            **recorder.context_options(),
        )
        recorder.attach(context)
        page = context.new_page()
        try:
            for start_url in start_urls:
                if len(history) >= max_steps or goal_met:
                    break
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
                except Exception as exc:
                    item = {
                        "step_id": bootstrap_id,
                        "action": "origin",
                        "url": start_url,
                        "ok": False,
                        "error": str(exc),
                    }
                    history.append(item)
                    events.action({"session_id": session_id, **item})
                    continue

                while len(history) < max_steps and not goal_met:
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
                    snapshot = _snapshot(
                        page,
                        session_id=session_id,
                        step_id=step_id,
                        context="decision",
                        form_values=session_form_values,
                    )
                    recorder.record_frame(
                        page,
                        label=step_id,
                        context="decision",
                        snapshot=snapshot,
                    )
                    snapshots.append(_compact_snapshot(snapshot))
                    discovered_routes.update(
                        route
                        for route in snapshot.get("discovered_routes") or []
                        if _safe_normalize(str(route))
                        and origin_url(str(route)) in allowed_origins
                    )
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
                        "available_value_keys": sorted(session_form_values.keys()),
                        "field_mapping": field_mapping,
                        "recent_history": history[-5:],
                        "last_transition": transitions[-1] if transitions else None,
                    }
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "deciding",
                            "current_url": str(page.url or ""),
                            "step": len(history) + 1,
                            "max_steps": max_steps,
                        }
                    )
                    try:
                        raw_decision = decide(model_context)
                    except Exception as exc:
                        raw_decision = {"action": "help", "question": f"Decision model unavailable: {exc}"}
                    coerced = normalize_decision(raw_decision)
                    if coerced is not None:
                        raw_decision = coerced
                    events.decision(
                        {
                            "session_id": session_id,
                            "step_id": step_id,
                            "snapshot_id": snapshot["snapshot_id"],
                            "decision": raw_decision,
                        }
                    )
                    recorder.record_decision(raw_decision)
                    action, validation_error = validate_action(
                        raw_decision,
                        snapshot,
                        discovered_routes,
                        allowed_origins,
                        session_form_values,
                    )
                    pending_item = {
                        "step_id": step_id,
                        "snapshot_id": snapshot["snapshot_id"],
                        "action": str(raw_decision.get("action") or "pending")
                        if isinstance(raw_decision, dict)
                        else "pending",
                        "reason": str(raw_decision.get("reason") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "target_id": str(raw_decision.get("target_id") or "")
                        if isinstance(raw_decision, dict)
                        else "",
                        "ok": None,
                    }
                    session_state.update(
                        {
                            "status": "deciding",
                            "session_id": session_id,
                            "history": [*history, pending_item],
                            "snapshots": snapshots[-20:],
                            "discovered_routes": sorted(discovered_routes),
                            "current_url": str(page.url or ""),
                        }
                    )
                    save_session_state(project_path, session_id, session_state)
                    if action is None:
                        if "not available in the generated form value store" in validation_error:
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
                                "ok": False,
                                "error": validation_error,
                            }
                            history.append(item)
                            events.action({"session_id": session_id, **item})
                            if sum(1 for row in history[-3:] if not row.get("ok")) >= 3:
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

                    item = {
                        "step_id": step_id,
                        "snapshot_id": snapshot["snapshot_id"],
                        "action": action["action"],
                        "reason": action.get("reason", ""),
                        "target_id": action.get("target_id", pending_item["target_id"]),
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
                        history.append(
                            {
                                **item,
                                "ok": True,
                                "form_values_added": sorted(action["form_values"].keys()),
                            }
                        )
                        events.action({"session_id": session_id, **history[-1]})
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
                        history.append({**item, "ok": bool(answer), "help_result": result})
                        if answer:
                            helper_guidance.append(
                                {"step_id": step_id, "question": action["question"], "answer": answer}
                            )
                            continue
                        break
                    if action["action"] in {"extract", "report"}:
                        events.controller(
                            {
                                "session_id": session_id,
                                "status": "extracting",
                                "current_url": str(page.url or ""),
                                "step": len(history) + 1,
                                "max_steps": max_steps,
                            }
                        )
                        page_result = _page_result(page, snapshot, step_id)
                        if (
                            action["action"] == "report"
                            and not report_rejected
                            and not page_matches_query(page_result.text, goal, min_chars=300)
                        ):
                            # One-time guard against reporting from a page that clearly
                            # lacks the requested data (404s, unrelated pages).
                            report_rejected = True
                            item = {
                                **item,
                                "ok": False,
                                "error": (
                                    "report rejected: current page text does not answer the goal — "
                                    "navigate to a page that does, or report again if you are certain"
                                ),
                            }
                            history.append(item)
                            events.action({"session_id": session_id, **item})
                            continue
                        if page_result.ok:
                            pages.append(page_result)
                            found_content = page_result.text
                            goal_met = action["action"] == "report"
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
                        history.append({**item, "ok": page_result.ok})
                        events.action({"session_id": session_id, **history[-1]})
                        if action["action"] == "report":
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
                    events.controller(
                        {
                            "session_id": session_id,
                            "status": "acting",
                            "current_url": before,
                            "action": action["action"],
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
                    post_snapshot = _snapshot(
                        page,
                        session_id=session_id,
                        step_id=step_id,
                        context="post_action",
                        form_values=session_form_values,
                    )
                    recorder.record_frame(
                        page,
                        label=f"{step_id}_after",
                        context="post_action",
                        snapshot=post_snapshot,
                    )
                    snapshots.append(_compact_snapshot(post_snapshot))
                    from ui_test.state_diff import diff_page_states

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
                    if delta["new_blockers"] or (
                        not item["ok"] and "intercept" in str(item.get("error") or "").lower()
                    ):
                        blocker = delta["new_blockers"] or delta["blocking_overlays"]
                        helper_guidance.append(
                            {
                                "step_id": step_id,
                                "kind": "blocking_overlay",
                                "error": item.get("error", ""),
                                "blockers": blocker,
                                "instruction": (
                                    "Resolve this blocker using available_value_keys and field_mapping. "
                                    "Use provide_values if new semantic keys are needed."
                                ),
                            }
                        )
                    history.append(item)
                    events.action({"session_id": session_id, **history[-1]})
                    session_state.update(
                        {
                            "status": "running",
                            "history": history,
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
                    "snapshots": snapshots[-20:],
                    "transitions": transitions[-20:],
                    "discovered_routes": sorted(discovered_routes),
                    "current_url": str(page.url or ""),
                    "goal_met": goal_met,
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
        "transitions": transitions,
    }
