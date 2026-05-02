from __future__ import annotations

import json
import unittest
from pathlib import Path

from novel_pipeline_stable.style_bible_prompt_assembler import (
    assemble_local_reducer_prompt,
    assemble_section_densify_prompt,
)


class StyleBiblePromptContractGenerationTest(unittest.TestCase):
    def test_local_reduce_uses_native_surface_specs_without_manual_contract_fragments(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        assembly = assemble_local_reducer_prompt(
            prompt_dir=prompt_dir,
            bucket_id="institutional_pipeline",
            axis_focus=["institutional_pipeline"],
            local_reduce_bundle={"bucket_memo": {}, "memo_ref_pool": ["scene:0001_001"]},
            section_targets={
                "bucket_id": "institutional_pipeline",
                "preferred_paths": ["worldbook_binding.routing_hints"],
                "scalar_paths": [],
                "prompt_hints": ["Prefer workflow-grounded routing rows."],
            },
            path_targets=[
                {
                    "path": "worldbook_binding.routing_hints",
                    "target_count": 2,
                    "max_new_rows": 1,
                    "retrieval_top_k": 8,
                    "downstream_shape": "matcher + route_target_action",
                    "prompt_hints": ["Prefer approval and notice checkpoints."],
                    "slot_specs": [
                        {
                            "slot_id": "procedural_notice_router",
                            "cue": "流程通知路由",
                            "canonical_description": "当通知、审批或表单先于人物情绪推进冲突时，优先路由到制度流程条目。",
                            "downstream_shape": "matcher + route_target_action",
                            "fresh_evidence_required": True,
                        }
                    ],
                }
            ],
        )

        self.assertNotIn("contract_fragments", assembly.user_payload["static_context"])
        self.assertNotIn("minimum_valid_row", assembly.user_payload["static_context"]["surface_path_specs"][0])
        self.assertEqual(
            assembly.user_payload["static_context"]["surface_path_specs"][0]["row_model"],
            "RoutingHintItem",
        )

        schema_text = json.dumps(assembly.response_model.model_json_schema(by_alias=True), ensure_ascii=False)
        self.assertIn("流程通知路由", schema_text)
        self.assertIn("当通知、审批或表单先于人物情绪推进冲突时", schema_text)

    def test_local_reduce_scalar_response_model_uses_native_enum_schema(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        assembly = assemble_local_reducer_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["dark_humor"],
            local_reduce_bundle={"bucket_memo": {}, "memo_ref_pool": ["scene:0001_001"]},
            section_targets={
                "bucket_id": "dark_humor",
                "preferred_paths": [],
                "scalar_paths": ["voice_contract.narrator_voice"],
                "prompt_hints": ["Prefer deadpan procedural voice."],
            },
        )

        schema = assembly.response_model.model_json_schema(by_alias=True)
        schema_text = json.dumps(schema, ensure_ascii=False)
        self.assertIn("deadpan_procedural", schema_text)
        self.assertIn("仅允许输出这些 canonical token", schema_text)

    def test_section_densify_response_model_injects_missing_slot_semantics(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        assembly = assemble_section_densify_prompt(
            prompt_dir=prompt_dir,
            target_path="worldbook_binding.routing_hints",
            path_target={
                "path": "worldbook_binding.routing_hints",
                "target_count": 4,
                "max_new_rows": 2,
                "retrieval_top_k": 10,
                "downstream_shape": "matcher + route_target_action",
                "prompt_hints": ["Prefer grounded router rows."],
                "slot_specs": [
                    {
                        "slot_id": "procedural_notice_router",
                        "cue": "流程通知路由",
                        "canonical_description": "当通知或审批先于人物情绪推进冲突时，路由到制度流程规则。",
                        "downstream_shape": "matcher + route_target_action",
                        "fresh_evidence_required": True,
                    }
                ],
            },
            densify_bundle={
                "target_path": "worldbook_binding.routing_hints",
                "target_gap": {"actual_count": 1, "target_count": 4, "deficit": 3},
                "existing_rows": [],
                "missing_slots": [
                    {
                        "slot_id": "repayment_gate_router",
                        "cue": "回款卡点路由",
                        "canonical_description": "当回款窗口、现金流或赔偿结算决定下一步动作时，路由到回款与资源压力条目。",
                        "downstream_shape": "matcher + route_target_action",
                        "fresh_evidence_required": True,
                    }
                ],
                "retrieved_reasoning_entries": [],
                "grounding_ref_pool": [],
                "source_bucket_ids": ["resource_pressure"],
                "burned_reasoning_ids": [],
                "burned_evidence_refs": [],
            },
        )

        self.assertNotIn("contract_fragments", assembly.user_payload["static_context"])
        schema_text = json.dumps(assembly.response_model.model_json_schema(by_alias=True), ensure_ascii=False)
        self.assertIn("回款卡点路由", schema_text)
        self.assertIn("回款窗口、现金流或赔偿结算决定下一步动作", schema_text)


if __name__ == "__main__":
    unittest.main()
