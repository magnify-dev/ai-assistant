from __future__ import annotations

import unittest
from typing import Any

from web_capture.analyzer import analyze_capture
from web_capture.capture import build_capture
from web_capture.page_map import (
    apply_content_defaults,
    merge_capture_elements,
    promote_clickable_content,
    summarize_map_layers,
)


def _state() -> dict[str, Any]:
    return {
        "url": "https://news.example/",
        "title": "News",
        "viewport": {
            "width": 1000,
            "height": 800,
            "scroll_x": 0,
            "scroll_y": 0,
            "document_width": 1000,
            "document_height": 1600,
        },
        "interactables": [
            {
                "id": "el-button-save",
                "index": 0,
                "kind": "button",
                "text": "Save",
                "rect": {"x": 900, "y": 700, "width": 80, "height": 36},
            }
        ],
        "page_content_map": [
            {
                "id": "el-card-headline-1",
                "index": 0,
                "kind": "card",
                "content_role": "card",
                "map_layer": "content",
                "title": "Patch notes for July",
                "text": "Patch notes for July",
                "dates": ["Jul 17, 2026"],
                "likely_clickable": True,
                "rect": {"x": 40, "y": 120, "width": 420, "height": 140},
            },
            {
                "id": "el-heading-section-1",
                "index": 1,
                "kind": "heading",
                "content_role": "heading",
                "map_layer": "content",
                "text": "Latest updates",
                "likely_clickable": False,
                "rect": {"x": 40, "y": 60, "width": 300, "height": 32},
            },
            {
                "id": "el-card-overlap",
                "index": 2,
                "kind": "card",
                "content_role": "card",
                "map_layer": "content",
                "text": "Save",
                "likely_clickable": True,
                "rect": {"x": 900, "y": 700, "width": 80, "height": 36},
            },
        ],
    }


class PageMapTests(unittest.TestCase):
    def test_merge_capture_elements_adds_content_and_skips_control_overlap(self) -> None:
        merged = merge_capture_elements(_state())
        self.assertEqual(len(merged), 3)
        layers = summarize_map_layers(merged)
        self.assertEqual(layers["controls"], 1)
        self.assertEqual(layers["content"], 2)
        self.assertEqual(layers["clickable_content"], 1)

    def test_build_capture_includes_content_blocks(self) -> None:
        capture = build_capture(_state(), elements=merge_capture_elements(_state()))
        self.assertEqual(len(capture["elements"]), 3)
        card = next(item for item in capture["elements"] if item["id"] == "el-card-headline-1")
        self.assertEqual(card["locator_status"], "content")
        self.assertEqual(card["dates"], ["Jul 17, 2026"])

    def test_apply_content_defaults_marks_clickable_cards(self) -> None:
        capture = build_capture(_state(), elements=merge_capture_elements(_state()))
        apply_content_defaults(capture)
        card = next(item for item in capture["elements"] if item["id"] == "el-card-headline-1")
        heading = next(item for item in capture["elements"] if item["id"] == "el-heading-section-1")
        self.assertTrue(card["ai_interactive"])
        self.assertFalse(heading["ai_interactive"])

    def test_promote_clickable_content_adds_agent_interactable(self) -> None:
        state = _state()
        capture = build_capture(state, elements=merge_capture_elements(state))
        apply_content_defaults(capture)
        for item in capture["elements"]:
            if item.get("map_layer") == "content":
                item["effective_interactive"] = bool(item.get("ai_interactive"))
        promote_clickable_content(state, capture)
        promoted = [item for item in state["interactables"] if item.get("from_content_map")]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["id"], "el-card-headline-1")

    def test_analyze_disabled_applies_content_defaults(self) -> None:
        import os
        from unittest.mock import patch

        state = _state()
        capture = build_capture(state, elements=merge_capture_elements(state))
        with patch.dict(os.environ, {"WEB_CAPTURE_AI": "0"}):
            analyzed = analyze_capture(capture)
        card = next(item for item in analyzed["elements"] if item["id"] == "el-card-headline-1")
        self.assertTrue(card["ai_interactive"])


if __name__ == "__main__":
    unittest.main()
