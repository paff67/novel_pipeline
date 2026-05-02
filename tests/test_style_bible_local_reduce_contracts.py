from __future__ import annotations

import unittest
from types import SimpleNamespace

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
from novel_pipeline_stable.models import (
    StyleBibleLocalReducerOutput,
    StyleBibleReasoningBundle,
    StyleBibleReasoningEntry,
    validate_local_rule_row,
)


def _client_stub() -> StableOpenAICompatibleStructuredClient:
    client = object.__new__(StableOpenAICompatibleStructuredClient)
    client.project_config = SimpleNamespace(
        model=SimpleNamespace(response_format="json_object"),
        stability=SimpleNamespace(compact_json=False),
    )
    return client


def _base_rule_row() -> dict[str, object]:
    return {
        "rule_id": "row_01",
        "surface_path": "narrative_system.engine",
        "text": "当角色推进关键动作时，必须先结算成本，再执行动作。",
        "trigger": "当角色推进关键动作时",
        "constraint": "必须先结算成本，再执行动作。",
        "_reasoning_ref": "reasoning_01",
        "evidence_refs": ["scene:0001_001"],
        "anti_pattern_codes": ["none"],
    }


class StyleBibleLocalReduceContractsTest(unittest.TestCase):
    def test_local_rule_row_rejects_invalid_surface_path(self) -> None:
        payload = _base_rule_row()
        payload["surface_path"] = "worldbook.rag"

        with self.assertRaises(ValueError):
            validate_local_rule_row(payload)

    def test_local_rule_row_rejects_legacy_surface_path_aliases(self) -> None:
        legacy_paths = [
            "humor_rules.dark_humor",
            "satire_rules.institutional_absurdity",
            "routing_target_actions.dark_humor",
            "negative_pitfalls.dark_humor",
            "narrative_engine.rule_rows[]",
            "nonstandard_xianxia.rule_rows[]",
            "routing.rule_rows[]",
        ]

        for legacy_path in legacy_paths:
            with self.subTest(legacy_path=legacy_path):
                payload = _base_rule_row()
                payload["surface_path"] = legacy_path
                with self.assertRaises(ValueError):
                    validate_local_rule_row(payload)

    def test_local_rule_row_requires_explicit_rule_id(self) -> None:
        payload = _base_rule_row()
        payload["rule_id"] = ""

        with self.assertRaises(ValueError):
            validate_local_rule_row(payload)

    def test_local_rule_row_enforces_routing_fields(self) -> None:
        payload = _base_rule_row()
        payload.update(
            {
                "surface_path": "worldbook_binding.routing_hints",
                "query_feature_matcher": "流程文件先于人物情绪推进冲突",
                "trigger": "",
                "constraint": "",
                "text": "当流程文件先于人物情绪推进冲突时，必须优先路由到制度流程相关世界书。",
            }
        )

        with self.assertRaises(ValueError):
            validate_local_rule_row(payload)

    def test_local_rule_row_enforces_negative_fields(self) -> None:
        payload = _base_rule_row()
        payload.update(
            {
                "surface_path": "negative_rules",
                "forbidden_action": "把制度惩罚写成空泛压抑感",
                "trigger": "",
                "constraint": "",
                "text": "禁止把制度惩罚写成空泛压抑感。",
            }
        )

        with self.assertRaises(ValueError):
            validate_local_rule_row(payload)

    def test_local_reducer_output_rejects_dangling_reasoning_ref(self) -> None:
        row = _base_rule_row()
        row["_reasoning_ref"] = "reasoning_missing"
        row["evidence_refs"] = ["scene:9999_999"]
        with self.assertRaises(ValueError):
            StyleBibleLocalReducerOutput.model_validate(
                {
                    "reasoning": {
                        "reasoning_version": "v2.0",
                        "style_id": "style.demo",
                        "scope": "novel",
                        "entries": [
                            {
                                "reasoning_id": "reasoning_02",
                                "bucket_id": "resource_pressure",
                                "axis_ids": ["resource_pressure"],
                                "claim": "结算逻辑先于收益兑现。",
                                "observed_commonality": "多处收益后立刻进入结算。",
                                "mechanism_inference": "收益先被账单截流。",
                                "downstream_constraint": "规则必须写清结算先行。",
                                "evidence_refs": ["scene:0001_001"],
                                "anti_pattern_codes": ["none"],
                            }
                        ],
                    },
                    "final": {
                        "style_id": "style.demo",
                        "scope": "novel",
                        "rule_rows": [row],
                    },
                }
            )

    def test_local_reducer_output_can_recover_reasoning_ref_from_evidence_refs(self) -> None:
        row = _base_rule_row()
        row["_reasoning_ref"] = "scene:0001_001"
        parsed = StyleBibleLocalReducerOutput.model_validate(
            {
                "reasoning": {
                    "reasoning_version": "v2.0",
                    "style_id": "style.demo",
                    "scope": "novel",
                    "entries": [
                        {
                            "reasoning_id": "reasoning_02",
                            "bucket_id": "resource_pressure",
                            "axis_ids": ["resource_pressure"],
                            "claim": "结算逻辑先于收益兑现。",
                            "observed_commonality": "多处收益后立刻进入结算。",
                            "mechanism_inference": "收益先被账单截流。",
                            "downstream_constraint": "规则必须写清结算先行。",
                            "evidence_refs": ["scene:0001_001", "scene:0001_002"],
                            "anti_pattern_codes": ["none"],
                        }
                    ],
                },
                "final": {
                    "style_id": "style.demo",
                    "scope": "novel",
                    "rule_rows": [row],
                },
            }
        )

        self.assertEqual(parsed.final.rule_rows[0].reasoning_ref, "reasoning_02")

    def test_local_reducer_output_accepts_compact_reasoning_entry_shape(self) -> None:
        row = _base_rule_row()
        row["_reasoning_ref"] = "scene:0001_001"
        parsed = StyleBibleLocalReducerOutput.model_validate(
            {
                "reasoning": {
                    "reasoning_version": "v2.0",
                    "style_id": "style.demo",
                    "scope": "novel",
                    "entries": [
                        {
                            "_reasoning_ref": "reasoning_03",
                            "text": "收益先被账单截流，再决定角色动作。",
                            "evidence_refs": ["scene:0001_001"],
                        }
                    ],
                },
                "final": {
                    "style_id": "style.demo",
                    "scope": "novel",
                    "rule_rows": [row],
                },
            }
        )

        self.assertEqual(parsed.reasoning.entries[0].reasoning_id, "reasoning_03")
        self.assertEqual(parsed.final.rule_rows[0].reasoning_ref, "reasoning_03")

    def test_local_reducer_output_drops_empty_placeholder_shells(self) -> None:
        parsed = StyleBibleLocalReducerOutput.model_validate(
            {
                "_scratchpad_cross_validation": [
                    {
                        "synthesis_step": "",
                        "source_memo_ids": [],
                        "extracted_common_mechanism": "",
                        "matched_evidence_refs": [],
                    }
                ],
                "reasoning": {
                    "reasoning_version": "",
                    "style_id": "",
                    "scope": "",
                    "entries": [
                        {
                            "reasoning_id": "",
                            "bucket_id": "",
                            "axis_ids": [],
                            "claim": "",
                            "observed_commonality": "",
                            "mechanism_inference": "",
                            "downstream_constraint": "",
                            "evidence_refs": [],
                            "anti_pattern_codes": [],
                        }
                    ],
                },
                "final": {
                    "style_id": "",
                    "scope": "",
                    "rule_rows": [
                        {
                            "rule_id": "",
                            "text": "",
                            "trigger": "",
                            "constraint": "",
                            "query_feature_matcher": "",
                            "route_target_action": "",
                            "forbidden_action": "",
                            "correction_guideline": "",
                            "_reasoning_ref": "",
                            "evidence_refs": [],
                            "anti_pattern_codes": [],
                            "surface_path": "",
                        }
                    ],
                },
            }
        )

        self.assertEqual(parsed.scratchpad_cross_validation, [])
        self.assertEqual(parsed.reasoning.entries, [])
        self.assertEqual(parsed.final.rule_rows, [])

    def test_local_reducer_output_rejects_duplicate_rule_ids(self) -> None:
        row = _base_rule_row()
        with self.assertRaises(ValueError):
            StyleBibleLocalReducerOutput.model_validate(
                {
                    "reasoning": StyleBibleReasoningBundle(
                        reasoning_version="v2.0",
                        style_id="style.demo",
                        scope="novel",
                        entries=[
                            StyleBibleReasoningEntry(
                                reasoning_id="reasoning_01",
                                bucket_id="resource_pressure",
                                axis_ids=["resource_pressure"],
                                claim="结算逻辑先于收益兑现。",
                                observed_commonality="多处收益后立刻进入结算。",
                                mechanism_inference="收益先被账单截流。",
                                downstream_constraint="规则必须写清结算先行。",
                                evidence_refs=["scene:0001_001"],
                                anti_pattern_codes=["none"],
                            )
                        ],
                    ).model_dump(mode="json"),
                    "final": {
                        "style_id": "style.demo",
                        "scope": "novel",
                        "rule_rows": [row, dict(row)],
                    },
                }
            )

    def test_client_can_disable_blueprint_explicitly(self) -> None:
        client = _client_stub()

        system_instruction = StableOpenAICompatibleStructuredClient._compose_system_instruction(
            client,
            "system prompt body",
            StyleBibleLocalReducerOutput,
            response_format_mode="json_object",
            output_contract_mode="none",
        )

        self.assertEqual(system_instruction, "system prompt body")

    def test_client_retries_empty_json_contract_errors(self) -> None:
        retryable, status_code = StableOpenAICompatibleStructuredClient._classify_retryable(
            ValueError("Model returned empty content instead of JSON.")
        )

        self.assertTrue(retryable)
        self.assertIsNone(status_code)

    def test_client_falls_back_from_stream_on_empty_json_contract_errors(self) -> None:
        self.assertTrue(
            StableOpenAICompatibleStructuredClient._should_fallback_responses_stream(
                ValueError("Model returned empty content instead of JSON.")
            )
        )


if __name__ == "__main__":
    unittest.main()

