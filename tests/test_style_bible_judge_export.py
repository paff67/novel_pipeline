from __future__ import annotations

import unittest

from novel_pipeline_stable.models import StyleBibleResult
from novel_pipeline_stable.style_bible_judge import _select_judge_projection_payload
from novel_pipeline_stable.style_bible_judge_export import build_judge_flat


class StyleBibleJudgeExportTest(unittest.TestCase):
    def test_build_judge_flat_repairs_shape_and_preserves_schema(self) -> None:
        final_record = {
            "style_id": "style.demo",
            "scope": "node.demo",
            "narrative_system": {
                "engine": [
                    {
                        "rule_id": "resource_pressure__r1",
                        "text": "人物要突破时先结算贷款和药费，再写行动选择。",
                        "_reasoning_ref": "reasoning_resource",
                        "evidence_refs": ["scene:0001_005"],
                        "trigger": "人物要突破时",
                        "constraint": "先结算贷款和药费",
                    },
                    {
                        "rule_id": "english_row",
                        "text": "When the scene starts, route to nothing.",
                        "_reasoning_ref": "reasoning_resource",
                        "evidence_refs": ["scene:0001_006"],
                        "trigger": "scene starts",
                        "constraint": "route to nothing",
                    },
                ],
                "pacing_rules": [],
                "plot_node_logic": [],
            },
            "expression_system": {
                "description_rules": [],
                "dialogue_rules": [],
                "characterization_rules": [],
                "sensory_rules": [],
            },
            "aesthetics_system": {
                "core_axes": [],
                "pressure_axes": [],
                "humor_recipe": [],
                "satire_targets": [],
                "nonstandard_xianxia_rules": [],
            },
            "voice_contract": {
                "register_mix": [],
                "negative_pitfalls": [],
            },
            "character_arc_rules": [],
            "worldbook_binding": {
                "routing_hints": [
                    {
                        "rule_id": "routing_01",
                        "text": "出现借贷、欠费或清债信号时检索资源压力规则集。",
                        "_reasoning_ref": "reasoning_resource",
                        "evidence_refs": ["scene:0001_007"],
                        "query_feature_matcher": "出现借贷、欠费或清债信号时",
                        "route_target_action": "检索资源压力规则集",
                    }
                ],
                "worldbook_worthy": [
                    {
                        "rule_id": "worldbook_01",
                        "text": "借贷会改变人物能否继续修炼。",
                        "_reasoning_ref": "reasoning_resource",
                        "evidence_refs": ["scene:0001_005"],
                        "trigger": "出现借贷时",
                        "constraint": "借贷改变修炼资格",
                    }
                ],
                "rag_worthy": [
                    {
                        "rule_id": "rag_01",
                        "text": "可检索原子：出现借贷修炼、补药断供或清债压力 -> 先结算欠款和现金流 -> 再决定继续、停下或转向打工。",
                        "_reasoning_ref": "reasoning_resource",
                        "evidence_refs": ["scene:0001_005"],
                        "trigger": "出现借贷修炼",
                        "constraint": "先结算欠款和现金流",
                    }
                ],
            },
            "negative_rules": [],
            "supporting_evidence": [],
        }
        source_bundle = {
            "scene_signal_samples": [
                {
                    "scene_id": "0001_005",
                    "scene_summary": "张羽因修仙借贷、药物维护和房租水电压力被债务压住。",
                }
            ]
        }
        reasoning_record = {
            "entries": [
                {
                    "reasoning_id": "reasoning_resource",
                    "claim": "资源压力先结算成本再推进。",
                    "evidence_refs": ["scene:0001_005", "scene:0001_007"],
                }
            ]
        }

        judge_flat = build_judge_flat(final_record, source_bundle, reasoning_record)

        StyleBibleResult.model_validate(judge_flat)
        self.assertEqual(len(judge_flat["narrative_system"]["engine"]), 1)
        self.assertTrue(judge_flat["narrative_system"]["engine"][0].startswith("当"))
        self.assertIn("路由到", judge_flat["worldbook_binding"]["routing_hints"][0])
        self.assertIn("规则", judge_flat["worldbook_binding"]["worldbook_worthy"][0])
        self.assertLessEqual(len(judge_flat["worldbook_binding"]["rag_worthy"][0]), 40)
        self.assertEqual(judge_flat["supporting_evidence"][0]["source_ref"], "scene:0001_005")

    def test_select_judge_projection_prefers_judge_flat(self) -> None:
        style_bible_payload = {"style_id": "style.demo", "scope": "node.demo"}
        export_flat_payload = {"style_id": "style.export", "scope": "node.demo"}
        judge_flat_payload = {"style_id": "style.judge", "scope": "node.demo"}

        payload, source = _select_judge_projection_payload(
            style_bible_payload=style_bible_payload,
            judge_flat_payload=judge_flat_payload,
            export_flat_payload=export_flat_payload,
        )

        self.assertIs(payload, judge_flat_payload)
        self.assertEqual(source, "judge_flat.json")


if __name__ == "__main__":
    unittest.main()
