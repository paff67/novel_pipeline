from __future__ import annotations

from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from novel_pipeline_stable.models import (
    RoutingHintItem,
    StyleBibleBucketBatchMemo,
    StyleBibleBucketMemo,
    StyleBibleBucketRuleCandidate,
    StyleBibleLocalReducerOutput,
    StyleBibleReasoningBundle,
    StyleBibleReasoningEntry,
    StyleBibleResultV2,
)
from novel_pipeline_stable.style_bible_section_targets import (
    SectionPathTarget,
    SectionSlotSpec,
    load_style_bible_section_targets,
)
from novel_pipeline_stable.style_bible_surface_specs import surface_path_spec_for_path
from novel_pipeline_stable.style_bible_reduction import (
    CriticalBucketReduceError,
    SectionDensifyRequest,
    _build_bucket_reduce_bundle,
    _build_section_densify_bundle,
    _filter_section_densify_candidates,
    _grounding_ref_pool,
    _resume_style_bible_hierarchical_from_bucket_memos,
    _run_local_reduce,
    _target_scalar_candidates,
    reduce_style_bible_from_bucket_memos,
)
from novel_pipeline_stable.style_bible_reduction.orchestrator import _select_count_expansion_slots


class FakeStructuredClient:
    responses: list[object] = []
    call_count: int = 0

    def __init__(self, config: object, *, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    def generate_structured(self, **_: object) -> SimpleNamespace:
        type(self).call_count += 1
        if not type(self).responses:
            raise AssertionError("FakeStructuredClient.responses is empty.")
        response = type(self).responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeEmbeddingClient:
    def __init__(self, config: object, *, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    def embed_texts(self, *, request_key: str, texts: object) -> SimpleNamespace:
        rows = [str(text or "") for text in texts]
        return SimpleNamespace(
            vectors=[self._vector_for(text) for text in rows],
            model_name="fake-embedding",
            usage_metadata={
                "prompt_tokens": len(rows),
                "input_tokens": len(rows),
                "total_tokens": len(rows),
                "cached_tokens": 0,
            },
            request_metrics={
                "request_key": request_key,
                "input_count": len(rows),
                "total_elapsed_seconds": 0.001,
                "attempts": [{"status": "success"}],
            },
        )

    @staticmethod
    def _vector_for(text: str) -> list[float]:
        lowered = text.casefold()
        if any(keyword in lowered for keyword in ("notice", "approval", "workflow", "form", "document")):
            return [1.0, 0.0, 0.0]
        if any(keyword in lowered for keyword in ("debt", "repayment", "cashflow", "compensation", "pricing")):
            return [0.0, 1.0, 0.0]
        if any(keyword in lowered for keyword in ("ranking", "score", "interview", "threshold", "admission")):
            return [0.0, 0.0, 1.0]
        return [0.0, 0.0, 0.001]


class GrayKeepEmbeddingClient:
    def __init__(self, config: object, *, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    def embed_texts(self, *, request_key: str, texts: object) -> SimpleNamespace:
        rows = [str(text or "") for text in texts]
        return SimpleNamespace(
            vectors=[self._vector_for(request_key, text) for text in rows],
            model_name="gray-keep-embedding",
            usage_metadata={
                "prompt_tokens": len(rows),
                "input_tokens": len(rows),
                "total_tokens": len(rows),
                "cached_tokens": 0,
            },
            request_metrics={
                "request_key": request_key,
                "input_count": len(rows),
                "total_elapsed_seconds": 0.001,
                "attempts": [{"status": "success"}],
            },
        )

    @staticmethod
    def _vector_for(request_key: str, text: str) -> list[float]:
        lowered_request = str(request_key or "").casefold()
        lowered_text = str(text or "").casefold()
        if "slot_specs" in lowered_request or "slot_queries" in lowered_request:
            if "approval" in lowered_text:
                return [1.0, 0.0]
            return [0.0, 1.0]
        if "candidate_rows" in lowered_request:
            if "approval" in lowered_text:
                return [0.7, 0.714]
            return [0.0, 1.0]
        if "existing_rows" in lowered_request:
            return [0.0, 1.0]
        if "reasoning_entries" in lowered_request:
            if "approval" in lowered_text:
                return [0.7, 0.714]
            return [0.0, 1.0]
        return [0.0, 1.0]


def _config(prompt_dir: Path, *, critical_buckets: list[str] | None = None, hard_cap: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_dir=prompt_dir,
        model=SimpleNamespace(
            style_bible_model="gpt-5.4",
            style_model="gpt-5.4",
            style_bible_temperature=0.2,
            style_temperature=0.2,
            style_bible_max_output_tokens=2048,
            style_max_output_tokens=2048,
        ),
        style_bible_reduce=SimpleNamespace(
            mode="hierarchical",
            local_reduce_concurrency=1,
            critical_buckets=critical_buckets or ["dark_humor"],
            max_failed_bucket_count=1,
            max_failed_bucket_ratio=0.5,
            supporting_evidence_soft_cap=hard_cap,
            supporting_evidence_hard_cap=hard_cap,
        ),
        embedding=SimpleNamespace(
            enabled=True,
            model="fake-embedding",
        ),
    )


def _bucket_memo(bucket_id: str, ref: str) -> StyleBibleBucketMemo:
    return StyleBibleBucketMemo(
        memo_version="v2.0",
        memo_id=f"{bucket_id}__memo",
        bucket_id=bucket_id,
        label=bucket_id,
        scope_hint="novel",
        axis_focus=[bucket_id],
        chapter_ids=["0101"],
        item_ids=[ref],
        allowed_refs=[ref],
        rule_candidates=[
            StyleBibleBucketRuleCandidate(
                candidate_id=f"{bucket_id}__candidate_01",
                trigger_condition=f"当{bucket_id}相关机制被触发时",
                execution_action="必须落到清晰的条件和动作链上",
                evidence_refs=[ref],
            )
        ],
    )


def _normalize_rule_payload_for_surface_path(path: str, rule: dict[str, object]) -> dict[str, object]:
    payload = dict(rule)
    spec = surface_path_spec_for_path(path)
    if spec is None or spec.row_model != "ScalarRuleItem":
        return payload
    normalized: dict[str, object] = {
        "text": payload.get("text", ""),
        "_reasoning_ref": payload.get("_reasoning_ref", ""),
        "evidence_refs": payload.get("evidence_refs", []),
    }
    if "rule_id" in payload:
        normalized["rule_id"] = payload["rule_id"]
    if "anti_pattern_codes" in payload:
        normalized["anti_pattern_codes"] = payload["anti_pattern_codes"]
    return normalized


def _reducer_response(
    *,
    bucket_id: str,
    ref: str,
    path: str,
    rule: dict[str, object],
    elapsed_seconds: float,
) -> SimpleNamespace:
    rule_payload = _normalize_rule_payload_for_surface_path(path, rule)
    rule_payload.setdefault("rule_id", f"{bucket_id}__row_01")
    rule_payload["surface_path"] = path
    parsed = StyleBibleLocalReducerOutput(
        reasoning=StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_01",
                    bucket_id=bucket_id,
                    axis_ids=[bucket_id],
                    claim=f"{bucket_id} 机制必须落到可执行规则。",
                    observed_commonality="多个样本都体现了同类动作链。",
                    mechanism_inference="先给触发条件，再给执行动作，最后让代价落地。",
                    downstream_constraint="规则必须写清触发条件、执行动作与可审计后果。",
                    evidence_refs=[ref],
                    anti_pattern_codes=["none"],
                )
            ],
        ),
        final={
            "style_id": "style.demo",
            "scope": "novel",
            "rule_rows": [rule_payload],
        },
    )
    return SimpleNamespace(
        parsed=parsed,
        request_metrics={
            "total_elapsed_seconds": elapsed_seconds,
            "response_chars": 256,
            "attempts": [{"first_chunk_seconds": round(elapsed_seconds / 2, 3)}],
        },
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cached_tokens": 0,
        },
    )


