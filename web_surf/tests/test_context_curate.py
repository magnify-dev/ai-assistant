from __future__ import annotations

import unittest

from web_surf.context_curate import (
    compact_history,
    compact_routes,
    curate_browse_context,
    curate_controls,
    curate_text,
    normalize_decision,
)


class ContextCurateTests(unittest.TestCase):
    def test_curate_controls_balances_overlay_goal_and_navigation(self) -> None:
        controls = curate_controls(
            [
                {"id": "footer-login", "kind": "link", "text": "Sign in", "href": "/login"},
                {"id": "overlay-ok", "kind": "button", "text": "Agree and continue"},
                {"id": "topic-link", "kind": "link", "text": "Product changelog", "href": "/changelog"},
                {"id": "nav-home", "kind": "link", "text": "Home", "href": "/", "landmark": "nav"},
            ],
            query="latest product changelog",
            has_overlay=True,
        )
        ids = [row["id"] for row in controls]
        self.assertIn("overlay-ok", ids)
        self.assertIn("topic-link", ids)
        self.assertIn("nav-home", ids)

    def test_curate_text_keeps_lead_even_without_query_overlap(self) -> None:
        text = curate_text(
            "Welcome to Example Corp. We build tools for everyone. Pricing is on another page.",
            query="unrelated astronomy facts",
            max_chars=300,
        )
        self.assertTrue(text.startswith("Welcome to Example Corp"))
        self.assertLessEqual(len(text), 300)

    def test_routes_keep_full_urls_for_multiple_origins(self) -> None:
        routes = compact_routes(
            {
                "https://a.example/docs",
                "https://b.example/guide",
                "https://a.example/pricing",
            }
        )
        self.assertEqual(routes[0], "https://a.example/docs")
        self.assertIn("https://b.example/guide", routes)

    def test_browse_context_uses_generic_schema(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "title": "Example",
            "visible_text": "Cookie notice. Product docs updated today.",
            "blocking_overlays": [{"id": "overlay-1", "text": "Cookie preferences"}],
            "interactables": [
                {"id": "btn-accept", "kind": "button", "text": "Accept"},
                {"id": "search-input", "kind": "input", "placeholder": "Search"},
            ],
        }
        payload = curate_browse_context(
            query="product documentation",
            step_id="step_001",
            snapshot=snapshot,
            discovered_routes={"https://example.com/docs"},
        )
        self.assertIn("page", payload)
        self.assertIn("overlays", payload)
        self.assertIn("controls", payload)
        self.assertNotIn("blockers", payload)
        self.assertEqual(payload["controls"][0]["action"], "click")

    def test_normalize_accepts_generic_malformed_shapes(self) -> None:
        fill = normalize_decision(
            {"next_action": [{"name": "provide_values", "value": {"search-input": "docs"}}]}
        )
        self.assertEqual(fill["action"], "fill")
        self.assertEqual(fill["target_id"], "search-input")

        semantic = normalize_decision(
            {"next_action": [{"name": "provide_values", "value": {"postal_code": "10115"}}]}
        )
        self.assertEqual(semantic["action"], "provide_values")

        click = normalize_decision(
            {
                "next_action": [
                    {"type": "click", "target": {"id": "btn-accept"}, "reason": "dismiss overlay"},
                ]
            }
        )
        self.assertEqual(click["action"], "click")
        self.assertEqual(click["target_id"], "btn-accept")

    def test_compact_history_is_short(self) -> None:
        lines = compact_history(
            [
                {"action": "click", "target_id": "btn-accept", "ok": True},
                {"action": "invalid", "ok": False, "error": "unsupported action"},
            ]
        )
        self.assertEqual(lines[0], "click:btn-accept ok")
        self.assertIn("fail", lines[1])

    def test_compact_history_includes_labels_and_urls(self) -> None:
        lines = compact_history(
            [
                {
                    "action": "navigate",
                    "target_id": "el_x",
                    "target_label": "Patch Notes",
                    "target_href": "https://example.com/patch-notes",
                    "ok": False,
                    "error": "navigate URL was not discovered from search or a page snapshot",
                }
            ]
        )
        self.assertIn('"Patch Notes"', lines[0])
        self.assertIn("https://example.com/patch-notes", lines[0])
        self.assertIn("fail", lines[0])

    def test_normalize_handles_nested_typed_and_keyed_shapes(self) -> None:
        nested = normalize_decision(
            {"action": {"type": "click", "target_id": "btn-a"}, "reason": "dismiss"}
        )
        self.assertEqual(nested["action"], "click")
        self.assertEqual(nested["target_id"], "btn-a")

        typed = normalize_decision({"type": "navigate", "url": "https://example.com/a"})
        self.assertEqual(typed["action"], "navigate")
        self.assertEqual(typed["url"], "https://example.com/a")

        keyed = normalize_decision({"click": {"target_id": "btn-b"}})
        self.assertEqual(keyed["action"], "click")
        self.assertEqual(keyed["target_id"], "btn-b")

        keyed_url = normalize_decision({"navigate": "https://example.com/b"})
        self.assertEqual(keyed_url["action"], "navigate")
        self.assertEqual(keyed_url["url"], "https://example.com/b")


if __name__ == "__main__":
    unittest.main()
