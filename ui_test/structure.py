from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, sync_playwright

from ui_test.step_log import SelectorMode, StepLogger


@dataclass(frozen=True)
class StructureResult:
    url: str
    present: set[str]
    missing: set[str]
    ok: bool


def scan_test_ids(page: Page, base_url: str, path: str, required: set[str]) -> StructureResult:
    url = f"{base_url.rstrip('/')}{path}" if path.startswith("/") else path
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    found: set[str] = set()
    for test_id in required:
        locator = page.locator(f'[data-testid="{test_id}"]')
        if locator.count() > 0:
            found.add(test_id)
    missing = required - found
    return StructureResult(
        url=page.url,
        present=found,
        missing=missing,
        ok=len(missing) == 0,
    )


def run_structure_pass(
    *,
    base_url: str,
    spec: dict[str, Any],
    required_ids: set[str],
    env: dict[str, str],
    logger: StepLogger,
    headless: bool = True,
) -> list[StructureResult]:
    if not required_ids:
        return []

    auth = spec.get("auth")
    results: list[StructureResult] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            if isinstance(auth, dict):
                from ui_test.runner import run_auth_flow

                run_auth_flow(page, base_url, auth, env, logger)

            pages_to_scan: set[str] = {"/"}
            for node in spec.get("tree") or []:
                if isinstance(node, dict) and node.get("url"):
                    pages_to_scan.add(str(node["url"]))

            page_ids: dict[str, set[str]] = {}
            for node in spec.get("tree") or []:
                if not isinstance(node, dict):
                    continue
                url = str(node.get("url") or "/")
                ids = set()
                if node.get("test_id"):
                    ids.add(str(node["test_id"]))
                for step in node.get("interactions") or []:
                    if not isinstance(step, dict):
                        continue
                    target = step.get("target")
                    if isinstance(target, dict) and target.get("test_id"):
                        ids.add(str(target["test_id"]))
                    expect = step.get("expect")
                    if isinstance(expect, dict):
                        for key in ("test_id_visible", "dialog_visible", "element_visible"):
                            val = expect.get(key)
                            if isinstance(val, str) and val:
                                ids.add(val)
                if ids:
                    page_ids[url] = ids

            if not page_ids:
                page_ids["/"] = required_ids

            for path, ids in page_ids.items():
                result = scan_test_ids(page, base_url, path, ids)
                results.append(result)
                logger.log(
                    mode=SelectorMode.STRICT,
                    ephemeral=False,
                    page_url=result.url,
                    action="structure-scan",
                    target=f"{len(result.present)}/{len(ids)} test_ids",
                    ok=result.ok,
                    message=f"missing: {', '.join(sorted(result.missing)) or 'none'}",
                )
        finally:
            context.close()
            browser.close()
    return results
