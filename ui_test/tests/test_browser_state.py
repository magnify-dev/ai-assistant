import unittest

from ui_test.browser_state import (
    _enrich_interactables,
    _infer_gate_interactables_from_overlays,
    _merge_interactables,
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


if __name__ == "__main__":
    unittest.main()
