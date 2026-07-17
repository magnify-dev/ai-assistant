from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from web_surf.extract import quote_supported
from web_surf.page_match import (
    focus_query,
    is_official_source,
    is_secondary_host,
    partition_by_source_tier,
    rank_search_results,
    score_result_url,
)
from web_surf.store import (
    cache_page_markdown,
    facts_summary_for_agent,
    load_facts,
    load_index,
    merge_facts,
    merge_page_index,
    normalize_url,
    read_cached_markdown,
    save_facts,
    save_index,
)


class StoreTests(unittest.TestCase):
    def test_normalize_url_strips_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_url("https://Example.com/docs/"),
            "https://example.com/docs",
        )

    def test_merge_page_and_facts_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            index, changed = merge_page_index(
                load_index(project),
                url="https://example.com/a",
                title="Example A",
                summary="Docs about widgets",
                fetch_tier=1,
                page_hash="abc123",
                search_query="widget docs",
            )
            self.assertTrue(changed)
            save_index(project, index)

            facts_doc, added = merge_facts(
                load_facts(project),
                [
                    {
                        "field": "price",
                        "value": "$10",
                        "source_url": "https://example.com/a",
                        "quote": "price is $10",
                    }
                ],
                research_query="widget price",
            )
            self.assertEqual(added, 1)
            save_facts(project, facts_doc)

            reloaded_index = load_index(project)
            reloaded_facts = load_facts(project)
            self.assertIn("https://example.com/a", reloaded_index["pages"])
            self.assertEqual(len(reloaded_facts["facts"]), 1)

            cache_page_markdown(project, "abc123", "# Example\n\nprice is $10")
            self.assertIn("price is $10", read_cached_markdown(project, "abc123"))

            summary = facts_summary_for_agent(reloaded_facts, query="widget price")
            self.assertIn("price", summary.lower())


