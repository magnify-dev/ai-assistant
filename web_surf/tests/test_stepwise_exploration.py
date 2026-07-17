from __future__ import annotations

import tempfile
import threading
import unittest
from io import StringIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ui_test.browser_state import _enrich_interactables, filter_blocking_overlays
from ui_test.state_diff import diff_page_states
from web_surf import events
from web_surf.form_values import (
    enforce_adult_verification_values,
    fallback_form_values,
    form_context_fingerprint,
    looks_like_age_gate,
    needs_form_value_plan,
    plan_form_values,
    report_is_negative,
    sanitize_form_values,
    suggest_overlay_action,
    build_overlay_map,
    _pick_adult_year,
)
from web_surf.browser_explore import (
    _content_collect_key,
    _content_collect_signature,
    _discover_official_outbound,
    validate_overlay_action,
    _json_object,
    _redact_form_values,
    _sync_branch_navigation,
    explore_candidates_in_browser,
    origin_url,
    stdin_help_provider,
    validate_action,
)
from web_surf.context_curate import curate_browse_context
from web_surf.extract import extract_facts_from_page
from web_surf.fetch import PageResult
from web_surf.runner import run_web_research
from web_surf.store import (
    content_hash,
    load_visit_graph,
    record_visit,
    run_state_path,
    save_run_state,
    save_session_state,
    session_state_path,
)


class SnapshotTests(unittest.TestCase):
    def test_interactables_get_stable_ids_and_absolute_routes(self) -> None:
        raw = [
            {"kind": "link", "text": "Docs", "href": "/docs"},
            {"kind": "button", "text": "Open", "href": None},
        ]
        first = _enrich_interactables(raw, "https://example.com/start")
        second = _enrich_interactables(raw, "https://example.com/start")
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual(first[0]["href"], "https://example.com/docs")


class DecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            "interactables": [
                {
                    "id": "el_docs",
                    "kind": "link",
                    "text": "Docs",
                    "href": "https://example.com/docs",
                    "disabled": False,
                }
            ]
        }

    def test_only_snapshot_ids_are_accepted(self) -> None:
        action, error = validate_action(
            {"action": "click", "target_id": "invented"},
            self.snapshot,
            {"https://example.com/docs"},
        )
        self.assertIsNone(action)
        self.assertIn("current snapshot", error)

    def test_navigation_is_limited_to_discovered_routes(self) -> None:
        action, error = validate_action(
            {"action": "navigate", "url": "https://example.com/admin"},
            self.snapshot,
            {"https://example.com/docs"},
        )
        self.assertIsNone(action)
        self.assertIn("not discovered", error)
        valid, error = validate_action(
            {"action": "navigate", "url": "https://example.com/docs"},
            self.snapshot,
            {"https://example.com/docs"},
        )
        self.assertEqual(valid["action"], "click")
        self.assertEqual(valid["target_id"], "el_docs")
        self.assertEqual(error, "")

    def test_navigate_with_target_id_is_coerced_to_click(self) -> None:
        action, error = validate_action(
            {"action": "navigate", "target_id": "el_docs", "url": ""},
            self.snapshot,
            set(),
        )
        self.assertEqual(error, "")
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "el_docs")

    def test_navigate_to_visible_link_href_is_coerced_to_click(self) -> None:
        action, error = validate_action(
            {"action": "navigate", "url": "https://example.com/docs"},
            self.snapshot,
            set(),
        )
        self.assertEqual(error, "")
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "el_docs")

    def test_navigate_same_page_anchor_is_coerced_to_click(self) -> None:
        snapshot = {
            "url": "https://news.example.com/article/patch-notes",
            "interactables": [
                {
                    "id": "el_patch",
                    "kind": "link",
                    "text": "3.1.1 patch",
                    "href": "https://news.example.com/article/patch-notes#3.1.1",
                    "disabled": False,
                }
            ],
        }
        action, error = validate_action(
            {
                "action": "navigate",
                "url": "https://news.example.com/article/patch-notes#3.1.1",
            },
            snapshot,
            {"https://news.example.com/article/patch-notes"},
        )
        self.assertEqual(error, "")
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "el_patch")

    def test_report_blocked_while_overlay_present(self) -> None:
        snapshot = {
            "url": "https://example.com/",
            "blocking_overlays": [{"id": "gate", "text": "Age Verification"}],
            "interactables": [],
        }
        action, error = validate_action(
            {"action": "report", "reason": "found it"},
            snapshot,
            set(),
        )
        self.assertIsNone(action)
        self.assertIn("overlay", error.lower())

    def test_extract_allowed_with_cookie_overlay_when_content_visible(self) -> None:
        snapshot = {
            "url": "https://gaming.example/patch-notes",
            "visible_text": "Patch notes for July 14, 2026. " * 40,
            "blocking_overlays": [
                {"id": "cookie", "text": "We use cookies for consent", "role": "dialog"},
            ],
            "interactables": [],
        }
        action, error = validate_action(
            {"action": "extract", "reason": "collect patch notes"},
            snapshot,
            set(),
        )
        self.assertEqual(error, "")
        self.assertEqual(action["action"], "extract")

    def test_extract_blocked_for_age_gate_even_with_visible_text(self) -> None:
        snapshot = {
            "url": "https://news.example/patch-notes",
            "visible_text": "Patch notes preview " * 30,
            "blocking_overlays": [{"id": "gate", "text": "Age Verification required"}],
            "interactables": [],
        }
        action, error = validate_action(
            {"action": "extract", "reason": "collect patch notes"},
            snapshot,
            set(),
        )
        self.assertIsNone(action)
        self.assertIn("overlay", error.lower())

    def test_origin_and_relaxed_json_fallbacks(self) -> None:
        self.assertEqual(origin_url("https://Example.com/a?q=1"), "https://example.com/")
        self.assertEqual(_json_object('```json\n{"action":"wait"}\n```')["action"], "wait")
        self.assertIsNone(_json_object("not json"))

    def test_cross_origin_actions_and_invented_deep_routes_are_rejected(self) -> None:
        external = {
            "interactables": [
                {
                    "id": "el_external",
                    "kind": "link",
                    "text": "External",
                    "href": "https://other.example/path",
                    "disabled": False,
                }
            ]
        }
        action, error = validate_action(
            {"action": "click", "target_id": "el_external"},
            external,
            {"https://example.com/"},
            {"https://example.com/"},
        )
        self.assertIsNone(action)
        self.assertIn("allowed candidate origins", error)

        action, error = validate_action(
            {"action": "navigate", "url": "https://example.com/seed/deep"},
            self.snapshot,
            {"https://example.com/"},
            {"https://example.com/"},
        )
        self.assertIsNone(action)
        self.assertIn("not discovered", error)

    def test_discovered_official_links_expand_allowed_origins(self) -> None:
        allowed = {"https://guide.example/"}
        routes = {"https://guide.example/wiki"}
        snapshot = {
            "interactables": [
                {
                    "id": "el_official",
                    "kind": "link",
                    "text": "Official patch notes",
                    "href": "https://news.publisher.com/en-us/article/1/product-patch-notes",
                }
            ]
        }
        promoted = _discover_official_outbound(
            snapshot,
            {"publisher.com"},
            allowed,
            routes,
        )
        self.assertEqual(len(promoted), 1)
        self.assertIn("https://news.publisher.com/", allowed)
        action, error = validate_action(
            {"action": "navigate", "url": promoted[0]},
            snapshot,
            routes,
            allowed,
        )
        self.assertIsNotNone(action, error)
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "el_official")

    def test_branch_redirect_expands_allowed_origins(self) -> None:
        allowed = {"https://news.blizzard.com/"}
        routes = {"https://news.blizzard.com/en-us/article/1/patch-notes"}
        snapshot = {
            "url": "https://timesaver.gg/guide",
            "discovered_routes": ["https://timesaver.gg/guide/patch-notes"],
            "interactables": [
                {
                    "id": "el_guide",
                    "kind": "link",
                    "text": "Patch notes",
                    "href": "https://timesaver.gg/guide/patch-notes",
                }
            ],
        }
        expanded = _sync_branch_navigation(
            page_url="https://timesaver.gg/guide",
            snapshot=snapshot,
            allowed_origins=allowed,
            discovered_routes=routes,
        )
        self.assertTrue(expanded)
        self.assertIn("https://timesaver.gg/", allowed)
        action, error = validate_action(
            {"action": "click", "target_id": "el_guide"},
            snapshot,
            routes,
            allowed,
        )
        self.assertIsNotNone(action, error)

    def test_helper_response_is_read_from_matching_ndjson(self) -> None:
        stream = StringIO(
            '{"type":"web_help_response","request_id":"other","ok":true,"content":"skip"}\n'
            '{"type":"web_help_response","request_id":"h1","ok":true,"content":"Click Docs"}\n'
        )
        with patch("web_surf.browser_explore.sys.stdin", stream):
            result = stdin_help_provider({"request_id": "h1"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], "Click Docs")

    def test_generated_value_key_is_resolved_without_model_value(self) -> None:
        snapshot = {
            "interactables": [
                {"id": "el_date", "kind": "input", "text": "", "disabled": False}
            ]
        }
        action, error = validate_action(
            {"action": "fill", "target_id": "el_date", "value_key": "birth_date"},
            snapshot,
            set(),
            form_values={"birth_date": "2000-01-01"},
        )
        self.assertEqual(error, "")
        self.assertEqual(action["value"], "2000-01-01")
        self.assertEqual(action["value_key"], "birth_date")

    def test_unknown_value_key_is_rejected(self) -> None:
        action, error = validate_action(
            {"action": "fill", "target_id": "el_docs", "value_key": "birth_date"},
            self.snapshot,
            set(),
            form_values={},
        )
        self.assertIsNone(action)
        self.assertIn("not available", error)

    def test_provide_values_action_is_validated(self) -> None:
        action, error = validate_action(
            {
                "action": "provide_values",
                "form_values": {"birth_date": "1990-01-01", "country": "Germany"},
                "reason": "Age gate",
            },
            self.snapshot,
            set(),
        )
        self.assertEqual(error, "")
        self.assertEqual(action["form_values"]["birth_date"], "1990-01-01")
        self.assertEqual(action["form_values"]["country"], "Germany")

    def test_verification_fields_reject_model_supplied_values(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Verify your age"}],
            "interactables": [{"id": "el_date", "kind": "input", "label": "Date of birth"}],
        }
        action, error = validate_action(
            {"action": "fill", "target_id": "el_date", "value": "2000-01-01"},
            snapshot,
            set(),
        )
        self.assertIsNone(action)
        self.assertIn("value_key", error)

    def test_fill_on_select_coerces_to_select_action(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Verify your age"}],
            "interactables": [
                {
                    "id": "el_year",
                    "kind": "select",
                    "name": "year",
                    "aria": "year",
                    "text": "year",
                    "disabled": False,
                }
            ],
        }
        action, error = validate_action(
            {"action": "fill", "target_id": "el_year", "value_key": "birth_year"},
            snapshot,
            set(),
            form_values={"birth_year": "1990"},
        )
        self.assertEqual(error, "")
        self.assertEqual(action["action"], "select")
        self.assertEqual(action["value"], "1990")

    def test_select_uses_name_for_stable_id(self) -> None:
        raw = [
            {
                "kind": "select",
                "name": "year",
                "aria": "year",
                "text": "year 2026 2025 2024 2023",
                "href": None,
            }
        ]
        items = _enrich_interactables(raw, "https://example.com/")
        self.assertEqual(items[0]["id"], "el-select-year")

    def test_detects_when_planning_is_needed(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Confirm your age"}],
            "interactables": [{"id": "dob", "kind": "textbox", "label": "Date of birth"}],
        }
        self.assertTrue(needs_form_value_plan(snapshot, {}))
        self.assertTrue(form_context_fingerprint(snapshot))

    def test_fallback_planner_generates_age_gate_values(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Age gate"}],
            "interactables": [
                {"id": "dob", "kind": "textbox", "label": "Date of birth", "action_hint": "fill"},
                {"id": "country", "kind": "combobox", "label": "Country", "action_hint": "select", "options": ["Germany", "France"]},
            ],
        }
        result = fallback_form_values(snapshot)
        self.assertIn("birth_date", result["form_values"])
        self.assertIn("country", result["form_values"])
        self.assertEqual(result["field_mapping"]["dob"], "birth_date")

    def test_report_is_negative_detects_failure_reasons(self) -> None:
        self.assertTrue(report_is_negative("patch notes are not available on this page"))
        self.assertTrue(report_is_negative("community feedback discussing patch 3.1.1, but no official patch notes"))
        self.assertFalse(report_is_negative("found patch notes for July 14, 2026"))

    def test_suggest_overlay_action_fills_birth_year_first(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Age Verification"}],
            "interactables": [
                {"id": "el-select-year", "kind": "select", "name": "year", "text": "year"},
                {"id": "el-select-month", "kind": "select", "name": "month", "text": "month"},
                {"id": "el-select-day", "kind": "select", "name": "day", "text": "day"},
            ],
        }
        action = suggest_overlay_action(
            snapshot,
            {"birth_year": "1990", "birth_month": "Jan", "birth_day": "1"},
            {
                "el-select-year": "birth_year",
                "el-select-month": "birth_month",
                "el-select-day": "birth_day",
            },
        )
        self.assertEqual(action["action"], "select")
        self.assertEqual(action["target_id"], "el-select-year")
        self.assertEqual(action["value_key"], "birth_year")

    def test_suggest_overlay_prefers_reject_over_cookie_policy_link(self) -> None:
        """Regression: nearby_text on legal links must not steal accept/reject matching."""
        snapshot = {
            "blocking_overlays": [
                {
                    "id": "privacy-banner",
                    "tag": "div",
                    "text": "By clicking Accept All Cookies, you agree to cookie storage. Cookie Policy",
                }
            ],
            "interactables": [
                {
                    "id": "el-link-cookie-policy",
                    "kind": "link",
                    "text": "Cookie Policy",
                    "href": "https://www.blizzard.com/cookies",
                    "landmark": "Privacy",
                    "nearby_text": "By clicking Accept All Cookies, you agree to cookie storage.",
                },
                {
                    "id": "el-button-reject-all",
                    "kind": "button",
                    "text": "Reject All",
                    "landmark": "Privacy",
                },
                {
                    "id": "el-button-accept-all-cookies",
                    "kind": "button",
                    "text": "Accept All Cookies",
                    "landmark": "Privacy",
                },
            ],
        }
        action = suggest_overlay_action(snapshot, {}, {})
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "el-button-reject-all")

    def test_suggest_overlay_detects_cookie_banner_without_blocking_overlay(self) -> None:
        snapshot = {
            "visible_text": (
                "By clicking Accept All Cookies, you agree to the storing of cookies "
                "on your device to enhance site navigation."
            ),
            "blocking_overlays": [],
            "interactables": [
                {
                    "id": "el-button-reject-all",
                    "kind": "button",
                    "text": "Reject All",
                    "landmark": "Privacy",
                },
                {
                    "id": "el-button-accept-all-cookies",
                    "kind": "button",
                    "text": "Accept All Cookies",
                    "landmark": "Privacy",
                },
            ],
        }
        action = suggest_overlay_action(snapshot, {}, {})
        self.assertEqual(action["action"], "click")
        self.assertEqual(action["target_id"], "el-button-reject-all")

    def test_normalize_gate_select_value_maps_numeric_month(self) -> None:
        from web_surf.form_values import normalize_gate_select_value

        field = {
            "name": "month",
            "options": ["month", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"],
        }
        self.assertEqual(normalize_gate_select_value("7", field), "Jul")
        self.assertEqual(normalize_gate_select_value("Jan", field), "Jan")

    def test_suggest_overlay_fills_month_after_year_despite_dom_defaults(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Age Verification"}],
            "interactables": [
                {"id": "el-select-year", "kind": "select", "name": "year", "text": "year", "value": "1990"},
                {"id": "el-select-month", "kind": "select", "name": "month", "text": "month", "value": "1"},
                {"id": "el-select-day", "kind": "select", "name": "day", "text": "day", "value": "1"},
                {
                    "id": "el-link-cookie-policy",
                    "kind": "link",
                    "text": "Cookie Policy",
                    "nearby_text": "Accept All Cookies",
                },
            ],
        }
        history = [
            {
                "ok": True,
                "action": "select",
                "target_id": "el-select-year",
            }
        ]
        action = suggest_overlay_action(
            snapshot,
            {"birth_year": "1990", "birth_month": "Jan", "birth_day": "1"},
            {
                "el-select-year": "birth_year",
                "el-select-month": "birth_month",
                "el-select-day": "birth_day",
            },
            recent_history=history,
        )
        self.assertEqual(action["action"], "select")
        self.assertEqual(action["target_id"], "el-select-month")
        self.assertEqual(action["value_key"], "birth_month")

    def test_suggest_overlay_skips_blocked_cookie_and_fills_age_gate(self) -> None:
        """After a failed cookie dismiss, fill age-gate fields instead of retrying the same click."""
        snapshot = {
            "blocking_overlays": [
                {"id": "privacy-banner", "text": "Cookie consent and privacy"},
                {"id": "age-gate", "text": "Age Verification date of birth"},
            ],
            "interactables": [
                {
                    "id": "el-button-allow-all",
                    "kind": "button",
                    "text": "Allow All",
                    "landmark": "Privacy",
                },
                {"id": "el-select-year", "kind": "select", "name": "year", "text": "year"},
                {"id": "el-select-month", "kind": "select", "name": "month", "text": "month"},
                {"id": "el-select-day", "kind": "select", "name": "day", "text": "day"},
            ],
        }
        history = [
            {
                "ok": False,
                "progress": False,
                "action": "click",
                "target_id": "el-button-allow-all",
                "reason": "Dismiss consent/cookie overlay (Allow All)",
            }
        ]
        action = suggest_overlay_action(
            snapshot,
            {"birth_year": "1990", "birth_month": "Jan", "birth_day": "1"},
            {
                "el-select-year": "birth_year",
                "el-select-month": "birth_month",
                "el-select-day": "birth_day",
            },
            recent_history=history,
            blocked_attempts=["click|el-button-allow-all|||"],
        )
        self.assertEqual(action["action"], "select")
        self.assertEqual(action["target_id"], "el-select-year")

    def test_suggest_overlay_skips_blocked_signature(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "privacy-banner", "text": "Cookie consent"}],
            "interactables": [
                {"id": "reject", "kind": "button", "text": "Reject All", "landmark": "Privacy"},
                {"id": "accept", "kind": "button", "text": "Accept All Cookies", "landmark": "Privacy"},
            ],
        }
        action = suggest_overlay_action(
            snapshot,
            {},
            {},
            blocked_attempts=["click|reject|||"],
        )
        self.assertEqual(action["target_id"], "accept")

    def test_summarize_overlay_actions_maps_modal_controls(self) -> None:
        from web_surf.form_values import summarize_overlay_actions

        snapshot = {
            "blocking_overlays": [{"id": "privacy-banner", "text": "Cookie consent"}],
            "interactables": [
                {"id": "reject", "kind": "button", "text": "Reject All", "landmark": "Privacy"},
                {"id": "accept", "kind": "button", "text": "Accept All Cookies", "landmark": "Privacy"},
                {"id": "policy", "kind": "link", "text": "Cookie Policy", "href": "/cookies"},
            ],
        }
        summary = summarize_overlay_actions(snapshot)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["kind"], "cookie")
        intents = {row["intent"] for row in summary[0]["actions"]}
        self.assertIn("reject", intents)
        self.assertIn("accept", intents)
        self.assertNotIn("policy", intents)

    def test_build_overlay_map_includes_i_accept_on_wowhead_style_banner(self) -> None:
        snapshot = {
            "blocking_overlays": [
                {
                    "id": "div-1",
                    "role": "dialog",
                    "label": "We Care About Your Privacy",
                    "text": "Selecting I Accept enables tracking. Reject All",
                }
            ],
            "interactables": [
                {
                    "id": "el-button-i-accept",
                    "kind": "button",
                    "text": "I Accept",
                    "landmark": "Your Privacy Choices",
                    "rect": {"x": 100, "y": 500, "width": 80, "height": 32},
                },
                {
                    "id": "el-button-reject-all",
                    "kind": "button",
                    "text": "Reject All",
                    "landmark": "Your Privacy Choices",
                    "rect": {"x": 200, "y": 500, "width": 80, "height": 32},
                },
                {
                    "id": "el-button-show-purposes",
                    "kind": "button",
                    "text": "Show Purposes",
                    "landmark": "Your Privacy Choices",
                },
            ],
        }
        overlay_map = build_overlay_map(snapshot)
        ids = {row["id"] for row in overlay_map["elements"]}
        self.assertIn("el-button-i-accept", ids)
        self.assertIn("el-button-reject-all", ids)
        reject = next(row for row in overlay_map["elements"] if row["id"] == "el-button-reject-all")
        self.assertEqual(reject["intent"], "reject")
        accept = next(row for row in overlay_map["elements"] if row["id"] == "el-button-i-accept")
        self.assertEqual(accept["intent"], "accept")
        self.assertIn("rect", reject)

    def test_validate_overlay_action_rejects_non_map_target(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Cookie consent"}],
            "interactables": [
                {"id": "reject", "kind": "button", "text": "Reject All", "landmark": "Privacy"},
                {"id": "nav-home", "kind": "link", "text": "Home", "href": "/"},
            ],
        }
        ok, _ = validate_overlay_action(
            {"action": "click", "target_id": "reject", "reason": "dismiss"},
            snapshot,
        )
        self.assertEqual(ok["target_id"], "reject")
        blocked, err = validate_overlay_action(
            {"action": "click", "target_id": "nav-home", "reason": "wrong"},
            snapshot,
        )
        self.assertIsNone(blocked)
        self.assertIn("overlay_map", err)

    def test_fallback_planner_maps_year_month_day_selects(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Age gate"}],
            "interactables": [
                {"id": "year", "kind": "select", "name": "year", "aria": "year", "action_hint": "select"},
                {"id": "month", "kind": "select", "name": "month", "aria": "month", "action_hint": "select"},
                {"id": "day", "kind": "select", "name": "day", "aria": "day", "action_hint": "select"},
            ],
        }
        result = fallback_form_values(snapshot)
        self.assertEqual(result["form_values"]["birth_year"], "1990")
        self.assertEqual(result["form_values"]["birth_month"], "Jan")
        self.assertEqual(result["form_values"]["birth_day"], "1")
        self.assertEqual(result["field_mapping"]["year"], "birth_year")

    def test_pick_adult_year_rejects_recent_options(self) -> None:
        self.assertEqual(_pick_adult_year(["2026", "2025", "1990", "1985"]), "1990")
        self.assertEqual(_pick_adult_year(["2026", "2025", "2020"]), "1990")

    def test_enforce_adult_verification_values_clamps_too_young_year(self) -> None:
        snapshot = {
            "interactables": [
                {
                    "id": "year",
                    "kind": "select",
                    "name": "year",
                    "options": ["2026", "2025", "1990", "1985"],
                }
            ]
        }
        enforced = enforce_adult_verification_values({"birth_year": "2026"}, snapshot=snapshot)
        self.assertEqual(enforced["birth_year"], "1990")

    def test_looks_like_age_gate_from_year_month_day_fields(self) -> None:
        snapshot = {
            "interactables": [
                {"id": "y", "kind": "select", "name": "year"},
                {"id": "m", "kind": "select", "name": "month"},
                {"id": "d", "kind": "select", "name": "day"},
            ]
        }
        self.assertTrue(looks_like_age_gate(snapshot))

    def test_looks_like_age_gate_ignores_embedded_game_widgets(self) -> None:
        snapshot = {
            "interactables": [
                {
                    "id": "el-select-difficulty",
                    "kind": "select",
                    "aria": "Difficulty",
                    "options": ["Easy", "Hard"],
                },
                {"id": "el-input-email", "kind": "input", "label": "Email Address", "name": "email"},
            ]
        }
        self.assertFalse(looks_like_age_gate(snapshot))

    def test_filter_blocking_overlays_drops_video_trailers(self) -> None:
        overlays = [
            {"id": "div-1", "text": "Diablo 4: Lord of Hatred Launch Trailer WATCH NEXT Play Video 2:32"},
            {"id": "privacy-banner", "text": "We use cookies. Accept All Cookies or Reject All."},
        ]
        filtered = filter_blocking_overlays(overlays)
        self.assertEqual([item["id"] for item in filtered], ["privacy-banner"])

    def test_needs_form_value_plan_false_for_video_overlay_and_game_fields(self) -> None:
        snapshot = {
            "blocking_overlays": [
                {"id": "div-1", "text": "Diablo 4: Lord of Hatred Launch Trailer WATCH NEXT Play Video 2:32"},
            ],
            "interactables": [
                {
                    "id": "el-select-difficulty",
                    "kind": "select",
                    "aria": "Difficulty",
                    "options": ["Easy", "Hard"],
                },
                {"id": "el-input-email", "kind": "input", "label": "Email Address", "name": "email"},
            ],
        }
        self.assertFalse(needs_form_value_plan(snapshot, {}))

    def test_suggest_overlay_action_does_not_fill_difficulty_for_false_gate(self) -> None:
        snapshot = {
            "blocking_overlays": [
                {"id": "div-1", "text": "Diablo 4: Lord of Hatred Launch Trailer WATCH NEXT Play Video 2:32"},
            ],
            "interactables": [
                {
                    "id": "el-select-difficulty",
                    "kind": "select",
                    "aria": "Difficulty",
                    "options": ["Easy", "Hard"],
                },
                {"id": "el-input-email", "kind": "input", "label": "Email Address", "name": "email"},
            ],
        }
        action = suggest_overlay_action(
            snapshot,
            {"difficulty": "Hard", "email": "test@example.com"},
            {
                "el-select-difficulty": "difficulty",
                "el-input-email": "email",
            },
        )
        self.assertIsNone(action)

    def test_sanitize_form_values_normalizes_keys(self) -> None:
        values = sanitize_form_values({"Birth Date": "1990-01-01", "": "x", "note": ""})
        self.assertEqual(values, {"birth_date": "1990-01-01"})

    def test_plan_form_values_uses_provider(self) -> None:
        snapshot = {
            "blocking_overlays": [{"id": "gate", "text": "Verify age"}],
            "interactables": [{"id": "dob", "kind": "textbox", "label": "Birth date", "action_hint": "fill"}],
        }

        def provider(_context: dict) -> dict:
            return {
                "form_values": {"birth_date": "1992-03-04"},
                "field_mapping": {"dob": "birth_date"},
                "reasoning": "Adult birth date for age gate",
            }

        result = plan_form_values(query="pricing", snapshot=snapshot, provider=provider)
        self.assertEqual(result["form_values"]["birth_date"], "1992-03-04")
        self.assertEqual(result["field_mapping"]["dob"], "birth_date")


