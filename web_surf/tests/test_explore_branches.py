from __future__ import annotations

import unittest

from web_surf.context_curate import curate_browse_context
from web_surf.explore_branches import (
    build_exploration_menu,
    summarize_exploration_branches,
    unexplored_seed_urls,
)


class ExploreBranchTests(unittest.TestCase):
    def test_summarize_marks_unexplored_alternatives(self) -> None:
        summary = summarize_exploration_branches(
            current_url="https://a.example/article",
            seed_urls=[
                "https://a.example/article",
                "https://b.example/patch-notes",
            ],
            candidates=[
                type("Row", (), {"title": "Article A"}),
                type("Row", (), {"title": "Patch notes B"}),
            ],
            history=[{"ok": True, "action": "origin", "url": "https://a.example/article"}],
            active_branch_url="https://a.example/article",
        )
        self.assertEqual(summary["current"]["status"], "active")
        self.assertEqual(len(summary["alternatives"]), 1)
        self.assertEqual(summary["alternatives"][0]["url"], "https://b.example/patch-notes")

    def test_unexplored_seed_urls_tracks_opened_branches(self) -> None:
        seeds = [
            "https://eu.forums.blizzard.com/en/d4/t/patch-notes-for-july-14th-2026/25487",
            "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
        ]
        history = [
            {
                "ok": True,
                "action": "extract",
                "branch_url": seeds[0],
            }
        ]
        pending = unexplored_seed_urls(seeds, history, active_branch_url=seeds[0])
        self.assertEqual(pending, [seeds[1]])

    def test_summarize_marks_stalled_branch(self) -> None:
        history = [
            {"ok": True, "action": "click", "branch_url": "https://a.example/article"},
            {"ok": False, "progress": False, "error": "no progress", "branch_url": "https://a.example/article"},
            {"ok": False, "progress": False, "error": "no progress", "branch_url": "https://a.example/article"},
            {"ok": False, "progress": False, "error": "no progress", "branch_url": "https://a.example/article"},
            {"ok": False, "progress": False, "error": "no progress", "branch_url": "https://a.example/article"},
            {"ok": False, "progress": False, "error": "no progress", "branch_url": "https://a.example/article"},
        ]
        summary = summarize_exploration_branches(
            current_url="https://a.example/article",
            seed_urls=["https://a.example/article", "https://b.example/other"],
            history=history,
            active_branch_url="https://a.example/article",
            branch_steps=6,
        )
        self.assertEqual(summary["current"]["status"], "stalled")
        self.assertIn("stalled", summary["advice"].lower())

    def test_summarize_keeps_redirected_branch_active(self) -> None:
        summary = summarize_exploration_branches(
            current_url="https://timesaver.gg/guide",
            seed_urls=["https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes"],
            history=[
                {
                    "ok": True,
                    "action": "fill",
                    "branch_url": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
                }
            ],
            active_branch_url="https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
            branch_steps=1,
        )
        self.assertEqual(summary["current"]["status"], "active")
        self.assertEqual(summary["current"]["current_page"], "https://timesaver.gg/guide")
        self.assertIn("redirect", summary["advice"].lower())

    def test_menu_includes_swap_and_back(self) -> None:
        branch_info = {
            "alternatives": [{"url": "https://b.example/other", "label": "Other site"}],
            "can_back": True,
            "advice": "",
            "current": {"status": "stalled"},
        }
        menu = build_exploration_menu(controls=[], overlay_actions=[], branch_info=branch_info)
        actions = {row["action"] for row in menu}
        self.assertIn("swap_branch", actions)
        self.assertIn("back", actions)

    def test_browse_context_includes_branch_and_menu(self) -> None:
        snapshot = {
            "url": "https://a.example/",
            "title": "Example",
            "visible_text": "Patch notes for July 14.",
            "blocking_overlays": [],
            "interactables": [
                {"id": "btn-notes", "kind": "link", "text": "Patch notes", "href": "https://a.example/notes"},
            ],
        }
        payload = curate_browse_context(
            query="diablo patch notes",
            step_id="step_001",
            snapshot=snapshot,
            discovered_routes={"https://a.example/", "https://b.example/"},
            seed_urls=["https://a.example/", "https://b.example/"],
            candidates=[type("Row", (), {"title": "A"}), type("Row", (), {"title": "B"})],
            recent_history=[],
            active_branch_url="https://a.example/",
        )
        self.assertIn("branch", payload)
        self.assertIn("menu", payload)
        self.assertIn("explore_note", payload)
        self.assertIn("redirect", payload["explore_note"].lower())
        self.assertTrue(any(row.get("action") == "swap_branch" for row in payload["menu"]))


if __name__ == "__main__":
    unittest.main()
