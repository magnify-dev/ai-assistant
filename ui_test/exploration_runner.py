from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from ui_test.browser_state import collect_page_state, emit_page_state
from ui_test.exploration_agent import decide_next_action, evaluate_exploration
from ui_test.exploration_match import (
    current_page_has_task_data,
    find_task_path_in_site_map,
    history_has_interaction,
    path_key,
    task_mentions_hidden_content,
    task_requires_interaction,
)
from ui_test.nav_registry import (
    load_nav_tree,
    merge_nav_discovery,
    nav_summary_for_agent,
    record_interactable_click,
    record_nav_transition,
    save_nav_tree,
)
from ui_test.page_content import extract_visible_content, format_visible_report, resolve_task_answer
from ui_test.page_registry import (
    load_site_map,
    merge_page_discovery,
    registry_summary_for_agent,
    save_site_map,
    semantic_summary_from_visible,
)
from ui_test.runner import RunResult, _fill_input_stable, _resolve_url, run_auth_flow
from ui_test.step_log import SelectorMode, StepLogger, substitute_env


@dataclass
class ExplorationResult:
    passed: bool
    final_url: str = ""
    error: str = ""
    steps: list[str] = field(default_factory=list)
    evaluation: dict[str, Any] | None = None
    report_path: str = ""
    report_markdown: str = ""
    task_answer: str = ""
    page_findings: dict[str, Any] | None = None
    playwright_session: dict[str, Any] | None = None
    site_map_path: str = ""
    pages_discovered: int = 0
    mode: str = "exploration"


def _unexplored_link_paths(nav_tree: dict[str, Any], current_path: str, interactables: list[dict[str, Any]]) -> list[str]:
    known_routes = set((nav_tree.get("routes") or {}).keys())
    current = path_key(current_path)
    out: list[str] = []
    for el in interactables:
        if not isinstance(el, dict) or el.get("kind") != "link":
            continue
        href = str(el.get("href") or el.get("reaches") or "")
        if not href:
            continue
        p = path_key(href)
        if p != current and p not in known_routes and p not in out:
            out.append(p)
    return out


def _allowed_navigate_paths(
    nav_tree: dict[str, Any],
    interactables: list[dict[str, Any]],
    current_path: str | None = None,
) -> set[str]:
    """Only paths reachable via on-page links or verified transitions from here."""
    allowed = {"/"}
    for el in interactables:
        if not isinstance(el, dict):
            continue
        href = str(el.get("href") or el.get("reaches") or "")
        if href:
            allowed.add(path_key(href))
    if current_path:
        routes = nav_tree.get("routes") or {}
        route = routes.get(path_key(current_path)) if isinstance(routes.get(path_key(current_path)), dict) else {}
        for dst in (route.get("verified_reaches") or {}):
            allowed.add(path_key(str(dst)))
    for el in nav_tree.get("global_nav") or []:
        if isinstance(el, dict) and el.get("href"):
            allowed.add(path_key(str(el["href"])))
    return allowed


def _sanitize_navigate_decision(
    decision: dict[str, Any],
    nav_tree: dict[str, Any],
    interactables: list[dict[str, Any]],
    current_path: str | None = None,
) -> dict[str, Any]:
    if str(decision.get("action") or "").lower() != "navigate":
        return decision
    url = str(decision.get("url") or "")
    if not url:
        return decision
    target = path_key(url if url.startswith("/") else urlparse(url).path or url)
    allowed = _allowed_navigate_paths(nav_tree, interactables, current_path)
    if target in allowed:
        return decision
    for i, el in enumerate(interactables):
        if not isinstance(el, dict) or el.get("kind") != "link":
            continue
        href = str(el.get("href") or el.get("reaches") or "")
        if href:
            return {
                "action": "click",
                "target": {"index": i, "text": el.get("text"), "href": el.get("href")},
                "reason": f"Rejected invented navigate to {target} — using discovered link",
            }
    return {
        "action": "wait",
        "value": 500,
        "reason": f"Rejected invented navigate to {target} — not in navigation tree",
    }