class ContentCollectTests(unittest.TestCase):
    def test_content_collect_key_stable_for_same_text(self) -> None:
        snapshot = {
            "url": "https://games.gg/news/patch/",
            "visible_text": "Diablo 4 patch 3.1.1 released July 14, 2026 with class fixes.",
        }
        first = _content_collect_key(snapshot, "patch notes July 14 2026")
        second = _content_collect_key(snapshot, "patch notes July 14 2026")
        self.assertEqual(first, second)
        self.assertIn("|", first)
        self.assertIn("games.gg", first)

    def test_content_collect_key_changes_when_text_changes(self) -> None:
        base = {
            "url": "https://games.gg/news/patch/",
            "visible_text": "Short summary only.",
        }
        expanded = {
            **base,
            "visible_text": "Short summary only. Full patch notes with detailed class fixes.",
        }
        self.assertNotEqual(
            _content_collect_key(base, "patch notes"),
            _content_collect_key(expanded, "patch notes"),
        )

    def test_content_collect_signature_includes_action(self) -> None:
        key = "https://example.com/|abc123"
        self.assertEqual(_content_collect_signature("extract", key), "extract|https://example.com/|abc123")

    def test_curate_browse_context_marks_report_ready_after_collect(self) -> None:
        payload = curate_browse_context(
            query="Diablo 4 patch notes July 14 2026",
            step_id="step_012",
            snapshot={
                "url": "https://games.gg/news/patch/",
                "title": "Patch notes",
                "visible_text": "Diablo 4 patch 3.1.1 released July 14, 2026.",
                "interactables": [],
            },
            discovered_routes=[],
            collected_evidence=[
                {
                    "url": "https://games.gg/news/patch/",
                    "step_id": "step_011",
                    "chars": 1200,
                }
            ],
            helper_guidance=[
                {
                    "step_id": "step_011",
                    "kind": "content_collected",
                    "instruction": "Use action=report now.",
                }
            ],
        )
        self.assertTrue(payload.get("report_ready"))
        self.assertIn("already collected", str(payload.get("evidence_collected") or "").lower())
        self.assertIn("report", str(payload.get("guidance") or []))


