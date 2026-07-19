from __future__ import annotations

import unittest

from web_capture.stitch import (
    accumulate_scroll_capture,
    compute_unique_bands,
    is_stitched_capture,
    merge_scroll_captures,
)


def _element(
    element_id: str,
    *,
    y: float,
    text: str = "Item",
    href: str = "",
) -> dict:
    return {
        "id": element_id,
        "kind": "link",
        "text": text,
        "href": href,
        "rect": {"x": 10, "y": y, "width": 120, "height": 24},
        "locator_status": "unique",
    }


def _capture(*, scroll_y: float, elements: list[dict], height: float = 720) -> dict:
    return {
        "capture_id": f"cap_{int(scroll_y)}",
        "url": "https://example.test/news",
        "viewport": {
            "width": 1280,
            "height": height,
            "scroll_x": 0,
            "scroll_y": scroll_y,
            "document_width": 1280,
            "document_height": 3000,
        },
        "elements": elements,
        "summary": {"raw": len(elements), "visible": len(elements)},
        "screenshot": f"screenshots/news-{int(scroll_y)}.jpg",
    }


class ScrollStitchTests(unittest.TestCase):
    def test_unique_bands_match_measured_scroll_delta(self) -> None:
        bands = compute_unique_bands(
            [
                {"scroll_y": 0, "height": 720, "screenshot": "a.jpg"},
                {"scroll_y": 648, "height": 720, "screenshot": "b.jpg"},  # scrolled 648px
                {"scroll_y": 1296, "height": 720, "screenshot": "c.jpg"},
            ]
        )
        self.assertEqual(bands[0]["content_top"], 0)
        self.assertEqual(bands[0]["content_height"], 720)
        self.assertEqual(bands[0]["draw_top"], 0)
        # Second slice only draws the newly revealed 648px band.
        self.assertAlmostEqual(bands[1]["content_top"], 72)  # 720 - 648
        self.assertAlmostEqual(bands[1]["content_height"], 648)
        self.assertAlmostEqual(bands[1]["draw_top"], 720)
        self.assertAlmostEqual(bands[1]["delta_from_prev"], 648)
        self.assertAlmostEqual(bands[2]["draw_top"], 1368)

    def test_merge_places_elements_in_document_space(self) -> None:
        top = _capture(
            scroll_y=0,
            elements=[_element("el_a", y=200, text="Article A", href="/news/a")],
        )
        lower = _capture(
            scroll_y=700,
            elements=[_element("el_b", y=120, text="Article B", href="/news/b")],
        )
        merged = merge_scroll_captures([top, lower])
        assert merged is not None
        by_id = {item["id"]: item for item in merged["elements"]}
        self.assertAlmostEqual(by_id["el_a"]["rect"]["y"], 200)
        self.assertAlmostEqual(by_id["el_b"]["rect"]["y"], 820)
        self.assertTrue(is_stitched_capture(merged))
        self.assertEqual(merged["scroll_map"]["coords"], "document")
        self.assertEqual(merged["scroll_map"]["slice_count"], 2)
        # Second band starts at previous coverage end (720), not raw scroll_y.
        second = merged["scroll_map"]["slices"][1]
        self.assertAlmostEqual(second["draw_top"], 720)
        self.assertAlmostEqual(second["content_height"], 700)
        self.assertAlmostEqual(merged["scroll_map"]["canvas_height"], 1420)
        self.assertLess(merged["scroll_map"]["canvas_height"], 3000)

    def test_merge_skips_persistent_header(self) -> None:
        header = _element("el_header", y=12, text="Log in", href="/login")
        top = _capture(scroll_y=0, elements=[header, _element("el_a", y=300, text="Article A")])
        lower = _capture(
            scroll_y=700,
            elements=[
                _element("el_header_dup", y=12, text="Log in", href="/login"),
                _element("el_b", y=150, text="Article B"),
            ],
        )
        merged = merge_scroll_captures([top, lower])
        assert merged is not None
        texts = [item.get("text") for item in merged["elements"]]
        self.assertEqual(texts.count("Log in"), 1)
        self.assertIn("Article A", texts)
        self.assertIn("Article B", texts)
        self.assertGreater(merged["scroll_map"]["persistent_skipped"], 0)

    def test_merge_skips_overlap_band_elements(self) -> None:
        """Elements still in the previously covered viewport band are not re-added."""
        top = _capture(
            scroll_y=0,
            elements=[
                _element("el_a", y=100, text="Keep", href="/a"),
                _element("el_old", y=650, text="NearBottom", href="/old"),
            ],
        )
        # After scrolling 700px, viewport y=50 is still in the overlapped strip (content_top=20).
        lower = _capture(
            scroll_y=700,
            elements=[
                # Still in the overlapped strip (content_top ≈ 20) — must not be re-added.
                _element("el_old_again", y=0, text="NearBottom", href="/old"),
                _element("el_b", y=200, text="Fresh", href="/b"),
            ],
        )
        merged = merge_scroll_captures([top, lower])
        assert merged is not None
        texts = [item.get("text") for item in merged["elements"]]
        self.assertEqual(texts.count("NearBottom"), 1)
        self.assertIn("Fresh", texts)
        self.assertIn("Keep", texts)

    def test_single_slice_keeps_viewport_canvas(self) -> None:
        top = _capture(scroll_y=0, elements=[_element("el_a", y=200, text="A")])
        annotated = merge_scroll_captures([top])
        assert annotated is not None
        self.assertFalse(is_stitched_capture(annotated))
        self.assertEqual(annotated["scroll_map"]["coords"], "viewport")
        self.assertEqual(annotated["scroll_map"]["canvas_height"], 720)
        self.assertAlmostEqual(annotated["elements"][0]["rect"]["y"], 200)

    def test_remerge_of_stitched_capture_does_not_double_offset(self) -> None:
        top = _capture(scroll_y=0, elements=[_element("el_a", y=200, text="A")])
        lower = _capture(scroll_y=700, elements=[_element("el_b", y=120, text="B")])
        merged = merge_scroll_captures([top, lower])
        assert merged is not None
        y_before = {item["id"]: item["rect"]["y"] for item in merged["elements"]}
        again = merge_scroll_captures([merged, lower])
        assert again is not None
        y_after = {item["id"]: item["rect"]["y"] for item in again["elements"]}
        self.assertEqual(y_before, y_after)

    def test_accumulate_rejects_stitched_as_new_slice(self) -> None:
        cache: dict = {}
        top = _capture(scroll_y=0, elements=[_element("el_a", y=100, text="A")])
        lower = _capture(scroll_y=700, elements=[_element("el_b", y=100, text="B")])
        first = accumulate_scroll_capture(cache, url="https://example.test/news", capture=top)
        second = accumulate_scroll_capture(cache, url="https://example.test/news", capture=lower)
        assert second is not None
        self.assertTrue(is_stitched_capture(second))
        y_b = next(item["rect"]["y"] for item in second["elements"] if item["id"] == "el_b")
        self.assertAlmostEqual(y_b, 800)
        third = accumulate_scroll_capture(cache, url="https://example.test/news", capture=second)
        assert third is not None
        y_b2 = next(item["rect"]["y"] for item in third["elements"] if item["id"] == "el_b")
        self.assertAlmostEqual(y_b2, 800)
        self.assertEqual(len(cache["https://example.test/news"]["by_scroll"]), 2)

    def test_accumulate_skips_weaker_same_scroll_reprocess(self) -> None:
        cache: dict = {}
        rich = _capture(
            scroll_y=0,
            elements=[_element("el_a", y=10, text="A"), _element("el_b", y=40, text="B")],
        )
        poor = _capture(scroll_y=0, elements=[_element("el_a", y=10, text="A")])
        accumulate_scroll_capture(cache, url="https://example.test/news", capture=rich)
        accumulate_scroll_capture(cache, url="https://example.test/news", capture=poor)
        self.assertEqual(len(cache["https://example.test/news"]["by_scroll"]), 1)
        kept = cache["https://example.test/news"]["by_scroll"][0.0]
        self.assertEqual(len(kept["elements"]), 2)


if __name__ == "__main__":
    unittest.main()
