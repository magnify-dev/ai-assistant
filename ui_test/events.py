from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class Phase(str, Enum):
    IDLE = "idle"
    TASK = "task_structure"
    GIT = "git"
    DEPLOY = "deploy"
    HEALTH = "health"
    STRUCTURE = "structure"
    UI_TEST = "ui_test"
    CURSOR = "cursor"
    DONE = "done"
    ERROR = "error"


EventHandler = Callable[[dict[str, Any]], None]

_handlers: list[EventHandler] = []
_emit_json: bool = False
_run_state: dict[str, Any] = {
    "running": False,
    "phase": Phase.IDLE.value,
    "phases": {},
    "log": [],
    "steps": [],
    "last_result": None,
}


def configure(*, emit_json: bool = False) -> None:
    global _emit_json
    _emit_json = emit_json


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
            "last_result": None,
        }
    )


def get_run_state() -> dict[str, Any]:
    return dict(_run_state)


def _dispatch(event: dict[str, Any]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"ts": ts, **event}
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
    phases[phase.value] = {"status": "running", "message": message}
    _run_state["phases"] = phases
    _dispatch({"type": "phase", "phase": phase.value, "status": "running", "message": message})
    log(message or f"Phase {phase.value} started", phase=phase.value)


def phase_done(phase: Phase, *, ok: bool, message: str = "") -> None:
    phases = dict(_run_state.get("phases") or {})
    phases[phase.value] = {"status": "done" if ok else "failed", "message": message}
    _run_state["phases"] = phases
    _dispatch(
        {
            "type": "phase",
            "phase": phase.value,
            "status": "done" if ok else "failed",
            "message": message,
        }
    )


def log(message: str, *, phase: str | None = None, level: str = "info") -> None:
    entry = {"message": message, "phase": phase or _run_state.get("phase"), "level": level}
    logs = list(_run_state.get("log") or [])
    logs.append(entry)
    _run_state["log"] = logs[-500:]
    _dispatch({"type": "log", **entry})


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