def _assess_exploration_status(
    registry: dict[str, Any],
    page: Page,
    state: dict[str, Any],
    task_text: str,
    step_history: list[str] | None = None,
) -> tuple[str, str]:
    visible = state.get("visible_content") if isinstance(state.get("visible_content"), dict) else {}
    semantic = str(state.get("semantic_summary") or "")
    snippet = _page_text_snippet(page)

    has_data, data_reason = current_page_has_task_data(
        task_text=task_text,
        semantic_summary=semantic,
        page_snippet=snippet,
        visible_content=visible,
    )
    if has_data:
        # Data-lookup heuristics must not short-circuit tasks that ask for
        # interactions (click a button, press Escape, ...) before any
        # interaction has been performed.
        if task_requires_interaction(task_text) and not history_has_interaction(step_history or []):
            return (
                "interact",
                "Task requires UI interactions (click/press) that have not been performed yet",
            )
        return "report_ready", data_reason

    known_path, known_reason = find_task_path_in_site_map(registry, task_text)
    if known_path and path_key(known_path) != path_key(page.url):
        return "go_to_known", f"{known_reason} → reach {known_path} via on-page link"

    if task_mentions_hidden_content(task_text):
        return "explore_buttons", "Task may need clicking buttons/menus on this page"

    return "explore_next", "Task location unknown — explore links and controls on this page"


def _path_key(url: str) -> str:
    return path_key(url)


def _page_text_snippet(page: Page, limit: int = 3000) -> str:
    try:
        text = page.locator("main, [role='main'], #root, body").first.inner_text(timeout=5000)
        return " ".join(text.split())[:limit]
    except Exception:
        try:
            return page.locator("body").inner_text(timeout=3000)[:limit]
        except Exception:
            return ""


def _loop_warning(step_history: list[str], decision: dict[str, Any]) -> str:
    action = str(decision.get("action") or "")
    url = str(decision.get("url") or "")
    target = decision.get("target") if isinstance(decision.get("target"), dict) else {}
    text = str(target.get("text") or "")
    sig = f"{action}:{url or text}".strip(":")
    if not sig:
        return ""
    repeats = sum(1 for s in step_history[-6:] if sig.lower() in s.lower())
    if repeats >= 2:
        return f"You already did '{sig}' recently — use report or done instead."
    return ""


def _via_from_decision(decision: dict[str, Any], interactables: list[dict[str, Any]]) -> dict[str, Any]:
    target = decision.get("target") if isinstance(decision.get("target"), dict) else {}
    action = str(decision.get("action") or "").lower()
    if action == "navigate":
        path = str(decision.get("url") or target.get("href") or "")
        return {"kind": "navigate", "text": path, "href": path}
    matched = _match_interactable(target, interactables)
    if matched:
        return {
            "kind": matched.get("kind") or "click",
            "text": matched.get("text") or matched.get("aria"),
            "href": matched.get("href"),
            "test_id": matched.get("test_id"),
        }
    return {
        "kind": action or "click",
        "text": target.get("text") or target.get("name"),
        "href": target.get("href"),
    }


