from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ui_test.browser_state import attach_web_capture, collect_page_state


class PlaywrightCaptureIntegrationTests(unittest.TestCase):
    def test_captures_main_shadow_and_iframe_controls(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")

        with patch.dict(os.environ, {"WEB_CAPTURE_AI": "0"}):
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Exception as exc:
                    self.skipTest(f"Chromium is unavailable: {exc}")
                page = browser.new_page(viewport={"width": 900, "height": 600})
                page.set_content(
                    """
                    <button>Main</button>
                    <div id="host"></div>
                    <iframe srcdoc="<button>Framed</button>"></iframe>
                    """
                )
                page.eval_on_selector(
                    "#host",
                    """el => {
                      const root = el.attachShadow({mode: "open"});
                      root.innerHTML = "<button>Shadow</button>";
                    }""",
                )
                page.wait_for_timeout(100)
                state = collect_page_state(page, include_screenshot=False)
                attach_web_capture(page, state, context="integration", analyze=True)
                browser.close()

        elements = state["web_capture"]["elements"]
        by_text = {str(item.get("text")): item for item in elements}
        self.assertEqual(by_text["Main"]["locator_status"], "unique")
        self.assertEqual(by_text["Shadow"]["shadow_host"], "div")
        self.assertEqual(by_text["Shadow"]["locator_status"], "unique")
        self.assertEqual(by_text["Framed"]["frame_index"], 0)
        self.assertEqual(by_text["Framed"]["locator_status"], "unique")


if __name__ == "__main__":
    unittest.main()
