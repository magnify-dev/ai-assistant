"""New research runs must not reuse prior facts — only URL maps."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from web_surf import runner as runner_mod
from web_surf.store import empty_facts, load_facts, merge_facts, save_facts


class RunIsolationTests(unittest.TestCase):
    def test_runner_does_not_load_or_save_cross_run_facts(self) -> None:
        source = inspect.getsource(runner_mod.run_web_research)
        self.assertIn("empty_facts()", source)
        self.assertIn("empty_index()", source)
        self.assertNotIn("load_facts(", source)
        self.assertNotIn("load_index(", source)
        self.assertNotIn("save_facts(", source)
        self.assertNotIn("save_index(", source)

    def test_disk_facts_remain_but_are_not_required_for_new_runs(self) -> None:
        """Stale facts.yaml may still exist on disk; new runs ignore it."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            stale = empty_facts()
            stale, _ = merge_facts(
                stale,
                [
                    {
                        "id": "old1",
                        "field": "latest_news_title",
                        "value": "Ancient Moth Mount Story",
                        "source_url": "https://www.wowhead.com/mop-classic/news/old",
                        "quote": "Ancient Moth Mount Story",
                    }
                ],
                research_query="go to wowhead and find latest news",
            )
            save_facts(project, stale)
            on_disk = load_facts(project)
            self.assertTrue(
                any(
                    "Moth Mount" in str(item.get("value") or "")
                    for item in on_disk.get("facts") or []
                    if isinstance(item, dict)
                )
            )
            # Run-local docs start empty regardless of disk.
            self.assertEqual(empty_facts().get("facts"), [])


if __name__ == "__main__":
    unittest.main()
