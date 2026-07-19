from __future__ import annotations

import unittest

from web_capture.display_meta import (
    enrich_item_display_meta,
    extract_authors,
    extract_dates,
    is_meta_line,
    parse_display_meta,
    preferred_display_title,
)


class DisplayMetaTests(unittest.TestCase):
    def test_posted_relative_date_and_author(self) -> None:
        text = "Patch notes for July\nPosted 1 day ago by Archimtiros"
        meta = parse_display_meta(text)
        self.assertEqual(meta["dates"], ["1 day ago"])
        self.assertEqual(meta["authors"], ["Archimtiros"])
        self.assertIn("Posted 1 day ago by Archimtiros", str(meta["byline"]))

    def test_author_is_not_a_date(self) -> None:
        self.assertEqual(extract_dates("Archimtiros"), [])
        self.assertEqual(extract_authors("Posted 1 day ago by Archimtiros"), ["Archimtiros"])

    def test_a_day_ago_variant(self) -> None:
        self.assertEqual(extract_dates("Updated a day ago"), ["a day ago"])

    def test_preferred_title_skips_byline(self) -> None:
        text = "Archimtiros\nPosted 1 day ago by Archimtiros\nSeason launch details"
        self.assertEqual(preferred_display_title(text), "Season launch details")
        self.assertTrue(is_meta_line("Posted 1 day ago by Archimtiros"))

    def test_enrich_replaces_author_only_label_when_byline_present(self) -> None:
        item = enrich_item_display_meta(
            {
                "kind": "link",
                "text": "Archimtiros",
                "title": "Archimtiros",
                "nearby_text": "Season launch details Posted 1 day ago by Archimtiros",
            }
        )
        self.assertEqual(item["dates"], ["1 day ago"])
        self.assertEqual(item["authors"], ["Archimtiros"])
        self.assertNotEqual(item["text"], "Archimtiros")
        self.assertIn("Season launch details", str(item["text"]))


if __name__ == "__main__":
    unittest.main()
