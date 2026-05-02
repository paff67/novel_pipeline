from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient as RealStructuredClient
from novel_pipeline_stable.models import (
    StyleBibleBatch,
    StyleBibleBucketBatchMemo,
    StyleBibleBucketMembership,
    StyleBibleBucketRuleCandidate,
    StyleBibleFeatureMetrics,
    StyleBibleRoutedItem,
)
from novel_pipeline_stable.style_bible_inputs import StyleBibleInputBundle
from novel_pipeline_stable.style_bible_batching import BatchState, BatchingRules, _candidate_score
from novel_pipeline_stable.style_bible_bucket_builder import (
    BatchMemoTask,
    _restore_cached_batch_execution,
    _run_batch_memo_task,
)
from novel_pipeline_stable.style_bible_builder import _prepare_style_bible_phase01_artifacts
from novel_pipeline_stable.style_bible_router import (
    _build_axis_scores,
    _bucket_memberships,
    _load_routing_rules,
    route_style_bible_inputs,
)


def _routed_item(
    item_id: str,
    *,
    chapter_ids: list[str],
    entity_ids: list[str],
    plot_node_ids: list[str],
    confidence: float = 0.62,
) -> StyleBibleRoutedItem:
    return StyleBibleRoutedItem(
        item_id=item_id,
        item_type="scene",
        source_ref=item_id,
        primary_chapter_id=chapter_ids[0],
        chapter_ids=chapter_ids,
        token_estimate=420,
        summary=item_id,
        axes=["resource_pressure"],
        features=StyleBibleFeatureMetrics(
            resource_pressure_density=0.72,
            evidence_density=0.66,
            conflict_intensity=0.35,
            institution_density=0.08,
            voice_novelty=0.12,
        ),
        bucket_memberships=[StyleBibleBucketMembership(bucket_id="resource_pressure", confidence=confidence)],
        support_refs={"entity_ids": entity_ids, "plot_node_ids": plot_node_ids},
    )


