from __future__ import annotations

import unittest
from pathlib import Path

from novel_pipeline_stable.models import StyleBibleReasoningBundle
from novel_pipeline_stable.style_bible_judge import (
    GoldSetCase,
    JudgeRules,
    _build_case_scope_context,
    _evaluate_trace_auditability,
    _summarize_cases,
)


def _judge_rules() -> JudgeRules:
    return JudgeRules(
        rules_path=Path("judge_rules.toml"),
        pass_score=8.0,
        warn_score=6.0,
        weights={
            "axis_coverage": 1.0,
            "mechanism_specificity": 1.0,
            "evidence_faithfulness": 1.0,
            "trace_auditability": 1.0,
            "routing_executability": 1.0,
            "worldbook_exportability": 1.0,
            "rag_atomicity": 1.0,
            "prompt_preset_usability": 1.0,
            "anti_genericity": 1.0,
            "anti_pattern_resistance": 1.0,
        },
        thresholds={
            "min_pass_case_ratio": 0.6,
            "warn_pass_case_ratio": 0.45,
            "min_trace_audit_ratio": 0.65,
            "warn_trace_audit_ratio": 0.45,
        },
        rule_prefixes=[],
        routing_prefixes=[],
        worldbook_prefixes=[],
        rag_prefixes=[],
        evidence_prefixes=[],
        actionable_cues=[],
        generic_patterns=[],
        axis_groups={},
        anti_pattern_registry={},
    )


def _gold_case(*, case_id: str, bucket_targets: list[str], source_refs: list[str]) -> GoldSetCase:
    return GoldSetCase(
        case_version="v1",
        case_id=case_id,
        node_id="node.demo",
        scope_type="bucket",
        source_refs=source_refs,
        bucket_targets=bucket_targets,
        batch_targets=[],
        must_hit_refs=[],
        required_axes=[],
        required_mechanisms=[],
        forbidden_patterns=[],
        forbidden_outputs=[],
        anti_pattern_watchlist=[],
        required_downstream_surfaces={},
        evidence_expectations={},
        trace_expectations={},
        human_notes="",
        file_path=Path("case.json"),
    )


class StyleBibleJudgeScopeAwareTest(unittest.TestCase):
    def test_case_scope_context_marks_missing_target_scope_not_applicable(self) -> None:
        case = _gold_case(
            case_id="case.body_assetization",
            bucket_targets=["body_assetization"],
            source_refs=["scene:0999_001"],
        )
        reduce_trace_payload = {
            "local_reduces": [
                {
                    "bucket_id": "dark_humor",
                    "batch_ids": ["dark_humor__batch_01"],
                    "grounding_ref_pool": ["scene:0001_001"],
                    "sparse": False,
                }
            ]
        }
        style_bible_payload = {"metadata": {"degradation_status": {"mode": "complete"}}}

        context = _build_case_scope_context(
            case,
            reduce_trace_payload=reduce_trace_payload,
            style_bible_payload=style_bible_payload,
        )

        self.assertFalse(context.applicable)
        self.assertEqual(context.reason, "target_scope_missing_from_run")

    def test_trace_auditability_returns_not_applicable_for_skipped_bucket(self) -> None:
        case = _gold_case(
            case_id="case.exam_screening",
            bucket_targets=["exam_screening"],
            source_refs=["scene:0003_001"],
        )
        reduce_trace_payload = {
            "skipped_sparse_bucket_ids": ["exam_screening"],
            "local_reduces": [
                {
                    "bucket_id": "exam_screening",
                    "batch_ids": ["exam_screening__batch_01"],
                    "grounding_ref_pool": ["scene:0003_001"],
                    "sparse": True,
                }
            ],
        }
        style_bible_payload = {
            "metadata": {
                "degradation_status": {
                    "mode": "degraded",
                    "skipped_sparse_buckets": ["exam_screening"],
                }
            }
        }
        context = _build_case_scope_context(
            case,
            reduce_trace_payload=reduce_trace_payload,
            style_bible_payload=style_bible_payload,
        )

        result = _evaluate_trace_auditability(
            case,
            case_scope_context=context,
            reasoning_bundle=StyleBibleReasoningBundle(),
            reduce_trace_payload=reduce_trace_payload,
            evidence_nodes=[],
            rule_nodes=[],
            routing_nodes=[],
            worldbook_nodes=[],
            rag_nodes=[],
            rules=_judge_rules(),
        )

        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["max_score"], 0.0)
        self.assertEqual(result["details"]["reason"], "target_scope_skipped_sparse")

    def test_summary_uses_only_applicable_cases_for_dynamic_denominator(self) -> None:
        rules = _judge_rules()
        case_results = [
            {
                "case_id": "case.pass",
                "score": 8.0,
                "max_score": 10.0,
                "status": "pass",
                "dimension_scores": {
                    key: {"score": 0.8, "max_score": 1.0, "status": "pass"}
                    for key in rules.weights
                },
            },
            {
                "case_id": "case.na",
                "score": 0.0,
                "max_score": 0.0,
                "status": "not_applicable",
                "dimension_scores": {
                    key: {"score": 0.0, "max_score": 0.0, "status": "not_applicable"}
                    for key in rules.weights
                },
            },
            {
                "case_id": "case.warn",
                "score": 6.0,
                "max_score": 10.0,
                "status": "warn",
                "dimension_scores": {
                    key: {"score": 0.6, "max_score": 1.0, "status": "warn"}
                    for key in rules.weights
                },
            },
        ]

        summary = _summarize_cases(case_results=case_results, judge_rules=rules)

        self.assertEqual(summary["applicable_case_count"], 2)
        self.assertEqual(summary["not_applicable_case_count"], 1)
        self.assertEqual(summary["overall_score"], 7.0)
        self.assertEqual(summary["max_score"], 10.0)
        self.assertEqual(summary["overall_ratio"], 0.7)
        self.assertEqual(summary["pass_case_ratio"], 0.5)
        self.assertEqual(summary["status"], "warn")

    def test_judge_source_drops_semantic_sidecar_report_fields(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "src" / "novel_pipeline_stable" / "style_bible_judge.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertNotIn("build_semantic_sidecar_report", source)
        self.assertNotIn('"semantic_sidecar":', source)
        self.assertNotIn('"semantic_observability":', source)


if __name__ == "__main__":
    unittest.main()
