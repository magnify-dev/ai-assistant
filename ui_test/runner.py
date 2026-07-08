from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from ui_test.browser_state import emit_page_state
from ui_test.step_log import SelectorMode, StepLogger, parse_mode, substitute_env


@dataclass
class StepResult:
    ok: bool
    message: str
    screenshot: str | None = None


@dataclass
class RunResult:
    passed: bool
    step_results: list[StepResult] = field(default_factory=list)
    final_url: str = ""
    error: str = ""


def _url_matches(page_url: str, expected: str) -> bool:
    if expected.startswith("/"):
        path = urlparse(page_url).path or "/"
        exp = expected if expected.startswith("/") else f"/{expected}"
        return path.rstrip("/") == exp.rstrip("/") or path == exp
    return expected in page_url


def _resolve_url(base_url: str, path: str) -> str:
    if path.startswith("http"):
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _target_selector(target: dict[str, Any], mode: SelectorMode) -> tuple[str, str]:
    if target.get("test_id"):
        tid = str(target["test_id"])
        return f'[data-testid="{tid}"]', f"test_id={tid}"
    if target.get("selector"):
        return str(target["selector"]), f"selector={target['selector']}"
    if target.get("role") and target.get("name"):
        role = str(target["role"])
        name = str(target["name"])
        return f"role={role}[name={name!r}]", f"role={role} name={name!r}"
    if mode == SelectorMode.FUZZY and target.get("text"):
        text = str(target["text"])
        return f"text={text}", f'text="{text}"'
    raise ValueError(f"No usable target for mode={mode.value}: {target}")


def _resolve_click_locator(page: Page, click: dict[str, Any], click_mode: SelectorMode):
    if click.get("role") and click.get("name"):
        label = f"role={click['role']} name={click['name']}"
        return page.get_by_role(str(click["role"]), name=str(click["name"])).first, label
    if click_mode == SelectorMode.FUZZY and click.get("text"):
        text = str(click["text"])
        button = page.get_by_role("button", name=text)
        if button.count() > 0:
            return button.first, f'button="{text}"'
    sel, label = _target_selector(click, click_mode)
    return page.locator(sel).first, label


def _maybe_screenshot(page: Page, artifacts_dir: Path | None, label: str) -> str | None:
    if not artifacts_dir:
        return None
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", label)[:80]
    path = artifacts_dir / f"{safe}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def _fill_input_stable(page: Page, selector: str, value: str, *, label: str) -> None:
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=15000)
    for attempt in range(3):
        loc.fill(value, timeout=15000)
        try:
            if loc.input_value(timeout=2000) == value:
                return
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(250 * (attempt + 1))
    raise RuntimeError(f"Could not set {label} — input value did not stick (React hydration?)")