def _discover_and_persist(
    project: Path,
    registry: dict[str, Any],
    nav_tree: dict[str, Any],
    page: Page,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], bool, int, bool, int]:
    from ui_test.browser_state import attach_web_capture, collect_page_state

    state = collect_page_state(page, include_screenshot=True)
    attach_web_capture(page, state, context="ui_exploration", analyze=True)
    visible = extract_visible_content(page)
    interactables = state.get("interactables") or []

    registry, site_changed, new_capabilities = merge_page_discovery(
        registry,
        url=state["url"],
        title=state.get("title") or "",
        visible_content=visible,
    )
    nav_tree, nav_changed, new_interactables = merge_nav_discovery(
        nav_tree,
        path=state["url"],
        title=state.get("title") or "",
        interactables=interactables,
    )

    if site_changed:
        save_site_map(project, registry)
    if nav_changed:
        save_nav_tree(project, nav_tree)

    state["visible_content"] = visible
    state["semantic_summary"] = semantic_summary_from_visible(visible)
    try:
        from ui_test.events import browser_state_event

        browser_state_event(
            url=state["url"],
            title=state.get("title") or "",
            interactables=interactables,
            context="ui_exploration",
            screenshot_b64=state.get("screenshot_b64"),
            web_capture=state.get("web_capture"),
        )
    except ImportError:
        pass
    return registry, nav_tree, state, visible, site_changed, new_capabilities, nav_changed, new_interactables