class StateDiffTests(unittest.TestCase):
    def test_progress_fingerprint_ignores_form_values(self) -> None:
        from ui_test.state_diff import progress_fingerprint

        before = {
            "url": "https://example.com/",
            "blocking_overlays": [{"id": "gate", "text": "Age gate"}],
            "interactables": [
                {"id": "year", "kind": "select", "disabled": False, "value": "2026"},
            ],
        }
        after = {
            **before,
            "interactables": [
                {"id": "year", "kind": "select", "disabled": False, "value": "1990"},
            ],
        }
        self.assertEqual(progress_fingerprint(before), progress_fingerprint(after))

    def test_is_no_progress_treats_successful_field_fill_as_progress(self) -> None:
        from ui_test.state_diff import is_no_progress

        before = {
            "url": "https://example.com/",
            "blocking_overlays": [{"id": "gate", "text": "Age gate"}],
            "interactables": [{"id": "year", "kind": "select", "value": "2026"}],
        }
        after = {
            **before,
            "interactables": [{"id": "year", "kind": "select", "value": "1990"}],
        }
        delta = diff_page_states(before, after)
        self.assertTrue(delta["meaningful_change"])
        self.assertFalse(is_no_progress(before, after, delta))

    def test_is_no_progress_when_nothing_changed(self) -> None:
        from ui_test.state_diff import is_no_progress

        snapshot = {
            "url": "https://example.com/",
            "blocking_overlays": [{"id": "gate", "text": "Age gate"}],
            "interactables": [{"id": "year", "kind": "select", "value": "1990"}],
        }
        delta = diff_page_states(snapshot, snapshot)
        self.assertTrue(is_no_progress(snapshot, snapshot, delta))

    def test_form_values_are_redacted_from_agent_snapshots(self) -> None:
        snapshot = {
            "visible_text": "Birth date: 2000-01-01",
            "interactables": [{"id": "date", "value": "2000-01-01"}],
        }
        result = _redact_form_values(snapshot, {"birth_date": "2000-01-01"})
        self.assertNotIn("2000-01-01", result["visible_text"])
        self.assertEqual(result["interactables"][0]["value"], "[user-provided]")

    def test_detects_new_modal_and_changed_controls(self) -> None:
        before = {
            "url": "https://example.com/",
            "visible_text": "Home",
            "interactables": [{"id": "menu", "kind": "button", "text": "Menu", "expanded": "false"}],
            "blocking_overlays": [],
        }
        after = {
            **before,
            "visible_text": "Home Confirm your age",
            "interactables": [
                {"id": "menu", "kind": "button", "text": "Menu", "expanded": "true"},
                {"id": "confirm", "kind": "button", "text": "Confirm"},
            ],
            "blocking_overlays": [{"id": "age-gate", "tag": "blz-age-gate", "text": "Confirm your age"}],
        }
        delta = diff_page_states(before, after)
        self.assertTrue(delta["meaningful_change"])
        self.assertEqual(delta["new_blockers"][0]["id"], "age-gate")
        self.assertEqual(delta["interactables_changed"][0]["fields"], ["expanded"])
        self.assertEqual(delta["interactables_added"][0]["id"], "confirm")


