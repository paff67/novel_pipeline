from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from novel_pipeline_stable.style_bible_inputs import StyleBibleInputBundle
from novel_pipeline_stable.style_bible_router import route_style_bible_inputs
from novel_pipeline_stable.style_bible_runtime_flags import (
    StyleBibleRuntimeFlags,
    load_style_bible_runtime_flags,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src" / "novel_pipeline_stable"


class SemanticCutoverTest(unittest.TestCase):
    def test_runtime_flags_drop_shadow_and_sidecar_switches(self) -> None:
        with patch.dict(
            os.environ,
            {
                "NOVEL_PIPELINE_ROUTER_SEMANTIC_CUTOVER_ENABLED": "true",
                "NOVEL_PIPELINE_ROUTER_LEXICAL_FALLBACK_ENABLED": "false",
                "NOVEL_PIPELINE_STYLE_BIBLE_SELECTIVE_CUTOVER_TARGET": "router",
            },
            clear=False,
        ):
            flags = load_style_bible_runtime_flags()

        payload = flags.as_dict()
        self.assertTrue(payload["router_semantic_cutover_enabled"])
        self.assertFalse(payload["router_lexical_fallback_enabled"])
        self.assertEqual(payload["selective_cutover_target"], "router")
        self.assertNotIn("semantic_shadow_enabled", payload)
        self.assertNotIn("reducer_semantic_sidecar_enabled", payload)
        self.assertNotIn("judge_semantic_sidecar_enabled", payload)

    def test_router_cutover_still_reports_semantic_router_decision(self) -> None:
        routed_index = route_style_bible_inputs(
            StyleBibleInputBundle(
                fact_rows=[],
                style_rows=[],
                chapter_rows=[],
                plot_rows=[],
                entity_rows=[],
                canon_index={},
                style_index={},
                story_node_scope=None,
            ),
            runtime_flags=StyleBibleRuntimeFlags(
                router_semantic_cutover_enabled=True,
                selective_cutover_target="router",
            ),
        )
        payload = routed_index.model_dump(mode="json")

        self.assertEqual(payload["coverage_summary"]["final_decision_source"], "semantic_router_cutover")
        self.assertTrue(payload["coverage_summary"]["feature_flags"]["router_semantic_cutover_enabled"])
        self.assertNotIn("semantic_shadow_enabled", payload["coverage_summary"]["feature_flags"])

    def test_semantic_cutover_sources_drop_legacy_shadow_and_ragas_surfaces(self) -> None:
        evaluator_source = (SRC_DIR / "style_bible_evaluator.py").read_text(encoding="utf-8")
        cli_source = (SRC_DIR / "cli.py").read_text(encoding="utf-8")
        runtime_flags_source = (SRC_DIR / "style_bible_runtime_flags.py").read_text(encoding="utf-8")
        full_rules_text = (ROOT_DIR / "config" / "style_bible_eval_rules.toml").read_text(encoding="utf-8")
        mini_rules_text = (ROOT_DIR / "config" / "style_bible_eval_rules_mini.toml").read_text(encoding="utf-8")

        self.assertNotIn("semantic_sidecar", evaluator_source)
        self.assertNotIn("_looks_generic", evaluator_source)
        self.assertNotIn("_is_actionable", evaluator_source)
        self.assertNotIn("evaluate-style-bible-ragas", cli_source)
        self.assertNotIn("semantic_shadow_enabled", runtime_flags_source)
        self.assertNotIn("[[required_keyword_groups]]", full_rules_text)
        self.assertNotIn("[generic_language]", full_rules_text)
        self.assertNotIn("[[required_keyword_groups]]", mini_rules_text)
        self.assertNotIn("[generic_language]", mini_rules_text)


if __name__ == "__main__":
    unittest.main()
