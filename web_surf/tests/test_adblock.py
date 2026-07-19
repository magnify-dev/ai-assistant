from __future__ import annotations

import unittest

from web_surf.adblock import host_is_ad, url_is_ad


class AdblockTests(unittest.TestCase):
    def test_blocks_common_ad_hosts(self) -> None:
        self.assertTrue(host_is_ad("pagead2.googlesyndication.com"))
        self.assertTrue(host_is_ad("securepubads.g.doubleclick.net"))
        self.assertTrue(host_is_ad("adservice.google.com"))
        self.assertTrue(host_is_ad("adservice.google.de"))
        self.assertTrue(host_is_ad("cdn.taboola.com"))
        self.assertTrue(host_is_ad("ads.example-network.com"))
        self.assertTrue(host_is_ad("cdn.venatusmedia.com"))
        self.assertTrue(host_is_ad("scripts.nitropay.com"))

    def test_allows_content_hosts(self) -> None:
        self.assertFalse(host_is_ad("www.wowhead.com"))
        self.assertFalse(host_is_ad("news.blizzard.com"))
        self.assertFalse(host_is_ad("example.com"))

    def test_url_blocks_ad_hosts_and_safe_paths(self) -> None:
        self.assertTrue(url_is_ad("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"))
        self.assertTrue(
            url_is_ad(
                "https://www.example.com/gpt/pubads_impl.js",
                resource_type="script",
            )
        )
        self.assertFalse(url_is_ad("https://www.wowhead.com/mop-classic/news", resource_type="document"))
        self.assertFalse(url_is_ad("https://www.wowhead.com/news/advertise-with-us", resource_type="document"))


if __name__ == "__main__":
    unittest.main()
