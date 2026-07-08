from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def archive_current(output_dir: Path, history_dir: Path) -> None:
    if not output_dir.exists() or not any(output_dir.iterdir()):
        return
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = history_dir / stamp
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(output_dir, target)


def _bullets(items: list[Any]) -> str:
    lines: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else "- (none)"


def render_report_markdown(payload: dict[str, Any]) -> str:
    project = payload.get("project", "")
    generated = payload.get("generated_at", "")
    overall_ok = payload.get("overall_ok", False)
    task = payload.get("structured_task") or {}
    git = payload.get("git") or {}
    deploy = payload.get("deploy") or {}
    health = payload.get("health") or []
    structure = payload.get("structure") or []
    ui_run = payload.get("ui_run") or {}
    step_log = payload.get("step_log") or []
    step_summary = payload.get("step_summary") or {}
    cursor_steps = payload.get("cursor_steps") or []

    status = "PASS" if overall_ok else "FAIL"
    test_target = payload.get("test_target") or {}
    local_env = payload.get("local_env") or {}

    all_missing_for_prompt: set[str] = set()
    for item in structure:
        if isinstance(item, dict):
            all_missing_for_prompt.update(item.get("missing") or [])

    quick_lines = [
        "Read .agent/current/REPORT.md and implement the fixes described there.",
    ]
    if all_missing_for_prompt:
        quick_lines.append("Add missing data-testid hooks listed under Structure. Keep changes minimal.")
    elif cursor_steps:
        quick_lines.append("Focus on the items under Recommended implementation.")
    else:
        quick_lines.append("Keep changes minimal.")

    lines = [
        "# UI Test Loop — Implementation Report",
        "",
        "> **For Cursor:** Read this file on the **target project** and implement fixes.",
        "> Re-run the test runner from ai-assistant after changes.",
        "",
        "## Quick prompt",
        "",
        "```",
        *quick_lines,
        "```",
        "",
        "## Run info",
        "",
        f"- **Project:** `{project}`",
        f"- **Generated:** {generated}",
        f"- **Overall status:** {status}",
        f"- **Run log:** `.agent/current/RUN-LOG.txt`",
    ]

    if test_target.get("url"):
        source = test_target.get("source", "unknown")
        source_label = {
            "local": "Local dev",
            "deployed_fallback": "Railway (local failed)",
            "deployed": "Railway",
        }.get(str(source), str(source))
        lines.append(f"- **Test target:** `{test_target['url']}` ({source_label})")

    if local_env and not local_env.get("ready"):
        missing = ", ".join(local_env.get("missing") or [])
        lines.append(
            f"- **Local env:** missing {missing} — copy `{local_env.get('env_example_path', '.env.example')}` "
            f"→ `{local_env.get('env_path', '.env')}`"
        )

    if task.get("summary"):
        lines.extend(["", "## Task", "", str(task["summary"]), ""])
    if task.get("success_criteria"):
        lines.extend(["### Success criteria", "", _bullets(task["success_criteria"]), ""])

    lines.extend(["", "## Git & deploy", ""])
    if git.get("is_repo"):
        lines.append(f"- **Branch:** `{git.get('branch') or '(detached)'}`")
        lines.append(f"- **Uncommitted changes:** {'yes' if git.get('has_uncommitted') else 'no'}")
        lines.append(f"- **Unpushed commits:** {git.get('unpushed_commits', 0)}")
        if git.get("push_message"):
            lines.append(f"- **Push:** {git['push_message']}")
    else:
        lines.append("- Not a git repository")

    deploy_results = deploy.get("results") or []
    if deploy_results:
        lines.append("")
        lines.append("### Railway deployments")
        for item in deploy_results:
            mark = "✓" if item.get("ok") else "✗"
            lines.append(f"- {mark} **{item.get('service')}**: {item.get('status')} — {item.get('message')}")

    if health:
        lines.extend(["", "### Health checks", ""])
        for item in health:
            mark = "✓" if item.get("ok") else "✗"
            lines.append(f"- {mark} **{item.get('service')}** `{item.get('url')}` — {item.get('message')}")

    lines.extend(["", "## Structure (`data-testid`)", ""])
    all_missing: set[str] = set()
    for item in structure:
        missing = item.get("missing") or []
        all_missing.update(missing)
        mark = "✓" if item.get("ok") else "✗"
        lines.append(f"- {mark} `{item.get('url')}` — missing: {', '.join(missing) or 'none'}")
    if all_missing:
        lines.extend(
            [
                "",
                "### Cursor: add these hooks",
                "",
                _bullets(sorted(f"`data-testid=\"{tid}\"` on relevant elements" for tid in all_missing)),
            ]
        )

    lines.extend(["", "## UI test execution", ""])
    lines.append(f"- **Passed:** {'yes' if ui_run.get('passed') else 'no'}")
    if ui_run.get("error"):
        lines.append(f"- **Error:** {ui_run['error']}")
    if ui_run.get("final_url"):
        lines.append(f"- **Final URL:** `{ui_run['final_url']}`")

    run_report = payload.get("run_report") or {}
    task_answer = str(run_report.get("task_answer") or ui_run.get("task_answer") or "").strip()
    page_report = str(ui_run.get("report_markdown") or "").strip()
    if not task_answer and page_report:
        from ui_test.page_content import extract_answer_from_report

        task_answer = extract_answer_from_report(page_report)
    if task_answer:
        plain = task_answer.replace("**", "")
        lines.extend(["", "## Answer", "", plain, ""])

    lines.extend(["", "## Selector mode log", ""])
    lines.append(
        f"- strict: {step_summary.get('strict_steps', 0)}, "
        f"fuzzy: {step_summary.get('fuzzy_steps', 0)}, "
        f"passed: {step_summary.get('passed', 0)}, "
        f"failed: {step_summary.get('failed', 0)}"
    )
    if step_log:
        lines.extend(["", "```", *step_log[:200], "```"])

    if cursor_steps:
        lines.extend(["", "## Recommended implementation", "", _bullets(cursor_steps), ""])

    notes = task.get("notes_for_cursor") or []
    if notes:
        lines.extend(["", "## Notes for Cursor", "", _bullets(notes), ""])

    return "\n".join(lines).rstrip() + "\n"


def write_report_bundle(
    *,
    output_dir: Path,
    history_dir: Path,
    payload: dict[str, Any],
) -> Path:
    archive_current(output_dir, history_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "REPORT.md"
    report_path.write_text(render_report_markdown(payload), encoding="utf-8")
    (output_dir / "task.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "ui-results.json").write_text(
        json.dumps(
            {
                "overall_ok": payload.get("overall_ok"),
                "ui_run": payload.get("ui_run"),
                "structure": payload.get("structure"),
                "step_summary": payload.get("step_summary"),
                "run_report": payload.get("run_report"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if payload.get("run_report"):
        (output_dir / "run-report.json").write_text(
            json.dumps(payload["run_report"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if payload.get("step_log"):
        (output_dir / "ui-step-log.txt").write_text("\n".join(payload["step_log"]) + "\n", encoding="utf-8")
    (output_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "ready_for_cursor",
                "overall_ok": bool(payload.get("overall_ok")),
                "report": str(report_path),
                "generated_at": payload.get("generated_at"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path
