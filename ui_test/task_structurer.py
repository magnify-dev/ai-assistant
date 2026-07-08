from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


STRUCTURE_PROMPT = """You structure free-text UI test tasks into JSON for a browser test runner.

CRITICAL: Preserve the user's full intent. Do NOT replace their goal with a generic "login and land on home page" task.
If they ask to write a report, discover data via UI, or verify specific pages — that MUST appear in success_criteria and deliverables.

Return ONLY valid JSON:
{
  "summary": "one line — must reflect the user's actual goal, not a generic login test",
  "deliverables": ["concrete outputs the user asked for"],
  "success_criteria": ["each must map to something the user explicitly or implicitly asked for"],
  "notes_for_cursor": ["gaps between what user asked and what the existing spec likely covers"],
  "preserves_intent": true,
  "intent_gaps": ["only if summary/criteria miss something from the user's prompt — else []"]
}

Rules:
- Copy key nouns from the user prompt into summary and success_criteria.
- If user asks for a report, add deliverable and criterion about producing a report.
- Do NOT include scope_urls, suggested_steps, or navigation instructions — exploration discovers routes at runtime via site map and navigation tree.
- Do NOT invent URLs or paths (e.g. /analytics) unless the user literally wrote that path.
- Do NOT add steps like "navigate to X section" — the agent discovers where data lives."""


def _keyword_in(text: str, keyword: str) -> bool:
    return keyword in text.lower()


def validate_task_alignment(source_text: str, structured: dict[str, Any]) -> list[str]:
    """Heuristic checks — surfaces when Ollama drifted from the user's prompt."""
    gaps: list[str] = list(structured.get("intent_gaps") or [])
    source = source_text.strip().lower()
    if not source:
        return gaps

    criteria = " ".join(str(c) for c in (structured.get("success_criteria") or [])).lower()
    summary = str(structured.get("summary") or "").lower()
    deliverables = " ".join(str(d) for d in (structured.get("deliverables") or [])).lower()
    scope = " ".join(str(u) for u in (structured.get("scope_urls") or [])).lower()
    combined = f"{criteria} {summary} {deliverables}"

    checks: list[tuple[str, str]] = [
        ("report", "Your prompt asks to write a report — structured task should include that as a deliverable/criterion."),
        ("discover", "Your prompt asks to discover via the UI — success criteria should mention exploration/discovery."),
        ("group", "Your prompt mentions groups — scope URLs and criteria should reference groups."),
    ]
    for keyword, message in checks:
        if _keyword_in(source, keyword) and not _keyword_in(combined, keyword):
            if message not in gaps:
                gaps.append(message)

    if "write" in source and "report" in source:
        if not _keyword_in(deliverables, "report") and not _keyword_in(criteria, "report"):
            msg = "Your prompt asks to write a report — add it as a deliverable, not just verify UI."
            if msg not in gaps:
                gaps.append(msg)

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
