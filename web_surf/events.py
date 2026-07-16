from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

_emit_json = False
_sink: Callable[[dict[str, Any]], None] | None = None


def configure(
    *,
    emit_json: bool = False,
    sink: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    global _emit_json, _sink
    _emit_json = emit_json
    _sink = sink


def _dispatch(event: dict[str, Any]) -> None:
    payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    if _sink is not None:
        try:
            _sink(payload)
        except Exception:
            pass
    if _emit_json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def snapshot(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_snapshot", **payload})


def decision(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_decision", **payload})


def agent_memory(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_agent_memory", **payload})


def playwright_session(payload: dict[str, Any]) -> None:
    _dispatch({"type": "playwright_session", **payload})


def action(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_step", **payload})


def transition(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_state_transition", **payload})


def form_values_plan(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_form_values_plan", **payload})


def llm_exchange(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_llm_exchange", **payload})


def evidence(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_evidence", **payload})


def extract_preview(payload: dict[str, Any]) -> None:
    """Emit page text and fact-extraction diagnostics for run review."""
    _dispatch({"type": "web_extract_preview", **payload})


def help_request(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_help_request", **payload})


def help_result(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_help_response", **payload})


def controller(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_controller_state", **payload})


def candidates(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_candidates", **payload})


def visit_graph(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_visit_graph", **payload})


def criteria(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_criteria", **payload})


def set_running(running: bool) -> None:
    _dispatch({"type": "run_state", "running": running})


def phase_start(phase: str, message: str = "") -> None:
    _dispatch({"type": "phase", "phase": phase, "status": "running", "message": message})


def phase_done(phase: str, *, ok: bool, message: str = "") -> None:
    _dispatch(
        {
            "type": "phase",
            "phase": phase,
            "status": "done" if ok else "failed",
            "message": message,
        }
    )


def log(message: str, *, level: str = "info") -> None:
    _dispatch({"type": "log", "message": message, "level": level, "phase": "web_research"})


def web_progress(
    *,
    step: str,
    url: str = "",
    index: int = 0,
    total: int = 0,
    message: str = "",
) -> None:
    _dispatch(
        {
            "type": "web_research_progress",
            "step": step,
            "url": url,
            "index": index,
            "total": total,
            "message": message,
        }
    )


def web_index_event(*, pages: dict[str, Any]) -> None:
    _dispatch({"type": "web_index", "pages": pages})


def web_facts_event(*, facts: list[dict[str, Any]]) -> None:
    _dispatch({"type": "web_facts", "facts": facts})


def web_result_event(payload: dict[str, Any]) -> None:
    _dispatch({"type": "web_research_result", **payload})


def finish(*, overall_ok: bool, error: str = "") -> None:
    _dispatch({"type": "done", "overall_ok": overall_ok, "error": error})
