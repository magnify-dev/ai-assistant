from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from web_surf.extract import quote_supported
from web_surf.page_match import focus_query, rank_search_results, score_result_url
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


class ExtractTests(unittest.TestCase):
    def test_quote_supported_exact_and_partial(self) -> None:
        page = "The widget price is $10 for the basic plan."
        self.assertTrue(quote_supported("widget price is $10", page))
        self.assertTrue(quote_supported("The widget price is $10 for the basic", page))
        self.assertFalse(quote_supported("price is $99", page))


if __name__ == "__main__":
    unittest.main()
