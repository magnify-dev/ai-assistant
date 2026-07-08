from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ui_test.config_loader import (
    artifacts_dir,
    history_dir,
    merged_config,
    ollama_model,
    ollama_url,
    output_dir,
)
from ui_test.env_loader import apply_env, load_project_env, require_keys
from ui_test.events import Phase, configure_run_log, finish, log as emit_log, phase_done, phase_start, reset_run_state, set_running, structured_task_event, test_target_event, run_report_event, cheatsheet_refined_event
from ui_test.cheatsheet_refiner import refine_cheatsheet_from_run
from ui_test.run_report import build_run_report
from ui_test.events import get_run_state as _event_state
from ui_test.nav_registry import load_nav_tree, nav_tree_delta
from ui_test.page_registry import load_site_map, site_map_delta
from ui_test.exploration_runner import run_exploration
from ui_test.git_deploy import collect_git_deploy_state
from ui_test.railway_client import check_health, wait_for_deployments
from ui_test.local_env import format_local_env_hint, local_env_status
from ui_test.local_server import ensure_local_server, local_run_config, url_is_up, _all_targets_up
from ui_test.project_profile import ensure_cheatsheet, ensure_profile, local_base_url
from ui_test.project_paths import agent_tasks_dir
from ui_test.project_setup import ensure_project_setup
from ui_test.railway_config import load_railway_config
from ui_test.report import write_report_bundle
from ui_test.runner import run_spec
from ui_test.spec_loader import base_url_for_spec, load_spec, resolve_spec_file, save_structured_task
from ui_test.step_log import StepLogger
from ui_test.structure import run_structure_pass
from ui_test.ollama import ensure_ollama_ready
from ui_test.task_structurer import fallback_structured_task, structure_task_with_ollama

logger = logging.getLogger(__name__)


def _read_task_text(project: Path, task: str | None, task_file: Path | None) -> str:
    if task and task.strip():
        return task.strip()
    if task_file and task_file.is_file():
        return task_file.read_text(encoding="utf-8").strip()
    default_file = agent_tasks_dir(project) / "current.txt"
    if default_file.is_file():
        return default_file.read_text(encoding="utf-8").strip()
    return ""


def _resolve_test_target(
    test_target: str | None,
    skip_deploy: bool,
    defaults: dict[str, Any],
) -> str:
    if test_target in ("local", "deployed"):
        return test_target
    cfg = defaults.get("test_target")
    if cfg in ("local", "deployed"):
        return str(cfg)
    return "local" if skip_deploy else "deployed"


