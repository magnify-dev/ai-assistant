"""Tests for single full-page screenshot map annotation."""

from __future__ import annotations

import unittest

from web_capture.capture import build_capture, clip_rect
from web_capture.full_page import (
    annotate_full_page_capture,
    clip_rect_to_document,
    is_full_page_capture,
)


class FullPageCaptureTests(unittest.TestCase):
    def test_document_clip_keeps_below_fold_elements(self) -> None:
        viewport = {
            "width": 1200.0,
            "height": 720.0,
            "scroll_x": 0.0,
            "scroll_y": 0.0,
            "document_width": 1200.0,
            "document_height": 2400.0,
        }
        # Would be dropped by viewport clipping.
        self.assertIsNone(
            clip_rect({"x": 10, "y": 900, "width": 200, "height": 40}, viewport, coord_space="viewport")
        )
        kept = clip_rect(
            {"x": 10, "y": 900, "width": 200, "height": 40},
            viewport,
            coord_space="document",
        )
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept["y"], 900.0)

    def test_annotate_full_page_sets_document_map(self) -> None:
        state = {
            "url": "https://www.wowhead.com/mop-classic/news",
            "title": "News",
            "screenshot_mode": "full_page",
            "viewport": {
                "width": 1200,
                "height": 720,
                "scroll_x": 0,
                "scroll_y": 0,
                "document_width": 1200,
                "document_height": 3000,
            },
            "interactables": [
                {
                    "id": "el_top",
                    "kind": "link",
                    "text": "Top",
                    "rect": {"x": 10, "y": 20, "width": 80, "height": 24},
                },
                {
                    "id": "el_low",
                    "kind": "link",
                    "text": "Below fold",
                    "rect": {"x": 10, "y": 1800, "width": 120, "height": 24},
                },
            ],
        }
        capture = build_capture(state, context="full_page", coord_space="document")
        capture["screenshot"] = "screenshots/news-full.jpg"
        annotated = annotate_full_page_capture(capture)
        self.assertTrue(is_full_page_capture(annotated))
        self.assertEqual(annotated["scroll_map"]["mode"], "full_page")
        self.assertEqual(annotated["scroll_map"]["coords"], "document")
        self.assertEqual(annotated["scroll_map"]["slice_count"], 1)
        self.assertGreaterEqual(annotated["scroll_map"]["canvas_height"], 1800)
        ids = {item["id"] for item in annotated["elements"]}
        self.assertEqual(ids, {"el_top", "el_low"})

    def test_clip_rect_to_document_helper(self) -> None:
        rect = clip_rect_to_document(
            {"x": -10, "y": 50, "width": 100, "height": 20},
            width=800,
            height=2000,
        )
        self.assertEqual(rect, {"x": 0.0, "y": 50.0, "width": 90.0, "height": 20.0})


if __name__ == "__main__":
    unittest.main()
