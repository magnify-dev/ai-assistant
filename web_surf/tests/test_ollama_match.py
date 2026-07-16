from __future__ import annotations

import unittest

from ui_test.ollama import _model_loaded, _models_equivalent, _normalize_model_ref


class OllamaModelMatchTests(unittest.TestCase):
    def test_normalize_model_ref_adds_latest_tag(self) -> None:
        self.assertEqual(_normalize_model_ref("qwen3"), "qwen3:latest")

    def test_models_equivalent_requires_matching_tag(self) -> None:
        self.assertTrue(_models_equivalent("qwen3:14b", "qwen3:14b"))
        self.assertFalse(_models_equivalent("qwen3:14b", "qwen3:30b"))

    def test_model_loaded_requires_exact_variant(self) -> None:
        ps = {"models": [{"name": "qwen3:30b"}]}
        self.assertFalse(_model_loaded(ps, "qwen3:14b"))
        self.assertTrue(_model_loaded(ps, "qwen3:30b"))


if __name__ == "__main__":
    unittest.main()
