from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + "\n\n... output truncated ..."


def archive_current(output_dir: Path, history_dir: Path) -> None:
    if not output_dir.exists() or not any(output_dir.iterdir()):
        return
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = history_dir / stamp
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(output_dir, target)


def _bullet_lines(items: list[Any]) -> str:
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            test = str(item.get("test") or "unknown")
            message = str(item.get("message") or "").strip()
            cause = str(item.get("likely_cause") or "").strip()
            block = f"- **{test}**"
            if message:
                block += f": {message}"
            if cause:
                block += f"\n  - Likely cause: {cause}"
            lines.append(block)
        else:
            text = str(item).strip()
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines) if lines else "- (none)"


def render_report_markdown(
    *,
    project: Path,
    test_result: dict[str, Any],
    git_context: dict[str, Any],
    analysis: dict[str, Any] | None,
    generated_at: datetime,
) -> str:
    status = str((analysis or {}).get("test_status") or test_result.get("status") or "unknown")
    summary = str((analysis or {}).get("summary") or "").strip()
    if not summary:
        if test_result.get("passed"):
            summary = "All tests passed. Review git changes before committing."
        elif test_result.get("skipped"):
            summary = "No test command was run. Inspect git changes or configure a test command."
        else:
            summary = "Tests failed. See raw output below."

    lines = [
        "# Dev Loop — Implementation Report",
        "",
        "> **For Cursor:** Read this file and implement the recommended changes.",
        "> When done, tell the user to re-run `run-dev-loop.ps1` (or `python -m dev_loop`) to verify.",
        "",
        "## Quick prompt",
        "",
        "```",
        "Read .agent/current/REPORT.md and implement the fixes described there.",
        "Keep changes minimal and match the acceptance criteria.",
        "```",
        "",
        "## Run info",
        "",
        f"- **Project:** `{project}`",
        f"- **Generated:** {generated_at.isoformat()}",
        f"- **Test command:** `{test_result.get('command') or '(none)'}`",
        f"- **Test exit code:** {test_result.get('exit_code')}",
        f"- **Status:** {status}",
    ]

    if git_context.get("is_repo"):
        lines.extend(
            [
                f"- **Branch:** `{git_context.get('branch') or '(detached)'}`",
            ]
        )

    lines.extend(["", "## Summary", "", summary, ""])

    if analysis:
        lines.extend(["## Failures", "", _bullet_lines(analysis.get("failures") or []), ""])
        lines.extend(
            [
                "## Recommended implementation",
                "",
                _bullet_lines(analysis.get("implementation_steps") or []),
                "",
                "## Files to inspect",
                "",
                _bullet_lines(analysis.get("files_to_inspect") or []),
                "",
                "## Acceptance criteria",
                "",
                _bullet_lines(analysis.get("acceptance_criteria") or []),
                "",
                "## Constraints",
                "",
                _bullet_lines(analysis.get("constraints") or []),
                "",
            ]
        )
        risk = str(analysis.get("risk_notes") or "").strip()
        if risk:
            lines.extend(["## Risk notes", "", risk, ""])

    if git_context.get("is_repo"):
        status_short = str(git_context.get("status") or "").strip()
        if status_short:
            lines.extend(["## Git status", "", "```", status_short, "```", ""])
        diff_stat = str(git_context.get("diff_stat") or "").strip()
        if diff_stat:
            lines.extend(["## Diff stat", "", "```", diff_stat, "```", ""])

    raw_output = str(test_result.get("output") or "").strip()
    if raw_output:
        lines.extend(["## Raw test output", "", "```", raw_output, "```", ""])

    return "\n".join(lines).rstrip() + "\n"


def write_report_bundle(
    *,
    output_dir: Path,
    history_dir: Path,
    project: Path,
    test_result: dict[str, Any],
    git_context: dict[str, Any],
    analysis: dict[str, Any] | None,
    max_test_output_chars: int,
) -> Path:
    archive_current(output_dir, history_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc)
    truncated_output = _truncate(str(test_result.get("output") or ""), max_test_output_chars)
    test_payload = {**test_result, "output": truncated_output}

    task = {
        "generated_at": generated_at.isoformat(),
        "project": str(project),
        "test_result": test_payload,
        "git_context": git_context,
        "analysis": analysis,
        "status": "ready_for_cursor",
    }

    report_path = output_dir / "REPORT.md"
    report_path.write_text(
        render_report_markdown(
            project=project,
            test_result=test_payload,
            git_context=git_context,
            analysis=analysis,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    (output_dir / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "test-output.txt").write_text(truncated_output, encoding="utf-8")
    (output_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "ready_for_cursor",
                "report": str(report_path),
                "generated_at": generated_at.isoformat(),
                "tests_passed": bool(test_result.get("passed")),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path
