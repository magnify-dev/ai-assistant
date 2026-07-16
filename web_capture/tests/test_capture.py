from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from web_capture.analyzer import analyze_capture
from web_capture.capture import build_capture, clip_rect
from web_capture.locators import validate_capture_locators
from web_capture.storage import persist_capture


def _state() -> dict[str, Any]:
    return {
        "url": "https://example.test/settings",
        "title": "Settings",
        "viewport": {
            "width": 1000,
            "height": 500,
            "scroll_x": 0,
            "scroll_y": 0,
            "document_width": 1000,
            "document_height": 1200,
        },
        "interactables": [
            {
                "id": "el-button-save",
                "index": 0,
                "kind": "button",
                "role": "button",
                "text": "Save",
                "rect": {"x": 900, "y": 450, "width": 200, "height": 100},
            },
            {
                "id": "offscreen",
                "index": 1,
                "kind": "button",
                "text": "Below",
                "rect": {"x": 0, "y": 600, "width": 100, "height": 40},
            },
        ],
    }


class _Locator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _Page:
    def get_by_role(self, role: str, **kwargs: Any) -> _Locator:
        return _Locator(1 if role == "button" and kwargs.get("name") == "Save" else 0)

    def get_by_test_id(self, value: str) -> _Locator:
        return _Locator(0)

    def get_by_label(self, value: str, **kwargs: Any) -> _Locator:
        return _Locator(0)

    def get_by_placeholder(self, value: str, **kwargs: Any) -> _Locator:
        return _Locator(0)

    def locator(self, value: str) -> _Locator:
        return _Locator(0)


class WebCaptureTests(unittest.TestCase):
    def test_clip_rect_to_visible_viewport(self) -> None:
        viewport = _state()["viewport"]
        self.assertEqual(
            clip_rect(
                {"x": 900, "y": 450, "width": 200, "height": 100},
                viewport,
            ),
            {"x": 900.0, "y": 450.0, "width": 100.0, "height": 50.0},
        )

    def test_build_capture_omits_offscreen_elements_and_is_stable(self) -> None:
        first = build_capture(_state())
        second = build_capture(_state())
        self.assertEqual(len(first["elements"]), 1)
        self.assertEqual(first["elements"][0]["id"], "el-button-save")
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(first["capture_id"], second["capture_id"])

    def test_locator_validation_requires_unique_match(self) -> None:
        capture = build_capture(_state())
        validate_capture_locators(_Page(), capture)
        element = capture["elements"][0]
        self.assertEqual(element["locator_status"], "unique")
        self.assertEqual(element["locator"]["kind"], "role")
        self.assertEqual(capture["summary"]["unique"], 1)

    def test_ai_disabled_keeps_raw_capture(self) -> None:
        with patch.dict(os.environ, {"WEB_CAPTURE_AI": "0"}):
            capture = build_capture(_state())
            analyzed = analyze_capture(capture)
        self.assertEqual(analyzed["ai"]["status"], "disabled")
        self.assertEqual(len(analyzed["elements"]), 1)
        self.assertIsNone(analyzed["elements"][0]["ai_interactive"])

    def test_persist_capture_writes_latest_artifact(self) -> None:
        capture = build_capture(_state())
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "current" / "ui-artifacts" / "playwright-session"
            saved = persist_capture(session, capture)
            latest = Path(temp) / "current" / "web-capture" / "latest.json"
            self.assertTrue(saved.is_file())
            self.assertTrue(latest.is_file())
            self.assertEqual(
                json.loads(latest.read_text(encoding="utf-8"))["capture_id"],
                capture["capture_id"],
            )


if __name__ == "__main__":
    unittest.main()
