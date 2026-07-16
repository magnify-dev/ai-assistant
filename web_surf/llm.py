from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from web_surf import events

logger = logging.getLogger(__name__)

PROMPT_LABELS: dict[str, str] = {
    "web_research.spec": "Research plan",
    "web_research.classify_sources": "Source classification",
    "web_research.browse_decide": "Browse decision",
    "web_research.plan_form_values": "Form value plan",
    "web_research.extract": "Fact extraction",
    "web_research.answer": "Answer synthesis",
    "web_research.classify": "Task classification",
    "web_research.help": "Browser helper",
}

_exchanges: list[dict[str, Any]] = []
_seq = 0
MAX_SYSTEM_CHARS = 16_000
MAX_USER_CHARS = 24_000
MAX_RESPONSE_CHARS = 12_000


def reset_trace() -> None:
    global _exchanges, _seq
    _exchanges = []
    _seq = 0


def get_trace() -> list[dict[str, Any]]:
    return list(_exchanges)


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}\n\n… ({len(text) - limit} more chars)", True


def _record_exchange(
    *,
    prompt_key: str,
    model: str,
    system: str,
    user: str,
    response: str,
    ok: bool,
    error: str = "",
    session_id: str = "",
    step_id: str = "",
    snapshot_id: str = "",
    url: str = "",
) -> dict[str, Any]:
    global _seq
    _seq += 1
    system_text, system_truncated = _clip(system, MAX_SYSTEM_CHARS)
    user_text, user_truncated = _clip(user, MAX_USER_CHARS)
    response_text, response_truncated = _clip(response, MAX_RESPONSE_CHARS)
    item = {
        "seq": _seq,
        "prompt_key": prompt_key,
        "label": PROMPT_LABELS.get(prompt_key, prompt_key),
        "model": model,
        "session_id": session_id,
        "step_id": step_id,
        "snapshot_id": snapshot_id,
        "url": url,
        "system_prompt": system_text,
        "user_input": user_text,
        "response": response_text,
        "ok": ok,
        "error": error,
        "truncated": system_truncated or user_truncated or response_truncated,
    }
    _exchanges.append(item)
    events.llm_exchange(item)
    return item


def ollama_chat(
    *,
    prompt_key: str,
    ollama_url: str,
    model: str,
    timeout_sec: float,
    system: str,
    user: str,
    format_json: bool = False,
    session_id: str = "",
    step_id: str = "",
    snapshot_id: str = "",
    url: str = "",
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if format_json:
        payload["format"] = "json"
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(f"{ollama_url.rstrip('/')}/api/chat", json=payload)
            response.raise_for_status()
            content = str((response.json().get("message") or {}).get("content") or "")
        _record_exchange(
            prompt_key=prompt_key,
            model=model,
            system=system,
            user=user,
            response=content,
            ok=bool(content.strip()),
            session_id=session_id,
            step_id=step_id,
            snapshot_id=snapshot_id,
            url=url,
        )
        return content
    except Exception as exc:
        logger.warning("Ollama chat failed (%s): %s", prompt_key, exc)
        _record_exchange(
            prompt_key=prompt_key,
            model=model,
            system=system,
            user=user,
            response="",
            ok=False,
            error=str(exc),
            session_id=session_id,
            step_id=step_id,
            snapshot_id=snapshot_id,
            url=url,
        )
        raise


def ollama_chat_json(
    *,
    prompt_key: str,
    ollama_url: str,
    model: str,
    timeout_sec: float,
    system: str,
    user: str,
    session_id: str = "",
    step_id: str = "",
    snapshot_id: str = "",
    url: str = "",
) -> dict[str, Any] | None:
    try:
        content = ollama_chat(
            prompt_key=prompt_key,
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=system,
            user=user,
            format_json=True,
            session_id=session_id,
            step_id=step_id,
            snapshot_id=snapshot_id,
            url=url,
        )
    except Exception:
        return None
    if not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
