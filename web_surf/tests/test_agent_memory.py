import unittest

from web_surf.agent_memory import (
    commit_agent_memory,
    compact_agent_memory_for_prompt,
    compact_avoid,
    compact_branch_note,
    compact_failed_steps,
    explain_failure,
    outcome_status,
    stuck_reason,
    summarize_step,
)


class AgentMemoryTests(unittest.TestCase):
    def test_outcome_status(self) -> None:
        self.assertEqual(outcome_status({"ok": True}), "ok")
        self.assertEqual(outcome_status({"ok": False, "progress": False}), "no_change")
        self.assertEqual(outcome_status({"ok": False}), "fail")

    def test_commit_agent_memory_includes_decision_and_outcome(self) -> None:
        entry = commit_agent_memory(
            step_id="step_002",
            decision={
                "action": "click",
                "target_id": "btn-1",
                "reason": "accept cookies",
            },
            outcome={
                "step_id": "step_002",
                "action": "click",
                "target_id": "btn-1",
                "ok": True,
                "url": "https://example.com/page",
            },
            page_url="https://example.com/page",
        )
        self.assertEqual(entry["step_id"], "step_002")
        self.assertEqual(entry["decision"]["action"], "click")
        self.assertEqual(entry["outcome"]["status"], "ok")
        self.assertIn("accept cookies", entry["summary"])

    def test_compact_agent_memory_for_prompt_truncates(self) -> None:
        entries = [
            commit_agent_memory(
                step_id=f"step_{index:03d}",
                decision={"action": "click", "reason": f"try {index}"},
                outcome={"ok": index % 2 == 0},
            )
            for index in range(1, 8)
        ]
        compact, note = compact_agent_memory_for_prompt(entries, limit=3)
        self.assertEqual(len(compact), 3)
        self.assertIn("Last 3 of 7", note)
        self.assertEqual(compact[-1]["step"], "step_007")
        self.assertIn("summary", compact[-1])
        self.assertNotIn("decision", compact[-1])

    def test_explain_failure_overlay_target(self) -> None:
        hint = explain_failure(
            {"action": "fill", "target_id": "div-1"},
            "fill target_id is not in the current snapshot",
            snapshot={
                "blocking_overlays": [{"id": "div-1", "text": "Age Verification"}],
                "interactables": [],
            },
        )
        self.assertIn("overlay container", hint)

    def test_compact_failed_steps_lists_failures(self) -> None:
        entries = [
            commit_agent_memory(
                step_id="step_003",
                decision={"action": "fill", "target_id": "div-1"},
                outcome={"ok": False, "error": "fill target_id is not in the current snapshot"},
                snapshot={"blocking_overlays": [{"id": "div-1"}], "interactables": []},
            )
        ]
        failed = compact_failed_steps(entries)
        self.assertEqual(len(failed), 1)
        self.assertIn("step_003", failed[0])
        self.assertIn("div-1", failed[0])

    def test_explain_failure_duplicate_collect(self) -> None:
        hint = explain_failure(
            {"action": "extract"},
            "extract rejected: this page content was already collected — use action=report",
        )
        self.assertIn("report", hint.lower())

    def test_compact_avoid_from_memory(self) -> None:
        entries = [
            commit_agent_memory(
                step_id="step_002",
                decision={"action": "extract"},
                outcome={"ok": False, "error": "clear blocking overlay first"},
            )
        ]
        avoid = compact_avoid(agent_memory=entries)
        self.assertTrue(any("extract" in line for line in avoid))

    def test_stuck_reason_for_overlay_without_controls(self) -> None:
        reason = stuck_reason(
            snapshot={
                "blocking_overlays": [{"id": "div-1", "label": "Age Verification"}],
                "interactables": [],
            }
        )
        self.assertIn("no controls", reason.lower())

    def test_compact_branch_note_includes_last_swap(self) -> None:
        note = compact_branch_note(
            {
                "current": {"label": "Patch Notes", "url": "https://news.example.com/patch"},
                "branch_steps": 6,
                "stall_count": 2,
            },
            [
                commit_agent_memory(
                    step_id="step_004",
                    decision={
                        "action": "swap_branch",
                        "url": "https://forum.example.com/thread",
                        "reason": "bypass age gate",
                    },
                    outcome={"ok": False, "error": "overlay blocks navigation"},
                )
            ],
        )
        self.assertIn("forum.example.com", note)
        self.assertIn("failed", note)

    def test_summarize_step_includes_failure_reason(self) -> None:
        summary = summarize_step(
            step_id="step_003",
            decision={"action": "navigate", "url": "https://example.com/old"},
            outcome={
                "ok": False,
                "progress": False,
                "error": "no progress — page state unchanged",
            },
            page_url="https://example.com/old",
        )
        self.assertIn("no_change", summary)
        self.assertIn("no progress", summary)


if __name__ == "__main__":
    unittest.main()
