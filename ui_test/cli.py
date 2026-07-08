from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ui_test.config_loader import default_project, engine_root, merged_config
from ui_test.events import configure as configure_events
from ui_test.project_paths import agent_dir
from ui_test.pipeline import run_ui_test_loop
from ui_test.project_setup import ensure_project_setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ui_test",
        description="UI test loop: Railway deploy → structure → Playwright → REPORT.md",
    )
    parser.add_argument("--project", help="Target project root (e.g. content-manager)")
    parser.add_argument("--task", help="Free-text test task")
    parser.add_argument("--task-file", help="Path to free-text task file")
    parser.add_argument("--push", action="store_true", help="Git push before waiting for Railway deploy")
    parser.add_argument("--skip-deploy", action="store_true", help="Skip Railway deploy wait")
    parser.add_argument("--skip-structure", action="store_true", help="Skip data-testid structure pass")
    parser.add_argument("--skip-ui", action="store_true", help="Skip Playwright UI execution")
    parser.add_argument("--no-structure-block", action="store_true", help="Run UI even when test_ids missing")
    parser.add_argument("--no-ollama", action="store_true", help="Skip Ollama task structuring")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--serve", action="store_true", help="Start web UI only")
    parser.add_argument("--init-project", action="store_true", help="Scaffold .agent/ and update .gitignore on target project, then exit")
    parser.add_argument("--emit-events", action="store_true", help="Emit NDJSON events on stdout for the test-runner UI")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _resolve_project(raw: str | None) -> Path:
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
    cfg = merged_config(engine_root())
    default = default_project(cfg)
    if default:
        return default
    return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.WARNING if args.emit_events else logging.INFO),
        format="%(levelname)s: %(message)s",
    )
    configure_events(emit_json=args.emit_events)

    if args.serve:
        print("Use .\\run-test-runner.ps1 for the React UI (pnpm dev on :5175).")
        print("Legacy FastAPI UI removed — engine still works via CLI.")
        return 0

    project = _resolve_project(args.project)
    if not project.is_dir():
        logging.error("Project path does not exist: %s", project)
        return 1

    if args.init_project:
        result = ensure_project_setup(project)
        print(f"Project: {project}")
        print(f"Gitignore updated: {result.gitignore_updated}")
        if result.migrated_paths:
            print("Migrated from ui-test/:")
            for path in result.migrated_paths:
                print(f"  {path}")
        if result.created_paths:
            print("Created:")
            for path in result.created_paths:
                print(f"  {path}")
        else:
            print("No new paths created (already present).")
        return 0

    if not agent_dir(project).is_dir():
        logging.error("Missing .agent/ folder in project: %s", project)
        return 1

    task_file = Path(args.task_file).resolve() if args.task_file else None

    try:
        result = run_ui_test_loop(
            project,
            task=args.task,
            task_file=task_file,
            skip_deploy=args.skip_deploy,
            skip_structure=args.skip_structure,
            skip_ui=args.skip_ui,
            do_push=args.push,
            no_ollama=args.no_ollama,
            headless=not args.headed,
            structure_blocks=not args.no_structure_block,
        )
    except Exception as exc:
        logging.exception("UI test loop failed: %s", exc)
        return 1

    report_path = result.get("report_path") or (project / ".agent" / "current" / "REPORT.md")
    try:
        rel = Path(str(report_path)).relative_to(project)
    except ValueError:
        rel = Path(".agent/current/REPORT.md")

    print()
    print("UI test report ready:")
    print(f"  {project / '.agent/current/REPORT.md'}")
    print()
    print("In Cursor Agent on the target project, paste:")
    print(f"  Read {rel} and implement the fixes described there.")
    print()
    if result.get("overall_ok"):
        print("Overall: PASS")
        return 0
    print("Overall: FAIL — report written for Cursor.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
