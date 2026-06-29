from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dev_loop.config import (
    history_dir,
    load_config,
    ollama_model,
    ollama_url,
    output_dir,
    repo_root,
)
from dev_loop.git_context import collect_git_context
from dev_loop.ollama import analyze_test_results
from dev_loop.report import write_report_bundle
from dev_loop.runner import detect_test_command, run_tests, _pytest_cmd

logger = logging.getLogger(__name__)


def _resolve_project(raw: str | None, demo: bool) -> Path:
    if demo:
        return repo_root().resolve()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
    return Path.cwd().resolve()


def _resolve_test_cmd(
    config: dict,
    project: Path,
    explicit: str | None,
    skip_tests: bool,
) -> str | None:
    if skip_tests:
        return None
    if explicit:
        return explicit
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    configured = defaults.get("test_cmd")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return detect_test_command(project)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev_loop",
        description="Run tests, analyze with Ollama, write .agent/current/REPORT.md for Cursor.",
    )
    parser.add_argument(
        "--project",
        help="Project root to test (default: current directory or config default).",
    )
    parser.add_argument(
        "--test-cmd",
        help='Test command to run, e.g. "pytest -q" or "npm test".',
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip running tests; analyze git state only (or pass --note for context).",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Extra context for Ollama (e.g. what you changed or what to focus on).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run against dev_loop/demo (intentionally failing sample tests).",
    )
    parser.add_argument(
        "--no-ollama",
        action="store_true",
        help="Write report from test output only; skip Ollama analysis.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    config = load_config()
    project = _resolve_project(args.project, args.demo)
    if not project.is_dir():
        logger.error("Project path does not exist: %s", project)
        return 1

    test_cmd = _resolve_test_cmd(config, project, args.test_cmd, args.skip_tests)
    if args.demo:
        test_cmd = args.test_cmd or _pytest_cmd("-q", "dev_loop/demo")

    logger.info("Project: %s", project)
    if test_cmd:
        logger.info("Running tests: %s", test_cmd)
    else:
        logger.warning("No test command configured or detected.")

    test_run = run_tests(project, test_cmd)
    if test_run.skipped:
        test_status = "skipped"
    elif test_run.passed:
        test_status = "passed"
    else:
        test_status = "failed"

    output_cfg = config.get("output") if isinstance(config.get("output"), dict) else {}
    max_test_output = int(output_cfg.get("max_test_output_chars") or 24000)
    max_diff_chars = int(output_cfg.get("max_diff_chars") or 12000)

    combined = test_run.combined_output
    if len(combined) > max_test_output:
        combined = combined[: max_test_output - 40] + "\n\n... output truncated ..."

    test_payload = {
        "command": test_run.command,
        "exit_code": test_run.exit_code,
        "passed": test_run.passed,
        "skipped": test_run.skipped,
        "status": test_status,
        "output": combined,
    }

    git = collect_git_context(project, max_diff_chars=max_diff_chars)
    git_payload = {
        "is_repo": git.is_repo,
        "branch": git.branch,
        "status": git.status,
        "diff_stat": git.diff_stat,
        "diff": git.diff,
    }

    analysis = None
    if not args.no_ollama:
        ollama_cfg = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
        timeout = float(ollama_cfg.get("timeout_sec") or 120)
        logger.info("Analyzing with Ollama (%s)...", ollama_model(config))
        analysis = analyze_test_results(
            url=ollama_url(config),
            model=ollama_model(config),
            timeout_sec=timeout,
            project_path=str(project),
            test_result=test_payload,
            git_context=git_payload,
            user_note=args.note,
        )
        if analysis is None:
            logger.warning("Ollama analysis failed; writing report with raw test output only.")
    else:
        logger.info("Skipping Ollama analysis (--no-ollama).")

    out_dir = output_dir(config, project)
    hist_dir = history_dir(config, project)
    report_path = write_report_bundle(
        output_dir=out_dir,
        history_dir=hist_dir,
        project=project,
        test_result=test_payload,
        git_context=git_payload,
        analysis=analysis,
        max_test_output_chars=max_test_output,
    )

    rel_report = report_path
    try:
        rel_report = report_path.relative_to(project)
    except ValueError:
        pass

    print()
    print("Dev loop report ready:")
    print(f"  {report_path}")
    print()
    print("In Cursor Agent, paste:")
    print(f'  Read {rel_report} and implement the fixes described there.')
    print()
    if test_run.passed:
        print("Tests passed.")
    elif test_run.skipped:
        print("Tests were skipped.")
    else:
        print("Tests failed — report written for Cursor to act on.")
    return 0 if test_run.passed or test_run.skipped else 2


if __name__ == "__main__":
    sys.exit(main())
