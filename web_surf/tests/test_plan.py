from __future__ import annotations

import unittest

from web_surf.plan import (
    compact_plan_for_prompt,
    fallback_accomplishment_steps,
    infer_step_completion,
    mark_step_done,
    normalize_accomplishment_steps,
    plan_progress,
)


class AccomplishmentPlanTests(unittest.TestCase):
    def test_fallback_includes_site_and_report(self) -> None:
        steps = fallback_accomplishment_steps(
            "go to wowhead and find wow mists of pandaria and copy the latest news"
        )
        blob = " ".join(step["description"].lower() for step in steps)
        self.assertIn("wowhead", blob)
        self.assertTrue(
            any(
                word in step["description"].lower()
                for step in steps
                for word in ("latest", "newest", "listing")
            )
        )
        self.assertTrue(any("report" in step["description"].lower() for step in steps))
        # Prefer listing discovery over a pre-chosen article.
        self.assertTrue(any("listing" in step["description"].lower() for step in steps))

    def test_normalize_fills_ids_and_status(self) -> None:
        steps = normalize_accomplishment_steps(
            [
                {"description": "Open Wowhead", "done_when": "On wowhead.com"},
                "Extract the latest news",
            ],
            query="copy latest wowhead news",
        )
        self.assertGreaterEqual(len(steps), 2)
        self.assertTrue(steps[0]["id"])
        self.assertEqual(steps[0]["status"], "pending")
        self.assertTrue(any("report" in str(s.get("description") or "").lower() for s in steps))

    def test_normalize_strips_homepage_and_fluff(self) -> None:
        steps = normalize_accomplishment_steps(
            [
                {"id": "s1", "description": "Open Wowhead homepage", "done_when": "On homepage"},
                {"id": "s2", "description": "Verify source credibility", "done_when": "Credible"},
                {"id": "s3", "description": "Extract latest news", "done_when": "Copied"},
            ],
            query="wowhead latest news",
        )
        blob = " ".join(f"{s['description']} {s['done_when']}" for s in steps).lower()
        self.assertNotIn("homepage", blob)
        self.assertNotIn("credibility", blob)
        self.assertTrue(any("extract" in s["description"].lower() for s in steps))

    def test_plan_progress_and_ready_to_report(self) -> None:
        steps = normalize_accomplishment_steps(
            [
                {"id": "s1", "description": "Open site", "done_when": "on site"},
                {"id": "s2", "description": "Extract facts", "done_when": "collected"},
                {"id": "s3", "description": "Report the answer", "done_when": "done"},
            ],
            query="test",
        )
        progress = plan_progress(steps)
        self.assertEqual(progress["current"]["id"], "s1")
        self.assertFalse(progress["ready_to_report"])
        mark_step_done(steps, "s1")
        mark_step_done(steps, "s2")
        progress = plan_progress(steps)
        self.assertTrue(progress["ready_to_report"])
        self.assertEqual(progress["current"]["id"], "s3")

    def test_infer_marks_extract_and_report(self) -> None:
        steps = fallback_accomplishment_steps("go to example.com and copy the latest news")
        infer_step_completion(
            steps,
            action="extract",
            page_relevant=True,
            evidence_collected=True,
            on_preferred_source=True,
        )
        progress = plan_progress(steps)
        self.assertTrue(progress["ready_to_report"] or any(s["status"] == "done" for s in steps))
        infer_step_completion(steps, action="report", reported=True)
        self.assertTrue(plan_progress(steps)["all_done"] or any(
            "report" in str(s.get("description") or "").lower() and s.get("status") == "done"
            for s in steps
        ))

    def test_compact_plan_for_prompt(self) -> None:
        steps = fallback_accomplishment_steps("find the latest patch notes")
        payload = compact_plan_for_prompt(steps)
        self.assertIn("user_goal_steps", payload)
        self.assertIn("current_step", payload)
        self.assertIn("plan_note", payload)
        self.assertFalse(payload["ready_to_report"])


if __name__ == "__main__":
    unittest.main()
