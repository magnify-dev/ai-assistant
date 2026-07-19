from __future__ import annotations

import unittest

from ui_test.browser_state import filter_blocking_overlays
from ui_test.state_diff import diagnose_action_stall, diff_page_states, is_no_progress


class DiagnoseActionStallTests(unittest.TestCase):
    def test_new_blocker_after_click_is_suspected(self) -> None:
        before = {
            "url": "https://example.com/news",
            "visible_text": "Latest news",
            "interactables": [
                {"id": "el-link-hotfixes", "kind": "link", "text": "Hotfixes"},
            ],
            "blocking_overlays": [],
        }
        after = {
            "url": "https://example.com/news",
            "visible_text": "Latest news Subscribe to newsletter",
            "interactables": [
                {"id": "el-link-hotfixes", "kind": "link", "text": "Hotfixes"},
                {"id": "el-btn-close", "kind": "button", "text": "No thanks"},
            ],
            "blocking_overlays": [
                {
                    "id": "newsletter",
                    "tag": "div",
                    "role": "dialog",
                    "text": "Subscribe to our newsletter",
                    "source": "pointer_block",
                }
            ],
        }
        delta = diff_page_states(before, after)
        diagnosis = diagnose_action_stall(
            before,
            after,
            error="",
            action={"action": "click", "target_id": "el-link-hotfixes"},
            delta=delta,
        )
        self.assertTrue(diagnosis["suspect_blocker"])
        self.assertEqual(diagnosis["recommended"], "clear_blocker")
        self.assertTrue(diagnosis["new_blockers"])
        self.assertTrue(
            any("No thanks" in str(c.get("text") or "") for c in diagnosis["dismiss_controls"])
        )

    def test_click_intercept_error_marks_blocker(self) -> None:
        before = {
            "url": "https://example.com/a",
            "visible_text": "Hello",
            "interactables": [{"id": "el-1", "kind": "link", "text": "Go"}],
            "blocking_overlays": [],
        }
        after = dict(before)
        delta = diff_page_states(before, after)
        self.assertTrue(is_no_progress(before, after, delta))
        diagnosis = diagnose_action_stall(
            before,
            after,
            error="<div class=popup> intercepts pointer events",
            action={"action": "click", "target_id": "el-1"},
            delta=delta,
        )
        self.assertTrue(diagnosis["suspect_blocker"])
        self.assertTrue(diagnosis["click_error"])
        self.assertEqual(diagnosis["recommended"], "clear_blocker")

    def test_dismiss_controls_without_overlay_row_still_suspect(self) -> None:
        before = {
            "url": "https://example.com/news",
            "visible_text": "Feed",
            "interactables": [
                {"id": "el-link-a", "kind": "link", "text": "Article"},
            ],
            "blocking_overlays": [],
        }
        after = {
            "url": "https://example.com/news",
            "visible_text": "Feed Get our newsletter",
            "interactables": [
                {"id": "el-link-a", "kind": "link", "text": "Article"},
                {"id": "el-btn-close", "kind": "button", "text": "Close"},
                {"id": "el-btn-accept", "kind": "button", "text": "Accept"},
            ],
            "blocking_overlays": [],
        }
        delta = diff_page_states(before, after)
        diagnosis = diagnose_action_stall(
            before,
            after,
            action={"action": "click", "target_id": "el-link-a"},
            delta=delta,
        )
        self.assertTrue(diagnosis["suspect_blocker"])
        self.assertIn("dismiss/consent", diagnosis["reason"])

    def test_filter_keeps_pointer_block_promo(self) -> None:
        filtered = filter_blocking_overlays(
            [
                {
                    "id": "promo",
                    "tag": "div",
                    "role": "dialog",
                    "text": "Sign up for deals",
                    "source": "pointer_block",
                },
                {
                    "id": "trailer",
                    "tag": "div",
                    "text": "Launch Trailer WATCH NEXT Play Video",
                    "source": "selector",
                },
            ]
        )
        self.assertEqual([item["id"] for item in filtered], ["promo"])


if __name__ == "__main__":
    unittest.main()