class FakeStructuredClient:
    responses: list[SimpleNamespace] = []
    call_count: int = 0

    def __init__(self, config: object, *, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    def generate_structured(self, **_: object) -> SimpleNamespace:
        type(self).call_count += 1
        if not type(self).responses:
            raise AssertionError("FakeStructuredClient.responses is empty.")
        return type(self).responses.pop(0)

    @classmethod
    def _merge_usage_metadata(cls, primary: object, secondary: object) -> object:
        return RealStructuredClient._merge_usage_metadata(primary, secondary)

    @classmethod
    def _usage_summary(cls, payload: dict[str, object]) -> dict[str, object]:
        return RealStructuredClient._usage_summary(payload)


class StyleBibleRouterBatchingBuilderGuardsTest(unittest.TestCase):
    def test_dark_humor_axis_rejects_single_loose_keyword(self) -> None:
        text = "主角只是顺口吐槽了一句，然后继续常规升级。"
        features = StyleBibleFeatureMetrics(
            dark_humor_signal=0.25,
            voice_novelty=0.08,
            evidence_density=0.40,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="scene")
        memberships = _bucket_memberships(axis_scores, features, text)

        self.assertEqual(axis_scores["dark_humor"], 0.0)
        self.assertNotIn("dark_humor", [row.bucket_id for row in memberships])

    def test_institutional_absurdity_axis_needs_secondary_signal(self) -> None:
        text = "学校发布考试排名和录取通知，所有人按流程入场。"
        features = StyleBibleFeatureMetrics(
            institution_density=0.48,
            dark_humor_signal=0.02,
            voice_novelty=0.05,
            contract_signal=0.02,
            conflict_intensity=0.08,
            evidence_density=0.44,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="scene")

        self.assertEqual(axis_scores["institutional_absurdity"], 0.0)

    def test_dark_humor_axis_accepts_compound_signal(self) -> None:
        text = "叙述一本正经地嘲讽制度，用反差和冷笑包装荒诞流程。"
        features = StyleBibleFeatureMetrics(
            dark_humor_signal=0.46,
            voice_novelty=0.34,
            institution_density=0.22,
            evidence_density=0.38,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="style_window")
        memberships = _bucket_memberships(axis_scores, features, text)

        self.assertGreater(axis_scores["dark_humor"], 0.22)
        self.assertIn("dark_humor", [row.bucket_id for row in memberships])

    def test_dark_humor_axis_rejects_voice_only_signal_without_secondary_support(self) -> None:
        text = "叙述一本正经，吐槽和黑色幽默只是口气，并没有制度或冲突承载。"
        features = StyleBibleFeatureMetrics(
            dark_humor_signal=0.24,
            voice_novelty=0.35,
            institution_density=0.04,
            conflict_intensity=0.05,
            evidence_density=0.38,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="scene")
        memberships = _bucket_memberships(axis_scores, features, text)

        self.assertEqual(axis_scores["dark_humor"], 0.0)
        self.assertNotIn("dark_humor", [row.bucket_id for row in memberships])

    def test_dark_humor_bucket_falls_back_to_orphanage_when_hard_gate_fails(self) -> None:
        text = "叙述带着黑色幽默的口气推进，但正文里没有更多稳定的反差关键词。"
        features = StyleBibleFeatureMetrics(
            dark_humor_signal=0.56,
            voice_novelty=0.28,
            institution_density=0.26,
            conflict_intensity=0.26,
            evidence_density=0.42,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="scene")
        memberships = _bucket_memberships(axis_scores, features, text)

        self.assertGreater(axis_scores["dark_humor"], 0.30)
        self.assertEqual([row.bucket_id for row in memberships], ["orphanage"])

    def test_institutional_pipeline_bucket_needs_hard_keyword_and_secondary_support(self) -> None:
        text = "学校按照流程发通知，所有人按制度排队入场。"
        features = StyleBibleFeatureMetrics(
            institution_density=0.54,
            dark_humor_signal=0.18,
            voice_novelty=0.18,
            contract_signal=0.04,
            conflict_intensity=0.14,
            evidence_density=0.46,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="scene")
        memberships = _bucket_memberships(axis_scores, features, text)

        self.assertGreater(axis_scores["institutional_absurdity"], 0.20)
        self.assertNotIn("institutional_pipeline", [row.bucket_id for row in memberships])

    def test_institutional_pipeline_bucket_accepts_strong_compound_signal(self) -> None:
        text = "学校按制度通知录取和分班，冷静地把淘汰、资格复核和审批流程写成标准动作。"
        features = StyleBibleFeatureMetrics(
            institution_density=0.62,
            dark_humor_signal=0.22,
            voice_novelty=0.31,
            contract_signal=0.08,
            conflict_intensity=0.24,
            evidence_density=0.50,
        )

        axis_scores, _ = _build_axis_scores(text, features, item_type="scene")
        memberships = _bucket_memberships(axis_scores, features, text)

        self.assertGreater(axis_scores["institutional_absurdity"], 0.24)
        self.assertIn("institutional_pipeline", [row.bucket_id for row in memberships])
        pipeline_membership = next(row for row in memberships if row.bucket_id == "institutional_pipeline")
        self.assertGreater(pipeline_membership.lexical_prior_score, 0.0)
        self.assertIn("bucket:institutional_pipeline", pipeline_membership.matched_vocab_ids)

    def test_router_rules_can_be_overridden_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "style_bible_router_rules.toml"
            rules_path.write_text(
                "\n".join(
                    (
                        "[selection]",
                        "axis_selection_min_score = 0.22",
                        "",
                        "[dark_humor]",
                        "bucket_min_axis_score = 0.95",
                        "bucket_min_voice_novelty = 0.30",
                        "bucket_min_secondary_signal = 0.20",
                        "bucket_keyword_hit_threshold = 4",
                        "bucket_min_confidence = 0.60",
                        "",
                        "[institutional_pipeline]",
                        "bucket_min_axis_score = 0.44",
                        "bucket_min_institution_density = 0.33",
                        "bucket_min_secondary_signal = 0.28",
                        "bucket_keyword_hit_threshold = 3",
                        "bucket_min_confidence = 0.55",
                    )
                ),
                encoding="utf-8",
            )
            rules = _load_routing_rules(rules_path)

        self.assertEqual(rules.dark_humor_bucket_min_axis_score, 0.95)
        self.assertEqual(rules.dark_humor_bucket_min_voice_novelty, 0.30)
        self.assertEqual(rules.dark_humor_bucket_min_secondary_signal, 0.20)
        self.assertEqual(rules.dark_humor_bucket_keyword_hit_threshold, 4)
        self.assertEqual(rules.dark_humor_bucket_min_confidence, 0.60)
        self.assertEqual(rules.institutional_pipeline_bucket_min_axis_score, 0.44)
        self.assertEqual(rules.institutional_pipeline_bucket_min_institution_density, 0.33)
        self.assertEqual(rules.institutional_pipeline_bucket_min_secondary_signal, 0.28)
        self.assertEqual(rules.institutional_pipeline_bucket_keyword_hit_threshold, 3)
        self.assertEqual(rules.institutional_pipeline_bucket_min_confidence, 0.55)
        self.assertEqual(Path(rules.source_path), rules_path.resolve())

    def test_routed_index_serialization_emits_rules_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "style_bible_router_rules.toml"
            rules_path.write_text("[selection]\naxis_selection_min_score = 0.22\n", encoding="utf-8")
            inputs = StyleBibleInputBundle(
                fact_rows=[],
                style_rows=[],
                chapter_rows=[],
                plot_rows=[],
                entity_rows=[],
                canon_index={},
                style_index={},
                story_node_scope=None,
            )

            routed_index = route_style_bible_inputs(inputs, rules_config=rules_path)
            payload = routed_index.model_dump(mode="json")

        self.assertEqual(payload["rules_config"], str(rules_path.resolve()))
        self.assertEqual(payload["coverage_summary"]["rules_config"], str(rules_path.resolve()))
        self.assertTrue(payload["coverage_summary"]["lexical_prior_config"].endswith("project_domain_vocabulary.toml"))

    def test_candidate_score_prefers_chapter_and_entity_continuity(self) -> None:
        rules = BatchingRules(
            token_budget=16000,
            max_items_per_batch=60,
            max_scene_items_per_batch=20,
            max_style_window_items_per_batch=10,
            entity_novelty=0.05,
            chapter_continuity_bonus=0.32,
            entity_overlap_bonus=0.22,
            plot_node_overlap_bonus=0.12,
            repeat_chapter_penalty=0.04,
        )
        state = BatchState(
            bucket_id="resource_pressure",
            label="resource pressure",
            axis_focus=["resource_pressure"],
            token_budget=16000,
            scene_count=1,
            chapter_ids={"0101"},
            axis_ids={"resource_pressure"},
            entity_ids={"mc", "boss"},
            plot_node_ids={"plot_a"},
            item_scores={"scene_existing": 0.81},
        )
        same_arc = _routed_item(
            "scene_same_arc",
            chapter_ids=["0101"],
            entity_ids=["mc", "boss"],
            plot_node_ids=["plot_a"],
        )
        novel_arc = _routed_item(
            "scene_novel_arc",
            chapter_ids=["0200"],
            entity_ids=["new_char"],
            plot_node_ids=["plot_z"],
        )
        candidates = [same_arc, novel_arc]

        same_score = _candidate_score(same_arc, "resource_pressure", state, {}, rules, candidates)
        novel_score = _candidate_score(novel_arc, "resource_pressure", state, {}, rules, candidates)

        self.assertGreater(same_score, novel_score)

    def test_bucket_builder_retries_when_all_candidates_are_removed_by_allowed_refs(self) -> None:
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            SimpleNamespace(
                parsed=StyleBibleBucketBatchMemo(
                    memo_id="memo_01",
                    bucket_id="resource_pressure",
                    batch_id="resource_pressure__b01",
                    label="resource pressure",
                    axis_focus=["resource_pressure"],
                    chapter_ids=["0101"],
                    item_ids=["scene:ok"],
                    allowed_refs=["scene:ok"],
                    rule_candidates=[
                        StyleBibleBucketRuleCandidate(
                            candidate_id="bad_ref_rule",
                            trigger_condition="当资源结算逼近时",
                            execution_action="先展示结算压力，再推动接单",
                            evidence_refs=["scene:not_allowed"],
                        )
                    ],
                ),
                request_metrics={
                    "request_key": "bucket_memo_resource_pressure__b01__lr01",
                    "total_elapsed_seconds": 1.25,
                    "response_chars": 120,
                    "cache_hit": False,
                    "attempts": [],
                },
                usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            ),
            SimpleNamespace(
                parsed=StyleBibleBucketBatchMemo(
                    memo_id="memo_01",
                    bucket_id="resource_pressure",
                    batch_id="resource_pressure__b01",
                    label="resource pressure",
                    axis_focus=["resource_pressure"],
                    chapter_ids=["0101"],
                    item_ids=["scene:ok"],
                    allowed_refs=["scene:ok"],
                    rule_candidates=[
                        StyleBibleBucketRuleCandidate(
                            candidate_id="good_ref_rule",
                            trigger_condition="当资源结算逼近时",
                            execution_action="必须先量化缺口，再选择回款更快的行动",
                            evidence_refs=["scene:ok"],
                        )
                    ],
                ),
                request_metrics={
                    "request_key": "bucket_memo_resource_pressure__b01__lr02",
                    "total_elapsed_seconds": 1.75,
                    "response_chars": 180,
                    "cache_hit": False,
                    "attempts": [],
                },
                usage_metadata={"input_tokens": 12, "output_tokens": 24, "total_tokens": 36},
            ),
        ]
        config = SimpleNamespace(
            model=SimpleNamespace(
                style_bible_model="gpt-5.4",
                style_model="gpt-5.4",
                style_bible_temperature=0.2,
                style_temperature=0.2,
                style_bible_max_output_tokens=2048,
                style_max_output_tokens=2048,
            )
        )
        task = BatchMemoTask(
            batch=StyleBibleBatch(
                batch_id="resource_pressure__b01",
                bucket_id="resource_pressure",
                label="resource pressure",
                axis_focus=["resource_pressure"],
                chapter_ids=["0101"],
                item_ids=["scene:ok"],
            ),
            prompt_bundle_xml="<bucket />",
            allowed_refs=["scene:ok"],
            prompt_bundle_path=Path("prompt_bundle.xml"),
            system_instruction="System prompt",
            user_payload={"dynamic_context": {"prompt_bundle_xml": "<bucket />"}},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_bucket_builder.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ):
                execution = _run_batch_memo_task(
                    config,
                    task=task,
                    output_dir=Path(tmpdir),
                    worker_slot=0,
                    gateway_label="test-gateway",
                )

        self.assertEqual(FakeStructuredClient.call_count, 2)
        self.assertTrue(execution.request_metrics["local_retry_used"])
        self.assertEqual(execution.request_metrics["local_retry_count"], 1)
        self.assertEqual(execution.memo.rule_candidates[0].evidence_refs, ["scene:ok"])
        self.assertEqual(execution.request_metrics["local_retry_history"][0]["sanitized_candidate_count"], 0)
        self.assertEqual(execution.request_metrics["local_retry_history"][1]["sanitized_candidate_count"], 1)
        self.assertEqual(execution.request_metrics["total_elapsed_seconds"], 3.0)
        self.assertEqual(execution.usage_metadata["input_tokens"], 22)
        self.assertEqual(execution.usage_metadata["output_tokens"], 44)
        self.assertEqual(execution.usage_metadata["total_tokens"], 66)

    def test_bucket_builder_can_resume_partial_batch_from_cached_success_even_if_last_attempt_failed(self) -> None:
        batch_memo = StyleBibleBucketBatchMemo(
            memo_id="gray_labor__b28__memo",
            bucket_id="gray_labor",
            batch_id="gray_labor__b28",
            label="gray labor",
            axis_focus=["resource_pressure"],
            chapter_ids=["0101"],
            item_ids=["scene:0101_001"],
            allowed_refs=["scene:0101_001"],
            rule_candidates=[
                StyleBibleBucketRuleCandidate(
                    candidate_id="gray_labor_rule_01",
                    trigger_condition="当制度把收益拆成工时和账目时",
                    execution_action="先写清账面压力，再推进人物动作。",
                    evidence_refs=["scene:0101_001"],
                )
            ],
        )
        cache_key = "ab" + ("0" * 62)
        success_metrics = {
            "request_key": "bucket_memo_gray_labor__b28__lr01",
            "completed": True,
            "cache_key": cache_key,
            "cache_path": "",
            "usage_metadata": {"input_tokens": 12, "output_tokens": 24, "total_tokens": 36},
            "gateway_label": "resume-gateway",
            "selected_antipattern_codes": ["GENERIC_MECHANISM"],
            "anti_pattern_token_estimate": 1482,
            "worker_slot": 2,
            "warmup_batch": False,
        }
        failure_metrics = {
            "request_key": "bucket_memo_gray_labor__b28__lr02",
            "completed": False,
            "cache_key": "",
            "cache_path": "",
            "usage_metadata": {},
            "gateway_label": "resume-gateway",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            cache_path = output_dir / "_bucket_requests" / "gray_labor__b28" / "_request_cache" / cache_key[:2] / f"{cache_key}.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_payload = {
                "cache_version": "v1",
                "cached_at": "2026-04-21T00:00:00+00:00",
                "request_key": success_metrics["request_key"],
                "model_name": "gpt-5.4",
                "parsed_payload": batch_memo.model_dump(mode="json", by_alias=True),
                "source_usage_metadata": {"input_tokens": 12, "output_tokens": 24, "total_tokens": 36},
            }
            cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            metrics_path = output_dir / "_bucket_requests" / "gray_labor__b28" / "request_metrics.jsonl"
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(
                json.dumps(success_metrics, ensure_ascii=False) + "\n" + json.dumps(failure_metrics, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            execution = _restore_cached_batch_execution(
                output_dir=output_dir,
                batch_id="gray_labor__b28",
            )

        self.assertIsNotNone(execution)
        assert execution is not None
        self.assertEqual(execution.memo.batch_id, "gray_labor__b28")
        self.assertTrue(execution.request_metrics["completed"])
        self.assertTrue(execution.request_metrics["resumed_partial_existing"])
        self.assertEqual(execution.usage_metadata["input_tokens"], 12)
        self.assertEqual(execution.selected_antipattern_codes, ["GENERIC_MECHANISM"])

    def test_phase01_router_and_batching_only_see_sampled_fact_and_style_inputs(self) -> None:
        inputs = StyleBibleInputBundle(
            fact_rows=[
                {
                    "chapter_id": "0001",
                    "scene_id": "0001_001",
                    "scene_summary": "Debt pressure spikes after a reward is delayed by settlement rules.",
                    "relationship_changes": [
                        {
                            "source": "Lin Qing",
                            "target": "Settlement Office",
                            "relation": "debtor",
                            "change": "approval stalls until the ledger clears",
                            "evidence": {"evidence_text": "The office blocks release until the debt ledger clears."},
                        }
                    ],
                    "power_system_notes": [
                        {
                            "topic": "settlement",
                            "note": "Every gain is offset by a fee-first settlement gate.",
                            "evidence": {"evidence_text": "Settlement logic cuts into every reward."},
                        }
                    ],
                    "style_markers": [
                        {
                            "marker": "resource pressure",
                            "explanation": "Cashflow pressure decides the next move.",
                            "evidence": {"evidence_text": "The character counts fees before celebrating."},
                        }
                    ],
                    "open_questions": ["How can the balance be cleared?"],
                },
                {
                    "chapter_id": "0002",
                    "scene_id": "0002_001",
                    "scene_summary": "A calm walk across campus.",
                    "relationship_changes": [],
                    "power_system_notes": [],
                    "style_markers": [],
                    "open_questions": [],
                },
                {
                    "chapter_id": "0003",
                    "scene_id": "0003_001",
                    "scene_summary": "Routine training without new pressure.",
                    "relationship_changes": [],
                    "power_system_notes": [],
                    "style_markers": [],
                    "open_questions": [],
                },
            ],
            style_rows=[
                {
                    "schema_version": "style_extract_v2",
                    "window_id": "0001_0002",
                    "chapter_ids": ["0001", "0002"],
                    "surface_markers": ["cost before reward", "procedural pressure"],
                    "scalar_contracts": {
                        "perspective": "close_third_person",
                        "distance": "close",
                        "temporality": "linear_forward",
                        "inner_monologue_mode": "sparse_inline",
                    },
                    "narrative_engine_rules": [
                        {
                            "mechanism_label": "cost_first_release_gate",
                            "execution_logic": "Delay payoff until the fee ledger or settlement gate is shown.",
                            "trigger": "when a reward or resource arrives",
                            "constraint": "quantify the cost before any emotional release",
                            "evidence_ids": ["ev_keep_1"],
                        }
                    ],
                    "pacing_rules": [],
                    "plot_node_logic_rules": [],
                    "description_rules": [],
                    "dialogue_rules": [],
                    "characterization_rules": [],
                    "sensory_rules": [],
                    "humor_rules": [],
                    "satire_rules": [],
                    "nonstandard_xianxia_rules": [],
                    "narrator_voice_rules": [],
                    "register_mix_rules": [],
                    "negative_pitfalls": [
                        {
                            "forbidden_action": "flatten the debt chain into vague atmosphere",
                            "correction_guideline": "keep the settlement step explicit and operational",
                            "evidence_ids": ["ev_keep_1"],
                        }
                    ],
                    "rag_candidates": [
                        {
                            "axis_id": "resource_pressure",
                            "bucket_id": "resource_pressure",
                            "query_feature_matcher": "a reward is immediately reduced by debt or settlement cost",
                            "route_target_action": "retrieve fee-first payoff rules",
                            "evidence_ids": ["ev_keep_1"],
                        }
                    ],
                    "worldbook_candidates": [],
                    "routing_hints": [
                        {
                            "axis_id": "resource_pressure",
                            "bucket_id": "resource_pressure",
                            "query_feature_matcher": "cashflow pressure decides the next move",
                            "route_target_action": "route to resource_pressure",
                            "evidence_ids": ["ev_keep_1"],
                        }
                    ],
                    "axis_hints": [{"axis_id": "resource_pressure", "evidence_ids": ["ev_keep_1"]}],
                    "bucket_hints": [{"bucket_id": "resource_pressure", "evidence_ids": ["ev_keep_1"]}],
                    "evidence_index": [
                        {
                            "evidence_id": "ev_keep_1",
                            "source_ref": "scene:0001_001",
                            "quote": "The office blocks release until the debt ledger clears.",
                        }
                    ],
                },
                {
                    "schema_version": "style_extract_v2",
                    "window_id": "0003_0004",
                    "chapter_ids": ["0003", "0004"],
                    "surface_markers": ["quiet campus"],
                    "scalar_contracts": {
                        "perspective": "close_third_person",
                        "distance": "close",
                        "temporality": "linear_forward",
                        "inner_monologue_mode": "sparse_inline",
                    },
                    "narrative_engine_rules": [],
                    "pacing_rules": [],
                    "plot_node_logic_rules": [],
                    "description_rules": [],
                    "dialogue_rules": [],
                    "characterization_rules": [],
                    "sensory_rules": [],
                    "humor_rules": [],
                    "satire_rules": [],
                    "nonstandard_xianxia_rules": [],
                    "narrator_voice_rules": [],
                    "register_mix_rules": [],
                    "negative_pitfalls": [],
                    "rag_candidates": [],
                    "worldbook_candidates": [],
                    "routing_hints": [],
                    "axis_hints": [],
                    "bucket_hints": [],
                    "evidence_index": [],
                },
            ],
            chapter_rows=[
                {
                    "chapter_id": "0001",
                    "chapter_title": "Chapter 1",
                    "scene_count": 1,
                    "scene_summaries": ["Debt blocks the reward release."],
                    "open_questions": ["Who clears the debt?"],
                },
                {
                    "chapter_id": "0002",
                    "chapter_title": "Chapter 2",
                    "scene_count": 1,
                    "scene_summaries": ["A quiet transition chapter."],
                    "open_questions": [],
                },
                {
                    "chapter_id": "0003",
                    "chapter_title": "Chapter 3",
                    "scene_count": 1,
                    "scene_summaries": ["Routine training."],
                    "open_questions": [],
                },
                {
                    "chapter_id": "0004",
                    "chapter_title": "Chapter 4",
                    "scene_count": 1,
                    "scene_summaries": ["Routine follow-up."],
                    "open_questions": [],
                },
            ],
            plot_rows=[
                {
                    "node_id": "plot_keep",
                    "chapter_id": "0001",
                    "title": "Settlement Gate",
                    "summary": "Debt clearance must happen before payoff.",
                    "event_names": ["Ledger Freeze"],
                    "participants": ["Lin Qing"],
                    "locations": ["Settlement Office"],
                    "scene_ids": ["0001_001"],
                    "plot_relevance_hint": "high",
                    "open_questions": [],
                },
                {
                    "node_id": "plot_drop",
                    "chapter_id": "0003",
                    "title": "Routine Training",
                    "summary": "Training continues without a new constraint.",
                    "event_names": ["Training"],
                    "participants": ["Lin Qing"],
                    "locations": ["Practice Field"],
                    "scene_ids": ["0003_001"],
                    "plot_relevance_hint": "low",
                    "open_questions": [],
                },
            ],
            entity_rows=[
                {
                    "entity_id": "entity_lin_qing",
                    "name": "Lin Qing",
                    "entity_type": "character",
                    "aliases": [],
                    "first_seen_chapter": "0001",
                    "supporting_scene_ids": ["0001_001"],
                    "notes": ["Main debtor under fee pressure."],
                },
                {
                    "entity_id": "entity_training_hall",
                    "name": "Training Hall",
                    "entity_type": "location",
                    "aliases": [],
                    "first_seen_chapter": "0003",
                    "supporting_scene_ids": ["0003_001"],
                    "notes": ["Routine training space."],
                },
            ],
            canon_index={},
            style_index={},
            story_node_scope=None,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("novel_pipeline_stable.style_bible_builder.load_style_bible_inputs", return_value=inputs):
                artifacts = _prepare_style_bible_phase01_artifacts(
                    "",
                    "",
                    "",
                    Path(tmpdir),
                    scope_label="sample scope",
                    max_style_windows=1,
                    max_scene_samples=1,
                    max_plot_nodes=1,
                    max_chapter_summaries=1,
                    max_entity_samples=1,
                )

            self.assertEqual(artifacts.routed_index["corpus_stats"]["scene_count"], 1)
            self.assertEqual(artifacts.routed_index["corpus_stats"]["style_window_count"], 1)
            routed_item_ids = {row["item_id"] for row in artifacts.routed_index["items"]}
            self.assertEqual(routed_item_ids, {"scene:0001_001", "0001_0002"})

            batch_item_ids = {
                item_id
                for batch in artifacts.batch_plan.get("batches", [])
                for item_id in batch.get("item_ids", [])
            }
            self.assertTrue(batch_item_ids.issubset(routed_item_ids))
            self.assertTrue((Path(tmpdir) / "sampled_input_scope.json").exists())


if __name__ == "__main__":
    unittest.main()

