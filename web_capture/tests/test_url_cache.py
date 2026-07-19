from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web_capture.url_cache import (
    capture_is_reusable,
    capture_path_for_url,
    load_capture_for_url,
    save_capture_for_url,
)


def _sample_capture(url: str, *, shot: str = "screenshots/example.jpg") -> dict:
    elements = [
        {
            "id": f"el-{i}",
            "kind": "link",
            "text": f"Story {i}",
            "rect": {"x": 0, "y": i * 10, "width": 10, "height": 10},
        }
        for i in range(12)
    ]
    return {
        "capture_id": "cap_test",
        "url": url,
        "created_at": "2026-07-19T20:00:00Z",
        "viewport": {"width": 1280, "height": 720, "document_height": 4000},
        "elements": elements,
        "screenshot": shot,
        "scroll_map": {
            "stitched": True,
            "mode": "full_page",
            "coords": "document",
            "canvas_height": 4000,
            "slice_count": 1,
            "slices": [{"scroll_y": 0, "height": 4000, "screenshot": shot}],
        },
    }


class UrlCacheTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            shot_rel = "screenshots/example-com-news.jpg"
            shot_path = project / ".agent" / "web-capture" / shot_rel
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            shot_path.write_bytes(b"fake-jpeg")
            url = "https://example.com/news"
            capture = _sample_capture(url, shot=shot_rel)
            saved = save_capture_for_url(project, capture)
            self.assertIsNotNone(saved)
            self.assertTrue(capture_path_for_url(project, url).is_file())
            loaded = load_capture_for_url(project, url)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["url"], url)
            self.assertEqual(loaded["capture_id"], "cap_test")
            self.assertTrue(capture_is_reusable(loaded, project=project))

    def test_missing_screenshot_not_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            url = "https://example.com/a"
            capture = _sample_capture(url)
            self.assertFalse(capture_is_reusable(capture, project=project))
            self.assertIsNone(save_capture_for_url(project, capture))
            self.assertIsNone(load_capture_for_url(project, url))

    def test_rejects_viewport_only_capture(self) -> None:
        capture = {
            "url": "https://example.com/",
            "elements": [
                {"id": f"a{i}", "kind": "link", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}}
                for i in range(12)
            ],
            "viewport": {"width": 1280, "height": 720, "document_height": 2506},
            "screenshot": "screenshots/x.jpg",
            "scroll_map": {
                "stitched": False,
                "coords": "viewport",
                "canvas_height": 720,
                "slice_count": 1,
                "slices": [],
            },
        }
        self.assertFalse(capture_is_reusable(capture))

    def test_does_not_overwrite_better_cached_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            shot_rel = "screenshots/example-com.jpg"
            shot_path = project / ".agent" / "web-capture" / shot_rel
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            shot_path.write_bytes(b"fake-jpeg")
            url = "https://example.com/"
            good = _sample_capture(url, shot=shot_rel)
            good["scroll_map"]["canvas_height"] = 8000
            save_capture_for_url(project, good)
            worse = _sample_capture(url, shot=shot_rel)
            worse["capture_id"] = "cap_worse"
            worse["scroll_map"]["canvas_height"] = 2000
            save_capture_for_url(project, worse)
            loaded = load_capture_for_url(project, url)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["capture_id"], "cap_test")
            self.assertEqual(loaded["scroll_map"]["canvas_height"], 8000)


if __name__ == "__main__":
    unittest.main()
