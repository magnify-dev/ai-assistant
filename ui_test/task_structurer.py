from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


STRUCTURE_PROMPT = """You structure free-text UI test tasks into YAML-friendly JSON for a browser test runner.

Return ONLY valid JSON with this shape:
{
  "summary": "one line",
  "scope_urls": ["/login", "/"],
  "success_criteria": ["..."],
  "suggested_steps": [
    {"action": "navigate|click|fill|wait", "description": "...", "mode": "strict|fuzzy", "ephemeral": false}
  ],
  "notes_for_cursor": ["optional hooks or code changes needed"]
}

Prefer strict mode. Use fuzzy only for dismissible one-off UI (tooltips, cookie banners).
Do not invent URLs not implied by the task or app context."""


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
        f"Existing spec summary:\n{spec_summary}\n\n"
        f"Developer task (free text):\n{free_text}\n"
    )
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": STRUCTURE_PROMPT},
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
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        logger.warning("Ollama task structuring failed: %s", exc)
        return None
