"""Preferred-site discovery seeds: listing hubs over deep search articles."""

from __future__ import annotations

import unittest

from web_surf.page_match import (
    is_content_listing_url,
    is_deep_article_url,
    listing_hub_url,
    preferred_discovery_seeds,
    seed_url_priority,
)


class DiscoverySeedTests(unittest.TestCase):
    def test_listing_vs_deep_article(self) -> None:
        listing = "https://www.wowhead.com/mop-classic/news"
        deep = (
            "https://www.wowhead.com/mop-classic/news/"
            "mists-of-pandaria-patch-5-5-3-ptr-development-notes-the-thunder-king-379095"
        )
        self.assertTrue(is_content_listing_url(listing))
        self.assertFalse(is_content_listing_url(deep))
        self.assertTrue(is_deep_article_url(deep))
        self.assertFalse(is_deep_article_url(listing))
        self.assertEqual(listing_hub_url(deep), listing)

    def test_preferred_latest_news_seeds_listing_hub(self) -> None:
        query = "go to wowhead and find the latest mop classic news"
        deep = (
            "https://www.wowhead.com/mop-classic/news/"
            "mists-of-pandaria-patch-5-5-3-ptr-development-notes-the-thunder-king-379095"
        )
        hubs = preferred_discovery_seeds(query, [deep])
        self.assertEqual(hubs, ["https://www.wowhead.com/mop-classic/news"])
        listing = hubs[0]
        self.assertGreater(
            seed_url_priority(listing, query)[0],
            seed_url_priority(deep, query)[0],
        )

    def test_no_preferred_site_keeps_empty_discovery(self) -> None:
        query = "latest patch notes"
        deep = "https://news.blizzard.com/en-us/article/123/patch-notes"
        self.assertEqual(preferred_discovery_seeds(query, [deep]), [])


if __name__ == "__main__":
    unittest.main()