def run_ui_test_loop(
    project: Path,
    *,
    task: str | None = None,
    task_file: Path | None = None,
    skip_deploy: bool = False,
    test_target: str | None = None,
    skip_structure: bool = False,
    skip_ui: bool = False,
    do_push: bool = False,
    no_ollama: bool = False,
    headless: bool = True,
    structure_blocks: bool = True,
) -> dict[str, Any]:
    reset_run_state()
    set_running(True)

    setup = ensure_project_setup(project)
    if setup.gitignore_updated:
        emit_log("Updated project .gitignore (.agent entries)")
    if setup.migrated_paths:
        emit_log(f"Migrated ui-test/ -> .agent/: {', '.join(setup.migrated_paths[:5])}")
    if setup.created_paths:
        emit_log(f"Created project paths: {', '.join(setup.created_paths)}")
    if ensure_cheatsheet(project):
        emit_log("Created .agent/cheatsheet.yaml — edit local run instructions once")
    if ensure_profile(project):
        emit_log("Created .agent/profile.json — project settings stored locally")

    config = merged_config(project)
    env = load_project_env(project)
    out = output_dir(config, project)
    configure_run_log(out / "RUN-LOG.txt")
    if not env.get("UI_TEST_EMAIL") and env.get("ADMIN_SEED_EMAIL"):
        env["UI_TEST_EMAIL"] = env["ADMIN_SEED_EMAIL"]
    if not env.get("UI_TEST_PASSWORD") and env.get("ADMIN_SEED_PASSWORD"):
        env["UI_TEST_PASSWORD"] = env["ADMIN_SEED_PASSWORD"]
    apply_env(env)

    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    target_mode = _resolve_test_target(test_target, skip_deploy, defaults)
    if target_mode == "local":
        skip_deploy = True

    default_mode = str(defaults.get("selector_mode") or "strict")
    log_modes = bool(defaults.get("log_selector_mode", True))
    step_logger = StepLogger(enabled=log_modes)

    deploy_cfg = config.get("deploy") if isinstance(config.get("deploy"), dict) else {}
    wait_timeout = float(deploy_cfg.get("wait_timeout_sec") or 600)
    poll_interval = float(deploy_cfg.get("poll_interval_sec") or 10)

    railway = load_railway_config(project)
    spec_path = resolve_spec_file(project, config)
    spec_bundle = load_spec(spec_path)
    base_url = base_url_for_spec(spec_bundle, config, railway.services, project=project, skip_deploy=skip_deploy)
    if not base_url:
        set_running(False)
        raise RuntimeError("Could not resolve base URL from railway.yaml or spec")

    emit_log(f"Project: {project}")
    emit_log(f"Test target: {target_mode}")
    emit_log(f"Base URL: {base_url}")
    if target_mode == "local":
        local = local_base_url(project)
        if local:
            emit_log(f"Local cheatsheet URL: {local}")
    elif target_mode == "deployed":
        admin_svc = railway.service("admin")
        if admin_svc and admin_svc.url:
            base_url = str(admin_svc.url).rstrip("/")
            emit_log(f"Deployed URL: {base_url}")
    emit_log(f"Spec: {spec_path}")

    if not no_ollama:
        phase_start(Phase.OLLAMA, "Preparing Ollama model")
        ollama_cfg = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
        try:
            ensure_ollama_ready(
                url=ollama_url(config),
                model=ollama_model(config),
                wait_timeout_sec=float(ollama_cfg.get("wait_timeout_sec") or 120),
                preload_timeout_sec=float(ollama_cfg.get("preload_timeout_sec") or 600),
                on_log=lambda msg: emit_log(msg, phase=Phase.OLLAMA.value),
            )
            phase_done(Phase.OLLAMA, ok=True, message=ollama_model(config))
        except Exception as exc:
            emit_log(f"Ollama setup failed: {exc}", phase=Phase.OLLAMA.value, level="error")
            phase_done(Phase.OLLAMA, ok=False, message=str(exc))
            set_running(False)
            raise

    tree_urls = [
        str(n.get("url") or "/")
        for n in (spec_bundle.data.get("tree") or [])
        if isinstance(n, dict)
    ]
    spec_summary = (
        f"Spec file: {spec_path.name}. "
        f"After login, Playwright navigates: {', '.join(tree_urls) or '(none)'}. "
        f"Used for auth only when task-driven exploration is enabled."
    )

    free_text = _read_task_text(project, task, task_file)
    exploration_cfg = defaults.get("exploration") if isinstance(defaults.get("exploration"), dict) else {}
    use_exploration = bool(free_text) and not no_ollama and bool(exploration_cfg.get("enabled", True))
    structured_task: dict[str, Any] | None = None
    if free_text:
        phase_start(Phase.TASK, "Structuring task")
        if not no_ollama:
            emit_log("Structuring free-text task with Ollama...", phase=Phase.TASK.value)
            ollama_cfg = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
            structured_task = structure_task_with_ollama(
                url=ollama_url(config),
                model=ollama_model(config),
                timeout_sec=float(ollama_cfg.get("timeout_sec") or 180),
                free_text=free_text,
                app_context=f"Mini admin at {base_url}. Auth via /login.",
                spec_summary=spec_summary,
            )
            if structured_task:
                structured_task["spec_runs"] = (
                    "Exploration mode: discover → decide → act → evaluate. "
                    "Fixed spec used for login/auth only. Exploration persists in .agent/exploration.yaml."
                    if use_exploration
                    else spec_summary
                )
                save_structured_task(project, structured_task)
                structured_task_event(structured_task)
                gaps = structured_task.get("intent_gaps") or []
                if gaps:
                    emit_log(
                        f"Task alignment warning: {gaps[0]}",
                        phase=Phase.TASK.value,
                        level="error",
                    )
                emit_log(
                    f"Structured task: {structured_task.get('summary', '(no summary)')}",
                    phase=Phase.TASK.value,
                )
        else:
            structured_task = fallback_structured_task(free_text)
            structured_task["spec_runs"] = spec_summary
            structured_task_event(structured_task)
        phase_done(Phase.TASK, ok=True, message=structured_task.get("summary", "") if structured_task else "")

    phase_start(Phase.GIT, "Checking git state")
    git = collect_git_deploy_state(project, do_push=do_push)
    git_payload = asdict(git)
    emit_log(f"Git branch: {git.branch or '(n/a)'}, uncommitted: {git.has_uncommitted}", phase=Phase.GIT.value)
    if git.has_uncommitted and (skip_deploy or target_mode == "local"):
        phase_done(
            Phase.GIT,
            ok=True,
            status="warning",
            message=f"uncommitted ({git.status[:80] if git.status else 'changes'}) — OK for local/deployed skip",
        )
    else:
        phase_done(Phase.GIT, ok=not git.has_uncommitted, message=git.status[:120] if git.status else "clean")

    deploy_results: list[dict[str, Any]] = []
    health_results: list[dict[str, Any]] = []

    token = env.get("RAILWAY_TOKEN") or ""
    if git.changed_files:
        affected = railway.services_for_paths(git.changed_files)
    else:
        admin = railway.service("admin")
        affected = [admin] if admin else list(railway.services.values())
    affected = [s for s in affected if s is not None]

    if not skip_deploy:
        phase_start(Phase.DEPLOY, "Railway deploy")
        if git.has_uncommitted:
            emit_log("WARNING: Uncommitted changes — deploy may not include latest code", phase=Phase.DEPLOY.value)
        if git.unpushed_commits and not do_push:
            emit_log(
                f"WARNING: {git.unpushed_commits} unpushed commit(s). Use --push or push manually.",
                phase=Phase.DEPLOY.value,
            )

        if do_push and git.is_repo:
            git = collect_git_deploy_state(project, do_push=True)
            git_payload = asdict(git)
            emit_log(git.push_message, phase=Phase.DEPLOY.value)

        deploy_ok = True
        if token and git.is_repo and (do_push or git.unpushed_commits == 0):
            if not require_keys(env, ["RAILWAY_TOKEN"]) and affected:
                emit_log(f"Waiting for Railway deploy ({len(affected)} service(s))...", phase=Phase.DEPLOY.value)
                results = wait_for_deployments(
                    token,
                    railway,
                    affected,
                    timeout_sec=wait_timeout,
                    poll_interval_sec=poll_interval,
                )
                deploy_results = [asdict(r) for r in results]
                deploy_ok = all(r.ok for r in results)
                for r in results:
                    emit_log(f"Deploy {r.service}: {r.status}", phase=Phase.DEPLOY.value)
        elif not token:
            emit_log("No RAILWAY_TOKEN — skipping deploy wait", phase=Phase.DEPLOY.value)
        phase_done(Phase.DEPLOY, ok=deploy_ok)
    else:
        emit_log("Deploy wait skipped", phase=Phase.DEPLOY.value)

    local_session = None
    local_server_payload: dict[str, Any] = {"skipped": True}
    testing_locally = target_mode == "local" and bool(local_base_url(project))
    used_deployed_fallback = False
    test_target_source = "deployed" if target_mode == "deployed" else "local"
    local_url = local_base_url(project) if testing_locally else ""

    if target_mode == "deployed":
        admin_svc = railway.service("admin")
        if admin_svc and admin_svc.url:
            base_url = str(admin_svc.url).rstrip("/")
        phase_start(Phase.LOCAL, "Using deployed service (Railway)")
        local_server_payload = {
            "skipped": True,
            "ok": True,
            "message": f"Testing deployed at {base_url}",
            "test_target_mode": "deployed",
        }
        phase_done(Phase.LOCAL, ok=True, message=f"deployed {base_url}")
    elif testing_locally:
        env_status = local_env_status(project)
        if not env_status["ready"]:
            emit_log(
                f"Local env not ready — missing: {', '.join(env_status['missing'])}",
                phase=Phase.LOCAL.value,
                level="error",
            )
        phase_start(Phase.LOCAL, "Starting local dev server from cheatsheet")
        local_session = ensure_local_server(
            project,
            on_log=lambda msg: emit_log(msg, phase=Phase.LOCAL.value),
        )
        if local_session:
            local_result = local_session.ensure()
            local_server_payload = {
                "skipped": False,
                "ok": local_result.ok,
                "message": local_result.message,
                "started_by_us": local_result.started_by_us,
                "already_running": local_result.already_running,
                "start_command": local_session.start_command,
                "env_ready": env_status["ready"],
                "env_missing": env_status["missing"],
                "keep_alive": local_session.config.keep_alive,
                "launch_in_terminal": local_session.config.launch_in_terminal,
            }
            if local_result.ok:
                base_url = local_url or base_url
                test_target_source = "local"
                test_target_event(url=base_url, source="local", local_url=local_url)
                emit_log(f"Testing against local: {base_url}")
                phase_done(Phase.LOCAL, ok=True, message=local_result.message)
            else:
                emit_log(f"Local server failed: {local_result.message}", phase=Phase.LOCAL.value, level="error")
                cfg = local_run_config(project)
                admin_svc = railway.service("admin")
                if cfg and cfg.fallback_to_deployed and admin_svc and admin_svc.url:
                    fallback_url = str(admin_svc.url).rstrip("/")
                    emit_log(
                        f"Falling back to deployed URL: {fallback_url}",
                        phase=Phase.LOCAL.value,
                    )
                    base_url = fallback_url
                    used_deployed_fallback = True
                    test_target_source = "deployed_fallback"
                    test_target_event(url=base_url, source="deployed_fallback", local_url=local_url)
                    emit_log(f"Base URL (deployed fallback): {base_url}")
                    local_server_payload.update(
                        {
                            "ok": True,
                            "used_fallback": True,
                            "fallback_url": fallback_url,
                            "message": f"local failed; using deployed {fallback_url}",
                        }
                    )
                    phase_done(Phase.LOCAL, ok=True, message=f"fallback -> {fallback_url}")
                else:
                    phase_done(Phase.LOCAL, ok=False, message=local_result.message)
        else:
            local_server_payload = {"skipped": True, "ok": False, "message": "No local config in cheatsheet"}
            phase_done(Phase.LOCAL, ok=False, message="No local config in cheatsheet")
    elif base_url:
        test_target_event(url=base_url, source=test_target_source, local_url=local_url)
        emit_log(f"Testing against: {base_url}")

    try:
        phase_start(Phase.HEALTH, "Health checks")
        health_ok = True
        if testing_locally and not used_deployed_fallback:
            cfg = local_run_config(project)
            if cfg:
                up, msg = _all_targets_up(cfg)
                health_results.append(
                    {
                        "service": "local",
                        "url": cfg.base_url,
                        "ok": up,
                        "status_code": 0,
                        "message": msg if up else f"Not reachable: {msg}",
                    }
                )
                health_ok = up
            else:
                up, msg = url_is_up(base_url)
                health_results.append(
                    {
                        "service": "local",
                        "url": base_url,
                        "ok": up,
                        "status_code": 0,
                        "message": msg if up else f"Not reachable: {msg}",
                    }
                )
                health_ok = up
            emit_log(f"Health local: {msg if health_ok else 'FAIL — ' + msg}", phase=Phase.HEALTH.value)
        elif testing_locally and used_deployed_fallback:
            admin_svc = railway.service("admin")
            if admin_svc:
                hr = check_health(admin_svc)
                health_results.append(asdict(hr))
                health_ok = hr.ok
                emit_log(f"Health deployed ({admin_svc.name}): {hr.message}", phase=Phase.HEALTH.value)
        else:
            admin_svc = railway.service("admin") or (affected[0] if affected else None)
            services_to_health = [admin_svc] if admin_svc else []
            for svc in services_to_health:
                if not svc:
                    continue
                hr = check_health(svc)
                health_results.append(asdict(hr))
                health_ok = health_ok and hr.ok
                emit_log(f"Health {svc.name}: {hr.message}", phase=Phase.HEALTH.value)
        phase_done(Phase.HEALTH, ok=health_ok)

        structure_payload: list[dict[str, Any]] = []
        all_missing: set[str] = set()
        if not skip_structure and health_ok:
            phase_start(Phase.STRUCTURE, "Scanning data-testid hooks")
            try:
                structure_results = run_structure_pass(
                    base_url=base_url,
                    spec=spec_bundle.data,
                    required_ids=spec_bundle.required_test_ids,
                    env=env,
                    logger=step_logger,
                    headless=headless,
                )
                for sr in structure_results:
                    structure_payload.append(
                        {
                            "url": sr.url,
                            "present": sorted(sr.present),
                            "missing": sorted(sr.missing),
                            "ok": sr.ok,
                        }
                    )
                    all_missing.update(sr.missing)
                emit_log(f"Structure: {len(all_missing)} missing test_id(s)", phase=Phase.STRUCTURE.value)
                phase_done(Phase.STRUCTURE, ok=len(all_missing) == 0)
            except Exception as exc:
                emit_log(f"Structure pass failed: {exc}", phase=Phase.STRUCTURE.value, level="error")
                structure_payload.append(
                    {"url": base_url, "present": [], "missing": [], "ok": False, "error": str(exc)}
                )
                phase_done(Phase.STRUCTURE, ok=False, message=str(exc))
        elif not skip_structure and not health_ok:
            emit_log("Structure skipped — local server not healthy", phase=Phase.STRUCTURE.value)
            phase_done(Phase.STRUCTURE, ok=False, message="Local server not reachable")

        ui_run_payload: dict[str, Any] = {"passed": False, "skipped": skip_ui}
        site_map_before = load_site_map(project)
        nav_tree_before = load_nav_tree(project)
        if not skip_ui and health_ok:
            if use_exploration:
                phase_start(Phase.EXPLORE, "AI exploration (discover → decide → act)")
                emit_log(
                    "Exploration mode: discover interactables, consult site map, Ollama decides next action",
                    phase=Phase.EXPLORE.value,
                )
                try:
                    ollama_cfg = config.get("ollama") if isinstance(config.get("ollama"), dict) else {}
                    explore_result = run_exploration(
                        project=project,
                        base_url=base_url,
                        spec=spec_bundle.data,
                        env=env,
                        logger=step_logger,
                        task_text=free_text,
                        structured_task=structured_task,
                        ollama_url=ollama_url(config),
                        ollama_model=ollama_model(config),
                        timeout_sec=float(ollama_cfg.get("timeout_sec") or 180),
                        max_steps=int(exploration_cfg.get("max_steps") or 20),
                        headless=headless,
                        artifacts_dir=artifacts_dir(config, project),
                        on_log=lambda msg: emit_log(msg, phase=Phase.EXPLORE.value),
                    )
                    ui_run_payload = {
                        "passed": explore_result.passed,
                        "final_url": explore_result.final_url,
                        "error": explore_result.error,
                        "steps": len(explore_result.steps),
                        "mode": "exploration",
                        "exploration_steps": explore_result.steps,
                        "evaluation": explore_result.evaluation,
                        "report_path": explore_result.report_path,
                        "report_markdown": explore_result.report_markdown,
                        "task_answer": explore_result.task_answer,
                        "page_findings": explore_result.page_findings,
                        "playwright_session": explore_result.playwright_session,
                        "site_map_path": explore_result.site_map_path,
                        "pages_discovered": explore_result.pages_discovered,
                    }
                    emit_log(
                        f"Exploration: {'PASS' if explore_result.passed else 'FAIL'} — "
                        f"{explore_result.error or 'ok'} ({explore_result.pages_discovered} pages in site map)",
                        phase=Phase.EXPLORE.value,
                    )
                    ui_run_payload["site_map_changes"] = site_map_delta(site_map_before, load_site_map(project))
                    ui_run_payload["nav_tree_changes"] = nav_tree_delta(nav_tree_before, load_nav_tree(project))
                    phase_done(Phase.EXPLORE, ok=explore_result.passed, message=explore_result.error or "ok")
                except Exception as exc:
                    ui_run_payload = {
                        "passed": False,
                        "final_url": "",
                        "error": str(exc),
                        "steps": 0,
                        "mode": "exploration",
                    }
                    emit_log(f"Exploration: FAIL — {exc}", phase=Phase.EXPLORE.value, level="error")
                    phase_done(Phase.EXPLORE, ok=False, message=str(exc))
            else:
                phase_start(Phase.UI_TEST, "Playwright UI test")
                try:
                    run_result = run_spec(
                        base_url=base_url,
                        spec=spec_bundle.data,
                        env=env,
                        logger=step_logger,
                        artifacts_dir=artifacts_dir(config, project),
                        default_mode=default_mode,
                        headless=headless,
                        stop_on_structure_missing=structure_blocks,
                        structure_missing=all_missing if structure_blocks else set(),
                    )
                    ui_run_payload = {
                        "passed": run_result.passed,
                        "final_url": run_result.final_url,
                        "error": run_result.error,
                        "steps": len(run_result.step_results),
                        "mode": "spec",
                    }
                    emit_log(
                        f"UI test: {'PASS' if run_result.passed else 'FAIL'} — {run_result.error or 'ok'}",
                        phase=Phase.UI_TEST.value,
                    )
                    phase_done(Phase.UI_TEST, ok=run_result.passed, message=run_result.error or "ok")
                except Exception as exc:
                    ui_run_payload = {
                        "passed": False,
                        "final_url": "",
                        "error": str(exc),
                        "steps": 0,
                        "mode": "spec",
                    }
                    emit_log(f"UI test: FAIL — {exc}", phase=Phase.UI_TEST.value, level="error")
                    phase_done(Phase.UI_TEST, ok=False, message=str(exc))
        elif not skip_ui and not health_ok:
            ui_run_payload = {
                "passed": False,
                "final_url": "",
                "error": "Local server not reachable",
                "steps": 0,
            }
            emit_log("UI test skipped — local server not healthy", phase=Phase.UI_TEST.value, level="error")
            phase_done(Phase.UI_TEST, ok=False, message="Local server not reachable")

        site_map_changes = site_map_delta(site_map_before, load_site_map(project))
        nav_tree_changes = nav_tree_delta(nav_tree_before, load_nav_tree(project))

        health_ok = all(h.get("ok") for h in health_results) if health_results else True
        deploy_ok = all(d.get("ok") for d in deploy_results) if deploy_results else True
        structure_ok = all(s.get("ok") for s in structure_payload) if structure_payload else True
        ui_ok = ui_run_payload.get("passed") if not skip_ui else True
        local_ok = local_server_payload.get("ok", True) if not local_server_payload.get("skipped") else True
        git_blocks = git.has_uncommitted and not skip_deploy and target_mode != "local"

        cursor_steps: list[str] = []
        if local_server_payload.get("used_fallback"):
            missing = local_server_payload.get("env_missing") or ["DATABASE_URL"]
            cursor_steps.append(format_local_env_hint(project, list(missing)))
        elif not local_ok and local_server_payload.get("message"):
            cursor_steps.append(f"Fix local dev server: {local_server_payload['message']}")
        if all_missing:
            cursor_steps.append("Add missing data-testid attributes listed under Structure.")
        if not ui_ok and ui_run_payload.get("error"):
            cursor_steps.append(f"Fix UI failure: {ui_run_payload['error']}")
        if git.has_uncommitted and not skip_deploy:
            cursor_steps.append("Commit and push changes so Railway can deploy them.")
        elif git.has_uncommitted and skip_deploy:
            emit_log("Git has uncommitted changes (ignored for local-only run)", phase=Phase.GIT.value)
        if structured_task and structured_task.get("notes_for_cursor"):
            cursor_steps.extend(str(n) for n in structured_task["notes_for_cursor"])

        overall_ok = health_ok and deploy_ok and structure_ok and ui_ok and local_ok and not git_blocks

        payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project": str(project),
            "structured_task": structured_task,
            "git": git_payload,
            "local_server": local_server_payload,
            "local_env": local_env_status(project) if testing_locally else None,
            "test_target": {"url": base_url, "source": test_target_source, "local_url": local_url, "mode": target_mode},
            "deploy": {"results": deploy_results},
            "health": health_results,
            "structure": structure_payload,
            "ui_run": ui_run_payload,
            "site_map_changes": site_map_changes,
            "nav_tree_changes": nav_tree_changes,
            "step_log": step_logger.lines() if log_modes else [],
            "step_entries": step_logger.entries_payload() if log_modes else [],
            "step_summary": step_logger.summary(),
            "cursor_steps": cursor_steps,
            "overall_ok": overall_ok,
        }

        cheatsheet_refine: dict[str, Any] = {"added_learnings": [], "added_notes": []}
        if not no_ollama:
            emit_log("Reviewing cheatsheet learnings (append-only)...", phase=Phase.DONE.value)
            cheatsheet_refine = refine_cheatsheet_from_run(
                project,
                run_report={},
                run_payload={
                    "structured_task": structured_task,
                    "ui_run": ui_run_payload,
                    "test_target": {"url": base_url, "source": test_target_source},
                    "local_server": local_server_payload,
                },
                url=ollama_url(config),
                model=ollama_model(config),
                timeout_sec=float(
                    (config.get("ollama") or {}).get("timeout_sec") or 180
                    if isinstance(config.get("ollama"), dict)
                    else 180
                ),
            )
            if cheatsheet_refine.get("added_learnings") or cheatsheet_refine.get("added_notes"):
                cheatsheet_refined_event(
                    added_learnings=cheatsheet_refine.get("added_learnings") or [],
                    added_notes=cheatsheet_refine.get("added_notes") or [],
                )
                emit_log(
                    f"Cheatsheet: +{len(cheatsheet_refine.get('added_learnings') or [])} learning(s), "
                    f"+{len(cheatsheet_refine.get('added_notes') or [])} note(s)",
                )
        payload["cheatsheet_refine"] = cheatsheet_refine

        run_report = build_run_report(payload)
        payload["run_report"] = run_report
        run_report_event(run_report)

        out = output_dir(config, project)
        hist = history_dir(config, project)
        report_path = write_report_bundle(output_dir=out, history_dir=hist, payload=payload)
        payload["report_path"] = str(report_path)
        emit_log(f"Report: {report_path}")
        finish(overall_ok=overall_ok, report_path=str(report_path))
        set_running(False)
        return payload
    finally:
        if local_session:
            local_session.stop()


def get_run_state() -> dict[str, Any]:
    return _event_state()