class PersistenceAndEventTests(unittest.TestCase):
    def test_session_run_graph_and_events_are_persistable_without_stdout(self) -> None:
        captured: list[dict] = []
        events.configure(emit_json=False, sink=captured.append)
        events.snapshot({"snapshot_id": "s1"})
        events.help_request({"request_id": "h1", "question": "Need context"})
        self.assertEqual([row["type"] for row in captured], ["web_snapshot", "web_help_request"])

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            save_run_state(project, "r1", {"status": "running"})
            save_session_state(project, "s1", {"history": []})
            record_visit(project, url="https://example.com/")
            record_visit(
                project,
                url="https://example.com/docs",
                source_url="https://example.com/",
                action="click",
                step_id="step_001",
            )
            self.assertTrue(run_state_path(project, "r1").is_file())
            self.assertTrue(session_state_path(project, "s1").is_file())
            graph = load_visit_graph(project)
            self.assertIn("https://example.com/docs", graph["nodes"])
            self.assertEqual(graph["edges"][0]["step_id"], "step_001")
        events.configure(emit_json=False)


class EvidenceTests(unittest.TestCase):
    def test_extracted_evidence_keeps_step_and_snapshot_source(self) -> None:
        parsed = {
            "page_summary": "Pricing",
            "facts": [{"field": "price", "value": "$10", "quote": "Price is $10"}],
        }
        with patch("web_surf.extract._ollama_json", return_value=parsed):
            facts, _ = extract_facts_from_page(
                page_text="The current Price is $10 per month.",
                page_url="https://example.com/pricing",
                page_title="Pricing",
                research_spec={"data_needed": ["price"]},
                ollama_url="http://ollama",
                model="model",
                source_session_id="web_session",
                source_step_id="step_004",
                source_snapshot_id="snap_abc",
            )
        self.assertEqual(facts[0]["source_session_id"], "web_session")
        self.assertEqual(facts[0]["source_step_id"], "step_004")
        self.assertEqual(facts[0]["source_snapshot_id"], "snap_abc")

    def test_extract_preview_reports_accepted_and_rejected_facts(self) -> None:
        parsed = {
            "page_summary": "Patch notes",
            "facts": [
                {"field": "date", "value": "July 14", "quote": "July 14, 2026"},
                {"field": "change", "value": "fixed bug", "quote": "not on page"},
            ],
        }
        previews: list[dict] = []
        with patch("web_surf.extract._ollama_json", return_value=parsed):
            with patch("web_surf.extract.events.extract_preview", side_effect=lambda payload: previews.append(payload)):
                facts, _ = extract_facts_from_page(
                    page_text="Patch notes for July 14, 2026 include balance changes.",
                    page_url="https://example.com/patch",
                    page_title="Patch",
                    research_spec={"data_needed": ["patch notes"]},
                    ollama_url="http://ollama",
                    model="model",
                )
        self.assertEqual(len(facts), 1)
        self.assertEqual(previews[-1]["accepted_count"], 1)
        self.assertEqual(previews[-1]["rejected_count"], 1)
        self.assertIn("July 14, 2026", previews[-1]["text_preview"])


