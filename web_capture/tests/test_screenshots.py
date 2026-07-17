from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from web_capture.screenshots import (
    load_screenshot_b64,
    persist_screenshot,
    screenshot_file_for_url,
    screenshot_rel_for_url,
)


class ScreenshotPersistenceTests(unittest.TestCase):
    def test_persist_and_load_roundtrip(self) -> None:
        payload = base64.b64encode(b"\xff\xd8\xff" + b"x" * 200).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            url = "https://www.wowhead.com/mop-classic/news"
            rel = persist_screenshot(project, url, payload, capture_id="cap_test")
            self.assertEqual(rel, screenshot_rel_for_url(url, capture_id="cap_test"))
            path = screenshot_file_for_url(project, url, capture_id="cap_test")
            self.assertTrue(path.is_file())
            loaded = load_screenshot_b64(project, url)
            self.assertIsNone(loaded)
            loaded_specific = base64.b64encode(path.read_bytes()).decode("ascii")
            self.assertEqual(loaded_specific, payload)


if __name__ == "__main__":
    unittest.main()
