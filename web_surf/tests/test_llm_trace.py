from __future__ import annotations

import unittest
from unittest.mock import patch

from web_surf import events
from web_surf.llm import get_trace, ollama_chat, reset_trace


class LlmTraceTests(unittest.TestCase):
    def test_records_exchange_and_emits_event(self) -> None:
        captured: list[dict] = []
        events.configure(emit_json=False, sink=captured.append)
        reset_trace()

        with patch(
            "web_surf.llm.httpx.Client",
        ) as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value.raise_for_status = lambda: None
            client.post.return_value.json.return_value = {
                "message": {"content": '{"action":"wait"}'}
            }
            content = ollama_chat(
                prompt_key="web_research.browse_decide",
                ollama_url="http://127.0.0.1:11434",
                model="test-model",
                timeout_sec=5,
                system="system prompt",
                user='{"query":"widgets"}',
                format_json=True,
                step_id="step_001",
            )

        self.assertIn("wait", content)
        trace = get_trace()
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["prompt_key"], "web_research.browse_decide")
        self.assertEqual(trace[0]["step_id"], "step_001")
        self.assertEqual(trace[0]["system_prompt"], "system prompt")
        self.assertEqual(trace[0]["response"], '{"action":"wait"}')
        self.assertEqual(captured[0]["type"], "web_llm_exchange")
        events.configure(emit_json=False)


if __name__ == "__main__":
    unittest.main()
