from __future__ import annotations

import tempfile
import threading
import unittest
from io import StringIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ui_test.browser_state import _enrich_interactables
from ui_test.state_diff import diff_page_states
from web_surf import events
from web_surf.form_values import (
    fallback_form_values,
    form_context_fingerprint,
    needs_form_value_plan,
    plan_form_values,
    sanitize_form_values,
)
from web_surf.browser_explore import (
    _json_object,
    _redact_form_values,
    explore_candidates_in_browser,
    origin_url,
    stdin_help_provider,
    validate_action,
)
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
        self.assertEqual(valid["url"], "https://example.com/docs")
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

    def test_navigate_to_visible_link_href_is_allowed(self) -> None:
        action, error = validate_action(
            {"action": "navigate", "url": "https://example.com/docs"},
            self.snapshot,
            set(),
        )
        self.assertEqual(error, "")
        self.assertEqual(action["url"], "https://example.com/docs")

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


class FormValuePlannerTests(unittest.TestCase):
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


class StateDiffTests(unittest.TestCase):
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
