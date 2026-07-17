from __future__ import annotations

import unittest

from web_surf.fetch import is_js_shell_text
from web_surf.spec import wants_verbatim_copy
from web_surf.store import facts_for_query, index_for_query, query_overlap_score


class FetchJsShellTests(unittest.TestCase):
    def test_detects_wowhead_js_placeholder(self) -> None:
        text = (
            "Skip to Main Content\n"
            "This site makes extensive use of JavaScript.\n"
            "Please enable JavaScript in your browser."
        )
        self.assertTrue(is_js_shell_text(text))

    def test_allows_real_article_text(self) -> None:
        text = "Phase 5 is now live with Siege of Orgrimmar and five world bosses. " * 20
        self.assertFalse(is_js_shell_text(text))


class QueryScopeTests(unittest.TestCase):
    def test_excludes_unrelated_research_queries(self) -> None:
        facts_doc = {
            "facts": [
                {
                    "field": "patch",
                    "value": "Diablo patch",
                    "source_url": "https://www.wowhead.com/diablo-4",
                    "research_query": "find me the latest diablo 4 patch notes",
                },
                {
                    "field": "news_title",
                    "value": "MoP Classic Phase 5",
                    "source_url": "https://www.wowhead.com/mop-classic/news/example",
                    "research_query": "go to wowhead and find wow mists of pandaria latest news",
                },
            ]
        }
        query = "go to wowhead and find wow mists of pandaria and copy the latest news"
        scoped = facts_for_query(facts_doc, query, max_facts=10)
        self.assertEqual(len(scoped), 1)
        self.assertIn("MoP", scoped[0]["value"])

    def test_index_scoped_by_search_query(self) -> None:
        index = {
            "pages": {
                "https://example.test/diablo": {
                    "title": "Diablo",
                    "summary": "diablo patch notes",
                    "search_query": "find diablo 4 patch notes",
                },
                "https://example.test/mop": {
                    "title": "MoP",
                    "summary": "mists of pandaria classic news",
                    "search_query": "wow mists of pandaria wowhead news",
                },
            }
        }
        rows = index_for_query(index, "wow mists of pandaria wowhead latest news")
        self.assertEqual(len(rows), 1)
        self.assertIn("mop", rows[0][0])


class CopyIntentTests(unittest.TestCase):
    def test_detects_copy_requests(self) -> None:
        self.assertTrue(
            wants_verbatim_copy("go to wowhead and copy the latest news on the page")
        )
        self.assertFalse(wants_verbatim_copy("what is the latest wow news"))


if __name__ == "__main__":
    unittest.main()
