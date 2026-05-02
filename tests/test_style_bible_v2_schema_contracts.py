from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
from novel_pipeline_stable.config import StyleBibleReduceConfig
from novel_pipeline_stable.models import (
    NarrativeRuleItem,
    NegativeRuleItem,
    RoutingHintItem,
    ScalarRuleItem,
    StyleBibleBucketBatchMemo,
    StyleBibleBucketRuleCandidate,
    StyleBibleLocalReducerOutput,
    StyleBibleReasoningBundle,
    StyleBibleReasoningEntry,
    StyleBibleResultV2,
    WorldbookFactItem,
    style_bible_payload_to_flat,
)
from novel_pipeline_stable.style_bible_prompt_assembler import (
    assemble_bucket_synthesis_prompt,
    assemble_local_reducer_prompt,
    assemble_section_densify_prompt,
)
from novel_pipeline_stable.style_bible_reduction import _sanitize_style_bible_result


class StyleBibleV2SchemaContractsTest(unittest.TestCase):
    def test_bucket_rule_candidate_accepts_phase2_structured_fields(self) -> None:
        candidate = StyleBibleBucketRuleCandidate.model_validate(
            {
                "candidate_id": "conflict_escalation_01",
                "trigger_condition": "when the aftermath lands",
                "execution_action": "write the physical residue instead of abstract summary",
                "evidence_refs": ["scene:0141_003"],
            }
        )

        self.assertEqual(candidate.trigger_condition, "when the aftermath lands")
        self.assertEqual(candidate.execution_action, "write the physical residue instead of abstract summary")
        self.assertIn("when the aftermath lands", candidate.text)
        self.assertIn("write the physical residue instead of abstract summary", candidate.text)

    def test_bucket_batch_memo_accepts_scratchpad_alias_and_antipattern_codes(self) -> None:
        memo = StyleBibleBucketBatchMemo.model_validate(
            {
                "_scratchpad": [
                    {
                        "step": "1. lock source evidence",
                        "target_ref": "scene:0141_003",
                        "exact_quote": "the metal is still hot",
                        "structural_analysis": "close with residue rather than abstract sentiment",
                    }
                ],
                "memo_id": "combat_aftermath__batch_01__memo",
                "bucket_id": "combat_aftermath",
                "batch_id": "combat_aftermath__batch_01",
                "label": "Combat Aftermath",
                "axis_focus": ["combat_aftermath"],
                "chapter_ids": ["0141"],
                "item_ids": ["scene:0141_003"],
                "allowed_refs": ["scene:0141_003"],
                "rule_candidates": [
                    {
                        "candidate_id": "conflict_escalation_01",
                        "trigger_condition": "when the aftermath lands",
                        "execution_action": "write the physical residue instead of abstract summary",
                        "evidence_refs": ["scene:0141_003"],
                        "anti_pattern_codes": ["VAGUE_ROUTING"],
                    }
                ],
            }
        )

        self.assertEqual(memo.scratchpad[0].target_ref, "scene:0141_003")
        self.assertEqual(memo.rule_candidates[0].anti_pattern_codes, ["VAGUE_ROUTING"])

    def test_rule_item_accepts_trigger_constraint_shape(self) -> None:
        rule = NarrativeRuleItem.model_validate(
            {
                "rule_id": "engine_rule_01",
                "trigger": "when combat resolves",
                "constraint": "ground the close in physical residue instead of a summary paragraph",
                "_reasoning_ref": "reasoning_01",
                "evidence_refs": ["scene:0141_003"],
            }
        )

        self.assertEqual(rule.trigger, "when combat resolves")
        self.assertEqual(rule.constraint, "ground the close in physical residue instead of a summary paragraph")
        self.assertIn("when combat resolves", rule.text)
        self.assertIn("physical residue", rule.text)

    def test_rule_item_accepts_worldbook_and_negative_shapes(self) -> None:
        worldbook_rule = WorldbookFactItem.model_validate(
            {
                "rule_id": "worldbook_rule_01",
                "trigger": "when a pill furnace blowout leaves leaking residue behind",
                "constraint": "capture the accident as a reusable worldbook fact instead of transient flavor",
                "_reasoning_ref": "reasoning_worldbook_01",
                "evidence_refs": ["scene:0220_004"],
            }
        )
        negative_rule = NegativeRuleItem.model_validate(
            {
                "rule_id": "negative_rule_01",
                "forbidden_action": "rewrite survival pressure as abstract angst",
                "correction_guideline": "replace it with concrete resource, time, and cost pressure",
                "_reasoning_ref": "reasoning_negative_01",
                "evidence_refs": ["scene:0141_003"],
            }
        )

        self.assertIn("pill furnace blowout", worldbook_rule.text)
        self.assertIn("reusable worldbook fact", worldbook_rule.text)
        self.assertIn("abstract angst", negative_rule.text)
        self.assertIn("concrete resource", negative_rule.text)

    def test_rule_item_accepts_worldbook_trigger_condition_target_action_aliases(self) -> None:
        worldbook_rule = RoutingHintItem.model_validate(
            {
                "rule_id": "routing_rule_01",
                "trigger_condition": "when an approval, rejection, or intake notice appears",
                "target_action": "route to the institutional screening worldbook entry",
                "_reasoning_ref": "reasoning_worldbook_alias_01",
                "evidence_refs": ["scene:0100_001"],
            }
        )

        self.assertEqual(worldbook_rule.query_feature_matcher, "when an approval, rejection, or intake notice appears")
        self.assertEqual(worldbook_rule.route_target_action, "route to the institutional screening worldbook entry")
        self.assertIn("approval", worldbook_rule.text)
        self.assertIn("institutional screening", worldbook_rule.text)

    def test_local_reducer_output_accepts_cross_validation_scratchpad(self) -> None:
        reducer_output = StyleBibleLocalReducerOutput.model_validate(
            {
                "_scratchpad_cross_validation": [
                    {
                        "synthesis_step": "merge equivalent rules",
                        "source_memo_ids": ["combat_aftermath__memo"],
                        "extracted_common_mechanism": "convert grounded evidence into executable rules",
                        "matched_evidence_refs": ["scene:0141_003"],
                    }
                ],
                "reasoning": {
                    "reasoning_version": "v2.0",
                    "style_id": "style.demo",
                    "scope": "novel",
                    "entries": [],
                },
                "final": {
                    "style_id": "style.demo",
                    "scope": "novel",
                    "rule_rows": [],
                },
            }
        )

        self.assertEqual(reducer_output.scratchpad_cross_validation[0].matched_evidence_refs, ["scene:0141_003"])

    def test_flatten_supports_structured_v2_rule_payload(self) -> None:
        payload = {
            "style_id": "style.demo",
            "scope": "novel",
            "narrative_system": {
                "engine": [
                    {
                        "rule_id": "engine_rule_01",
                        "trigger": "when combat resolves",
                        "constraint": "ground the close in physical residue instead of a summary paragraph",
                        "_reasoning_ref": "reasoning_01",
                        "evidence_refs": ["scene:0141_003"],
                    }
                ],
                "perspective": None,
                "distance": None,
                "temporality": None,
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
                "narrator_voice": None,
                "inner_monologue_mode": None,
                "register_mix": [],
                "negative_pitfalls": [
                    {
                        "forbidden_action": "rewrite pressure as abstract angst",
                        "correction_guideline": "replace it with concrete resource pressure",
                        "_reasoning_ref": "reasoning_02",
                        "evidence_refs": ["scene:0141_003"],
                    }
                ],
            },
            "character_arc_rules": [],
            "worldbook_binding": {
                "rag_worthy": [
                    {
                        "rule_id": "worldbook_rule_01",
                        "trigger": "when a pill furnace blowout leaves leaking residue behind",
                        "constraint": "retrieve the alchemy accident entry as reusable worldbook fact",
                        "_reasoning_ref": "reasoning_03",
                        "evidence_refs": ["scene:0220_004"],
                    }
                ],
                "worldbook_worthy": [],
                "routing_hints": [],
            },
            "negative_rules": [
                {
                    "forbidden_action": "collapse the mechanism into impressionistic summary",
                    "correction_guideline": "return to trigger plus constraint",
                    "_reasoning_ref": "reasoning_04",
                    "evidence_refs": ["scene:0300_001"],
                }
            ],
            "supporting_evidence": [
                {
                    "claim": "combat endings must land through physical residue",
                    "evidence_text": "multiple samples close on heat, fragments, and residue",
                    "source_ref": "scene:0141_003",
                }
            ],
        }

        flattened = style_bible_payload_to_flat(payload)

        self.assertIn("when combat resolves", flattened["narrative_system"]["engine"][0])
        self.assertIn("physical residue", flattened["narrative_system"]["engine"][0])
        self.assertIn("abstract angst", flattened["voice_contract"]["negative_pitfalls"][0])
        self.assertIn("alchemy accident entry", flattened["worldbook_binding"]["rag_worthy"][0])
        self.assertIn("trigger plus constraint", flattened["negative_rules"][0])

    def test_client_blueprint_uses_alias_keys_for_local_reducer_scratchpad_and_reasoning_ref(self) -> None:
        reducer_blueprint = StableOpenAICompatibleStructuredClient._build_output_blueprint(StyleBibleLocalReducerOutput)
        batch_memo_blueprint = StableOpenAICompatibleStructuredClient._build_output_blueprint(StyleBibleBucketBatchMemo)

        self.assertIn("_scratchpad_cross_validation", reducer_blueprint)
        self.assertIn("_scratchpad", batch_memo_blueprint)
        self.assertIn("_reasoning_ref", reducer_blueprint["final"]["rule_rows"][0])
        self.assertIn("anti_pattern_codes", batch_memo_blueprint["rule_candidates"][0])

    def test_client_coerce_to_model_shape_preserves_alias_fields_for_local_reducer(self) -> None:
        payload = {
            "_scratchpad_cross_validation": [
                {
                    "synthesis_step": "merge equivalent rules",
                    "source_memo_ids": ["combat_aftermath__memo"],
                    "extracted_common_mechanism": "ground close through residue",
                    "matched_evidence_refs": ["scene:0141_003"],
                }
            ],
            "reasoning": {
                "reasoning_version": "v2.0",
                "style_id": "style.demo",
                "scope": "novel",
                "entries": [
                    {
                        "reasoning_id": "reasoning_01",
                        "bucket_id": "dark_humor",
                        "axis_ids": ["dark_humor"],
                        "claim": "make bureaucratic cruelty land through concrete procedure",
                        "observed_commonality": "the scene routes emotion through procedure",
                        "mechanism_inference": "procedure outruns sentiment",
                        "downstream_constraint": "always show the procedural surface first",
                        "evidence_refs": ["scene:0141_003"],
                        "anti_pattern_codes": ["none"],
                    }
                ],
            },
            "final": {
                "style_id": "style.demo",
                "scope": "novel",
                "rule_rows": [
                    {
                        "rule_id": "engine_rule_01",
                        "surface_path": "narrative_system.engine",
                        "trigger": "when combat resolves",
                        "constraint": "ground the close in physical residue",
                        "_reasoning_ref": "reasoning_01",
                        "evidence_refs": ["scene:0141_003"],
                        "anti_pattern_codes": ["none"],
                    }
                ],
            },
        }

        normalized = StableOpenAICompatibleStructuredClient._coerce_to_model_shape(
            payload,
            StyleBibleLocalReducerOutput,
        )

        self.assertIn("scratchpad_cross_validation", normalized)
        self.assertEqual(normalized["final"]["rule_rows"][0]["reasoning_ref"], "reasoning_01")

    def test_bucket_prompt_keeps_ref_redlines_and_refusal_strategy(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "style_bible_bucket_synthesis.md"
        content = prompt_path.read_text(encoding="utf-8")

        required_fragments = (
            "rule_candidates",
            "anti_pattern_codes",
            "XML",
            "ref",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, content)

    def test_local_reduce_prompt_drops_manual_json_contract_prose(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "style_bible_local_reduce.md"
        content = prompt_path.read_text(encoding="utf-8")

        required_fragments = (
            "runtime schema",
            "section_targets",
            "path_targets",
            "repair_request",
            "reasoning scratchpad",
            "canonical paths",
            "targeted repair",
            "标量路径只从当前候选与 alias 归一后的 canonical token 中选择",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, content)
        forbidden_fragments = (
            "contract_fragments",
            "minimum_valid_row",
            "forbidden_outputs",
            "JSON 结构",
        )
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, content)
        self.assertNotIn("worldbook_atom_candidates", content)

    def test_section_densify_prompt_drops_manual_json_contract_prose(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "style_bible_section_densify.md"
        content = prompt_path.read_text(encoding="utf-8")

        required_fragments = (
            "runtime schema",
            "static_context.target_path",
            "static_context.path_target",
            "missing_slots",
            "existing_rows",
            "reasoning scratchpad",
            "一条新 row 只核销一个 slot",
            "burned_reasoning_ids",
            "burned_evidence_refs",
            "burned_reasoning_ids",
            "禁止多 slot 大杂烩",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, content)
        forbidden_fragments = (
            "contract_fragments",
            "minimum_valid_row",
            "JSON 结构",
        )
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, content)
        self.assertNotIn("worldbook_atom_candidates", content)

    def test_bucket_prompt_antipattern_context_stays_within_hard_budget(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        assembly = assemble_bucket_synthesis_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["dark_humor", "institutional_absurdity"],
            static_axis_focus=["dark_humor", "institutional_absurdity"],
            prompt_bundle_xml="<bucket />",
            memo_id="dark_humor__b01__memo",
            batch_id="dark_humor__b01",
            label="dark humor",
            chapter_ids=["0433", "0434"],
            item_ids=["scene:0434_003"],
            allowed_refs=["scene:0434_003"],
            anti_pattern_token_budget=1600,
            max_anti_pattern_examples=6,
        )

        self.assertLessEqual(assembly.anti_pattern_token_estimate, assembly.anti_pattern_token_budget)
        self.assertGreater(len(assembly.selected_antipattern_codes), 0)

    def test_local_reducer_prompt_antipattern_context_stays_within_hard_budget(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        assembly = assemble_local_reducer_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["institutional_absurdity", "dark_humor"],
            local_reduce_bundle={"bucket_memo": {}, "memo_ref_pool": ["scene:0434_003"]},
            section_targets={
                "bucket_id": "dark_humor",
                "preferred_paths": ["aesthetics_system.humor_recipe", "voice_contract.narrator_voice"],
                "scalar_paths": ["voice_contract.narrator_voice"],
                "prompt_hints": ["Prefer deadpan delivery."],
            },
            path_targets=[
                {
                    "path": "aesthetics_system.humor_recipe",
                    "target_count": 2,
                    "max_new_rows": 1,
                    "retrieval_top_k": 8,
                    "downstream_shape": "trigger + constraint",
                    "slot_specs": [
                        {
                            "slot_id": "deadpan_absurd_humor",
                            "cue": "冷面荒诞笑法",
                            "canonical_description": "用一本正经的业务或流程口吻承载荒诞内容，而不是直接抖包袱。",
                            "downstream_shape": "trigger + constraint",
                            "fresh_evidence_required": False,
                        }
                    ],
                }
            ],
            repair_request={
                "mode": "repair",
                "requested_paths": ["voice_contract.narrator_voice", "aesthetics_system.humor_recipe"],
                "missing_scalar_paths": ["voice_contract.narrator_voice"],
                "underfilled_paths": [
                    {
                        "path": "aesthetics_system.humor_recipe",
                        "actual_count": 0,
                        "target_count": 1,
                        "deficit": 1,
                    }
                ],
                "existing_rows": [
                    {
                        "path": "aesthetics_system.humor_recipe",
                        "rule_id": "dark_humor__rule_01",
                        "text": "keep the delivery deadpan",
                    }
                ],
                "target_scalar_candidates": {
                    "voice_contract.narrator_voice": [
                        {"value": "deadpan_procedural", "count": 3, "source_refs": ["0433_0434"]}
                    ]
                },
                "enum_hints": {
                    "voice_contract.narrator_voice": ["deadpan_procedural"],
                },
                "enum_aliases": {
                    "voice_contract.narrator_voice": {
                        "deadpan": "deadpan_procedural",
                    }
                },
            },
            anti_pattern_token_budget=1600,
            max_anti_pattern_examples=6,
        )

        self.assertLessEqual(assembly.anti_pattern_token_estimate, assembly.anti_pattern_token_budget)
        self.assertGreater(len(assembly.selected_antipattern_codes), 0)
        surface_path_specs = assembly.user_payload["static_context"]["surface_path_specs"]
        self.assertTrue(surface_path_specs)
        self.assertEqual(
            {row["path"] for row in surface_path_specs},
            {"aesthetics_system.humor_recipe", "voice_contract.narrator_voice"},
        )
        narrator_voice_spec = next(row for row in surface_path_specs if row["path"] == "voice_contract.narrator_voice")
        self.assertIn("deadpan_procedural", narrator_voice_spec["enum_candidates"])
        self.assertEqual(narrator_voice_spec["value_aliases"]["deadpan"], "deadpan_procedural")
        self.assertEqual(narrator_voice_spec["rule_family"], "scalar")
        self.assertEqual(narrator_voice_spec["row_model"], "ScalarRuleItem")
        section_targets = assembly.user_payload["static_context"]["section_targets"]
        self.assertEqual(section_targets["bucket_id"], "dark_humor")
        self.assertIn("voice_contract.narrator_voice", section_targets["scalar_paths"])
        self.assertNotIn("contract_fragments", assembly.user_payload["static_context"])
        path_targets = assembly.user_payload["static_context"]["path_targets"]
        self.assertEqual(path_targets[0]["slot_specs"][0]["cue"], "冷面荒诞笑法")
        repair_request = assembly.user_payload["dynamic_context"]["repair_request"]
        self.assertEqual(repair_request["mode"], "repair")
        self.assertIn("voice_contract.narrator_voice", repair_request["missing_scalar_paths"])
        self.assertEqual(
            repair_request["target_scalar_candidates"]["voice_contract.narrator_voice"][0]["value"],
            "deadpan_procedural",
        )
        self.assertEqual(
            repair_request["enum_aliases"]["voice_contract.narrator_voice"]["deadpan"],
            "deadpan_procedural",
        )
        schema_text = json.dumps(assembly.response_model.model_json_schema(by_alias=True), ensure_ascii=False)
        self.assertIn("冷面荒诞笑法", schema_text)
        self.assertIn("用一本正经的业务或流程口吻承载荒诞内容", schema_text)
        self.assertIn("deadpan_procedural", schema_text)

    def test_bucket_prompt_static_context_stays_stable_across_runtime_changes(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        first = assemble_bucket_synthesis_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["dark_humor", "institutional_absurdity"],
            static_axis_focus=["institutional_absurdity", "dark_humor"],
            prompt_bundle_xml="<bucket batch='01' />",
            memo_id="dark_humor__b01__memo",
            batch_id="dark_humor__b01",
            label="dark humor",
            chapter_ids=["0433"],
            item_ids=["scene:0433_001"],
            allowed_refs=["scene:0433_001"],
        )
        second = assemble_bucket_synthesis_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["institutional_absurdity", "dark_humor"],
            static_axis_focus=["dark_humor", "institutional_absurdity"],
            prompt_bundle_xml="<bucket batch='02' />",
            memo_id="dark_humor__b02__memo",
            batch_id="dark_humor__b02",
            label="dark humor",
            chapter_ids=["0434"],
            item_ids=["scene:0434_002"],
            allowed_refs=["scene:0434_002"],
        )

        self.assertEqual(list(first.user_payload.keys()), ["static_context", "dynamic_context", "runtime_identifiers"])
        self.assertEqual(first.user_payload["static_context"], second.user_payload["static_context"])
        self.assertEqual(first.selected_antipattern_codes, second.selected_antipattern_codes)

    def test_local_reducer_prompt_sorts_axis_focus_for_static_prefix(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        first = assemble_local_reducer_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["institutional_absurdity", "dark_humor"],
            local_reduce_bundle={"bucket_memo": {"memo_id": "a"}},
        )
        second = assemble_local_reducer_prompt(
            prompt_dir=prompt_dir,
            bucket_id="dark_humor",
            axis_focus=["dark_humor", "institutional_absurdity"],
            local_reduce_bundle={"bucket_memo": {"memo_id": "b"}},
        )

        self.assertEqual(first.user_payload["static_context"], second.user_payload["static_context"])
        self.assertEqual(
            first.user_payload["static_context"]["axis_focus"],
            ["dark_humor", "institutional_absurdity"],
        )

    def test_section_densify_prompt_exposes_slots_retrieval_and_runtime_identifiers(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        assembly = assemble_section_densify_prompt(
            prompt_dir=prompt_dir,
            target_path="worldbook_binding.routing_hints",
            path_target={
                "path": "worldbook_binding.routing_hints",
                "target_count": 4,
                "max_new_rows": 2,
                "retrieval_top_k": 10,
                "bucket_allowlist": ["institutional_pipeline", "resource_pressure"],
                "downstream_shape": "matcher + route_target_action",
                "prompt_hints": ["Prefer actionable router rows."],
                "dedupe_threshold": 0.92,
                "slot_match_threshold": 0.8,
                "soft_slot_match_floor": 0.7,
                "max_gray_keep": 1,
                "enabled": True,
                "slot_specs": [
                    {
                        "slot_id": "procedural_notice_router",
                        "label": "Procedural Notice Router",
                        "cue": "流程通知路由",
                        "canonical_description": "当通知或审批决定下一步动作时，路由到制度流程规则。",
                        "downstream_shape": "matcher + route_target_action",
                        "fresh_evidence_required": True,
                    }
                ],
            },
            densify_bundle={
                "style_bible_id_hint": "style.demo",
                "scope_hint": "novel",
                "target_path": "worldbook_binding.routing_hints",
                "target_gap": {
                    "actual_count": 1,
                    "target_count": 4,
                    "deficit": 3,
                },
                "existing_rows": [
                    {
                        "path": "worldbook_binding.routing_hints",
                        "rule_id": "routing_rule_01",
                        "text": "When a notice or approval dictates the next move, route to the institutional workflow rules.",
                        "query_feature_matcher": "notice or approval dictates the next move",
                        "route_target_action": "route to the institutional workflow rules",
                    }
                ],
                "missing_slots": [
                    {
                        "slot_id": "repayment_gate_router",
                        "label": "Repayment Gate Router",
                        "cue": "回款卡点路由",
                        "canonical_description": "当回款窗口、现金流或赔偿结算决定下一步动作时，路由到回款与资源压力规则。",
                        "downstream_shape": "matcher + route_target_action",
                        "fresh_evidence_required": True,
                    }
                ],
                "retrieved_reasoning_entries": [
                    {
                        "reasoning_id": "reasoning_02",
                        "bucket_id": "resource_pressure",
                        "axis_ids": ["resource_pressure"],
                        "claim": "Debt service and cashflow decide whether the character can act.",
                        "observed_commonality": "Scenes advance only after repayment windows are surfaced.",
                        "mechanism_inference": "Turn repayment windows into routing triggers.",
                        "downstream_constraint": "Route cashflow gates to the repayment rule family.",
                        "evidence_refs": ["scene:0002_001"],
                        "retrieval_score": 0.96,
                        "matched_slot_ids": ["repayment_gate_router"],
                    }
                ],
                "grounding_ref_pool": ["scene:0002_001"],
                "source_bucket_ids": ["resource_pressure"],
                "burned_reasoning_ids": ["reasoning_legacy_01"],
                "burned_evidence_refs": ["scene:0001_001"],
                "notes": ["Fill the missing slot without rewriting existing rows."],
            },
            anti_pattern_token_budget=1600,
            max_anti_pattern_examples=6,
        )

        self.assertLessEqual(assembly.anti_pattern_token_estimate, assembly.anti_pattern_token_budget)
        self.assertGreater(len(assembly.selected_antipattern_codes), 0)
        self.assertEqual(
            assembly.user_payload["static_context"]["target_path"],
            "worldbook_binding.routing_hints",
        )
        self.assertEqual(
            assembly.user_payload["static_context"]["path_target"]["slot_specs"][0]["slot_id"],
            "procedural_notice_router",
        )
        self.assertEqual(
            assembly.user_payload["static_context"]["path_target"]["slot_specs"][0]["cue"],
            "流程通知路由",
        )
        self.assertEqual(
            set(assembly.user_payload["static_context"]["path_target"]["slot_specs"][0].keys()),
            {
                "slot_id",
                "cue",
                "canonical_description",
                "downstream_shape",
                "fresh_evidence_required",
            },
        )
        self.assertEqual(
            assembly.user_payload["static_context"]["path_target"]["soft_slot_match_floor"],
            0.7,
        )
        self.assertEqual(
            assembly.user_payload["static_context"]["path_target"]["max_gray_keep"],
            1,
        )
        self.assertEqual(
            assembly.user_payload["dynamic_context"]["densify_bundle"]["missing_slots"][0]["slot_id"],
            "repayment_gate_router",
        )
        self.assertEqual(
            assembly.user_payload["dynamic_context"]["densify_bundle"]["missing_slots"][0]["cue"],
            "回款卡点路由",
        )
        self.assertEqual(
            set(assembly.user_payload["dynamic_context"]["densify_bundle"]["missing_slots"][0].keys()),
            {
                "slot_id",
                "cue",
                "canonical_description",
                "downstream_shape",
                "fresh_evidence_required",
            },
        )
        self.assertEqual(
            assembly.user_payload["dynamic_context"]["densify_bundle"]["retrieved_reasoning_entries"][0]["reasoning_id"],
            "reasoning_02",
        )
        self.assertEqual(
            assembly.user_payload["dynamic_context"]["densify_bundle"]["burned_reasoning_ids"],
            ["reasoning_legacy_01"],
        )
        self.assertEqual(
            assembly.user_payload["dynamic_context"]["densify_bundle"]["burned_evidence_refs"],
            ["scene:0001_001"],
        )
        self.assertEqual(
            assembly.user_payload["runtime_identifiers"]["missing_slot_ids"],
            ["repayment_gate_router"],
        )
        surface_path_specs = assembly.user_payload["static_context"]["surface_path_specs"]
        self.assertTrue(surface_path_specs)
        self.assertEqual([row["path"] for row in surface_path_specs], ["worldbook_binding.routing_hints"])
        self.assertEqual(surface_path_specs[0]["rule_family"], "routing_hint")
        self.assertEqual(surface_path_specs[0]["row_model"], "RoutingHintItem")
        self.assertNotIn("minimum_valid_row", surface_path_specs[0])
        schema_text = json.dumps(assembly.response_model.model_json_schema(by_alias=True), ensure_ascii=False)
        self.assertIn("回款卡点路由", schema_text)
        self.assertIn("回款窗口、现金流或赔偿结算决定下一步动作时", schema_text)

    def test_style_bible_reduce_config_is_hierarchical_only(self) -> None:
        self.assertEqual(StyleBibleReduceConfig().mode, "hierarchical")
        with self.assertRaises(ValidationError):
            StyleBibleReduceConfig(mode="global")

    def test_reducer_sanitizer_keeps_sparse_sections_without_backfill(self) -> None:
        reasoning_bundle = StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_01",
                    bucket_id="resource_pressure",
                    axis_ids=["resource_pressure", "labor_logic"],
                    claim="action is delayed until cost and repayment are exposed",
                    observed_commonality="multiple samples bind action to bills and repayment windows",
                    mechanism_inference="put action behind cost calculation and eligibility checks",
                    downstream_constraint="expose the bill or eligibility gate before the action fires",
                    evidence_refs=["scene:0001_001", "scene:0001_002"],
                    anti_pattern_codes=["KEYWORD_STUFFING"],
                ),
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_02",
                    bucket_id="institutional_pipeline",
                    axis_ids=["institutional_absurdity", "education_filter"],
                    claim="institutional cruelty lands through notices and approvals",
                    observed_commonality="multiple scenes package cruelty as a cold notice or review pipeline",
                    mechanism_inference="carry the mechanism with interfaces instead of abstract slogans",
                    downstream_constraint="show the notice, approval, review, or rejection touchpoint",
                    evidence_refs=["scene:0002_001", "scene:0002_002"],
                    anti_pattern_codes=["VAGUE_ROUTING"],
                ),
            ],
        )

        result = _sanitize_style_bible_result(
            StyleBibleResultV2(style_id="style.demo", scope="novel"),
            style_id_hint="style.demo",
            scope_hint="novel",
            reasoning_bundle=reasoning_bundle,
            memo_ref_pool={"scene:0001_001", "scene:0001_002", "scene:0002_001", "scene:0002_002"},
        )

        self.assertIsNone(result.narrative_system.perspective)
        self.assertIsNone(result.narrative_system.distance)
        self.assertIsNone(result.narrative_system.temporality)
        self.assertIsNone(result.voice_contract.inner_monologue_mode)
        self.assertEqual(result.expression_system.sensory_rules, [])
        self.assertEqual(result.worldbook_binding.routing_hints, [])
        self.assertEqual(result.negative_rules, [])
        self.assertEqual(result.supporting_evidence, [])
        flattened = style_bible_payload_to_flat(result.model_dump(mode="json", by_alias=True))
        self.assertNotIn("narrative_system", flattened)
        self.assertNotIn("worldbook_binding", flattened)

    def test_reducer_sanitizer_normalizes_verbose_scalar_optional_rule_to_enum(self) -> None:
        reasoning_bundle = StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_01",
                    bucket_id="resource_pressure",
                    axis_ids=["resource_pressure", "labor_logic"],
                    claim="the character converts emotion into cost accounting before acting",
                    observed_commonality="multiple scenes expose cost, risk, and repayment windows first",
                    mechanism_inference="internal thought behaves like a temporary budget sheet",
                    downstream_constraint="list the shortage or repayment window before the decision",
                    evidence_refs=["scene:0001_001"],
                    anti_pattern_codes=["KEYWORD_STUFFING"],
                )
            ],
        )
        partial = StyleBibleResultV2(style_id="style.demo", scope="novel")
        partial.voice_contract.inner_monologue_mode = NarrativeRuleItem.model_validate(
            {
                "rule_id": "inner_rule_01",
                "text": "before emotion surfaces, the inner thought should act like an ad hoc budget sheet",
                "trigger": "when the character receives new information or must choose quickly",
                "constraint": "emit sparse_inline and keep the internal thought compressed into comparable items",
                "_reasoning_ref": "reasoning_01",
                "evidence_refs": ["scene:0001_001"],
            }
        )

        result = _sanitize_style_bible_result(
            partial,
            style_id_hint="style.demo",
            scope_hint="novel",
            reasoning_bundle=reasoning_bundle,
            memo_ref_pool={"scene:0001_001"},
        )

        self.assertIsNotNone(result.voice_contract.inner_monologue_mode)
        self.assertIsInstance(result.voice_contract.inner_monologue_mode, ScalarRuleItem)
        self.assertEqual(result.voice_contract.inner_monologue_mode.text, "sparse_inline")
        flattened = style_bible_payload_to_flat(result.model_dump(mode="json", by_alias=True))
        self.assertEqual(flattened["voice_contract"]["inner_monologue_mode"], "sparse_inline")


if __name__ == "__main__":
    unittest.main()