def _multi_reducer_response(
    *,
    bucket_id: str,
    ref: str,
    rows: list[tuple[str, dict[str, object]]],
    elapsed_seconds: float,
) -> SimpleNamespace:
    rule_rows: list[dict[str, object]] = []
    for index, (path, rule) in enumerate(rows, start=1):
        rule_payload = _normalize_rule_payload_for_surface_path(path, rule)
        rule_payload.setdefault("rule_id", f"{bucket_id}__row_{index:02d}")
        rule_payload["surface_path"] = path
        rule_rows.append(rule_payload)
    parsed = StyleBibleLocalReducerOutput(
        reasoning=StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_01",
                    bucket_id=bucket_id,
                    axis_ids=[bucket_id],
                    claim=f"{bucket_id} rules should be grounded and executable",
                    observed_commonality="multiple samples converge on the same downstream constraint",
                    mechanism_inference="the reducer should emit trigger-action pairs that can be merged reliably",
                    downstream_constraint="repair output should add missing sections without duplicating existing rows",
                    evidence_refs=[ref],
                    anti_pattern_codes=["none"],
                )
            ],
        ),
        final={
            "style_id": "style.demo",
            "scope": "novel",
            "rule_rows": rule_rows,
        },
    )
    return SimpleNamespace(
        parsed=parsed,
        request_metrics={
            "total_elapsed_seconds": elapsed_seconds,
            "response_chars": 256 * max(len(rule_rows), 1),
            "attempts": [{"first_chunk_seconds": round(elapsed_seconds / 2, 3)}],
        },
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cached_tokens": 0,
        },
    )


def _structured_reducer_response(
    *,
    reasoning_entries: list[dict[str, object]],
    rule_rows: list[dict[str, object]],
    elapsed_seconds: float,
) -> SimpleNamespace:
    parsed = StyleBibleLocalReducerOutput(
        reasoning=StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[StyleBibleReasoningEntry.model_validate(row) for row in reasoning_entries],
        ),
        final={
            "style_id": "style.demo",
            "scope": "novel",
            "rule_rows": rule_rows,
        },
    )
    return SimpleNamespace(
        parsed=parsed,
        request_metrics={
            "total_elapsed_seconds": elapsed_seconds,
            "response_chars": 256 * max(len(rule_rows), 1),
            "attempts": [{"first_chunk_seconds": round(elapsed_seconds / 2, 3)}],
        },
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cached_tokens": 0,
        },
    )


def _sparse_reducer_response(*, elapsed_seconds: float = 0.8) -> SimpleNamespace:
    parsed = StyleBibleLocalReducerOutput(
        reasoning=StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[],
        ),
        final={
            "style_id": "style.demo",
            "scope": "novel",
            "rule_rows": [],
        },
    )
    return SimpleNamespace(
        parsed=parsed,
        request_metrics={
            "total_elapsed_seconds": elapsed_seconds,
            "response_chars": 64,
            "attempts": [{"first_chunk_seconds": round(elapsed_seconds / 2, 3)}],
        },
        usage_metadata={
            "input_tokens": 80,
            "output_tokens": 16,
            "total_tokens": 96,
            "cached_tokens": 0,
        },
    )


