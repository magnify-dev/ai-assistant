from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _parse_step_line(line: str) -> dict[str, Any]:
    ok = " ✓" in line or line.rstrip().endswith("✓")
    failed = " ✗" in line or line.rstrip().endswith("✗")
    if failed:
        ok = False
    m = re.match(r"\[(strict|fuzzy)(?:, ephemeral)?\]\s*(.*?)\s→\s*(\w+)\s+(.*)", line)
    if m:
        return {
            "mode": m.group(1),
            "page_url": m.group(2).strip(),
            "action": m.group(3).strip(),
            "target": m.group(4).strip().rstrip("✓").rstrip("✗").strip(),
            "ok": ok,
            "line": line,
        }
    return {"ok": ok, "line": line, "action": "", "target": "", "page_url": ""}


def _report_has_content(page_report: str, page_findings: dict[str, Any] | None) -> bool:
    if page_findings:
        if page_findings.get("empty_message"):
            return True
        for key in ("tables", "metrics", "lists", "sections"):
            val = page_findings.get(key)
            if isinstance(val, list) and val:
                return True
    if not page_report.strip():
        return False
    lower = page_report.lower()
    markers = ("## metrics", "## table", "## lists", "## sections", "## empty state", "## visible page text")
    return any(m in lower for m in markers)