class PageMatchTests(unittest.TestCase):
    def test_focus_query_strips_collaboration_wrapper(self) -> None:
        wrapped = (
            "You are the local UI testing agent. Explore the live app.\n\n"
            "Original user task:\nfind the latest product patch notes"
        )
        self.assertEqual(focus_query(wrapped), "find the latest product patch notes")
        self.assertEqual(focus_query("plain question"), "plain question")

    def test_score_result_url_prefers_topic_domains_and_content_paths(self) -> None:
        official = score_result_url(
            "https://news.examplegame.com/en-us/patch-notes", "examplegame patch notes"
        )
        social = score_result_url(
            "https://www.reddit.com/r/examplegame/comments/1", "examplegame patch notes"
        )
        self.assertGreater(official, social)

    def test_rank_prefers_official_content_pages(self) -> None:
        rows = [
            SimpleNamespace(
                title="Fan forum thread",
                url="https://www.reddit.com/r/game/comments/1",
                snippet="discussion about patch notes",
                query="q",
            ),
            SimpleNamespace(
                title="Game patch notes",
                url="https://news.examplegame.com/en-us/patch-notes",
                snippet="Official patch notes",
                query="q",
            ),
        ]
        ranked = rank_search_results(rows, "examplegame patch notes")
        self.assertEqual(ranked[0].url, "https://news.examplegame.com/en-us/patch-notes")

    def test_rank_keeps_domain_diversity(self) -> None:
        rows = [
            SimpleNamespace(
                title=f"Result {i}",
                url=f"https://same.example/patch-notes-{i}",
                snippet="patch notes",
                query="q",
            )
            for i in range(4)
        ]
        rows.append(
            SimpleNamespace(
                title="Other source",
                url="https://other.example/patch-notes",
                snippet="patch notes",
                query="q",
            )
        )
        ranked = rank_search_results(rows, "patch notes", per_domain=2)
        top_hosts = [row.url.split("/")[2] for row in ranked[:3]]
        self.assertIn("other.example", top_hosts)

    def test_official_tier_before_secondary(self) -> None:
        rows = [
            SimpleNamespace(
                title="Fan wiki",
                url="https://wiki.fan.com/examplegame-patch",
                snippet="community summary",
                query="q",
            ),
            SimpleNamespace(
                title="Official patch notes",
                url="https://news.examplegame.com/en-us/patch-notes",
                snippet="Official patch notes",
                query="q",
            ),
            SimpleNamespace(
                title="Reddit thread",
                url="https://www.reddit.com/r/examplegame/comments/1",
                snippet="discussion",
                query="q",
            ),
        ]
        official, secondary = partition_by_source_tier(rows, "examplegame patch notes")
        self.assertEqual(len(official), 1)
        self.assertEqual(official[0].url, "https://news.examplegame.com/en-us/patch-notes")
        self.assertEqual(len(secondary), 2)
        ranked = rank_search_results(rows, "examplegame patch notes")
        self.assertTrue(is_official_source(ranked[0].url, "examplegame patch notes"))
        self.assertFalse(is_official_source(ranked[-1].url, "examplegame patch notes"))

    def test_is_secondary_host_detects_social(self) -> None:
        self.assertTrue(is_secondary_host("https://www.reddit.com/r/game"))
        self.assertTrue(is_secondary_host("https://eu.forums.blizzard.com/en/d4/t/patch-notes/1"))
        self.assertFalse(is_secondary_host("https://news.examplegame.com/patch-notes"))

    def test_seed_url_priority_prefers_publisher_articles_over_forums(self) -> None:
        from web_surf.page_match import seed_url_priority

        query = "diablo 4 patch notes 14.7.2026"
        news = seed_url_priority(
            "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
            query,
        )
        forum = seed_url_priority(
            "https://eu.forums.blizzard.com/en/d4/t/patch-notes-for-july-14th-2026/25487",
            query,
        )
        self.assertGreater(news[0], forum[0])

    def test_publisher_article_urls_are_official(self) -> None:
        self.assertTrue(
            is_official_source(
                "https://news.publisher.com/en-us/article/12345/product-patch-notes",
                "product patch notes",
            )
        )
        self.assertFalse(
            is_official_source(
                "https://wiki.fan.com/product-patch",
                "product patch notes",
            )
        )


    def test_parse_target_dates_reads_numeric_and_named_dates(self) -> None:
        from web_surf.page_match import parse_target_dates

        self.assertEqual(parse_target_dates("patch notes 14.7.2026"), [(14, 7, 2026)])
        self.assertEqual(parse_target_dates("notes for July 14, 2026"), [(14, 7, 2026)])

    def test_page_matches_query_requires_substantive_dated_content(self) -> None:
        from web_surf.page_match import page_matches_query

        header_only = (
            "Diablo IV Patch Notes 3.1.1 Build #72805 (All Platforms)—July 14, 2026 "
            "3.1.0 Build #72592 (All Platforms)—June 30, 2026"
        )
        self.assertFalse(
            page_matches_query(header_only, "diablo 4 patch notes 14.7.2026")
        )
        detailed = (
            header_only
            + " Fixed an issue where Tower Halo rewards could appear inverted. "
            "Fixed an issue where Forgotten Souls were not dropping from Whisper Caches."
        )
        self.assertTrue(page_matches_query(detailed, "diablo 4 patch notes 14.7.2026"))

    def test_suggest_expand_action_targets_collapsed_patch_section(self) -> None:
        from web_surf.page_match import suggest_expand_action

        snapshot = {
            "interactables": [
                {
                    "id": "patch-july",
                    "kind": "link",
                    "text": "3.1.1 Build #72805 (All Platforms)—July 14, 2026",
                    "href": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes#3.1.1",
                    "expands_section": True,
                    "collapsed": True,
                },
                {
                    "id": "patch-june",
                    "kind": "link",
                    "text": "3.1.0 Build #72592 (All Platforms)—June 30, 2026",
                    "href": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes#3.1.0",
                    "expands_section": True,
                    "collapsed": True,
                },
            ],
        }
        action = suggest_expand_action(snapshot, "diablo 4 patch notes 14.7.2026")
        self.assertIsNotNone(action)
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "patch-july")

    def test_goal_is_satisfied_prefers_publisher_when_routes_exist(self) -> None:
        from web_surf.page_match import goal_is_satisfied

        detailed = (
            "3.1.1 Build July 14, 2026 Fixed an issue where Tower Halo rewards "
            "could appear inverted. Fixed an issue where Forgotten Souls were not dropping."
        )
        self.assertFalse(
            goal_is_satisfied(
                detailed,
                "diablo 4 patch notes 14.7.2026",
                source_url="https://gamingpromax.com/diablo-4-season-14-update-3-1-1-patch-notes",
                publisher_domains={"blizzard.com"},
                publisher_routes={
                    "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes"
                },
            )
        )
        self.assertTrue(
            goal_is_satisfied(
                detailed,
                "diablo 4 patch notes 14.7.2026",
                source_url="https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
                publisher_domains={"blizzard.com"},
                publisher_routes={
                    "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes"
                },
            )
        )

    def test_user_preferred_wowhead_overrides_official_default(self) -> None:
        from web_surf.page_match import (
            goal_is_satisfied,
            parse_user_preferred_domains,
            seed_url_priority,
        )

        query = "go to wowhead and find wow mists of pandaria latest news"
        preferred = parse_user_preferred_domains(query)
        self.assertIn("wowhead.com", preferred)
        wowhead = seed_url_priority("https://www.wowhead.com/mists-of-pandaria", query)
        blizzard = seed_url_priority(
            "https://news.blizzard.com/en-us/article/24267939/mists-of-pandaria-classic-escalation-now-live",
            query,
        )
        self.assertGreater(wowhead[0], blizzard[0])
        detailed = (
            "Mists of Pandaria Classic Escalation is now live with new raid tuning. "
            "Fixed an issue where players could not complete the weekly quest. "
            "Increased the drop rate for rare mounts. Balance update for monk class abilities."
        )
        self.assertTrue(
            goal_is_satisfied(
                detailed,
                query,
                source_url="https://www.wowhead.com/mists-of-pandaria/news",
                publisher_domains={"blizzard.com"},
                publisher_routes={
                    "https://news.blizzard.com/en-us/article/24267939/mists-of-pandaria-classic-escalation-now-live"
                },
                preferred_domains=preferred,
            )
        )

    def test_listing_url_requires_substantive_content_not_nav_shell(self) -> None:
        from web_surf.page_match import goal_is_satisfied, should_defer_collect_on_listing

        nav_shell = (
            "WOW RETAIL CLASSIC TBC MISTS LOG IN WITH BATTLE.NET "
            "PHASE 5 TRENDING TOPICS CREATE ACCOUNT "
            "Mists of Pandaria Classic news and updates "
        ) * 8
        query = "go to wowhead and find wow mists of pandaria latest news"
        snapshot = {
            "url": "https://www.wowhead.com/mop-classic/news",
            "visible_text": nav_shell,
            "viewport": {
                "width": 1280,
                "height": 720,
                "scroll_x": 0,
                "scroll_y": 0,
                "document_width": 1280,
                "document_height": 7263,
            },
            "interactables": [
                {
                    "id": "el_news_1",
                    "kind": "link",
                    "text": "MoP Classic Phase 5 Now Live",
                    "href": "https://www.wowhead.com/mop-classic/news/phase-5-live",
                    "disabled": False,
                }
            ],
        }
        self.assertFalse(
            goal_is_satisfied(
                nav_shell,
                query,
                source_url=snapshot["url"],
                preferred_domains={"wowhead.com"},
            )
        )
        self.assertTrue(should_defer_collect_on_listing(snapshot, query))

    def test_viewport_helpers_detect_unexplored_long_page(self) -> None:
        from web_surf.page_match import (
            page_extends_beyond_viewport,
            viewport_has_content_below,
            viewport_explored_fraction,
        )

        snapshot = {
            "viewport": {
                "width": 1280,
                "height": 720,
                "scroll_x": 0,
                "scroll_y": 0,
                "document_width": 1280,
                "document_height": 7263,
            }
        }
        self.assertTrue(page_extends_beyond_viewport(snapshot))
        self.assertTrue(viewport_has_content_below(snapshot))
        self.assertLess(viewport_explored_fraction(snapshot), 0.2)

    def test_suggest_content_link_action_picks_article_on_listing(self) -> None:
        from web_surf.page_match import suggest_content_link_action

        snapshot = {
            "url": "https://www.wowhead.com/mop-classic/news",
            "interactables": [
                {
                    "id": "el_home",
                    "kind": "link",
                    "text": "Home",
                    "href": "https://www.wowhead.com/",
                    "disabled": False,
                },
                {
                    "id": "el_article",
                    "kind": "link",
                    "text": "MoP Classic Phase 5 Now Live",
                    "href": "https://www.wowhead.com/mop-classic/news/phase-5-live",
                    "disabled": False,
                },
            ],
        }
        action = suggest_content_link_action(
            snapshot,
            "wow mists of pandaria latest news",
        )
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["target_id"], "el_article")
        self.assertEqual(action["action"], "click")

    def test_suggest_content_link_prefers_newer_dated_article(self) -> None:
        from web_surf.page_match import suggest_content_link_action

        snapshot = {
            "url": "https://www.example.com/news",
            "interactables": [
                {
                    "id": "el_old",
                    "kind": "link",
                    "text": "Season 8 recap",
                    "href": "https://www.example.com/news/season-8-recap-june-1-2025",
                    "disabled": False,
                },
                {
                    "id": "el_new",
                    "kind": "link",
                    "text": "Season 9 launch July 15, 2026",
                    "href": "https://www.example.com/news/season-9-launch",
                    "disabled": False,
                },
            ],
        }
        action = suggest_content_link_action(snapshot, "find the most recent news")
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["target_id"], "el_new")

    def test_filter_text_by_recency_keeps_newest_section(self) -> None:
        from web_surf.page_match import filter_text_by_recency

        text = (
            "Season 8 recap June 1, 2025. Older balance changes and bug fixes from last season. "
            "Season 9 launch July 15, 2026. New raid opens today with fresh rewards and quests."
        )
        filtered = filter_text_by_recency(text, "most recent news")
        self.assertIn("July 15, 2026", filtered)
        self.assertNotIn("June 1, 2025", filtered)

    def test_query_implies_recency_detects_temporal_phrases(self) -> None:
        from web_surf.page_match import query_implies_recency

        self.assertTrue(query_implies_recency("find the most recent news"))
        self.assertTrue(query_implies_recency("what's new today"))
        self.assertFalse(query_implies_recency("find patch notes for 14.7.2026"))


class ExtractTests(unittest.TestCase):
    def test_quote_supported_exact_and_partial(self) -> None:
        page = "The widget price is $10 for the basic plan."
        self.assertTrue(quote_supported("widget price is $10", page))
        self.assertTrue(quote_supported("The widget price is $10 for the basic", page))
        self.assertFalse(quote_supported("price is $99", page))


if __name__ == "__main__":
    unittest.main()
