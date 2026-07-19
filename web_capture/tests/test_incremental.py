from __future__ import annotations

import unittest

from web_capture.incremental import (
    diff_capture_elements,
    maybe_incremental_capture,
    patch_map_with_viewport,
    same_page_url,
    signature_overlap,
)
from web_capture.stitch import is_stitched_capture, merge_scroll_captures


def _element(element_id: str, *, y: float, text: str, href: str = "") -> dict:
    return {
        "id": element_id,
        "kind": "link",
        "text": text,
        "href": href or f"/{text.lower().replace(' ', '-')}",
        "aria": text,
        "rect": {"x": 10, "y": y, "width": 120, "height": 24},
        "locator_status": "unique",
        "ai_interactive": True,
        "ai_reason": "prior",
    }


def _capture(*, scroll_y: float, elements: list[dict], height: float = 720, url: str = "https://example.test/news") -> dict:
    return {
        "capture_id": f"cap_{int(scroll_y)}",
        "fingerprint": f"fp_{int(scroll_y)}",
        "url": url,
        "viewport": {
            "width": 1280,
            "height": height,
            "scroll_x": 0,
            "scroll_y": scroll_y,
            "document_width": 1280,
            "document_height": 4000,
        },
        "elements": elements,
        "summary": {"raw": len(elements), "visible": len(elements)},
        "screenshot": f"screenshots/news-{int(scroll_y)}.jpg",
    }


class IncrementalMapTests(unittest.TestCase):
    def test_same_page_url_ignores_hash(self) -> None:
        self.assertTrue(
            same_page_url("https://example.test/news#main", "https://example.test/news/")
        )

    def test_diff_detects_added_removed_moved(self) -> None:
        prev = [_element("a", y=10, text="A"), _element("b", y=40, text="B")]
        curr = [
            _element("a", y=10, text="A"),
            {**_element("b", y=80, text="B"), "id": "b2"},
            _element("c", y=120, text="C"),
        ]
        diff = diff_capture_elements(prev, curr)
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(diff["added"][0]["text"], "C")
        self.assertEqual(len(diff["moved"]), 1)
        self.assertEqual(len(diff["removed"]), 0)
        self.assertGreater(diff["overlap"], 0.5)

    def test_same_url_same_scroll_patches_without_full_redraw(self) -> None:
        previous = _capture(
            scroll_y=0,
            elements=[_element("a", y=20, text="Keep"), _element("old", y=200, text="Gone")],
        )
        current = _capture(
            scroll_y=0,
            elements=[
                _element("a", y=22, text="Keep"),
                _element("new", y=300, text="New"),
            ],
        )
        patched = maybe_incremental_capture(previous, current)
        texts = [item.get("text") for item in patched["elements"]]
        self.assertIn("Keep", texts)
        self.assertIn("New", texts)
        self.assertNotIn("Gone", texts)
        self.assertEqual(patched["map_update"]["mode"], "patch")
        kept = next(item for item in patched["elements"] if item["text"] == "Keep")
        self.assertTrue(kept.get("ai_interactive"))
        self.assertEqual(kept.get("ai_reason"), "prior")

    def test_url_change_forces_full_redraw(self) -> None:
        previous = _capture(scroll_y=0, elements=[_element("a", y=20, text="A")])
        current = _capture(
            scroll_y=0,
            elements=[_element("b", y=20, text="B")],
            url="https://example.test/other",
        )
        result = maybe_incremental_capture(previous, current)
        self.assertEqual(result["map_update"]["mode"], "full")
        self.assertEqual(result["map_update"]["reason"], "url_changed")
        self.assertEqual(result["elements"][0]["text"], "B")

    def test_stitched_map_absorbs_new_scroll_slice(self) -> None:
        top = _capture(scroll_y=0, elements=[_element("a", y=100, text="Top")])
        lower = _capture(scroll_y=700, elements=[_element("b", y=100, text="Mid")])
        stitched = merge_scroll_captures([top, lower])
        assert stitched is not None
        self.assertTrue(is_stitched_capture(stitched))

        newer = _capture(
            scroll_y=1400,
            elements=[
                _element("header", y=12, text="Log in", href="/login"),
                _element("c", y=80, text="Bottom"),
            ],
        )
        patched = patch_map_with_viewport(stitched, newer)
        assert patched is not None
        texts = [item.get("text") for item in patched["elements"]]
        self.assertIn("Top", texts)
        self.assertIn("Mid", texts)
        self.assertIn("Bottom", texts)
        self.assertEqual(patched["scroll_map"]["slice_count"], 3)
        bottom = next(item for item in patched["elements"] if item["text"] == "Bottom")
        self.assertAlmostEqual(bottom["rect"]["y"], 1480)

    def test_overlap_high_for_stable_page(self) -> None:
        elems = [_element("a", y=10, text="A"), _element("b", y=40, text="B")]
        self.assertGreaterEqual(signature_overlap(elems, elems), 0.99)


if __name__ == "__main__":
    unittest.main()
