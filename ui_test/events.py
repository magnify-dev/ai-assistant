from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class Phase(str, Enum):
    IDLE = "idle"
    OLLAMA = "ollama"
    TASK = "task_structure"
    GIT = "git"
    LOCAL = "local_server"
    DEPLOY = "deploy"
    HEALTH = "health"
    STRUCTURE = "structure"
    UI_TEST = "ui_test"
    EXPLORE = "exploration"
    CURSOR = "cursor"
    DONE = "done"
    ERROR = "error"


EventHandler = Callable[[dict[str, Any]], None]

_handlers: list[EventHandler] = []
_emit_json: bool = False
_run_log_path: Path | None = None
_run_state: dict[str, Any] = {
    "running": False,
    "phase": Phase.IDLE.value,
    "phases": {},
    "log": [],
    "steps": [],
    "browser_state": None,
    "last_result": None,
}


def configure(*, emit_json: bool = False) -> None:
    global _emit_json
    _emit_json = emit_json


def configure_run_log(path: Path) -> None:
    """Persist human-readable run lines for agents (`.agent/current/RUN-LOG.txt`)."""
    global _run_log_path
    _run_log_path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# UI test run log — started {datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")


def format_event_line(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if event_type == "step":
        mark = "✓" if event.get("ok") else "✗"
        msg = f" {event['message']}" if event.get("message") else ""
        return f"[{event.get('mode', 'strict')}] {event.get('action')} {event.get('target')} {mark}{msg}".strip()
    if event_type == "phase":
        return f"[phase:{event.get('phase')}] {event.get('status')} {event.get('message') or ''}".strip()
    if event_type == "log":
        return str(event.get("message") or "")
    if event_type == "done":
        return f"[done] overall_ok={event.get('overall_ok')}"
    if event_type == "run_state":
        return f"[run_state] running={event.get('running')}"
    if event_type == "browser_state":
        count = len(event.get("interactables") or [])
        ctx = f" ({event.get('context')})" if event.get("context") else ""
        return f"[browser] {event.get('url')} — {count} interactables{ctx}"
    if event_type == "test_target":
        return f"[target] {event.get('source')}: {event.get('url')}"
    if event_type == "site_map":
        pages = event.get("pages") or {}
        return f"[site_map] {len(pages)} page(s), +{event.get('new_elements', 0)} element(s)"
    if event_type == "agent_decision":
        return f"[agent] {event.get('action')}: {event.get('reason') or ''}".strip()
    if event_type == "cursor":
        return f"[cursor] {event.get('status', '')} {event.get('message', '')}".strip()
    if event_type == "cursor_text" and event.get("text"):
        return f"[cursor] {event['text']}"
    return json.dumps(event, ensure_ascii=False)


def _append_run_log(event: dict[str, Any]) -> None:
    if not _run_log_path:
        return
    line = format_event_line(event)
    if not line:
        return
    with _run_log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def subscribe(handler: EventHandler) -> None:
    _handlers.append(handler)


def reset_run_state() -> None:
    _run_state.update(
        {
            "running": False,
            "phase": Phase.IDLE.value,
            "phases": {},
            "log": [],
            "steps": [],
            "browser_state": None,
            "last_result": None,
            "test_target": None,
            "structured_task": None,
            "run_report": None,
            "site_map": None,
            "last_agent_decision": None,
        }
    )


def get_run_state() -> dict[str, Any]:
    return dict(_run_state)


def _dispatch(event: dict[str, Any]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"ts": ts, **event}
    _append_run_log(payload)
    for handler in _handlers:
        handler(payload)
    if _emit_json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def set_running(running: bool) -> None:
    _run_state["running"] = running
    _dispatch({"type": "run_state", "running": running})


def phase_start(phase: Phase, message: str = "") -> None:
    _run_state["phase"] = phase.value
    phases = dict(_run_state.get("phases") or {})
    for key, entry in list(phases.items()):
        if key != phase.value and entry.get("status") == "running":
            prev_msg = str(entry.get("message") or "").strip()
            phases[key] = {"status": "done", "message": prev_msg}
            _dispatch(
                {
                    "type": "phase",
                    "phase": key,
                    "status": "done",
                    "message": prev_msg,
                }
            )
    phases[phase.value] = {"status": "running", "message": message}
    _run_state["phases"] = phases
    _dispatch({"type": "phase", "phase": phase.value, "status": "running", "message": message})
    log(message or f"Phase {phase.value} started", phase=phase.value)


def phase_done(phase: Phase, *, ok: bool, message: str = "", status: str | None = None) -> None:
    final_status = status or ("done" if ok else "failed")
    phases = dict(_run_state.get("phases") or {})
    phases[phase.value] = {"status": final_status, "message": message}
    _run_state["phases"] = phases
    _dispatch(
        {
            "type": "phase",
            "phase": phase.value,
            "status": final_status,
            "message": message,
        }
    )


def test_target_event(*, url: str, source: str, local_url: str = "") -> None:
    _run_state["test_target"] = {"url": url, "source": source, "local_url": local_url}
    _dispatch({"type": "test_target", "url": url, "source": source, "local_url": local_url})


def structured_task_event(task: dict[str, Any]) -> None:
    _run_state["structured_task"] = task
    _dispatch({"type": "structured_task", **task})


def run_report_event(report: dict[str, Any]) -> None:
    _run_state["run_report"] = report
    _dispatch({"type": "run_report", "report": report})


def nav_tree_event(
    *,
    routes: dict[str, Any],
    global_nav: list[dict[str, Any]] | None = None,
    changed: bool = False,
    new_elements: int = 0,
) -> None:
    payload = {
        "routes": routes,
        "global_nav": global_nav or [],
        "changed": changed,
        "new_elements": new_elements,
    }
    _run_state["nav_tree"] = payload
    _dispatch({"type": "nav_tree", **payload})


def site_map_event(
    *,
    pages: dict[str, Any],
    changed: bool = False,
    new_elements: int = 0,
) -> None:
    payload = {"pages": pages, "changed": changed, "new_elements": new_elements}
    _run_state["site_map"] = payload
    _dispatch({"type": "site_map", **payload})


def agent_decision_event(decision: dict[str, Any]) -> None:
    _run_state["last_agent_decision"] = decision
    _dispatch({"type": "agent_decision", **decision})


def cheatsheet_refined_event(*, added_learnings: list[dict[str, Any]], added_notes: list[str]) -> None:
    payload = {"added_learnings": added_learnings, "added_notes": added_notes}
    _run_state["cheatsheet_refined"] = payload
    _dispatch({"type": "cheatsheet_refined", **payload})


def log(message: str, *, phase: str | None = None, level: str = "info") -> None:
    entry = {"message": message, "phase": phase or _run_state.get("phase"), "level": level}
    logs = list(_run_state.get("log") or [])
    logs.append(entry)
    _run_state["log"] = logs[-500:]
    _dispatch({"type": "log", **entry})


def browser_state_event(
    *,
    url: str,
    title: str = "",
    interactables: list[dict[str, Any]] | None = None,
    context: str = "",
    node_url: str = "",
    screenshot_b64: str | None = None,
    error: str | None = None,
) -> None:
    entry = {
        "url": url,
        "title": title,
        "interactables": interactables or [],
        "context": context,
        "node_url": node_url,
        "screenshot_b64": screenshot_b64,
        "error": error,
    }
    _run_state["browser_state"] = entry
    _dispatch({"type": "browser_state", **entry})


def step_event(
    *,
    mode: str,
    ephemeral: bool,
    page_url: str,
    action: str,
    target: str,
    ok: bool,
    message: str = "",
) -> None:
    entry = {
        "mode": mode,
        "ephemeral": ephemeral,
        "page_url": page_url,
        "action": action,
        "target": target,
        "ok": ok,
        "message": message,
    }
    steps = list(_run_state.get("steps") or [])
    steps.append(entry)
    _run_state["steps"] = steps[-300:]
    _dispatch({"type": "step", **entry})


def finish(*, overall_ok: bool, report_path: str, error: str = "") -> None:
    result = {"overall_ok": overall_ok, "report": report_path, "error": error}
    _run_state["last_result"] = result
    _run_state["running"] = False
    _run_state["phase"] = Phase.DONE.value if overall_ok else Phase.ERROR.value
    _dispatch({"type": "done", "overall_ok": overall_ok, "report": report_path, "error": error})


def cursor_event(**fields: Any) -> None:
    _dispatch({"type": "cursor", **fields})
