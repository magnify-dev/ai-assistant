from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ui_test.prompts import get_prompt

logger = logging.getLogger(__name__)


def validate_task_alignment(_source_text: str, structured: dict[str, Any]) -> list[str]:
    """Surface when Ollama drifted from the user's prompt."""
    gaps: list[str] = list(structured.get("intent_gaps") or [])

    if structured.get("preserves_intent") is False:
        gap = "Structured task flagged as not fully preserving your prompt — review success criteria."
        if gap not in gaps:
            gaps.append(gap)

    return gaps


def sanitize_structured_task(source_text: str, structured: dict[str, Any]) -> dict[str, Any]:
    """Remove invented navigation — exploration discovers routes at runtime."""
    out = dict(structured)
    out["scope_urls"] = []
    out["suggested_steps"] = []
    source_l = source_text.lower()
    # Drop criteria that invent navigation the user never asked for
    cleaned: list[str] = []
    for c in out.get("success_criteria") or []:
        cl = str(c).lower()
        if "navigate" in cl and "navigate" not in source_l:
            continue
        if re.search(r"/[a-z]", cl) and not re.search(r"/[a-z]", source_text):
            continue
        cleaned.append(str(c))
    if cleaned:
        out["success_criteria"] = cleaned
    return out


def structure_task_with_ollama(
    *,
    url: str,
    model: str,
    timeout_sec: float,
    free_text: str,
    app_context: str,
    spec_summary: str,
) -> dict[str, Any] | None:
    user_content = (
        f"App context:\n{app_context}\n\n"
        f"Existing spec summary (what Playwright actually runs today — may NOT match the user task):\n{spec_summary}\n\n"
        f"Developer task (free text — preserve ALL of this intent):\n{free_text}\n"
    )
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": get_prompt("task_structure.system")},
            {"role": "user", "content": user_content},
        ],
    }
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(f"{url.rstrip('/')}/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        content = (body.get("message") or {}).get("content") or ""
        if not content.strip():
            return None
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        parsed["source_text"] = free_text
        parsed = sanitize_structured_task(free_text, parsed)
        parsed["intent_gaps"] = validate_task_alignment(free_text, parsed)
        return parsed
    except Exception as exc:
        logger.warning("Ollama task structuring failed: %s", exc)
        return None


def fallback_structured_task(free_text: str) -> dict[str, Any]:
    """When Ollama is off, keep the user's words as the task."""
    task = {
        "summary": free_text.strip()[:200],
        "source_text": free_text.strip(),
        "deliverables": [],
        "scope_urls": [],
        "success_criteria": [free_text.strip()] if free_text.strip() else [],
        "suggested_steps": [],
        "notes_for_cursor": [],
        "preserves_intent": True,
        "intent_gaps": [],
    }
    return task