def run_auth_flow(
    page: Page,
    base_url: str,
    auth: dict[str, Any],
    env: dict[str, str],
    logger: StepLogger,
) -> None:
    auth_url = str(auth.get("url") or "/login")
    page.goto(_resolve_url(base_url, auth_url), wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)
    emit_page_state(page, context="auth", node_url=auth_url)
    for step in auth.get("steps") or []:
        if not isinstance(step, dict):
            continue
        mode = parse_mode(step)
        ephemeral = bool(step.get("ephemeral"))
        if "fill" in step:
            target = step["fill"] if isinstance(step["fill"], dict) else {}
            if target.get("test_id"):
                sel = f'[data-testid="{target["test_id"]}"]'
                label = f"test_id={target['test_id']}"
            else:
                sel = str(target.get("selector") or "")
                label = sel
            value = substitute_env(str(target.get("value") or step.get("value") or ""), env)
            if not value:
                raise RuntimeError(f"Missing env value for auth fill target {label}")
            _fill_input_stable(page, sel, value, label=label)
            logger.log(
                mode=mode,
                ephemeral=ephemeral,
                page_url=page.url,
                action="fill",
                target=label,
                ok=True,
            )
        elif "click" in step:
            click = step["click"] if isinstance(step["click"], dict) else {}
            if isinstance(step["click"], str):
                click = {"text": step["click"]}
            click_mode = mode
            if click.get("text") and not click.get("test_id") and not click.get("selector"):
                click_mode = SelectorMode.FUZZY
            sel, label = _resolve_click_locator(page, click, click_mode)
            login_response: Any = None
            try:
                with page.expect_response(
                    lambda r: "/api/auth/login" in r.url and r.request.method == "POST",
                    timeout=30000,
                ) as resp_info:
                    sel.click(timeout=15000)
                login_response = resp_info.value
            except PlaywrightTimeout:
                sel.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            click_ok = True
            click_msg = ""
            if login_response is not None:
                click_ok = login_response.ok
                click_msg = f"login HTTP {login_response.status}"
            logger.log(
                mode=mode,
                ephemeral=ephemeral,
                page_url=page.url,
                action="click",
                target=label,
                ok=click_ok,
                message=click_msg,
            )
            if login_response is not None and not login_response.ok:
                try:
                    body = login_response.json()
                    err = body.get("error") if isinstance(body, dict) else None
                    if err:
                        raise RuntimeError(f"Login API failed: {err}")
                except RuntimeError:
                    raise
                except Exception:
                    raise RuntimeError(f"Login API failed: HTTP {login_response.status}")
            emit_page_state(page, context="after_auth_click")
        elif step.get("expect_url"):
            expected = str(step["expect_url"])
            timeout_ms = int(step.get("timeout_ms") or auth.get("expect_url_timeout_ms") or 30000)
            try:
                page.wait_for_url(lambda url: _url_matches(url, expected), timeout=timeout_ms)
            except PlaywrightTimeout:
                pass
            ok = _url_matches(page.url, expected)
            detail = ""
            if not ok and "/login" in page.url:
                try:
                    err = page.locator("form .text-red-200").first
                    if err.count() and err.is_visible():
                        detail = f" — {err.inner_text(timeout=1000).strip()}"
                except Exception:
                    pass
            logger.log(
                mode=SelectorMode.STRICT,
                ephemeral=ephemeral,
                page_url=page.url,
                action="expect_url",
                target=expected,
                ok=ok,
                message="" if ok else f"got {page.url}{detail}",
            )
            if not ok:
                raise RuntimeError(
                    f"Auth expect_url failed: wanted {expected}, got {page.url}{detail}"
                )


def _run_expect(page: Page, expect: dict[str, Any], artifacts_dir: Path | None, label: str) -> StepResult:
    if expect.get("url"):
        expected = str(expect["url"])
        ok = _url_matches(page.url, expected)
        if not ok:
            shot = _maybe_screenshot(page, artifacts_dir, label)
            return StepResult(False, f"URL expected {expected}, got {page.url}", shot)
    for key in ("test_id_visible", "dialog_visible", "element_visible"):
        if expect.get(key):
            tid = str(expect[key])
            loc = page.locator(f'[data-testid="{tid}"]')
            if loc.count() == 0:
                shot = _maybe_screenshot(page, artifacts_dir, label)
                return StepResult(False, f"Missing visible test_id={tid}", shot)
    if expect.get("text_contains"):
        text = str(expect["text_contains"])
        content = page.locator("body").inner_text(timeout=5000)
        if text not in content:
            shot = _maybe_screenshot(page, artifacts_dir, label)
            return StepResult(False, f'Page text does not contain "{text}"', shot)
    if expect.get("title_contains"):
        title = page.title()
        if str(expect["title_contains"]) not in title:
            shot = _maybe_screenshot(page, artifacts_dir, label)
            return StepResult(False, f"Title mismatch: {title}", shot)
    return StepResult(True, "expectations met")


