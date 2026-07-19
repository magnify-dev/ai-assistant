import unittest

from ui_test.browser_state import (
    _enrich_interactables,
    _infer_gate_interactables_from_overlays,
    _interactable_action_hint,
    _merge_interactables,
    build_semantic_snapshot,
)
from web_surf.form_values import collect_gate_fields, fallback_form_values, needs_form_value_plan


class BrowserStateGateFieldTests(unittest.TestCase):
    def test_infer_gate_fields_from_overlay_copy(self) -> None:
        overlays = [
            {
                "id": "div-1",
                "label": "Age Verification",
                "text": (
                    "Age Verification Please enter your date of birth to continue. "
                    "year 2026 2025 2024 month Jan Feb day 1 2 3"
                ),
            }
        ]
        inferred = _infer_gate_interactables_from_overlays(overlays)
        self.assertEqual(len(inferred), 3)
        self.assertEqual({item["name"] for item in inferred}, {"year", "month", "day"})

    def test_inferred_gate_fields_enable_form_value_planning(self) -> None:
        overlays = [
            {
                "id": "div-1",
                "label": "Age Verification",
                "text": "Age Verification year 2026 month day",
            }
        ]
        interactables = _enrich_interactables(
            _infer_gate_interactables_from_overlays(overlays),
            "https://news.blizzard.com/en-us/article/patch-notes",
        )
        snapshot = {"blocking_overlays": overlays, "interactables": interactables}
        self.assertTrue(needs_form_value_plan(snapshot, {}))
        gate_fields = collect_gate_fields(snapshot)
        self.assertGreaterEqual(len(gate_fields), 2)
        result = fallback_form_values(snapshot)
        self.assertIn("birth_year", result["form_values"])
        self.assertIn("birth_month", result["form_values"])
        self.assertIn("birth_day", result["form_values"])

    def test_merge_interactables_prefers_unique_controls(self) -> None:
        merged = _merge_interactables(
            [{"kind": "link", "text": "Docs", "href": "https://example.com/docs"}],
            [{"kind": "select", "name": "year", "text": "year"}],
        )
        self.assertEqual(len(merged), 2)

    def test_build_semantic_snapshot_includes_visible_text(self) -> None:
        snapshot = build_semantic_snapshot(
            {
                "title": "Patch Notes",
                "headings": ["Diablo IV", "July 14"],
                "visible_text": "Fixed an issue where players could not claim rewards.",
            }
        )
        self.assertIn("Patch Notes", snapshot)
        self.assertIn("Fixed an issue", snapshot)

    def test_link_action_hint_prefers_display_label(self) -> None:
        hint = _interactable_action_hint(
            {
                "kind": "link",
                "text": "Patch notes for July",
                "href": "https://news.example/story/patch-notes",
            }
        )
        self.assertIn("Patch notes for July", hint)
        self.assertIn("https://news.example/story/patch-notes", hint)

    def test_semantic_snapshot_includes_page_understanding_feed(self) -> None:
        snapshot = build_semantic_snapshot(
            {
                "title": "News",
                "page_understanding": {
                    "page_type": "article_list",
                    "summary": "A feed of news cards is visible.",
                    "feed_items": [
                        {
                            "title": "Season launch details",
                            "date": "1 day ago",
                            "author": "Archimtiros",
                        }
                    ],
                    "how_to_proceed": ["Open the newest feed item"],
                },
                "visible_text": "Season launch details Posted 1 day ago by Archimtiros",
            }
        )
        self.assertIn("Page understanding", snapshot)
        self.assertIn("Season launch details", snapshot)
        self.assertIn("1 day ago", snapshot)


if __name__ == "__main__":
    unittest.main()
