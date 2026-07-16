from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web_capture.capture import build_capture
from web_capture.maps import (
    apply_site_map,
    element_signature,
    load_site_map,
    save_element_correction,
    site_key,
)


class SiteMapTests(unittest.TestCase):
    def test_site_key_normalizes_path(self) -> None:
        self.assertEqual(site_key("https://Example.com/settings/"), "example.com/settings")

    def test_save_and_apply_correction_on_next_capture(self) -> None:
        state = {
            "url": "https://example.com/settings",
            "title": "Settings",
            "viewport": {"width": 800, "height": 600, "scroll_x": 0, "scroll_y": 0, "document_width": 800, "document_height": 600},
            "interactables": [
                {
                    "id": "el-button-save",
                    "kind": "button",
                    "role": "button",
                    "text": "Save",
                    "rect": {"x": 10, "y": 10, "width": 80, "height": 30},
                },
                {
                    "id": "el-link-help",
                    "kind": "link",
                    "role": "link",
                    "text": "Help",
                    "href": "/help",
                    "rect": {"x": 120, "y": 10, "width": 60, "height": 20},
                },
            ],
        }
        first = build_capture(state)
        element = first["elements"][0]
        signature = element_signature(element)
        self.assertTrue(signature.startswith("sig_"))

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            save_element_correction(
                project,
                url=str(state["url"]),
                capture_id=str(first["capture_id"]),
                element=element,
                interactive=True,
                note="Primary action",
            )
            self.assertIsNotNone(load_site_map(project, str(state["url"])))

            second = build_capture(state)
            apply_site_map(second, project)
            by_id = {item["id"]: item for item in second["elements"]}
            self.assertTrue(by_id["el-button-save"]["map_matched"])
            self.assertTrue(by_id["el-button-save"]["user_interactive"])
            self.assertFalse(by_id["el-link-help"].get("map_matched"))


if __name__ == "__main__":
    unittest.main()