def _evaluate_criteria(
    criteria: list[str],
    *,
    overall_ok: bool,
    ui_passed: bool,
    ui_error: str,
    final_url: str,
    executed: list[dict[str, Any]],
    exploration_evaluation: dict[str, Any] | None = None,
    page_report: str = "",
    page_findings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if exploration_evaluation and exploration_evaluation.get("criteria_results"):
        results = list(exploration_evaluation["criteria_results"])
        for row in results:
            criterion = str(row.get("criterion") or "")
            lower = criterion.lower()
            if "report" in lower and ("write" in lower or "produce" in lower or "detail" in lower or "present" in lower):
                has_content = _report_has_content(page_report, page_findings)
                row["met"] = has_content and ui_passed
                row["note"] = (
                    "Report captured visible page content"
                    if has_content
                    else "Report missing grounded page content"
                )
            elif "account" in lower and ("report" in lower or "detail" in lower or "present" in lower):
                has_content = _report_has_content(page_report, page_findings)
                tables = (page_findings or {}).get("tables") or []
                row_count = sum(len(t.get("rows") or []) for t in tables if isinstance(t, dict))
                row["met"] = has_content and ui_passed
                row["note"] = (
                    f"Report includes page data ({row_count} table row(s))"
                    if has_content and row_count
                    else (
                        "Report documents empty or text-only page state"
                        if has_content
                        else "Report missing page content"
                    )
                )
        return results

    results: list[dict[str, Any]] = []
    login_ok = any(e.get("ok") and e.get("action") == "click" and "Sign in" in str(e.get("target", "")) for e in executed)
    for criterion in criteria:
        text = str(criterion).strip()
        lower = text.lower()
        met: bool | None = None
        note = ""
        if "log in" in lower or "logged in" in lower or "login" in lower:
            met = login_ok and ui_passed
            note = "Login steps in UI test" if met else (ui_error or "Login step failed")
        elif "redirect" in lower or "home" in lower or "page" in lower or "display" in lower:
            met = ui_passed and bool(final_url) and "/login" not in final_url
            note = f"Final URL: {final_url or '(none)'}"
        elif "group" in lower:
            met = ui_passed and "group" in final_url.lower()
            note = f"Final URL: {final_url or '(none)'}"
        elif "report" in lower and ("write" in lower or "produce" in lower):
            has_content = _report_has_content(page_report, page_findings)
            met = has_content and ui_passed
            note = "Report captured visible page content" if has_content else "Report missing grounded content"
        elif "account" in lower and ("report" in lower or "detail" in lower or "present" in lower):
            has_content = _report_has_content(page_report, page_findings)
            tables = (page_findings or {}).get("tables") or []
            row_count = sum(len(t.get("rows") or []) for t in tables if isinstance(t, dict))
            met = has_content and ui_passed
            note = (
                f"Report includes page data ({row_count} table row(s))"
                if has_content and row_count
                else ("Report documents page state" if has_content else "Report missing page content")
            )
        else:
            met = overall_ok if ui_passed else False
            note = "Inferred from overall run result"
        results.append({"criterion": text, "met": met, "note": note})
    return results


def _extract_answer(page_report: str) -> str:
    from ui_test.page_content import extract_answer_from_report

    return extract_answer_from_report(page_report)


def _resolve_task_answer(
    ui_run: dict[str, Any],
    page_report: str,
    page_findings: dict[str, Any] | None,
    task: dict[str, Any],
) -> str:
    answer = str(ui_run.get("task_answer") or "").strip()
    if answer:
        return answer
    answer = _extract_answer(page_report)
    if answer:
        return answer
    prompt = str(task.get("source_text") or task.get("summary") or "").strip()
    if page_findings and prompt:
        from ui_test.page_content import derive_task_answer

        return derive_task_answer(page_findings, prompt).strip()
    return ""


def _resolve_page_report(ui_run: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    page_findings = ui_run.get("page_findings") if isinstance(ui_run.get("page_findings"), dict) else None
    page_report = str(ui_run.get("report_markdown") or "").strip()
    if not page_report:
        evaluation = ui_run.get("evaluation")
        if isinstance(evaluation, dict):
            page_report = str(evaluation.get("report_markdown") or "").strip()
    if not page_report:
        report_path = str(ui_run.get("report_path") or "").strip()
        if report_path:
            path = Path(report_path)
            if path.is_file():
                page_report = path.read_text(encoding="utf-8").strip()
    return page_report, page_findings


def build_run_report(payload: dict[str, Any]) -> dict[str, Any]:
    task = payload.get("structured_task") or {}
    step_log = payload.get("step_log") or []
    step_entries = payload.get("step_entries") or []
    ui_run = payload.get("ui_run") or {}
    exploration_eval = ui_run.get("evaluation") if isinstance(ui_run.get("evaluation"), dict) else None
    step_summary = payload.get("step_summary") or {}
    overall_ok = bool(payload.get("overall_ok"))
    test_target = payload.get("test_target") or {}
    local_server = payload.get("local_server") or {}
    page_report, page_findings = _resolve_page_report(ui_run)
    task_answer = _resolve_task_answer(ui_run, page_report, page_findings, task)

    if step_entries:
        executed = [
            {
                "mode": e.get("mode"),
                "page_url": e.get("page_url"),
                "action": e.get("action"),
                "target": e.get("target"),
                "ok": e.get("ok"),
                "message": e.get("message", ""),
            }
            for e in step_entries
        ]
    else:
        executed = [_parse_step_line(line) for line in step_log]

    criteria = task.get("success_criteria") or []
    criteria_results = _evaluate_criteria(
        criteria,
        overall_ok=overall_ok,
        ui_passed=bool(ui_run.get("passed")),
        ui_error=str(ui_run.get("error") or ""),
        final_url=str(ui_run.get("final_url") or ""),
        executed=executed,
        exploration_evaluation=exploration_eval,
        page_report=page_report,
        page_findings=page_findings,
    )

    phases: list[dict[str, Any]] = []
    if local_server and not local_server.get("skipped"):
        phases.append(
            {
                "name": "Local dev",
                "ok": bool(local_server.get("ok")),
                "detail": local_server.get("message", ""),
            }
        )
    phase_name = "Exploration" if ui_run.get("mode") == "exploration" else "UI test"
    phases.append(
        {
            "name": phase_name,
            "ok": bool(ui_run.get("passed")),
            "detail": ui_run.get("error") or ("PASS" if ui_run.get("passed") else "FAIL"),
        }
    )

    page_content_summary: dict[str, Any] = {}
    if page_findings and isinstance(page_findings, dict):
        for key in ("heading", "tables", "metrics", "lists", "sections", "empty_message", "summary"):
            if key in page_findings:
                page_content_summary[key] = page_findings[key]

    playwright_session = ui_run.get("playwright_session") if isinstance(ui_run.get("playwright_session"), dict) else None

    return {
        "overall_ok": overall_ok,
        "requested": {
            "summary": task.get("summary") or "",
            "source_text": task.get("source_text") or "",
            "success_criteria": criteria,
            "scope_urls": task.get("scope_urls") or [],
            "deliverables": task.get("deliverables") or [],
            "intent_gaps": task.get("intent_gaps") or [],
        },
        "executed": executed,
        "step_summary": step_summary,
        "criteria_results": criteria_results,
        "phases": phases,
        "test_target": test_target,
        "final_url": ui_run.get("final_url") or "",
        "ui_error": ui_run.get("error") or "",
        "mode": ui_run.get("mode") or "spec",
        "exploration_report_path": ui_run.get("report_path") or "",
        "page_report": page_report,
        "task_answer": task_answer,
        "page_findings": page_content_summary,
        "site_map_pages": ui_run.get("pages_discovered") or 0,
        "site_map_changes": payload.get("site_map_changes") or ui_run.get("site_map_changes") or {},
        "nav_tree_changes": payload.get("nav_tree_changes") or ui_run.get("nav_tree_changes") or {},
        "cheatsheet_changes": payload.get("cheatsheet_refine") or {},
        "playwright_session": playwright_session,
    }
