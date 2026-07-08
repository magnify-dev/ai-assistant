from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from ui_test.project_paths import agent_dir
from ui_test.project_profile import cheatsheet_path, load_cheatsheet

logger = logging.getLogger(__name__)

LEARNINGS_FILE = "cheatsheet-learnings.yaml"

REFINE_PROMPT = """You help improve a project's local-dev cheatsheet based on one test run.

Return ONLY valid JSON:
{
  "add_learnings": [
    {"insight": "short factual note", "source": "what run evidence showed"}
  ],
  "add_notes": [
    "optional bullet for cheatsheet notes — only if truly new and important"
  ]
}

Rules:
- NEVER rewrite or replace existing setup — append-only suggestions.
- Max 2 learnings and max 1 note per run.
- Skip if nothing new (return empty arrays).
- Do not duplicate insights already in existing learnings or notes.
- Focus on: env vars, ports, proxy, Windows/127.0.0.1, startup order, common failures."""


def learnings_path(project: Path) -> Path:
    return agent_dir(project) / LEARNINGS_FILE


def load_learnings(project: Path) -> list[dict[str, Any]]:
    path = learnings_path(project)
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return [e for e in data["entries"] if isinstance(e, dict)]
    except (yaml.YAMLError, OSError):
        pass
    return []


def save_learnings(project: Path, entries: list[dict[str, Any]]) -> Path:
    path = learnings_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "entries": entries}
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _existing_insights(project: Path) -> set[str]:
    known: set[str] = set()
    sheet = load_cheatsheet(project)
    for note in sheet.get("notes") or []:
        if isinstance(note, str):
            known.add(note.strip().lower())
    for entry in load_learnings(project):
        insight = str(entry.get("insight") or "").strip().lower()
        if insight:
            known.add(insight)
    return known


def _append_notes_to_cheatsheet(project: Path, new_notes: list[str]) -> list[str]:
    """Append new bullets to cheatsheet notes section (no other edits)."""
    path = cheatsheet_path(project)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    sheet = yaml.safe_load(text)
    if not isinstance(sheet, dict):
        return []
    notes = list(sheet.get("notes") or [])
    added: list[str] = []
    for note in new_notes:
        n = str(note).strip()
        if not n or n.lower() in {x.lower() for x in notes if isinstance(x, str)}:
            continue
        notes.append(n)
        added.append(n)
    if not added:
        return []
    sheet["notes"] = notes
    path.write_text(yaml.safe_dump(sheet, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return added


def refine_cheatsheet_from_run(
    project: Path,
    *,
    run_report: dict[str, Any],
    run_payload: dict[str, Any],
    url: str,
    model: str,
    timeout_sec: float = 120,
) -> dict[str, Any]:
    """Conservative append-only cheatsheet improvements from a run."""
    existing = _existing_insights(project)
    sheet = load_cheatsheet(project)
    learnings = load_learnings(project)

    context = {
        "overall_ok": run_report.get("overall_ok"),
        "ui_error": run_report.get("ui_error"),
        "final_url": run_report.get("final_url"),
        "local_server": run_payload.get("local_server"),
        "test_target": run_report.get("test_target"),
        "criteria_results": run_report.get("criteria_results"),
        "existing_notes": sheet.get("notes") or [],
        "existing_learnings": [e.get("insight") for e in learnings[-10:]],
    }
    user = f"Run context JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(
                f"{url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": REFINE_PROMPT},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            content = (response.json().get("message") or {}).get("content") or ""
        if not content.strip():
            return {"added_learnings": [], "added_notes": []}
        parsed = json.loads(content)
    except Exception as exc:
        logger.warning("Cheatsheet refine failed: %s", exc)
        return {"added_learnings": [], "added_notes": [], "error": str(exc)}

    added_learnings: list[dict[str, Any]] = []
    for item in parsed.get("add_learnings") or []:
        if not isinstance(item, dict):
            continue
        insight = str(item.get("insight") or "").strip()
        if not insight or insight.lower() in existing:
            continue
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "insight": insight,
            "source": str(item.get("source") or "ui test run").strip(),
        }
        learnings.append(entry)
        existing.add(insight.lower())
        added_learnings.append(entry)

    if added_learnings:
        save_learnings(project, learnings)

    note_candidates = [str(n).strip() for n in (parsed.get("add_notes") or []) if str(n).strip()]
    added_notes = _append_notes_to_cheatsheet(project, note_candidates)

    return {"added_learnings": added_learnings, "added_notes": added_notes}