class ExplorationSeedingTests(unittest.TestCase):
    def test_deep_search_results_are_navigable_start_points(self) -> None:
        """Seed URLs land directly on result pages instead of bare origins."""
        requests: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                requests.append(self.path)
                body = "<main><h1>Release notes</h1><p>Version 2.0 shipped with fixes.</p></main>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        candidate = SimpleNamespace(
            url=f"{base}/deep/release-notes",
            title="Release notes",
            snippet="Version 2.0",
            query="release notes",
        )

        def decide(_context: dict) -> dict:
            return {"action": "report", "note": "Notes visible", "reason": "Answer on page"}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    _, content, goal_met, _ = explore_candidates_in_browser(
                        query="latest release notes for the fixture",
                        candidates=[candidate],
                        project_path=Path(tmp),
                        max_visits=1,
                        max_steps=4,
                        decision_provider=decide,
                    )
                except Exception as exc:
                    if "Executable doesn't exist" in str(exc):
                        self.skipTest("Playwright Chromium is not installed")
                    raise
        finally:
            server.shutdown()
            server.server_close()

        non_asset_requests = [path for path in requests if path != "/favicon.ico"]
        self.assertEqual(non_asset_requests[0], "/deep/release-notes")
        self.assertTrue(goal_met)
        self.assertIn("Version 2.0", content)