def _execute_step(
    page: Page,
    base_url: str,
    step: dict[str, Any],
    env: dict[str, str],
    logger: StepLogger,
    artifacts_dir: Path | None,
    default_mode: str,
) -> StepResult:
    mode = parse_mode(step, default_mode)
    ephemeral = bool(step.get("ephemeral"))
    action = str(step.get("action") or "")
    label = action or "step"

    try:
        if action == "navigate":
            path = str(step.get("url") or "/")
            page.goto(_resolve_url(base_url, path), wait_until="domcontentloaded", timeout=60000)
            logger.log(mode=mode, ephemeral=ephemeral, page_url=page.url, action="navigate", target=path, ok=True)
            emit_page_state(page, context="after_navigate", node_url=path)
            return StepResult(True, f"navigated to {page.url}")

        target = step.get("target") if isinstance(step.get("target"), dict) else {}

        if action == "fill":
            sel, tlabel = _target_selector(target, mode)
            value = substitute_env(str(step.get("value") or target.get("value") or ""), env)
            page.locator(sel).first.fill(value, timeout=15000)
            logger.log(mode=mode, ephemeral=ephemeral, page_url=page.url, action="fill", target=tlabel, ok=True)
        elif action == "click":
            loc, tlabel = _resolve_click_locator(page, target, mode)
            loc.click(timeout=15000)
            page.wait_for_timeout(800)
            logger.log(mode=mode, ephemeral=ephemeral, page_url=page.url, action="click", target=tlabel, ok=True)
        elif action == "wait":
            ms = int(step.get("ms") or 1000)
            page.wait_for_timeout(ms)
            logger.log(mode=mode, ephemeral=ephemeral, page_url=page.url, action="wait", target=f"{ms}ms", ok=True)
        else:
            return StepResult(False, f"Unknown action: {action}")

        expect = step.get("expect")
        if isinstance(expect, dict):
            result = _run_expect(page, expect, artifacts_dir, label)
            logger.log(
                mode=SelectorMode.STRICT,
                ephemeral=ephemeral,
                page_url=page.url,
                action="expect",
                target=",".join(expect.keys()),
                ok=result.ok,
                message=result.message,
            )
            emit_page_state(page, context="after_expect" if result.ok else "expect_failed")
            return result
        emit_page_state(page, context=f"after_{action}")
        return StepResult(True, "ok")
    except (PlaywrightTimeout, ValueError, RuntimeError) as exc:
        shot = _maybe_screenshot(page, artifacts_dir, label)
        target_label = str(step.get("target") or action)
        logger.log(
            mode=mode,
            ephemeral=ephemeral,
            page_url=page.url,
            action=action,
            target=target_label,
            ok=False,
            message=str(exc),
        )
        return StepResult(False, str(exc), shot)


def run_spec(
    *,
    base_url: str,
    spec: dict[str, Any],
    env: dict[str, str],
    logger: StepLogger,
    artifacts_dir: Path | None,
    default_mode: str = "strict",
    headless: bool = True,
    stop_on_structure_missing: bool = False,
    structure_missing: set[str] | None = None,
) -> RunResult:
    step_results: list[StepResult] = []
    missing = structure_missing or set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            auth = spec.get("auth")
            if isinstance(auth, dict):
                run_auth_flow(page, base_url, auth, env, logger)

            for node in spec.get("tree") or []:
                if not isinstance(node, dict):
                    continue
                path = str(node.get("url") or "/")
                page.goto(_resolve_url(base_url, path), wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1000)
                emit_page_state(page, context="node", node_url=path)

                node_test_id = node.get("test_id")
                if node_test_id:
                    if str(node_test_id) in missing and stop_on_structure_missing:
                        msg = f"Blocked: missing test_id={node_test_id}"
                        step_results.append(StepResult(False, msg))
                        return RunResult(False, step_results, page.url, msg)

                for step in node.get("interactions") or []:
                    if not isinstance(step, dict):
                        continue
                    target = step.get("target") if isinstance(step.get("target"), dict) else {}
                    tid = target.get("test_id")
                    mode = parse_mode(step, default_mode)
                    if (
                        tid
                        and str(tid) in missing
                        and mode == SelectorMode.STRICT
                        and not step.get("ephemeral")
                    ):
                        msg = f"Blocked strict step: missing test_id={tid}"
                        logger.log(
                            mode=SelectorMode.STRICT,
                            ephemeral=False,
                            page_url=page.url,
                            action=str(step.get("action") or "step"),
                            target=f"test_id={tid}",
                            ok=False,
                            message=msg,
                        )
                        step_results.append(StepResult(False, msg))
                        return RunResult(False, step_results, page.url, msg)

                    result = _execute_step(page, base_url, step, env, logger, artifacts_dir, default_mode)
                    step_results.append(result)
                    if not result.ok:
                        return RunResult(False, step_results, page.url, result.message)

            return RunResult(True, step_results, page.url, "")
        finally:
            context.close()
            browser.close()

    return RunResult(False, step_results, "", "No tree nodes executed")
