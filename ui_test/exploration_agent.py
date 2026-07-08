from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DECIDE_PROMPT = """You are a UI exploration agent. Auth is already done (not on /login).

You explore unknown apps by building one persistent store in `.agent/exploration.yaml`:
- NAVIGATION: how to move (tree, links, verified routes).
- PAGES: what lives on each URL (sections, table columns — not live values).

Each step you receive an exploration_status telling you the situation. Follow it.

Return ONLY valid JSON:
{
  "action": "click|navigate|wait|report",
  "target": {"index": 0, "text": "...", "href": "..."},
  "url": "only for navigate — MUST be a path from navigation tree links, never invented",
  "reason": "why",
  "done": false
}

Rules by exploration_status:
- report_ready: use action=report immediately — current page has the task data.
- go_to_known: task data is cataloged on another path — use navigation links or verified routes to get there. NEVER invent URLs unless that exact href appears in navigation or interactables.
- explore_next: task location unknown — pick ONE link from current interactables that might lead toward task keywords (unvisited routes preferred). Do not repeat steps from history.
- stuck: try a different link than recent history, or report if current visible text already answers the task.

General:
- Navigate ONLY to paths seen in navigation routes or interactables href fields.
- click target MUST match an interactable on the current page.
- Do NOT use structured task scope URLs — they are not reliable.
- Max one action per response."""


EVALUATE_PROMPT = """Evaluate whether a UI exploration run satisfied the user's task.

Return ONLY valid JSON:
{
  "passed": true,
  "summary": "one line",
  "criteria_results": [{"criterion": "...", "met": true, "note": "..."}],
  "report_markdown": "optional"
}

Rules:
- Login criterion is met if final URL is not /login.
- Task is met if report_markdown includes a plain-English Answer section that responds to the user prompt using visible data.
- passed=true when success criteria are reasonably met."""

ANSWER_PROMPT = """You answer the user's question using ONLY the page data provided.
Write 1-3 sentences in plain English. Do not invent numbers, names, or facts.
If the data is insufficient to answer, say what is missing."""


def decide_next_action(
    *,
    url: str,
    model: str,
    ollama_url: str,
    timeout_sec: float,
    task_text: str,
    exploration_status: str,
    status_detail: str,
    site_map_summary: str,
    nav_summary: str = "",
    interactables: list[dict[str, Any]],
    step_history: list[str],
    page_text_snippet: str = "",
    page_content_summary: str = "",
    auth_complete: bool = True,
    loop_warning: str = "",
    unexplored_paths: list[str] | None = None,
) -> dict[str, Any] | None:
    user = (
        f"User task (original prompt — this is the source of truth):\n{task_text}\n\n"
        f"Exploration status: {exploration_status}\n"
        f"Status detail: {status_detail or '(none)'}\n\n"
        f"Auth completed: {auth_complete}\n"
        f"Current URL: {url}\n\n"
        f"Site map (what lives where):\n{site_map_summary}\n\n"
        f"Navigation tree (how to move):\n{nav_summary or '(empty — build by clicking links)'}\n\n"
    )
    if unexplored_paths:
        user += f"Unexplored routes from here (candidates): {json.dumps(unexplored_paths[:12])}\n\n"
    user += f"Current interactables (JSON):\n{json.dumps(interactables[:50], ensure_ascii=False)}\n\n"
    if page_content_summary:
        user += f"Current page catalog:\n{page_content_summary[:1500]}\n\n"
    if page_text_snippet:
        user += f"Visible page text (live values):\n{page_text_snippet[:2500]}\n\n"
    if loop_warning:
        user += f"LOOP WARNING: {loop_warning}\n\n"
    user += "Steps so far:\n" + ("\n".join(step_history[-12:]) if step_history else "(none)")
    return _ollama_json(ollama_url, model, timeout_sec, DECIDE_PROMPT, user)


def evaluate_exploration(
    *,
    url: str,
    model: str,
    ollama_url: str,
    timeout_sec: float,
    task_text: str,
    structured_task: dict[str, Any] | None,
    step_history: list[str],
    site_map_summary: str,
    nav_summary: str = "",
    page_text_snippet: str = "",
    report_markdown: str = "",
) -> dict[str, Any] | None:
    user = (
        f"User task:\n{task_text}\n\n"
        f"Final URL: {url}\n\n"
        f"Site map:\n{site_map_summary}\n\n"
        f"Navigation tree:\n{nav_summary or '(empty)'}\n\n"
        f"Steps executed:\n" + "\n".join(step_history)
    )
    if page_text_snippet:
        user += f"\n\nVisible page text:\n{page_text_snippet[:4000]}"
    if report_markdown.strip():
        user += f"\n\nReport already written:\n{report_markdown[:6000]}"
    return _ollama_json(ollama_url, model, timeout_sec, EVALUATE_PROMPT, user)


def synthesize_task_answer(
    *,
    task_text: str,
    content: dict[str, Any],
    ollama_url: str,
    model: str,
    timeout_sec: float,
) -> str:
    payload = {
        "heading": content.get("heading"),
        "path": content.get("path"),
        "metrics": content.get("metrics"),
        "tables": content.get("tables"),
    }
    user = (
        f"Task:\n{task_text.strip()}\n\n"
        f"Visible page data (JSON):\n{json.dumps(payload, ensure_ascii=False)[:8000]}"
    )
    return _ollama_text(ollama_url, model, timeout_sec, ANSWER_PROMPT, user)


def _ollama_text(
    ollama_url: str,
    model: str,
    timeout_sec: float,
    system: str,
    user: str,
) -> str:
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(
                f"{ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            content = (response.json().get("message") or {}).get("content") or ""
        return content.strip()
    except Exception as exc:
        logger.warning("Task answer synthesis failed: %s", exc)
        return ""


def _ollama_json(
    ollama_url: str,
    model: str,
    timeout_sec: float,
    system: str,
    user: str,
) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(
                f"{ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            content = (response.json().get("message") or {}).get("content") or ""
        if not content.strip():
            return None
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        logger.warning("Exploration agent call failed: %s", exc)
        return None
