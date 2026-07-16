from __future__ import annotations

import unittest

from ui_test.expandable import is_collapse_toggle, is_collapsed_section, section_text_growth
from ui_test.state_diff import diff_page_states


class ExpandableTests(unittest.TestCase):
    def test_detects_bootstrap_collapse_link(self) -> None:
        item = {
            "kind": "link",
            "href": "https://news.blizzard.com/en-us/article/123#3.1.1",
            "expands_section": True,
            "collapsed": True,
            "data_toggle": "collapse",
        }
        self.assertTrue(is_collapse_toggle(item))
        self.assertTrue(is_collapsed_section(item))

    def test_detects_patch_section_anchor_without_bootstrap_metadata(self) -> None:
        item = {
            "kind": "link",
            "text": "3.1.1 Build #72805 (All Platforms)—July 14, 2026",
            "href": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes#3.1.1",
        }
        self.assertTrue(is_collapse_toggle(item))

    def test_section_text_growth_counts_as_progress_signal(self) -> None:
        before = {"visible_text": "Header only " * 20}
        after = {"visible_text": ("Header only " * 20) + ("Bug fix details " * 80)}
        self.assertTrue(section_text_growth(before, after))

    def test_collapsed_field_change_is_meaningful(self) -> None:
        before = {
            "url": "https://example.com/",
            "visible_text": "Patch notes",
            "interactables": [
                {
                    "id": "patch",
                    "kind": "link",
                    "text": "3.1.1 July 14",
                    "collapsed": True,
                    "expands_section": True,
                }
            ],
        }
        after = {
            **before,
            "visible_text": "Patch notes Bug Fixes Season updates",
            "interactables": [
                {
                    "id": "patch",
                    "kind": "link",
                    "text": "3.1.1 July 14",
                    "collapsed": False,
                    "expands_section": True,
                }
            ],
        }
        delta = diff_page_states(before, after)
        self.assertTrue(delta["meaningful_change"])
        self.assertEqual(delta["interactables_changed"][0]["fields"], ["collapsed"])


if __name__ == "__main__":
    unittest.main()