class RunnerIntegrationTests(unittest.TestCase):
    def test_browser_runs_when_event_printing_is_disabled(self) -> None:
        candidate = SimpleNamespace(
            url="https://example.com/docs",
            title="Docs",
            snippet="Reference",
            query="widgets",
        )
        second_candidate = SimpleNamespace(
            url="https://example.org/deep/result",
            title="Other result",
            snippet="Another source",
            query="widgets",
        )
        text = "Widget documentation with enough source content."
        browser_page = PageResult(
            url=candidate.url,
            title=candidate.title,
            text=text,
            markdown=text,
            content_hash=content_hash(text),
            fetch_tier=2,
            evidence_context={"source_step_id": "step_002", "source_snapshot_id": "snap_2"},
        )
        with tempfile.TemporaryDirectory() as tmp, patch(
            "web_surf.runner.structure_research_spec",
            return_value={
                "summary": "widgets",
                "data_needed": ["docs"],
                "search_queries": ["widgets"],
                "max_pages": 2,
            },
        ), patch("web_surf.runner.web_search", return_value=[candidate, second_candidate]), patch(
            "web_surf.browser_explore.explore_candidates_in_browser",
            return_value=([browser_page], text, True),
        ) as browse, patch(
            "web_surf.runner.extract_facts_from_page",
            return_value=([], "Widget docs"),
        ), patch(
            "web_surf.runner.fetch_page_tier1",
        ) as direct_fetch, patch(
            "web_surf.runner._synthesize_answer", return_value="Answer"
        ):
            result = run_web_research(
                "widgets",
                project=tmp,
                emit_events=False,
                use_ollama=True,
                config={"ollama_model": "test"},
            )
        browse.assert_called_once()
        direct_fetch.assert_not_called()
        self.assertTrue(result.goal_met)
        self.assertEqual(result.pages_fetched, 1)

    def test_real_browser_walks_from_origin_through_human_links(self) -> None:
        requests: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                requests.append(self.path)
                pages = {
                    "/": '<main><h1>Fixture home</h1><a href="/docs">Documentation</a></main>',
                    "/docs": '<main><h1>Documentation</h1><a href="/target">Pricing facts</a></main>',
                    "/target": "<main><h1>Pricing</h1><p>The verified fixture price is $10.</p></main>",
                }
                body = pages.get(self.path.split("?", 1)[0], "not found")
                self.send_response(200 if self.path.split("?", 1)[0] in pages else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        # Seed with the site root: the run must still walk human links to the answer.
        candidate = SimpleNamespace(
            url=f"{base}/",
            title="Fixture home",
            snippet="Fixture pricing",
            query="fixture price",
        )

        def decide(context: dict) -> dict:
            url = context["snapshot"]["url"]
            if url.rstrip("/") == base:
                target = next(
                    item for item in context["snapshot"]["interactables"]
                    if item.get("text") == "Documentation"
                )
                return {"action": "click", "target_id": target["id"], "reason": "Open docs"}
            if url.endswith("/docs"):
                target = next(
                    item for item in context["snapshot"]["interactables"]
                    if item.get("text") == "Pricing facts"
                )
                return {"action": "click", "target_id": target["id"], "reason": "Open pricing"}
            return {"action": "report", "note": "Fixture price is visible", "reason": "Goal met"}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    pages, content, goal_met, metadata = explore_candidates_in_browser(
                        query="What is the fixture price?",
                        candidates=[candidate],
                        project_path=Path(tmp),
                        max_visits=1,
                        max_steps=6,
                        decision_provider=decide,
                        success_criteria=["Find the verified fixture price"],
                    )
                except Exception as exc:
                    if "Executable doesn't exist" in str(exc):
                        self.skipTest("Playwright Chromium is not installed")
                    raise
        finally:
            server.shutdown()
            server.server_close()

        non_asset_requests = [path for path in requests if path != "/favicon.ico"]
        self.assertEqual(non_asset_requests[0], "/")
        self.assertEqual(non_asset_requests[:3], ["/", "/docs", "/target"])
        self.assertTrue(goal_met)
        self.assertIn("$10", content)
        self.assertEqual(pages[0].url, f"{base}/target")
        self.assertEqual(metadata["unmet_criteria"], [])


if __name__ == "__main__":
    unittest.main()
