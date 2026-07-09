"""One-off verification of the Open Modal feature on deployed content-manager.

Performs the checks the exploration agent claimed to run but never executed:
scroll to bottom, click Open Modal, verify Success dialog, close via X,
Close button, and Escape. Saves screenshots as evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(r"C:\Users\marce\Documents\Programming\content-manager")
BASE_URL = "https://content-manager-production-535d.up.railway.app"
OUT = PROJECT / ".agent" / "current" / "ui-artifacts" / "manual-verify"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("\"'")
    return env


def main() -> int:
    env = load_env(PROJECT / ".agent" / ".env")
    email = env.get("UI_TEST_EMAIL") or env.get("ADMIN_SEED_EMAIL") or ""
    password = env.get("UI_TEST_PASSWORD") or env.get("ADMIN_SEED_PASSWORD") or ""
    if not email or not password:
        print("FAIL: no login credentials in .agent/.env")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            note = fn() or ""
            results.append((name, True, note))
            print(f"PASS  {name} {note}")
        except Exception as exc:
            results.append((name, False, str(exc)))
            print(f"FAIL  {name}: {exc}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.fill("#email", email)
        page.fill("#password", password)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(f"{BASE_URL}/", timeout=30000)
        page.wait_for_timeout(1500)

        def btn_visible():
            btn = page.get_by_role("button", name="Open Modal")
            btn.scroll_into_view_if_needed(timeout=10000)
            expect(btn).to_be_visible(timeout=10000)
            page.screenshot(path=str(OUT / "01_button_bottom.png"), full_page=True)

        check("Open Modal button visible at bottom", btn_visible)

        def open_modal():
            page.get_by_role("button", name="Open Modal").click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible(timeout=5000)
            expect(dialog.get_by_text("Success")).to_be_visible(timeout=5000)
            page.screenshot(path=str(OUT / "02_modal_open.png"))

        check("Modal opens with Success message", open_modal)

        def close_via_x():
            page.get_by_role("dialog").get_by_role("button", name="Close").first.click()
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=5000)

        check("Close via top-right X", close_via_x)

        def close_via_button():
            page.get_by_role("button", name="Open Modal").click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible(timeout=5000)
            dialog.get_by_role("button", name="Close").last.click()
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=5000)

        check("Close via footer Close button", close_via_button)

        def close_via_escape():
            page.get_by_role("button", name="Open Modal").click()
            expect(page.get_by_role("dialog")).to_be_visible(timeout=5000)
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=5000)
            page.screenshot(path=str(OUT / "03_after_escape.png"), full_page=True)

        check("Close via Escape key", close_via_escape)

        def page_intact():
            expect(page.get_by_role("button", name="Add account")).to_be_visible(timeout=5000)
            expect(page.get_by_text("Account statistics")).to_be_visible(timeout=5000)

        check("Home page content intact after modal", page_intact)

        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