def _match_interactable(target: dict[str, Any], interactables: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not interactables:
        return None
    idx = target.get("index")
    if idx is not None:
        try:
            i = int(idx)
            if 0 <= i < len(interactables):
                return interactables[i]
        except (TypeError, ValueError):
            pass
    text = str(target.get("text") or "").strip().lower()
    href = str(target.get("href") or "").strip().lower()
    role = str(target.get("role") or "").strip().lower()
    name = str(target.get("name") or target.get("text") or "").strip().lower()
    for el in interactables:
        if text and text in str(el.get("text") or "").lower():
            return el
        if text and text in str(el.get("aria") or "").lower():
            return el
        if href and href in str(el.get("href") or "").lower():
            return el
        if role and name:
            el_role = str(el.get("role") or el.get("kind") or "").lower()
            el_name = str(el.get("text") or el.get("aria") or "").lower()
            if role in el_role and name in el_name:
                return el
    return None


def _click_target(page: Page, target: dict[str, Any], interactables: list[dict[str, Any]]) -> str:
    el = _match_interactable(target, interactables)
    if el:
        if el.get("test_id"):
            loc = page.locator(f'[data-testid="{el["test_id"]}"]').first
            label = f'test_id={el["test_id"]}'
        elif el.get("href") and el.get("kind") == "link":
            href = str(el["href"])
            text = str(el.get("text") or el.get("aria") or "")
            if text:
                loc = page.get_by_role("link", name=text).first
                label = f'link="{text}"'
            else:
                path = urlparse(href).path or href
                loc = page.locator(f'a[href*="{path}"]').first
                label = f'link href={path}'
        elif el.get("text"):
            text = str(el["text"])
            for role in ("button", "link", "menuitem"):
                candidate = page.get_by_role(role, name=text)
                if candidate.count() > 0:
                    loc = candidate.first
                    label = f'{role}="{text}"'
                    break
            else:
                loc = page.get_by_text(text, exact=False).first
                label = f'text="{text}"'
        else:
            raise RuntimeError(f"Cannot click matched element: {el}")
    elif target.get("selector"):
        loc = page.locator(str(target["selector"])).first
        label = str(target["selector"])
    elif target.get("role") and target.get("name"):
        loc = page.get_by_role(str(target["role"]), name=str(target["name"])).first
        label = f'role={target["role"]} name={target["name"]}'
    elif target.get("text"):
        loc = page.get_by_text(str(target["text"]), exact=False).first
        label = f'text="{target["text"]}"'
    else:
        raise RuntimeError(f"No click target resolved: {target}")
    loc.click(timeout=15000)
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(800)
    return label


def _fill_target(
    page: Page,
    target: dict[str, Any],
    value: str,
    interactables: list[dict[str, Any]],
) -> str:
    el = _match_interactable(target, interactables)
    if el and el.get("test_id"):
        sel = f'[data-testid="{el["test_id"]}"]'
        label = f'test_id={el["test_id"]}'
    elif target.get("selector"):
        sel = str(target["selector"])
        label = sel
    elif el and el.get("placeholder"):
        sel = f'[placeholder="{el["placeholder"]}"]'
        label = sel
    else:
        raise RuntimeError(f"No fill target resolved: {target}")
    _fill_input_stable(page, sel, value, label=label)
    return label


def _write_exploration_report(project: Path, markdown: str) -> Path:
    out = project / ".agent" / "current" / "exploration-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown.strip() + "\n", encoding="utf-8")
    return out


def _emit_agent_decision(decision: dict[str, Any]) -> None:
    try:
        from ui_test.events import agent_decision_event

        agent_decision_event(decision)
    except ImportError:
        pass
    try:
        from ui_test.playwright_session import get_active_recorder

        recorder = get_active_recorder()
        if recorder:
            recorder.record_decision(decision)
    except ImportError:
        pass


def _emit_nav_tree(nav_tree: dict[str, Any], *, changed: bool, new_count: int) -> None:
    try:
        from ui_test.events import nav_tree_event

        routes = nav_tree.get("routes") or {}
        nav_tree_event(routes=routes, global_nav=nav_tree.get("global_nav") or [], changed=changed, new_elements=new_count)
    except ImportError:
        pass


def _emit_site_map(registry: dict[str, Any], *, changed: bool, new_count: int) -> None:
    try:
        from ui_test.events import site_map_event

        pages = registry.get("pages") or {}
        site_map_event(pages=pages, changed=changed, new_elements=new_count)
    except ImportError:
        pass


def _execute_agent_action(
    page: Page,
    base_url: str,
    decision: dict[str, Any],
    interactables: list[dict[str, Any]],
    env: dict[str, str],
    logger: StepLogger,
) -> tuple[bool, str]:
    action = str(decision.get("action") or "").lower()
    target = decision.get("target") if isinstance(decision.get("target"), dict) else {}

    if action == "navigate":
        path = str(decision.get("url") or target.get("href") or "/")
        requested_path = _path_key(path if path.startswith("/") else urlparse(path).path or path)
        if path.startswith("http"):
            page.goto(path, wait_until="domcontentloaded", timeout=60000)
        else:
            page.goto(_resolve_url(base_url, path), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)
        actual_path = _path_key(page.url)
        redirect_note = ""
        if not path.startswith("http") and requested_path not in ("/", "") and requested_path != actual_path:
            redirect_note = f" (no route — landed on {actual_path})"
        logger.log(
            mode=SelectorMode.FUZZY,
            ephemeral=True,
            page_url=page.url,
            action="navigate",
            target=path,
            ok=True,
            message=(decision.get("reason") or "") + redirect_note,
        )
        emit_page_state(page, context="explore_navigate", node_url=path)
        return True, f"navigate {path}{redirect_note}"

    if action == "click":
        label = _click_target(page, target, interactables)
        logger.log(
            mode=SelectorMode.FUZZY,
            ephemeral=True,
            page_url=page.url,
            action="click",
            target=label,
            ok=True,
            message=decision.get("reason") or "",
        )
        emit_page_state(page, context="explore_click")
        return True, f"click {label}"

    if action == "fill":
        value = substitute_env(str(decision.get("value") or ""), env)
        if not value:
            return False, "fill missing value"
        label = _fill_target(page, target, value, interactables)
        logger.log(
            mode=SelectorMode.FUZZY,
            ephemeral=True,
            page_url=page.url,
            action="fill",
            target=label,
            ok=True,
        )
        emit_page_state(page, context="explore_fill")
        return True, f"fill {label}"

    if action == "press":
        key = str(decision.get("value") or decision.get("key") or "")
        if not key:
            return False, "press missing key"
        page.keyboard.press(key)
        page.wait_for_timeout(500)
        logger.log(
            mode=SelectorMode.FUZZY,
            ephemeral=True,
            page_url=page.url,
            action="press",
            target=key,
            ok=True,
            message=decision.get("reason") or "",
        )
        emit_page_state(page, context="explore_press")
        return True, f"press {key}"

    if action == "wait":
        ms = int(decision.get("value") or 1000)
        page.wait_for_timeout(ms)
        logger.log(
            mode=SelectorMode.STRICT,
            ephemeral=True,
            page_url=page.url,
            action="wait",
            target=f"{ms}ms",
            ok=True,
        )
        return True, f"wait {ms}ms"

    if action in ("done", "report"):
        return True, action

    return False, f"unknown action: {action}"


def run_exploration(
    *,
    project: Path,
    base_url: str,
    spec: dict[str, Any],
    env: dict[str, str],
    logger: StepLogger,
    task_text: str,
    structured_task: dict[str, Any] | None,
    ollama_url: str,
    ollama_model: str,
    timeout_sec: float = 180,
    max_steps: int = 20,
    headless: bool = True,
    artifacts_dir: Path | None = None,
    on_log: Callable[[str], None] | None = None,
) -> ExplorationResult:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    registry = load_site_map(project)
    nav_tree = load_nav_tree(project)
    step_history: list[str] = []
    report_markdown = ""
    task_answer = ""
    page_findings: dict[str, Any] | None = None
    pages_discovered = len(registry.get("pages") or {})

    recorder = None
    session_manifest: dict[str, Any] | None = None
    if artifacts_dir:
        from ui_test.playwright_session import PlaywrightSessionRecorder, set_active_recorder

        recorder = PlaywrightSessionRecorder(artifacts_dir / "playwright-session")
        recorder.prepare()
        set_active_recorder(recorder)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context_opts: dict[str, Any] = {
            "viewport": {"width": 960, "height": 640},
            "device_scale_factor": 1,
            "color_scheme": "light",
        }
        if recorder:
            context_opts.update(recorder.context_options())
        context = browser.new_context(**context_opts)
        if recorder:
            recorder.attach(context)
        page = context.new_page()
        final_result: ExplorationResult | None = None
        try:
            auth = spec.get("auth")
            if isinstance(auth, dict):
                run_auth_flow(page, base_url, auth, env, logger, artifacts_dir=artifacts_dir)

            registry, nav_tree, state, _, site_changed, site_new, nav_changed, nav_new = _discover_and_persist(
                project, registry, nav_tree, page
            )
            _emit_site_map(registry, changed=site_changed, new_count=site_new)
            _emit_nav_tree(nav_tree, changed=nav_changed, new_count=nav_new)
            emit_page_state(page, context="explore_discover")
            if site_changed and site_new:
                log(f"Site map: +{site_new} capability description(s) on {_path_key(page.url)}")
            if nav_changed and nav_new:
                log(f"Nav tree: +{nav_new} interactable(s) on {_path_key(page.url)}")

            log("Auth complete — exploration loop starting")

            for step_num in range(max_steps):
                # 1. Catalog current page → site map + navigation tree
                registry, nav_tree, state, visible, site_changed, site_new, nav_changed, nav_new = _discover_and_persist(
                    project, registry, nav_tree, page
                )
                if site_changed:
                    _emit_site_map(registry, changed=True, new_count=site_new)
                if nav_changed:
                    _emit_nav_tree(nav_tree, changed=True, new_count=nav_new)

                interactables = state.get("interactables") or []
                site_summary = registry_summary_for_agent(registry)
                nav_summary = nav_summary_for_agent(nav_tree)
                current_path = _path_key(page.url)
                page_snippet = _page_text_snippet(page)
                page_content_summary = str(state.get("semantic_summary") or "")

                # 2. Check site map + current page for task data
                exploration_status, status_detail = _assess_exploration_status(
                    registry, page, state, task_text, step_history
                )
                log(f"Step {step_num + 1}: status={exploration_status} — {status_detail}")

                decision: dict[str, Any] | None = None
                if exploration_status == "report_ready":
                    decision = {"action": "report", "reason": status_detail}

                if decision is None:
                    unexplored = _unexplored_link_paths(nav_tree, page.url, interactables)
                    decision = decide_next_action(
                        url=page.url,
                        model=ollama_model,
                        ollama_url=ollama_url,
                        timeout_sec=timeout_sec,
                        task_text=task_text,
                        exploration_status=exploration_status,
                        status_detail=status_detail,
                        site_map_summary=site_summary,
                        nav_summary=nav_summary,
                        interactables=interactables,
                        step_history=step_history,
                        page_text_snippet=page_snippet,
                        page_content_summary=page_content_summary,
                        auth_complete="/login" not in current_path,
                        unexplored_paths=unexplored,
                    )
                if not decision:
                    final_result = ExplorationResult(
                        False,
                        page.url,
                        "Ollama returned no decision",
                        step_history,
                        pages_discovered=len(registry.get("pages") or {}),
                    )
                    break

                loop_msg = _loop_warning(step_history, decision)
                if loop_msg:
                    log(loop_msg)
                    retry = decide_next_action(
                        url=page.url,
                        model=ollama_model,
                        ollama_url=ollama_url,
                        timeout_sec=timeout_sec,
                        task_text=task_text,
                        exploration_status="stuck",
                        status_detail=loop_msg,
                        site_map_summary=site_summary,
                        nav_summary=nav_summary,
                        interactables=interactables,
                        step_history=step_history,
                        page_text_snippet=page_snippet,
                        page_content_summary=page_content_summary,
                        auth_complete="/login" not in current_path,
                        loop_warning=loop_msg,
                    )
                    if retry:
                        decision = retry

                decision = _sanitize_navigate_decision(decision, nav_tree, interactables, current_path)

                _emit_agent_decision(decision)
                reason = str(decision.get("reason") or "")
                action = str(decision.get("action") or "")
                log(f"Step {step_num + 1}: {action} — {reason}")

                if decision.get("report_markdown") and action != "report":
                    report_markdown = str(decision["report_markdown"])

                if action in ("done", "report") or decision.get("done"):
                    if action == "report":
                        page_findings = extract_visible_content(page)
                        task_answer = resolve_task_answer(
                            page_findings,
                            task_text,
                            ollama_url=ollama_url,
                            ollama_model=ollama_model,
                            timeout_sec=timeout_sec,
                        )
                        report_markdown = format_visible_report(
                            page_findings,
                            task_text=task_text,
                            answer=task_answer,
                        )
                        log(f"Report built from visible UI ({len(report_markdown)} chars)")
                    step_history.append(f"{action}: {reason}")
                    break

                path_before = current_path
                ok, detail = _execute_agent_action(
                    page, base_url, decision, interactables, env, logger
                )
                step_history.append(f"{action} {detail}: {reason}")
                if ok:
                    path_after = _path_key(page.url)
                    via = _via_from_decision(decision, interactables)
                    if str(decision.get("action") or "").lower() == "click":
                        nav_tree = record_interactable_click(nav_tree, path=path_before, via=via)
                        save_nav_tree(project, nav_tree)
                    if path_before != path_after:
                        nav_tree, edge_changed = record_nav_transition(
                            nav_tree,
                            from_path=path_before,
                            to_path=path_after,
                            via=via,
                        )
                        if edge_changed:
                            save_nav_tree(project, nav_tree)
                            _emit_nav_tree(nav_tree, changed=True, new_count=0)
                            log(f"Nav tree: verified {path_before} → {path_after}")
                    elif str(decision.get("action") or "").lower() == "click":
                        registry, nav_tree, state, _, site_changed, site_new, nav_changed, nav_new = (
                            _discover_and_persist(project, registry, nav_tree, page)
                        )
                        if site_changed:
                            _emit_site_map(registry, changed=True, new_count=site_new)
                        if nav_changed:
                            _emit_nav_tree(nav_tree, changed=True, new_count=nav_new)
                        emit_page_state(page, context="explore_click_same_url")
                        log(f"Re-cataloged page after click on {path_before}")
                if not ok:
                    final_result = ExplorationResult(
                        False,
                        page.url,
                        detail,
                        step_history,
                        pages_discovered=len(registry.get("pages") or {}),
                    )
                    break

            if final_result is None:
                registry, nav_tree, state, _, site_changed, site_new, nav_changed, nav_new = _discover_and_persist(
                    project, registry, nav_tree, page
                )
                _emit_site_map(registry, changed=site_changed, new_count=site_new)
                _emit_nav_tree(nav_tree, changed=nav_changed, new_count=nav_new)
                emit_page_state(page, context="explore_final")

                evaluation = evaluate_exploration(
                    url=page.url,
                    model=ollama_model,
                    ollama_url=ollama_url,
                    timeout_sec=timeout_sec,
                    task_text=task_text,
                    structured_task=structured_task,
                    step_history=step_history,
                    site_map_summary=registry_summary_for_agent(registry),
                    nav_summary=nav_summary_for_agent(nav_tree),
                    page_text_snippet=_page_text_snippet(page, limit=4000),
                    report_markdown=report_markdown,
                )
                passed = bool((evaluation or {}).get("passed"))
                interactions_satisfied = not task_requires_interaction(task_text) or history_has_interaction(step_history)
                if not passed and report_markdown and "/login" not in _path_key(page.url) and interactions_satisfied:
                    passed = True
                    if not evaluation:
                        evaluation = {"passed": True, "summary": "Report written after UI exploration"}
                elif passed and not interactions_satisfied:
                    passed = False
                    evaluation = dict(evaluation or {})
                    evaluation["passed"] = False
                    evaluation["summary"] = (
                        "Task requires UI interactions (click/press) but none were executed — "
                        "verification claims are ungrounded"
                    )
                if evaluation and evaluation.get("report_markdown") and not report_markdown:
                    report_markdown = str(evaluation["report_markdown"])

                report_path = ""
                if report_markdown:
                    rp = _write_exploration_report(project, report_markdown)
                    report_path = str(rp)
                    log(f"Exploration report: {report_path}")

                site_map_file = save_site_map(project, registry)
                save_nav_tree(project, nav_tree)
                final_result = ExplorationResult(
                    passed=passed,
                    final_url=page.url,
                    error="" if passed else str((evaluation or {}).get("summary") or "Task not satisfied"),
                    steps=step_history,
                    evaluation=evaluation,
                    report_path=report_path,
                    report_markdown=report_markdown,
                    task_answer=task_answer,
                    page_findings=page_findings,
                    site_map_path=str(site_map_file),
                    pages_discovered=len(registry.get("pages") or {}),
                )
        except (PlaywrightTimeout, ValueError, RuntimeError) as exc:
            emit_page_state(page, context="explore_failed", error=str(exc))
            final_result = ExplorationResult(
                False,
                page.url,
                str(exc),
                step_history,
                pages_discovered=len(registry.get("pages") or {}),
            )
        finally:
            if recorder:
                try:
                    recorder.record_frame(page, label="session_end", context="session_end")
                except Exception:
                    pass
                recorder.stop_tracing()
            context.close()
            browser.close()
            if recorder:
                from ui_test.playwright_session import session_manifest_paths, set_active_recorder

                session_manifest = session_manifest_paths(recorder.finalize())
                set_active_recorder(None)
                if final_result is not None:
                    final_result.playwright_session = session_manifest

    return final_result or ExplorationResult(False, "", "Exploration produced no result", step_history)


def exploration_to_run_result(result: ExplorationResult) -> RunResult:
    from ui_test.runner import RunResult, StepResult

    steps = [StepResult(True, s) for s in result.steps]
    return RunResult(result.passed, steps, result.final_url, result.error)
