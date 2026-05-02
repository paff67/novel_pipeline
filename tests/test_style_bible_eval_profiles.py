from __future__ import annotations

import unittest
from pathlib import Path

from novel_pipeline_stable.style_bible_evaluator import (
    _evaluate_section_completeness,
    _load_rules,
)
from novel_pipeline_stable.style_bible_section_targets import load_style_bible_section_targets


class StyleBibleEvalProfilesTest(unittest.TestCase):
    def test_section_targets_load_default_bucket_mapping(self) -> None:
        targets = load_style_bible_section_targets()

        resource_targets = targets.targets_for_bucket("resource_pressure")
        self.assertEqual(targets.section_rules_path.name, "style_bible_eval_rules.toml")
        self.assertGreater(targets.repair_max_rounds, 0)
        self.assertIn("narrative_system.engine", resource_targets.preferred_paths)
        self.assertIn("voice_contract.inner_monologue_mode", resource_targets.scalar_paths)

    def test_section_targets_load_densify_path_targets(self) -> None:
        targets = load_style_bible_section_targets()

        register_target = targets.densify_target_for_path("voice_contract.register_mix")
        pitfall_target = targets.densify_target_for_path("voice_contract.negative_pitfalls")
        arc_target = targets.densify_target_for_path("character_arc_rules")
        negative_target = targets.densify_target_for_path("negative_rules")
        routing_target = targets.densify_target_for_path("worldbook_binding.routing_hints")
        rag_target = targets.densify_target_for_path("worldbook_binding.rag_worthy")
        worldbook_target = targets.densify_target_for_path("worldbook_binding.worldbook_worthy")

        self.assertTrue(targets.densify_enabled)
        self.assertIsNotNone(register_target)
        self.assertIsNotNone(pitfall_target)
        self.assertIsNotNone(arc_target)
        self.assertIsNotNone(negative_target)
        self.assertIsNotNone(routing_target)
        self.assertIsNotNone(rag_target)
        self.assertIsNotNone(worldbook_target)
        assert register_target is not None
        assert negative_target is not None
        assert routing_target is not None
        self.assertEqual(register_target.target_count, 4)
        self.assertGreaterEqual(len(register_target.slot_specs), 4)
        self.assertTrue(register_target.slot_specs[0].cue)
        self.assertEqual(negative_target.target_count, 6)
        self.assertGreaterEqual(len(negative_target.slot_specs), 6)
        self.assertEqual(routing_target.target_count, 4)
        self.assertEqual(routing_target.max_new_rows, 2)
        self.assertGreaterEqual(routing_target.retrieval_top_k, 1)
        self.assertGreaterEqual(len(routing_target.slot_specs), 4)
        self.assertEqual(routing_target.slot_specs[0].slot_id, "procedural_notice_router")
        self.assertTrue(routing_target.slot_specs[0].fresh_evidence_required)

    def test_eval_rules_are_semantic_only(self) -> None:
        config_dir = Path(__file__).resolve().parents[1] / "config"
        full_rules = _load_rules(config_dir / "style_bible_eval_rules.toml")
        mini_rules = _load_rules(config_dir / "style_bible_eval_rules_mini.toml")

        self.assertEqual(set(full_rules.weights), {"specificity", "actionability", "grounding"})
        self.assertEqual(set(mini_rules.weights), {"specificity", "actionability", "grounding"})
        self.assertIn("row_pass_score", full_rules.thresholds)
        self.assertIn("row_warn_score", full_rules.thresholds)
        self.assertIn("section_pass_ratio", full_rules.thresholds)
        self.assertIn("section_warn_ratio", full_rules.thresholds)
        self.assertNotIn("min_required_keyword_group_hits", full_rules.thresholds)
        self.assertNotIn("generic_language", full_rules.weights)

    def test_section_completeness_passes_mini_profile_but_not_full_profile(self) -> None:
        config_dir = Path(__file__).resolve().parents[1] / "config"
        full_rules = _load_rules(config_dir / "style_bible_eval_rules.toml")
        mini_rules = _load_rules(config_dir / "style_bible_eval_rules_mini.toml")
        payload = {
            "style_id": "style.demo",
            "scope": "novel",
            "narrative_system": {
                "engine": ["ground action in cost and constraint"],
                "perspective": "close_third_person",
                "distance": "close",
                "temporality": "linear_forward",
                "pacing_rules": ["move from bill to action"],
                "plot_node_logic": ["gate turning points with eligibility or repayment"],
            },
            "expression_system": {
                "description_rules": ["description stays concrete and procedural"],
                "dialogue_rules": ["dialogue exposes leverage or ranking"],
                "characterization_rules": ["characterization shows tradeoffs through behavior"],
                "sensory_rules": ["sensory detail lands through residue and bodily cost"],
            },
            "aesthetics_system": {
                "core_axes": ["resource pressure is always concrete"],
                "pressure_axes": ["cost and deadline pressure stay visible"],
                "humor_recipe": ["keep the joke deadpan and procedural"],
                "satire_targets": ["satirize the workflow rather than moralize it"],
                "nonstandard_xianxia_rules": ["treat advancement like paid labor"],
            },
            "voice_contract": {
                "narrator_voice": "deadpan_procedural",
                "inner_monologue_mode": "sparse_inline",
                "register_mix": ["mix procedural diction with survival slang"],
                "negative_pitfalls": ["avoid lyrical self-pity"],
            },
            "character_arc_rules": ["arc turns when the tradeoff cost changes"],
            "worldbook_binding": {
                "rag_worthy": ["retrieve debt and repayment constraints"],
                "worldbook_worthy": ["store stable institutional gate definitions"],
                "routing_hints": ["route intake notices to the admissions worldbook entry"],
            },
            "negative_rules": ["do not replace concrete pressure with vague angst"],
        }

        full_check = _evaluate_section_completeness(payload, full_rules)
        mini_check = _evaluate_section_completeness(payload, mini_rules)

        self.assertEqual(full_check["status"], "fail")
        self.assertEqual(mini_check["status"], "pass")


if __name__ == "__main__":
    unittest.main()
