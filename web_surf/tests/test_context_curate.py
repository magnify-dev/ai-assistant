from __future__ import annotations

import unittest

from web_surf.agent_memory import commit_agent_memory
from web_surf.context_curate import (
    compact_history,
    compact_routes,
    curate_browse_context,
    curate_controls,
    curate_overlay_context,
    curate_text,
    normalize_decision,
)
from web_surf.page_match import page_has_goal_links, filter_text_by_date, page_contains_target_date, parse_target_dates


class ContextCurateTests(unittest.TestCase):
    def test_curate_controls_balances_overlay_goal_and_navigation(self) -> None:
        controls = curate_controls(
            [
                {"id": "footer-login", "kind": "link", "text": "Sign in", "href": "/login"},
                {"id": "overlay-ok", "kind": "button", "text": "Agree and continue"},
                {"id": "topic-link", "kind": "link", "text": "Product changelog", "href": "/changelog"},
                {"id": "nav-home", "kind": "link", "text": "Home", "href": "/", "landmark": "nav"},
            ],
            query="latest product changelog",
            has_overlay=True,
        )
        ids = [row["id"] for row in controls]
        self.assertIn("overlay-ok", ids)
        self.assertIn("topic-link", ids)
        self.assertIn("nav-home", ids)

    def test_curate_text_keeps_lead_even_without_query_overlap(self) -> None:
        text = curate_text(
            "Welcome to Example Corp. We build tools for everyone. Pricing is on another page.",
            query="unrelated astronomy facts",
            max_chars=300,
        )
        self.assertTrue(text.startswith("Welcome to Example Corp"))
        self.assertLessEqual(len(text), 300)

    def test_routes_keep_full_urls_for_multiple_origins(self) -> None:
        routes = compact_routes(
            {
                "https://a.example/docs",
                "https://b.example/guide",
                "https://a.example/pricing",
            }
        )
        self.assertEqual(routes[0], "https://a.example/docs")
        self.assertIn("https://b.example/guide", routes)

    def test_browse_context_uses_generic_schema(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "title": "Example",
            "visible_text": "Cookie notice. Product docs updated today.",
            "blocking_overlays": [{"id": "overlay-1", "text": "Cookie preferences"}],
            "interactables": [
                {"id": "btn-accept", "kind": "button", "text": "Accept"},
                {"id": "search-input", "kind": "input", "placeholder": "Search"},
            ],
        }
        payload = curate_browse_context(
            query="product documentation",
            step_id="step_001",
            snapshot=snapshot,
            discovered_routes={"https://example.com/docs"},
            accomplishment_steps=[
                {
                    "id": "s1",
                    "description": "Open docs",
                    "done_when": "on docs",
                    "status": "pending",
                },
                {
                    "id": "s2",
                    "description": "Report the answer",
                    "done_when": "done",
                    "status": "pending",
                },
            ],
            data_needed=["API endpoint"],
            success_criteria=["Find the docs page"],
        )
        self.assertIn("page", payload)
        self.assertIn("overlays", payload)
        self.assertIn("controls", payload)
        self.assertNotIn("blockers", payload)
        self.assertEqual(payload["controls"][0]["action"], "click")
        self.assertEqual(payload["goal"], "product documentation")
        self.assertIn("user_goal_steps", payload)
        self.assertEqual(payload["current_step"]["id"], "s1")
        self.assertEqual(payload["data_needed"], ["API endpoint"])
        self.assertFalse(payload["ready_to_report"])

    def test_browse_context_includes_age_gate_note(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "title": "Example",
            "visible_text": "Age Verification Please enter your date of birth.",
            "blocking_overlays": [{"id": "gate", "text": "Age Verification"}],
            "interactables": [
                {"id": "year", "kind": "select", "name": "year", "aria": "year"},
                {"id": "month", "kind": "select", "name": "month", "aria": "month"},
                {"id": "day", "kind": "select", "name": "day", "aria": "day"},
            ],
        }
        payload = curate_browse_context(
            query="patch notes",
            step_id="step_001",
            snapshot=snapshot,
            discovered_routes=set(),
        )
        self.assertIn("age_gate_note", payload)
        self.assertIn("too young", payload["age_gate_note"].lower())
        self.assertIn("form_fields", payload)
        self.assertEqual(payload["form_fields"][0]["action"], "select")

    def test_select_control_action_is_select_not_fill(self) -> None:
        controls = curate_controls(
            [
                {"id": "year", "kind": "select", "widget": "select", "name": "year", "text": "year"},
                {"id": "search", "kind": "input", "widget": "text", "placeholder": "Search"},
            ],
            query="patch notes",
            has_overlay=True,
        )
        by_id = {row["id"]: row for row in controls}
        self.assertEqual(by_id["year"]["action"], "select")
        self.assertEqual(by_id["year"]["widget"], "select")
        self.assertEqual(by_id["search"]["action"], "fill")

    def test_normalize_accepts_generic_malformed_shapes(self) -> None:
        fill = normalize_decision(
            {"next_action": [{"name": "provide_values", "value": {"search-input": "docs"}}]}
        )
        self.assertEqual(fill["action"], "fill")
        self.assertEqual(fill["target_id"], "search-input")

        semantic = normalize_decision(
            {"next_action": [{"name": "provide_values", "value": {"postal_code": "10115"}}]}
        )
        self.assertEqual(semantic["action"], "provide_values")

        click = normalize_decision(
            {
                "next_action": [
                    {"type": "click", "target": {"id": "btn-accept"}, "reason": "dismiss overlay"},
                ]
            }
        )
        self.assertEqual(click["action"], "click")
        self.assertEqual(click["target_id"], "btn-accept")

    def test_compact_history_is_short(self) -> None:
        lines = compact_history(
            [
                {"action": "click", "target_id": "btn-accept", "ok": True},
                {"action": "invalid", "ok": False, "error": "unsupported action"},
            ]
        )
        self.assertEqual(lines[0], "click:btn-accept ok")
        self.assertIn("fail", lines[1])

    def test_compact_history_marks_no_progress(self) -> None:
        lines = compact_history(
            [
                {
                    "action": "select",
                    "target_id": "el-select-year",
                    "ok": False,
                    "progress": False,
                    "error": "no progress — try a different control",
                }
            ]
        )
        self.assertIn("no_change", lines[0])

    def test_compact_history_shows_field_values_that_changed(self) -> None:
        lines = compact_history(
            [
                {
                    "action": "select",
                    "target_id": "el-select-year",
                    "value_key": "birth_year",
                    "ok": True,
                    "transition": {
                        "interactables_changed": [
                            {
                                "id": "el-select-year",
                                "fields": ["value"],
                                "after": {"value": "1990"},
                            }
                        ]
                    },
                }
            ]
        )
        self.assertIn("[birth_year]", lines[0])
        self.assertIn("set=1990", lines[0])
        self.assertIn("ok", lines[0])

    def test_compact_transition_includes_field_changes(self) -> None:
        from web_surf.context_curate import compact_transition

        payload = compact_transition(
            {
                "delta": {
                    "url_changed": False,
                    "visible_text_changed": False,
                    "interactables_changed": [
                        {
                            "id": "el-select-year",
                            "fields": ["value"],
                            "after": {"value": "1990"},
                        }
                    ],
                }
            }
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["fields_set"][0]["id"], "el-select-year")
        self.assertEqual(payload["fields_set"][0]["set"]["value"], "1990")

    def test_browse_context_includes_stuck_feedback(self) -> None:
        snapshot = {
            "url": "https://news.example.com/patch",
            "title": "Patch Notes",
            "visible_text": "Age Verification Please enter your date of birth.",
            "blocking_overlays": [{"id": "div-1", "label": "Age Verification"}],
            "interactables": [],
        }
        memory = [
            commit_agent_memory(
                step_id="step_003",
                decision={"action": "fill", "target_id": "div-1", "value_key": "2001"},
                outcome={"ok": False, "error": "fill target_id is not in the current snapshot"},
                snapshot=snapshot,
            )
        ]
        payload = curate_browse_context(
            query="patch notes",
            step_id="step_004",
            snapshot=snapshot,
            discovered_routes=set(),
            agent_memory=memory,
            blocked_attempts=["fill|div-1||2001|"],
            branch_steps=4,
            active_branch_url="https://news.example.com/patch",
        )
        self.assertIn("stuck", payload)
        self.assertIn("avoid", payload)
        self.assertIn("failed", payload)
        self.assertIn("branch_note", payload)
        self.assertIn("steps", payload)
        self.assertNotIn("blocked_attempts", payload)
        self.assertNotIn("agent_memory", payload)

    def test_compact_history_includes_labels_and_urls(self) -> None:
        lines = compact_history(
            [
                {
                    "action": "navigate",
                    "target_id": "el_x",
                    "target_label": "Patch Notes",
                    "target_href": "https://example.com/patch-notes",
                    "ok": False,
                    "error": "navigate URL was not discovered from search or a page snapshot",
                }
            ]
        )
        self.assertIn('"Patch Notes"', lines[0])
        self.assertIn("https://example.com/patch-notes", lines[0])
        self.assertIn("fail", lines[0])

    def test_normalize_handles_nested_typed_and_keyed_shapes(self) -> None:
        nested = normalize_decision(
            {"action": {"type": "click", "target_id": "btn-a"}, "reason": "dismiss"}
        )
        self.assertEqual(nested["action"], "click")
        self.assertEqual(nested["target_id"], "btn-a")

        typed = normalize_decision({"type": "navigate", "url": "https://example.com/a"})
        self.assertEqual(typed["action"], "navigate")
        self.assertEqual(typed["url"], "https://example.com/a")

        keyed = normalize_decision({"click": {"target_id": "btn-b"}})
        self.assertEqual(keyed["action"], "click")
        self.assertEqual(keyed["target_id"], "btn-b")

        keyed_url = normalize_decision({"navigate": "https://example.com/b"})
        self.assertEqual(keyed_url["action"], "navigate")
        self.assertEqual(keyed_url["url"], "https://example.com/b")

    def test_curate_controls_prioritizes_patch_notes_over_purchase(self) -> None:
        controls = curate_controls(
            [
                {"id": "buy", "kind": "button", "text": "Purchase Expansion"},
                {"id": "patch", "kind": "link", "text": "Diablo IV Patch Notes", "href": "/patch-notes"},
            ],
            query="diablo 4 patch notes 14.7.2026",
        )
        ids = [row["id"] for row in controls]
        self.assertLess(ids.index("patch"), ids.index("buy"))

    def test_browse_context_sets_overlay_required(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "title": "Example",
            "visible_text": "Cookie notice",
            "blocking_overlays": [{"id": "overlay-1", "text": "Cookie preferences"}],
            "interactables": [{"id": "btn-accept", "kind": "button", "text": "Accept"}],
        }
        payload = curate_browse_context(
            query="patch notes",
            step_id="step_001",
            snapshot=snapshot,
            discovered_routes=set(),
        )
        self.assertTrue(payload.get("overlay_required"))

    def test_browse_context_defers_report_ready_when_overlay_present(self) -> None:
        snapshot = {
            "url": "https://example.com/article",
            "title": "Article",
            "visible_text": "Article body",
            "blocking_overlays": [{"id": "overlay-1", "text": "Cookie preferences"}],
            "interactables": [
                {"id": "btn-reject", "kind": "button", "text": "Reject All", "landmark": "Privacy"},
            ],
        }
        payload = curate_browse_context(
            query="latest news",
            step_id="step_002",
            snapshot=snapshot,
            discovered_routes=set(),
            collected_evidence=[
                {"url": "https://example.com/article", "step_id": "step_001", "chars": 900},
            ],
        )
        self.assertNotIn("report_ready", payload)
        self.assertIn("overlay", str(payload.get("evidence_collected") or "").lower())
        self.assertTrue(any(row.get("id") == "btn-reject" for row in payload.get("overlay_map") or []))

    def test_curate_overlay_context_builds_menu_from_map(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "title": "Example",
            "blocking_overlays": [{"id": "overlay-1", "role": "dialog", "text": "We use cookies"}],
            "interactables": [
                {"id": "btn-accept", "kind": "button", "text": "I Accept", "landmark": "Privacy"},
                {"id": "btn-reject", "kind": "button", "text": "Reject All", "landmark": "Privacy"},
            ],
        }
        payload = curate_overlay_context(step_id="step_001", snapshot=snapshot)
        ids = {row["id"] for row in payload.get("overlay_map") or []}
        self.assertIn("btn-accept", ids)
        self.assertIn("btn-reject", ids)
        menu_ids = {row.get("target_id") for row in payload.get("menu") or []}
        self.assertEqual(ids, menu_ids)

    def test_page_has_goal_links_ignores_toc_when_target_date_section_present(self) -> None:
        snapshot = {
            "visible_text": (
                "3.1.1 Build #72805 (All Platforms) July 14, 2026 "
                "General bug fixes and balance updates for Season 9. "
                "3.0.4 Build #70000 (All Platforms) June 10, 2026 Older notes."
            ),
            "interactables": [
                {
                    "id": "patch-july",
                    "kind": "link",
                    "text": "3.1.1 Build #72805 (All Platforms) July 14, 2026",
                    "href": "#3.1.1",
                },
                {
                    "id": "patch-june",
                    "kind": "link",
                    "text": "3.0.4 Build #70000 (All Platforms) June 10, 2026",
                    "href": "#3.0.4",
                },
            ],
        }
        self.assertFalse(page_has_goal_links(snapshot, "diablo 4 patch notes 14.7.2026"))

    def test_filter_text_by_date_keeps_only_target_section(self) -> None:
        text = (
            "3.0.4 Build #70000 (All Platforms) June 10, 2026 "
            "Older season changes. "
            "3.1.1 Build #72805 (All Platforms) July 14, 2026 "
            "Season 9 balance updates and bug fixes."
        )
        filtered = filter_text_by_date(text, "diablo 4 patch notes 14.7.2026")
        self.assertIn("July 14, 2026", filtered)
        self.assertIn("Season 9 balance", filtered)
        self.assertNotIn("June 10, 2026", filtered)

    def test_browse_context_includes_target_dates_and_content_hint(self) -> None:
        snapshot = {
            "url": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
            "title": "Diablo IV Patch Notes",
            "visible_text": (
                "3.1.1 Build #72805 (All Platforms) July 14, 2026 "
                "Season 9 balance updates."
            ),
            "interactables": [],
        }
        payload = curate_browse_context(
            query="find diablo 4 patch notes for 14.7.2026",
            step_id="step_007",
            snapshot=snapshot,
            discovered_routes=set(),
        )
        self.assertEqual(payload["target_dates"], ["14.07.2026"])
        self.assertIn("content_on_page", payload)
        self.assertIn("July 14, 2026", payload["page"]["text"])

    def test_browse_context_includes_collapsed_sections_hint(self) -> None:
        snapshot = {
            "url": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes",
            "title": "Patch Notes",
            "visible_text": "3.1.1 Build July 14, 2026",
            "interactables": [
                {
                    "id": "patch-july",
                    "kind": "link",
                    "text": "3.1.1 Build July 14, 2026",
                    "href": "https://news.blizzard.com/en-us/article/24287406/diablo-iv-patch-notes#3.1.1",
                    "expands_section": True,
                    "collapsed": True,
                    "data_toggle": "collapse",
                }
            ],
        }
        payload = curate_browse_context(
            query="diablo 4 patch notes 14.7.2026",
            step_id="step_006",
            snapshot=snapshot,
            discovered_routes=set(),
        )
        self.assertIn("collapsed_sections", payload)
        self.assertIn("expand_note", payload)
        self.assertTrue(payload["collapsed_sections"][0]["collapsed"])

    def test_browse_context_includes_age_gate_note(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "title": "Example",
            "visible_text": "Age Verification Please enter your date of birth.",
            "blocking_overlays": [{"id": "gate", "text": "Age Verification"}],
            "interactables": [
                {"id": "year", "kind": "select", "name": "year", "aria": "year"},
                {"id": "month", "kind": "select", "name": "month", "aria": "month"},
                {"id": "day", "kind": "select", "name": "day", "aria": "day"},
            ],
        }
        payload = curate_browse_context(
            query="patch notes",
            step_id="step_001",
            snapshot=snapshot,
            discovered_routes=set(),
        )
        self.assertIn("age_gate_note", payload)
        self.assertIn("too young", payload["age_gate_note"].lower())

    def test_browse_context_strips_collaboration_wrapper_from_goal(self) -> None:
        wrapped = (
            "You are the local UI testing agent.\n\n"
            "Original user task:\n"
            "find diablo 4 patch notes for 14.7.2026"
        )
        payload = curate_browse_context(
            query=wrapped,
            step_id="step_001",
            snapshot={"url": "https://example.com/", "title": "Ex", "visible_text": "hello"},
            discovered_routes=set(),
        )
        self.assertEqual(payload["goal"], "find diablo 4 patch notes for 14.7.2026")

    def test_browse_context_includes_recency_note(self) -> None:
        snapshot = {
            "url": "https://www.example.com/news",
            "title": "News",
            "visible_text": "Latest headlines",
            "interactables": [],
        }
        payload = curate_browse_context(
            query="find the most recent news",
            step_id="step_008",
            snapshot=snapshot,
            discovered_routes=set(),
        )
        self.assertTrue(payload.get("recency_requirement"))
        self.assertIn("recency_note", payload)
        self.assertIn("NEWEST", payload["recency_note"])

    def test_browse_context_includes_agent_memory(self) -> None:
        from web_surf.agent_memory import commit_agent_memory

        memory = [
            commit_agent_memory(
                step_id="step_001",
                decision={"action": "click", "target_id": "btn-1", "reason": "accept cookies"},
                outcome={"ok": True},
                page_url="https://example.com/",
            )
        ]
        payload = curate_browse_context(
            query="patch notes",
            step_id="step_002",
            snapshot={"url": "https://example.com/", "title": "Ex", "visible_text": "hello"},
            discovered_routes=set(),
            agent_memory=memory,
        )
        self.assertIn("steps", payload)
        self.assertEqual(payload["steps"][0]["step"], "step_001")
        self.assertIn("accept cookies", payload["steps"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