class StyleBibleHierarchicalReducerTest(unittest.TestCase):
    def test_target_scalar_candidates_include_narrator_voice_and_normalize_aliases(self) -> None:
        source_bundle = {
            "global_style_signals": {
                "scalar_contracts": {
                    "inner_monologue_mode": [
                        {
                            "value": "embedded",
                            "count": 2,
                            "source_refs": ["window:0001_0002"],
                        }
                    ],
                    "narrator_voice": [
                        {
                            "value": "deadpan",
                            "count": 3,
                            "source_refs": ["window:0001_0002"],
                        }
                    ],
                }
            }
        }

        candidates = _target_scalar_candidates(
            source_bundle,
            requested_paths=["voice_contract.inner_monologue_mode", "voice_contract.narrator_voice"],
        )

        self.assertEqual(candidates["voice_contract.inner_monologue_mode"][0]["value"], "sparse_inline")
        self.assertEqual(candidates["voice_contract.narrator_voice"][0]["value"], "deadpan_procedural")

    def test_build_bucket_reduce_bundle_does_not_expose_worldbook_atom_candidates(self) -> None:
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
            "corpus_stats": {},
            "sampling": {},
            "global_style_signals": {},
            "fact_signal_summary": {},
            "worldbook_atom_candidates": [
                {
                    "atom_id": "fact__0001_001__01",
                    "atom_type": "fact",
                    "chapter_id": "0001",
                    "scene_id": "0001_001",
                    "source_ref": "scene:0001_001",
                    "grounding_refs": ["scene:0001_001"],
                    "text": "【事实】审批没过，流程不会放人。",
                },
                {
                    "atom_id": "chapter__0001",
                    "atom_type": "chapter_summary",
                    "chapter_id": "0001",
                    "scene_id": "",
                    "source_ref": "chapter:0001",
                    "grounding_refs": [],
                    "text": "【章节摘要】第0001章：审批卡住了。",
                },
                {
                    "atom_id": "fact__0009_001__01",
                    "atom_type": "fact",
                    "chapter_id": "0009",
                    "scene_id": "0009_001",
                    "source_ref": "scene:0009_001",
                    "grounding_refs": ["scene:0009_001"],
                    "text": "【事实】无关场景。",
                },
            ],
        }
        bucket_memo = StyleBibleBucketMemo(
            memo_version="v2.0",
            memo_id="institutional_pipeline__memo",
            bucket_id="institutional_pipeline",
            label="institutional_pipeline",
            scope_hint="novel",
            axis_focus=["institutional_pipeline"],
            chapter_ids=["0001"],
            item_ids=["scene:0001_001"],
            allowed_refs=["scene:0001_001"],
            rule_candidates=[],
            batch_memos=[],
        )

        bundle = _build_bucket_reduce_bundle(source_bundle=source_bundle, bucket_memo=bucket_memo)

        self.assertNotIn("worldbook_atom_candidates", bundle)
        self.assertEqual(bundle["bucket_memo_summary"]["bucket_id"], "institutional_pipeline")
        self.assertIn("section_signal_context", bundle)

    def test_build_section_densify_bundle_excludes_burned_reasoning_and_tracks_burned_refs(self) -> None:
        section_targets = load_style_bible_section_targets()
        path_target = section_targets.path_targets["worldbook_binding.routing_hints"]
        request = SectionDensifyRequest(
            path="worldbook_binding.routing_hints",
            actual_count=0,
            target_count=path_target.target_count,
            deficit=1,
            path_target=path_target,
        )
        reasoning_bundle = StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_approval_01",
                    bucket_id="institutional_pipeline",
                    axis_ids=["institutional_absurdity"],
                    claim="Approval notices and workflow forms decide when the character can move.",
                    observed_commonality="Scenes pause on approvals, signatures, and notice handling.",
                    mechanism_inference="Turn approval checkpoints into routing triggers.",
                    downstream_constraint="Route approval-driven scenes to institutional workflow rules.",
                    evidence_refs=["scene:0002_001"],
                    anti_pattern_codes=["none"],
                ),
                StyleBibleReasoningEntry(
                    reasoning_id="reasoning_approval_02",
                    bucket_id="institutional_pipeline",
                    axis_ids=["institutional_absurdity"],
                    claim="Approval notices still govern access after the queue moves.",
                    observed_commonality="A later scene still blocks movement on an approval checkpoint.",
                    mechanism_inference="Keep approval checkpoints retrievable for routing.",
                    downstream_constraint="Route approval-driven scenes to institutional workflow rules.",
                    evidence_refs=["scene:0002_002"],
                    anti_pattern_codes=["none"],
                )
            ],
        )
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            embedding_client = FakeEmbeddingClient(SimpleNamespace(), artifacts_dir=Path(tmpdir))
            bundle, grounding_ref_pool, trace = _build_section_densify_bundle(
                source_bundle=source_bundle,
                reasoning_bundle=reasoning_bundle,
                final_result=StyleBibleResultV2(style_id="style.demo", scope="novel"),
                request=request,
                missing_slots=[path_target.slot_specs[0]],
                embedding_client=embedding_client,
                request_key_prefix="densify_worldbook",
                burned_reasoning_ids={"reasoning_approval_01"},
                burned_evidence_refs={"scene:0002_001"},
            )

        self.assertNotIn("worldbook_atom_candidates", bundle)
        self.assertEqual(bundle["burned_reasoning_ids"], ["reasoning_approval_01"])
        self.assertEqual(bundle["burned_evidence_refs"], ["scene:0002_001"])
        self.assertEqual(
            [row["reasoning_id"] for row in bundle["retrieved_reasoning_entries"]],
            ["reasoning_approval_02"],
        )
        self.assertIn("scene:0002_002", grounding_ref_pool)
        self.assertNotIn("scene:0002_001", grounding_ref_pool)
        self.assertEqual(trace["burned_reasoning_id_count"], 1)
        self.assertEqual(trace["burned_evidence_ref_count"], 1)
        self.assertEqual(trace["burned_candidate_count"], 1)
        self.assertGreaterEqual(bundle["retrieved_reasoning_entries"][0]["semantic_slot_score"], 0.0)
        self.assertGreaterEqual(bundle["retrieved_reasoning_entries"][0]["cue_score"], 0.0)
        self.assertGreaterEqual(bundle["retrieved_reasoning_entries"][0]["combined_score"], 0.0)
        self.assertIn("selected_rows", trace)
        self.assertEqual(trace["selected_rows"][0]["reasoning_id"], "reasoning_approval_02")

    def test_hierarchical_reduce_writes_local_artifacts_and_trims_evidence(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"], hard_cap=1)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
            "corpus_stats": {},
            "sampling": {},
            "global_style_signals": {},
            "fact_signal_summary": {},
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        resource_pressure_memo = _bucket_memo("resource_pressure", "scene:0002_001")
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="aesthetics_system.humor_recipe",
                rule={
                    "trigger": "when a process stays formally serious while becoming absurd",
                    "constraint": "deliver the punchline through concrete procedure instead of abstract commentary",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=1.2,
            ),
            _reducer_response(
                bucket_id="resource_pressure",
                ref="scene:0002_001",
                path="narrative_system.engine",
                rule={
                    "trigger": "当角色要推进关键动作时",
                    "constraint": "必须先核算成本和回款窗口，再执行动作",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0002_001"],
                },
                elapsed_seconds=1.4,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [resource_pressure_memo, dark_humor_memo],
                    Path(tmpdir),
                )

            self.assertEqual(result.reduce_mode, "hierarchical")
            self.assertEqual(result.prompt_name, "style_bible_local_reduce.md")
            self.assertEqual(result.request_metrics["local_reduce_success_count"], 2)
            self.assertEqual(result.request_metrics["failed_bucket_ids"], [])
            self.assertEqual(result.request_metrics["supporting_evidence_final_count"], 1)
            self.assertEqual(len(result.record["supporting_evidence"]), 1)
            self.assertEqual(result.record["metadata"]["degradation_status"]["mode"], "complete")
            self.assertTrue(result.local_artifact_root and result.local_artifact_root.exists())
            self.assertTrue((Path(tmpdir) / "_local_reduce" / "dark_humor" / "local_partial.json").exists())

            dark_humor_local_final = json.loads(
                (Path(tmpdir) / "_local_reduce" / "dark_humor" / "local_final.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                dark_humor_local_final["aesthetics_system"]["humor_recipe"][0]["rule_id"].startswith("dark_humor__")
            )
            self.assertTrue(result.record["aesthetics_system"]["humor_recipe"][0]["rule_id"].startswith("dark_humor__"))
            self.assertTrue(result.record["narrative_system"]["engine"][0]["rule_id"].startswith("resource_pressure__"))
            self.assertIn("rule_lineage_map", result.reduce_trace)
            self.assertGreaterEqual(len(result.reduce_trace["rule_lineage_map"]), 2)
            self.assertTrue(
                any(
                    row["surface_path"] == "narrative_system.engine"
                    and row["final_rule_id"].startswith("resource_pressure__")
                    for row in result.reduce_trace["rule_lineage_map"]
                )
            )
            self.assertTrue(
                any(
                    row["bucket_id"] == "dark_humor"
                    and "scene:0001_001" in row.get("grounding_ref_pool", [])
                    for row in result.reduce_trace["local_reduces"]
                )
            )

    def test_hierarchical_reduce_fuses_critical_bucket_failure(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"])
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [RuntimeError("upstream timeout")]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ):
                with self.assertRaises(CriticalBucketReduceError):
                    reduce_style_bible_from_bucket_memos(
                        config,
                        source_bundle,
                        [dark_humor_memo],
                        Path(tmpdir),
                    )

            summary_path = Path(tmpdir) / "_local_reduce" / "dark_humor" / "local_reduce_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["bucket_id"], "dark_humor")
            self.assertEqual(summary["error_type"], "RuntimeError")

    def test_hierarchical_reduce_skips_sparse_noncritical_bucket(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"], hard_cap=1)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        exam_screening_memo = _bucket_memo("exam_screening", "scene:0003_001")
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="aesthetics_system.humor_recipe",
                rule={
                    "trigger": "当制度荒诞被一本正经地执行时",
                    "constraint": "必须用冷面笔调写出荒诞结果，而不是直接点评好笑",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=1.1,
            ),
            _sparse_reducer_response(),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [dark_humor_memo, exam_screening_memo],
                    Path(tmpdir),
                )

            self.assertEqual(result.request_metrics["local_reduce_sparse_count"], 1)
            self.assertEqual(result.request_metrics["skipped_sparse_bucket_ids"], ["exam_screening"])
            self.assertEqual(result.record["metadata"]["degradation_status"]["mode"], "degraded")
            self.assertEqual(
                result.record["metadata"]["degradation_status"]["skipped_sparse_buckets"],
                ["exam_screening"],
            )

            summary_path = Path(tmpdir) / "_local_reduce" / "exam_screening" / "local_reduce_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "sparse")

    def test_grounding_ref_pool_keeps_allowed_refs_without_rule_candidates(self) -> None:
        memo = StyleBibleBucketMemo(
            memo_version="v2.0",
            memo_id="body_assetization__memo",
            bucket_id="body_assetization",
            label="body_assetization",
            scope_hint="novel",
            axis_focus=["body_assetization"],
            chapter_ids=["0104"],
            item_ids=["scene:0004_001"],
            allowed_refs=["scene:0004_001"],
            rule_candidates=[],
            batch_memos=[
                StyleBibleBucketBatchMemo(
                    memo_id="body_assetization__batch_01__memo",
                    bucket_id="body_assetization",
                    batch_id="body_assetization__batch_01",
                    label="body_assetization",
                    allowed_refs=["scene:0004_002"],
                    rule_candidates=[],
                )
            ],
        )

        self.assertEqual(
            _grounding_ref_pool([memo]),
            ["scene:0004_001", "scene:0004_002"],
        )

    def test_hierarchical_reduce_preflight_sparse_skip_marks_bucket_sparse_and_allows_targeted_repair(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"], hard_cap=1)
        base_targets = load_style_bible_section_targets()
        custom_targets = replace(base_targets, repair_max_rounds=1, densify_enabled=False)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
            "global_style_signals": {
                "scalar_contracts": {
                    "narrator_voice": [
                        {
                            "value": "deadpan_procedural",
                            "count": 3,
                            "source_refs": ["scene:0001_001"],
                        }
                    ]
                }
            },
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        empty_memo = StyleBibleBucketMemo(
            memo_version="v2.0",
            memo_id="exam_screening__memo",
            bucket_id="exam_screening",
            label="exam_screening",
            scope_hint="novel",
            axis_focus=["exam_screening"],
            chapter_ids=["0105"],
            item_ids=[],
            allowed_refs=[],
            rule_candidates=[],
            batch_memos=[],
        )
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="aesthetics_system.humor_recipe",
                rule={
                    "trigger": "当制度荒诞被一本正经地执行时",
                    "constraint": "必须用冷面笔调写出荒诞结果，而不是直接点评好笑",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=1.1,
            ),
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="voice_contract.narrator_voice",
                rule={
                    "text": "deadpan_procedural",
                    "trigger": "when narration reports institutional absurdity",
                    "constraint": "select deadpan_procedural so the voice stays procedural and emotionally flat",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=0.9,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.load_style_bible_section_targets",
                return_value=custom_targets,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [dark_humor_memo, empty_memo],
                    Path(tmpdir),
                )

            self.assertEqual(FakeStructuredClient.call_count, 2)
            self.assertEqual(result.request_metrics["local_reduce_sparse_count"], 1)
            self.assertEqual(result.request_metrics["repair_pass_count"], 1)
            self.assertEqual(result.record["voice_contract"]["narrator_voice"]["text"], "deadpan_procedural")
            summary_path = Path(tmpdir) / "_local_reduce" / "exam_screening" / "local_reduce_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "sparse")
            self.assertTrue(summary["preflight_skip"])
            self.assertEqual(summary["sparse_reason"], "empty_bucket_without_candidates_or_grounding")
            dark_humor_summary = json.loads(
                (Path(tmpdir) / "_local_reduce" / "dark_humor" / "local_reduce_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(dark_humor_summary["repair_pass_count"], 1)

    def test_hierarchical_reduce_section_repair_merges_scalar_and_list_sections(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"], hard_cap=2)
        base_targets = load_style_bible_section_targets()
        custom_targets = replace(base_targets, repair_max_rounds=1, densify_enabled=False)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
            "global_style_signals": {
                "scalar_contracts": {
                    "narrator_voice": [
                        {
                            "value": "deadpan_procedural",
                            "count": 4,
                            "source_refs": ["scene:0001_001"],
                        }
                    ]
                }
            },
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="aesthetics_system.humor_recipe",
                rule={
                    "trigger": "when a process stays formally serious while becoming absurd",
                    "constraint": "deliver the punchline through concrete procedure instead of abstract commentary",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=1.0,
            ),
            _multi_reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                rows=[
                    (
                        "voice_contract.narrator_voice",
                        {
                            "text": "deadpan_procedural",
                            "trigger": "when narration summarizes the rule in a neutral report",
                            "constraint": "keep the narrator in deadpan_procedural mode and avoid emotional release",
                            "_reasoning_ref": "reasoning_01",
                            "evidence_refs": ["scene:0001_001"],
                        },
                    ),
                    (
                        "expression_system.dialogue_rules",
                        {
                            "trigger": "when characters explain a rule or report a cost",
                            "constraint": "dialogue should retain ledger-like or procedural wording instead of pure emotion",
                            "_reasoning_ref": "reasoning_01",
                            "evidence_refs": ["scene:0001_001"],
                        },
                    ),
                ],
                elapsed_seconds=0.9,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.load_style_bible_section_targets",
                return_value=custom_targets,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [dark_humor_memo],
                    Path(tmpdir),
                )

            self.assertEqual(FakeStructuredClient.call_count, 2)
            self.assertEqual(result.request_metrics["repair_pass_count"], 1)
            self.assertEqual(result.request_metrics["repair_used_bucket_count"], 1)
            self.assertEqual(result.record["voice_contract"]["narrator_voice"]["text"], "deadpan_procedural")
            self.assertEqual(len(result.record["expression_system"]["dialogue_rules"]), 1)
            self.assertEqual(
                result.record["expression_system"]["dialogue_rules"][0]["constraint"],
                "dialogue should retain ledger-like or procedural wording instead of pure emotion",
            )
            local_summary_path = Path(tmpdir) / "_local_reduce" / "dark_humor" / "local_reduce_summary.json"
            local_summary = json.loads(local_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(local_summary["repair_pass_count"], 1)
            self.assertEqual(local_summary["repair_passes"][0]["mode"], "repair")
            self.assertIn("voice_contract.narrator_voice", local_summary["repair_passes"][0]["requested_paths"])
            repair_summary_path = (
                Path(tmpdir)
                / "_local_reduce"
                / "dark_humor"
                / "_repair_passes"
                / "pass_01"
                / "local_reduce_summary.json"
            )
            repair_summary = json.loads(repair_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(repair_summary["status"], "success")
            self.assertEqual(repair_summary["request_metrics"]["repair_mode"], "repair")

    def test_hierarchical_reduce_resume_local_artifacts_replays_repair_passes(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"], hard_cap=2)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
            "global_style_signals": {
                "scalar_contracts": {
                    "narrator_voice": [
                        {
                            "value": "deadpan_procedural",
                            "count": 3,
                            "source_refs": ["scene:0001_001"],
                        }
                    ]
                }
            },
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        base_targets = load_style_bible_section_targets()
        custom_targets = replace(base_targets, densify_enabled=False)
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="aesthetics_system.humor_recipe",
                rule={
                    "trigger": "when a formal process stays serious while becoming absurd",
                    "constraint": "land the humor through audit-like procedure instead of generic jokes",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=1.0,
            ),
            _multi_reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                rows=[
                    (
                        "voice_contract.narrator_voice",
                        {
                            "text": "deadpan_procedural",
                            "trigger": "when narration summarizes the event like a report",
                            "constraint": "keep the narrator in deadpan_procedural mode",
                            "_reasoning_ref": "reasoning_01",
                            "evidence_refs": ["scene:0001_001"],
                        },
                    ),
                    (
                        "expression_system.dialogue_rules",
                        {
                            "trigger": "when characters explain the cost of the next step",
                            "constraint": "dialogue should preserve ledger-like or procedural wording",
                            "_reasoning_ref": "reasoning_01",
                            "evidence_refs": ["scene:0001_001"],
                        },
                    ),
                ],
                elapsed_seconds=0.8,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            bucket_dir = Path(tmpdir) / "_local_reduce" / "dark_humor"
            repair_dir = bucket_dir / "_repair_passes" / "pass_01"
            repair_request = {
                "mode": "repair",
                "requested_paths": ["voice_contract.narrator_voice", "expression_system.dialogue_rules"],
                "missing_scalar_paths": ["voice_contract.narrator_voice"],
                "underfilled_paths": [
                    {
                        "path": "expression_system.dialogue_rules",
                        "actual_count": 0,
                        "target_count": 1,
                        "deficit": 1,
                    }
                ],
            }
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.load_style_bible_section_targets",
                return_value=custom_targets,
            ):
                _run_local_reduce(
                    config,
                    source_bundle=source_bundle,
                    bucket_memo=dark_humor_memo,
                    output_dir=bucket_dir,
                    section_targets=custom_targets,
                )
                _run_local_reduce(
                    config,
                    source_bundle=source_bundle,
                    bucket_memo=dark_humor_memo,
                    output_dir=repair_dir,
                    section_targets=custom_targets,
                    repair_request=repair_request,
                    request_key_suffix="__repair_01",
                )
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [dark_humor_memo],
                    Path(tmpdir),
                    resume_local_reduce=True,
                )

            self.assertEqual(FakeStructuredClient.call_count, 2)
            self.assertEqual(result.record["voice_contract"]["narrator_voice"]["text"], "deadpan_procedural")
            self.assertEqual(len(result.record["expression_system"]["dialogue_rules"]), 1)
            local_summary_path = Path(tmpdir) / "_local_reduce" / "dark_humor" / "local_reduce_summary.json"
            local_summary = json.loads(local_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(local_summary["repair_pass_count"], 1)
            self.assertEqual(local_summary["repair_passes"][0]["mode"], "repair")
            self.assertIn("voice_contract.narrator_voice", local_summary["repair_passes"][0]["requested_paths"])
            self.assertTrue((Path(tmpdir) / "style_bible_final.json").exists())
            self.assertTrue((Path(tmpdir) / "style_bible_reduce_trace.json").exists())
            self.assertTrue((Path(tmpdir) / "style_bible_source_bundle.json").exists())

    def test_hierarchical_reduce_drops_conflicting_routing_group_and_records_metadata(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor"], hard_cap=2)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        institutional_memo = _bucket_memo("institutional_pipeline", "scene:0002_001")
        resource_memo = _bucket_memo("resource_pressure", "scene:0003_001")
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _reducer_response(
                bucket_id="dark_humor",
                ref="scene:0001_001",
                path="worldbook_binding.routing_hints",
                rule={
                    "query_feature_matcher": "流程文件先于人物情绪推进冲突",
                    "route_target_action": "优先路由到 dark_humor 的黑色制度笑点规则",
                    "text": "当流程文件先于人物情绪推进冲突时，优先路由到 dark_humor 的黑色制度笑点规则。",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0001_001"],
                },
                elapsed_seconds=1.0,
            ),
            _reducer_response(
                bucket_id="institutional_pipeline",
                ref="scene:0002_001",
                path="worldbook_binding.routing_hints",
                rule={
                    "query_feature_matcher": "流程文件先于人物情绪推进冲突",
                    "route_target_action": "优先路由到 institutional_pipeline 的制度流程世界书",
                    "text": "当流程文件先于人物情绪推进冲突时，优先路由到 institutional_pipeline 的制度流程世界书。",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0002_001"],
                },
                elapsed_seconds=1.0,
            ),
            _reducer_response(
                bucket_id="resource_pressure",
                ref="scene:0003_001",
                path="narrative_system.engine",
                rule={
                    "trigger": "当角色推进关键动作时",
                    "constraint": "必须先结算成本，再执行动作",
                    "_reasoning_ref": "reasoning_01",
                    "evidence_refs": ["scene:0003_001"],
                },
                elapsed_seconds=1.0,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [dark_humor_memo, institutional_memo, resource_memo],
                    Path(tmpdir),
                )

            self.assertEqual(result.record["worldbook_binding"]["routing_hints"], [])
            self.assertEqual(result.record["metadata"]["degradation_status"]["mode"], "degraded")
            self.assertEqual(len(result.record["metadata"]["degradation_status"]["assembler_conflicts"]), 1)
            self.assertEqual(
                result.record["metadata"]["degradation_status"]["assembler_conflicts"][0]["surface_path"],
                "worldbook_binding.routing_hints",
            )

    def test_filter_section_densify_candidates_drops_semantic_duplicate_existing(self) -> None:
        base_targets = load_style_bible_section_targets()
        routing_target = base_targets.densify_target_for_path("worldbook_binding.routing_hints")
        assert routing_target is not None
        existing_rows = [
            RoutingHintItem.model_validate(
                {
                    "rule_id": "existing_notice_router",
                    "text": "When a notice or approval decides the next move, route to the institutional workflow rule family.",
                    "query_feature_matcher": "notice or approval decides the next move",
                    "route_target_action": "route to the institutional workflow rule family",
                    "_reasoning_ref": "reasoning_notice",
                    "evidence_refs": ["scene:0001_001"],
                }
            )
        ]
        candidate_rows = [
            RoutingHintItem.model_validate(
                {
                    "rule_id": "candidate_duplicate_notice",
                    "text": "If an approval notice determines the next move, route to the institutional workflow rule family.",
                    "query_feature_matcher": "approval notice determines the next move",
                    "route_target_action": "route to the institutional workflow rule family",
                    "_reasoning_ref": "reasoning_notice",
                    "evidence_refs": ["scene:0001_001"],
                }
            ),
            RoutingHintItem.model_validate(
                {
                    "rule_id": "candidate_repayment_router",
                    "text": "When debt repayment or cashflow settlement gates the action, route to the repayment pressure rule family.",
                    "query_feature_matcher": "debt repayment or cashflow settlement gates the action",
                    "route_target_action": "route to the repayment pressure rule family",
                    "_reasoning_ref": "reasoning_repayment",
                    "evidence_refs": ["scene:0001_001"],
                }
            ),
        ]
        kept_rows, filter_trace = _filter_section_densify_candidates(
            candidate_rows=candidate_rows,
            existing_rows=existing_rows,
            missing_slots=[],
            path_target=replace(routing_target, dedupe_threshold=0.92),
            max_keep=2,
            embedding_client=FakeEmbeddingClient(SimpleNamespace(), artifacts_dir=Path(".")),
            request_key_prefix="unit_test",
        )

        self.assertEqual([row.rule_id for row in kept_rows], ["candidate_repayment_router"])
        candidate_statuses = {
            row["rule_id"]: row["status"]
            for row in filter_trace["candidates"]
        }
        self.assertEqual(candidate_statuses["candidate_duplicate_notice"], "drop_semantic_duplicate_existing")
        self.assertEqual(candidate_statuses["candidate_repayment_router"], "keep")
        self.assertEqual(filter_trace["semantic_dedupe_drop_count"], 1)
        self.assertEqual(len(filter_trace["semantic_dedupe_drops"]), 1)
        self.assertEqual(
            filter_trace["semantic_dedupe_drops"][0]["matched_rule_id"],
            "existing_notice_router",
        )

    def test_filter_section_densify_candidates_records_gray_keep_trace(self) -> None:
        path_target = SectionPathTarget(
            path="worldbook_binding.routing_hints",
            target_count=1,
            max_new_rows=1,
            retrieval_top_k=1,
            downstream_shape="matcher + route_target_action",
            dedupe_threshold=0.95,
            slot_match_threshold=0.8,
            soft_slot_match_floor=0.6,
            max_gray_keep=1,
            slot_specs=(
                SectionSlotSpec(
                    slot_id="approval_router",
                    label="Approval Router",
                    cue="审批卡点路由",
                    canonical_description="When approval gates the action, route to workflow logic.",
                    downstream_shape="matcher + route_target_action",
                    fresh_evidence_required=True,
                ),
            ),
        )
        candidate_rows = [
            RoutingHintItem.model_validate(
                {
                    "rule_id": "candidate_gray_keep_router",
                    "text": "When approval gates the next move, route to the workflow rule family.",
                    "query_feature_matcher": "approval gates the next move",
                    "route_target_action": "route to the workflow rule family",
                    "_reasoning_ref": "reasoning_approval",
                    "evidence_refs": ["scene:0003_001"],
                }
            )
        ]
        kept_rows, filter_trace = _filter_section_densify_candidates(
            candidate_rows=candidate_rows,
            existing_rows=[],
            missing_slots=[path_target.slot_specs[0]],
            path_target=path_target,
            max_keep=1,
            embedding_client=GrayKeepEmbeddingClient(SimpleNamespace(), artifacts_dir=Path(".")),
            request_key_prefix="unit_test_gray_keep",
            retrieved_reasoning_entries=[
                {
                    "reasoning_id": "reasoning_approval",
                    "bucket_id": "institutional_pipeline",
                    "axis_ids": ["institutional_pipeline"],
                    "claim": "Approval still gates the scene.",
                    "observed_commonality": "The scene waits on approval.",
                    "mechanism_inference": "Treat approval as a routing trigger.",
                    "downstream_constraint": "Route approval gates to workflow logic.",
                    "evidence_refs": ["scene:0003_001"],
                    "retrieval_score": 0.7,
                    "matched_slot_ids": ["approval_router"],
                }
            ],
        )

        self.assertEqual([row.rule_id for row in kept_rows], ["candidate_gray_keep_router"])
        self.assertEqual(filter_trace["gray_keep_count"], 1)
        self.assertEqual(filter_trace["candidates"][0]["status"], "keep_gray_slot")
        self.assertTrue(filter_trace["candidates"][0]["gray_keep_eligible"])
        self.assertTrue(filter_trace["candidates"][0]["fresh_slot_evidence_hit"])
        self.assertGreater(filter_trace["candidates"][0]["semantic_slot_score"], 0.0)
        self.assertGreater(filter_trace["candidates"][0]["cue_score"], 0.0)
        self.assertGreater(filter_trace["candidates"][0]["combined_score"], 0.0)
        self.assertGreater(filter_trace["candidates"][0]["evidence_overlap_score"], 0.0)

    def test_count_expansion_slots_allow_densify_when_slots_are_covered_but_count_is_low(self) -> None:
        path_target = SectionPathTarget(
            path="narrative_system.pacing_rules",
            target_count=4,
            max_new_rows=2,
            retrieval_top_k=4,
            downstream_shape="pacing rules",
            slot_match_threshold=0.7,
            soft_slot_match_floor=0.6,
            slot_specs=(
                SectionSlotSpec(
                    slot_id="approval_delay",
                    label="Approval Delay",
                    cue="approval delay",
                    canonical_description="Pacing stalls on approval.",
                ),
                SectionSlotSpec(
                    slot_id="repayment_countdown",
                    label="Repayment Countdown",
                    cue="repayment countdown",
                    canonical_description="Pacing tightens around repayment.",
                ),
            ),
        )

        slots = _select_count_expansion_slots(
            path_target,
            slot_coverage_trace=[
                {"slot_id": "approval_delay", "best_score": 0.91},
                {"slot_id": "repayment_countdown", "best_score": 0.83},
            ],
            deficit=3,
        )

        self.assertEqual([slot.slot_id for slot in slots], ["repayment_countdown", "approval_delay"])

    def test_global_merge_keeps_distinct_negative_rules_instead_of_empty_alias_merge(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["dark_humor", "resource_pressure"], hard_cap=4)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
        }
        base_targets = load_style_bible_section_targets()
        custom_targets = replace(
            base_targets,
            repair_max_rounds=0,
            densify_enabled=False,
            minimums={"negative_rules": 2},
            path_targets={},
        )
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _structured_reducer_response(
                reasoning_entries=[
                    {
                        "reasoning_id": "dark_humor_reasoning",
                        "bucket_id": "dark_humor",
                        "axis_ids": ["dark_humor"],
                        "claim": "Deadpan humor must not become broad comedy labels.",
                        "observed_commonality": "Scenes use dry notices rather than loud punchlines.",
                        "mechanism_inference": "Keep humor tied to operational wording.",
                        "downstream_constraint": "Ban loud punchlines.",
                        "evidence_refs": ["scene:0001_001"],
                        "anti_pattern_codes": ["none"],
                    }
                ],
                rule_rows=[
                    {
                        "surface_path": "negative_rules",
                        "rule_id": "dark_humor_negative",
                        "text": "Do not explain the joke as generic absurdity when a notice, price, or service phrase is doing the comic work.",
                        "forbidden_action": "Replace the procedural joke with a broad absurdity label.",
                        "correction_guideline": "Name the notice, price, or service phrase that creates the deadpan mismatch.",
                        "_reasoning_ref": "dark_humor_reasoning",
                        "evidence_refs": ["scene:0001_001"],
                    }
                ],
                elapsed_seconds=1.0,
            ),
            _structured_reducer_response(
                reasoning_entries=[
                    {
                        "reasoning_id": "resource_pressure_reasoning",
                        "bucket_id": "resource_pressure",
                        "axis_ids": ["resource_pressure"],
                        "claim": "Debt pressure must not collapse into vague suffering.",
                        "observed_commonality": "Scenes keep naming repayment and settlement mechanics.",
                        "mechanism_inference": "Keep hardship tied to payable mechanisms.",
                        "downstream_constraint": "Ban vague angst.",
                        "evidence_refs": ["scene:0002_001"],
                        "anti_pattern_codes": ["none"],
                    }
                ],
                rule_rows=[
                    {
                        "surface_path": "negative_rules",
                        "rule_id": "resource_pressure_negative",
                        "text": "Do not flatten debt, repayment, or shortage pressure into vague misery when a payable item is driving the scene.",
                        "forbidden_action": "Describe pressure as vague misery without the payable item.",
                        "correction_guideline": "Name the debt amount, repayment window, settlement order, or renewed bill.",
                        "_reasoning_ref": "resource_pressure_reasoning",
                        "evidence_refs": ["scene:0002_001"],
                    }
                ],
                elapsed_seconds=1.0,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.load_style_bible_section_targets",
                return_value=custom_targets,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [
                        _bucket_memo("dark_humor", "scene:0001_001"),
                        _bucket_memo("resource_pressure", "scene:0002_001"),
                    ],
                    Path(tmpdir),
                )

        negative_rules = result.record["negative_rules"]
        self.assertEqual(len(negative_rules), 2)
        merge_events = [
            row for row in result.reduce_trace["merge_events"]
            if row["surface_path"] == "negative_rules"
        ]
        self.assertFalse(
            any(row["group_key"] == "|" for row in merge_events),
            merge_events,
        )

    def test_hierarchical_reduce_section_densify_keeps_only_semantic_increment(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["institutional_pipeline"], hard_cap=4)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
        }
        institutional_memo = _bucket_memo("institutional_pipeline", "scene:0001_001")
        base_targets = load_style_bible_section_targets()
        routing_target = base_targets.densify_target_for_path("worldbook_binding.routing_hints")
        assert routing_target is not None
        custom_routing_target = replace(
            routing_target,
            target_count=2,
            max_new_rows=1,
            retrieval_top_k=2,
            bucket_allowlist=("institutional_pipeline",),
            slot_specs=tuple(routing_target.slot_specs[:2]),
        )
        custom_targets = replace(
            base_targets,
            repair_max_rounds=0,
            densify_enabled=True,
            densify_max_rounds=1,
            densify_max_paths_per_round=1,
            minimums={**base_targets.minimums, "worldbook_binding.routing_hints": 2},
            path_targets={"worldbook_binding.routing_hints": custom_routing_target},
        )
        FakeStructuredClient.call_count = 0
        FakeStructuredClient.responses = [
            _structured_reducer_response(
                reasoning_entries=[
                    {
                        "reasoning_id": "reasoning_notice",
                        "bucket_id": "institutional_pipeline",
                        "axis_ids": ["institutional_pipeline"],
                        "claim": "When a notice or approval governs the next beat, route it through workflow rules.",
                        "observed_commonality": "Scenes advance through notices, approvals, and workflow documents.",
                        "mechanism_inference": "Turn procedural notice moments into routing triggers.",
                        "downstream_constraint": "Use notice or approval matchers instead of generic emotion cues.",
                        "evidence_refs": ["scene:0001_001"],
                        "anti_pattern_codes": ["none"],
                    },
                    {
                        "reasoning_id": "reasoning_repayment",
                        "bucket_id": "institutional_pipeline",
                        "axis_ids": ["institutional_pipeline", "resource_pressure"],
                        "claim": "Debt repayment and cashflow settlement decide whether action can proceed.",
                        "observed_commonality": "The scene keeps surfacing debt, repayment, and cashflow windows.",
                        "mechanism_inference": "Treat repayment pressure as a routing trigger, not a generic hardship note.",
                        "downstream_constraint": "Route debt or cashflow gates to the repayment rule family.",
                        "evidence_refs": ["scene:0001_001"],
                        "anti_pattern_codes": ["none"],
                    },
                ],
                rule_rows=[
                    {
                        "surface_path": "worldbook_binding.routing_hints",
                        "rule_id": "institutional_pipeline__routing_01",
                        "text": "When a notice or approval decides the next move, route to the institutional workflow rule family.",
                        "query_feature_matcher": "notice or approval decides the next move",
                        "route_target_action": "route to the institutional workflow rule family",
                        "_reasoning_ref": "reasoning_notice",
                        "evidence_refs": ["scene:0001_001"],
                    }
                ],
                elapsed_seconds=1.0,
            ),
            _structured_reducer_response(
                reasoning_entries=[
                    {
                        "reasoning_id": "reasoning_repayment",
                        "bucket_id": "institutional_pipeline",
                        "axis_ids": ["institutional_pipeline", "resource_pressure"],
                        "claim": "Debt repayment and cashflow settlement decide whether action can proceed.",
                        "observed_commonality": "The scene keeps surfacing debt, repayment, and cashflow windows.",
                        "mechanism_inference": "Treat repayment pressure as a routing trigger, not a generic hardship note.",
                        "downstream_constraint": "Route debt or cashflow gates to the repayment rule family.",
                        "evidence_refs": ["scene:0001_001"],
                        "anti_pattern_codes": ["none"],
                    }
                ],
                rule_rows=[
                    {
                        "surface_path": "worldbook_binding.routing_hints",
                        "rule_id": "candidate_duplicate_notice",
                        "text": "If an approval notice determines the next move, route to the institutional workflow rule family.",
                        "query_feature_matcher": "approval notice determines the next move",
                        "route_target_action": "route to the institutional workflow rule family",
                        "_reasoning_ref": "reasoning_repayment",
                        "evidence_refs": ["scene:0001_001"],
                    },
                    {
                        "surface_path": "worldbook_binding.routing_hints",
                        "rule_id": "candidate_repayment_router",
                        "text": "When debt repayment or cashflow settlement gates the action, route to the repayment pressure rule family.",
                        "query_feature_matcher": "debt repayment or cashflow settlement gates the action",
                        "route_target_action": "route to the repayment pressure rule family",
                        "_reasoning_ref": "reasoning_repayment",
                        "evidence_refs": ["scene:0001_001"],
                    },
                ],
                elapsed_seconds=0.9,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleStructuredClient",
                FakeStructuredClient,
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.StableOpenAICompatibleEmbeddingClient",
                FakeEmbeddingClient,
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.load_style_bible_section_targets",
                return_value=custom_targets,
            ):
                result = reduce_style_bible_from_bucket_memos(
                    config,
                    source_bundle,
                    [institutional_memo],
                    Path(tmpdir),
                )

            self.assertEqual(FakeStructuredClient.call_count, 2)
            self.assertEqual(result.request_metrics["section_densify_attempt_count"], 1)
            self.assertEqual(result.request_metrics["section_densify_success_count"], 1)
            self.assertEqual(
                result.request_metrics["section_densify_paths"],
                ["worldbook_binding.routing_hints"],
            )
            routing_hints = result.record["worldbook_binding"]["routing_hints"]
            self.assertEqual(len(routing_hints), 2)
            self.assertEqual(
                routing_hints[0]["query_feature_matcher"],
                "notice or approval decides the next move",
            )
            self.assertEqual(
                routing_hints[1]["query_feature_matcher"],
                "debt repayment or cashflow settlement gates the action",
            )
            section_densify_trace = result.reduce_trace["section_densify"]
            self.assertEqual(len(section_densify_trace), 1)
            self.assertEqual(section_densify_trace[0]["status"], "success")
            self.assertEqual(section_densify_trace[0]["kept_rule_count"], 1)
            candidate_statuses = {
                row["rule_id"]: row["status"]
                for row in section_densify_trace[0]["candidate_filter"]["candidates"]
            }
            self.assertEqual(
                candidate_statuses["section_densify__worldbook_binding_routing_hints__candidate_duplicate_notice"],
                "drop_slot_miss",
            )
            self.assertEqual(
                candidate_statuses["section_densify__worldbook_binding_routing_hints__candidate_repayment_router"],
                "keep",
            )
            summary_path = (
                Path(tmpdir)
                / "_section_densify"
                / "worldbook_binding_routing_hints"
                / "pass_01"
                / "section_densify_summary.json"
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["kept_rule_count"], 1)
            aggregate_path = Path(tmpdir) / "semantic_dedupe_drop_pairs_aggregate.json"
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
            self.assertEqual(aggregate["pair_file_count"], 1)
            self.assertIn("feature_flags", result.reduce_trace)
            self.assertEqual(result.reduce_trace["final_decision_source"], "hierarchical_reducer")
            self.assertNotIn("semantic_observability", result.reduce_trace)
            self.assertNotIn("semantic_sidecar", result.reduce_trace)
            self.assertEqual(aggregate["drop_pair_count"], 0)

    def test_resume_reruns_sparse_critical_bucket_before_merge(self) -> None:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        config = _config(prompt_dir, critical_buckets=["institutional_pipeline"], hard_cap=1)
        source_bundle = {
            "style_bible_id_hint": "style.demo",
            "scope_hint": "novel",
            "story_node_scope": {},
        }
        dark_humor_memo = _bucket_memo("dark_humor", "scene:0001_001")
        institutional_memo = _bucket_memo("institutional_pipeline", "scene:0002_001")
        existing_artifact = SimpleNamespace(bucket_id="dark_humor", sparse=False, repair_passes=[])
        sparse_artifact = SimpleNamespace(bucket_id="institutional_pipeline", sparse=True, repair_passes=[])
        rerun_artifact = SimpleNamespace(bucket_id="institutional_pipeline", sparse=False, repair_passes=[])

        def _assert_complete(*args: object, **kwargs: object) -> str:
            observed = kwargs["observed_local_artifacts"]
            local_artifacts = kwargs["local_artifacts"]
            failed_bucket_ids = kwargs["failed_bucket_ids"]
            skipped_sparse_bucket_ids = kwargs["skipped_sparse_bucket_ids"]
            self.assertEqual([artifact.bucket_id for artifact in observed], ["dark_humor", "institutional_pipeline"])
            self.assertEqual([artifact.bucket_id for artifact in local_artifacts], ["dark_humor", "institutional_pipeline"])
            self.assertFalse(failed_bucket_ids)
            self.assertFalse(skipped_sparse_bucket_ids)
            return "completed"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator.load_style_bible_section_targets",
                return_value=load_style_bible_section_targets(),
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator._load_resumable_local_reduce_artifacts",
                return_value=([existing_artifact, sparse_artifact], [existing_artifact], [], ["institutional_pipeline"]),
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator._evaluate_local_reduce_preflight",
                return_value=SimpleNamespace(skip=False),
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator._run_local_reduce",
                return_value=rerun_artifact,
            ) as run_local_reduce_mock, patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator._run_section_repair_passes",
                side_effect=lambda *args, **kwargs: list(kwargs["local_artifacts"]),
            ), patch(
                "novel_pipeline_stable.style_bible_reduction.orchestrator._complete_hierarchical_reduce_from_local_artifacts",
                side_effect=_assert_complete,
            ):
                result = _resume_style_bible_hierarchical_from_bucket_memos(
                    config,
                    source_bundle,
                    [dark_humor_memo, institutional_memo],
                    Path(tmpdir),
                )

        self.assertEqual(result, "completed")
        run_local_reduce_mock.assert_called_once()
        self.assertEqual(run_local_reduce_mock.call_args.kwargs["request_key_suffix"], "__resume_rerun")


if __name__ == "__main__":
    unittest.main()
