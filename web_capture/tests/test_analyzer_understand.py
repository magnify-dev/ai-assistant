from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from web_capture.analyzer import understand_page_capture


class UnderstandPageCaptureTests(unittest.TestCase):
    def test_understand_disabled_skips_llm(self) -> None:
        capture = {"fingerprint": "fp-test", "elements": []}
        with patch.dict(os.environ, {"WEB_CAPTURE_AI": "0"}):
            understand_page_capture(capture, {"visible_text": "hello"})
        self.assertEqual(capture["page_understanding_meta"]["status"], "disabled")
        self.assertNotIn("page_understanding", capture)


if __name__ == "__main__":
    unittest.main()
