from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web_capture.visual import (
    merge_display_cells,
    normalize_tiles,
    overlay_from_elements,
    resolve_visual_map,
    stamp_element_correction_on_visual,
)


class VisualMapTests(unittest.TestCase):
    def test_normalize_tiles(self) -> None:
        raw = {
            "cols": 2,
            "rows": 2,
            "cells": [
                {"color": "#111111", "kind": "button"},
                {"color": "#222222", "kind": "text"},
                {"color": "#333333", "kind": "link"},
                {"color": "#444444", "kind": "chrome"},
            ],
        }
        normalized = normalize_tiles(raw)
        assert normalized is not None
        self.assertEqual(normalized["cols"], 2)
        self.assertEqual(len(normalized["cells"]), 4)
        self.assertTrue(normalized["cells"][0].startswith("#111111|button"))

    def test_build_and_stamp_correction(self) -> None:
        fresh = normalize_tiles(
            {
                "cols": 4,
                "rows": 4,
                "cells": [{"color": "#ffffff", "kind": "chrome"}] * 16,
            }
        )
        assert fresh is not None
        elements = [
            {
                "id": "btn-save",
                "user_interactive": True,
                "rect": {"x": 0, "y": 0, "width": 50, "height": 50},
            }
        ]
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            visual = resolve_visual_map(
                project,
                url="https://example.com/app",
                capture_id="cap_test",
                viewport={"width": 100, "height": 100},
                elements=elements,
                fresh_tiles=fresh,
            )
            self.assertEqual(visual["status"], "built")
            self.assertEqual(len(visual["display_cells"]), 16)
            self.assertIn("kept", visual["display_cells"][0])

            stamp_element_correction_on_visual(
                project,
                "https://example.com/app",
                {
                    "rect": {"x": 50, "y": 50, "width": 50, "height": 50},
                },
                interactive=False,
            )
            second = resolve_visual_map(
                project,
                url="https://example.com/app",
                capture_id="cap_test_2",
                viewport={"width": 100, "height": 100},
                elements=elements,
                fresh_tiles=fresh,
            )
            self.assertEqual(second["active_source"], "corrected")

    def test_merge_display_cells(self) -> None:
        base = ["#fff|chrome", "#000|button"]
        overlay = ["+", None]
        merged = merge_display_cells(base, overlay)
        self.assertEqual(merged[0], "#fff|kept")
        self.assertEqual(merged[1], "#000|button")

    def test_overlay_from_elements(self) -> None:
        overlay = overlay_from_elements(
            [{"user_interactive": False, "rect": {"x": 0, "y": 0, "width": 20, "height": 20}}],
            cols=4,
            rows=4,
            viewport={"width": 100, "height": 100},
        )
        self.assertEqual(overlay[0], "-")


if __name__ == "__main__":
    unittest.main()
