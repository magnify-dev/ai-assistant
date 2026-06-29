from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

ANALYZER_SYSTEM = """\
You analyze software test results for a local coding assistant.
Your output is consumed by Cursor (a code editor agent) that will implement fixes.

Rules:
- Base every failure and recommendation on the provided test output and git diff only.
- Do not invent test names, stack traces, or files that are not supported by the input.
- Prefer concrete file paths and actionable steps over vague advice.
- If tests passed, summarize health and note any risky uncommitted changes from git status.
- Output valid JSON only, matching the requested schema exactly.
"""

ANALYSIS_SCHEMA = {
    "summary": "One paragraph overview for the coding agent",
    "test_status": "passed | failed | error | skipped | no_tests",
    "failures": [
        {
            "test": "test id or name",
            "message": "error message or assertion",
            "likely_cause": "short diagnosis",
        }
    ],
    "files_to_inspect": ["relative/path/from/project/root"],
    "implementation_steps": ["ordered step the coding agent should take"],
    "acceptance_criteria": ["how to know the fix worked"],
    "constraints": ["scope limits, e.g. do not change unrelated files"],
    "risk_notes": "optional notes about side effects or uncertainty",
}


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def analyze_test_results(
    *,
    url: str,
    model: str,
    timeout_sec: float,
    project_path: str,
    test_result: dict[str, Any],
    git_context: dict[str, Any],
    user_note: str = "",
) -> dict[str, Any] | None:
    user_payload = {
        "project_path": project_path,
        "test_result": test_result,
        "git_context": git_context,
        "user_note": user_note.strip(),
        "required_json_schema": ANALYSIS_SCHEMA,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ANALYZER_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Analyze this test run and produce implementation guidance for Cursor.\n\n"
                    f"{json.dumps(user_payload, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        logger.error("Ollama request failed: %s", exc)
        return None
    except TimeoutError:
        logger.error("Ollama request timed out after %ss", timeout_sec)
        return None

    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    return _parse_json_object(str(message.get("content") or ""))
