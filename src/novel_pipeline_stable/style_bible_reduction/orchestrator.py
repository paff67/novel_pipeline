from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
from novel_pipeline_stable.embedding_client import StableOpenAICompatibleEmbeddingClient
from novel_pipeline_stable.config import StableProjectConfig
from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json
from novel_pipeline_stable.models import (
    LocalRuleRow,
    NarrativeRuleItem,
    NegativeRuleItem,
    RoutingHintItem,
    ScalarRuleItem,
    StyleBibleAssemblerConflict,
    StyleBibleBucketMemo,
    StyleBibleEvidence,
    StyleBibleLocalReducerOutput,
    StyleBibleMergeEvent,
    StyleBibleReduceCrossValidationStep,
    StyleBibleReasoningBundle,
    StyleBibleReasoningEntry,
    StyleBibleReduceTraceEntry,
    StyleBibleResult,
    StyleBibleResultV2,
    StyleBibleRuleBase,
    StyleBibleRuleLineageEntry,
    WorldbookFactItem,
    coerce_style_bible_rule_item,
    style_bible_payload_to_flat,
)
from novel_pipeline_stable.style_bible_surface_specs import (
    LIST_SURFACE_PATHS,
    SCALAR_ENUM_SPECS,
    SCALAR_SURFACE_PATH_ALIASES,
    SCALAR_SURFACE_PATHS,
    SURFACE_PATH_SPECS,
    ScalarEnumSpec,
    canonical_scalar_surface_path,
    canonicalize_scalar_value,
    scalar_enum_spec_for_path,
    scalar_value_aliases_for_path,
    scalar_value_lookup_rows,
)
from novel_pipeline_stable.style_bible_contracts import EXPORT_FLAT_FILE, REASONING_FILE, REDUCE_TRACE_FILE
from novel_pipeline_stable.style_bible_inputs import clean_text
from novel_pipeline_stable.style_bible_prompt_assembler import (
    assemble_local_reducer_prompt,
    assemble_section_densify_prompt,
)
from novel_pipeline_stable.style_bible_runtime_flags import (
    StyleBibleRuntimeFlags,
    load_style_bible_runtime_flags,
)
from novel_pipeline_stable.style_bible_section_targets import (
    SectionPathTarget,
    SectionSlotSpec,
    StyleBibleSectionTargets,
    load_style_bible_section_targets,
)


LIST_RULE_PATHS = LIST_SURFACE_PATHS
OPTIONAL_RULE_PATHS = SCALAR_SURFACE_PATHS
SURFACE_PATH_SPECS_BY_VALUE = {surface_path.value: spec for surface_path, spec in SURFACE_PATH_SPECS.items()}
SURFACE_PATH_LOOKUP_ALIASES = {
    surface_path.value.replace(".", "_"): surface_path.value
    for surface_path in SURFACE_PATH_SPECS
}

FINAL_RULE_TYPE_BY_ROW_MODEL: dict[str, type[StyleBibleRuleBase]] = {
    "NarrativeRuleItem": NarrativeRuleItem,
    "WorldbookFactItem": WorldbookFactItem,
    "RoutingHintItem": RoutingHintItem,
    "NegativeRuleItem": NegativeRuleItem,
    "ScalarRuleItem": ScalarRuleItem,
}

OPTIONAL_SCALAR_RULE_PATH_ALIASES = {
    "narrative_system_perspective": "narrative_system.perspective",
    "narrative_system_distance": "narrative_system.distance",
    "narrative_system_temporality": "narrative_system.temporality",
    "voice_contract_narrator_voice": "voice_contract.narrator_voice",
    "voice_contract_inner_monologue_mode": "voice_contract.inner_monologue_mode",
}

REPAIR_CONTROL_PLANE_PRIORITY_PATHS = {
    "voice_contract.register_mix",
    "voice_contract.negative_pitfalls",
    "character_arc_rules",
    "negative_rules",
}


@dataclass(frozen=True, slots=True)
class OptionalScalarRuleSpec:
    allowed_values: tuple[str, ...]
    default_value: str = ""
    constraint_template: str = ""
    default_when_missing: bool = False


OPTIONAL_SCALAR_RULE_SPECS = {
    "narrative_system.perspective": OptionalScalarRuleSpec(
        allowed_values=("close_third_person", "limited_first_person", "restricted_omniscient"),
        default_value="close_third_person",
        constraint_template="视角枚举必须选择 `{value}`",
        default_when_missing=True,
    ),
    "narrative_system.distance": OptionalScalarRuleSpec(
        allowed_values=("close", "medium", "far"),
        default_value="close",
        constraint_template="叙事距离枚举必须选择 `{value}`",
        default_when_missing=True,
    ),
    "narrative_system.temporality": OptionalScalarRuleSpec(
        allowed_values=("linear_forward",),
        default_value="linear_forward",
        constraint_template="时间组织默认使用 `{value}` 主线",
    ),
    "voice_contract.narrator_voice": OptionalScalarRuleSpec(
        allowed_values=("deadpan_procedural",),
        default_value="deadpan_procedural",
        constraint_template="旁白音色默认选择 `{value}`",
    ),
    "voice_contract.inner_monologue_mode": OptionalScalarRuleSpec(
        allowed_values=("sparse_inline", "quoted_fragments", "free_indirect"),
        default_value="sparse_inline",
        constraint_template="内心独白模式枚举必须选择 `{value}`",
        default_when_missing=True,
    ),
}

OptionalScalarRuleSpec = ScalarEnumSpec
OPTIONAL_SCALAR_RULE_PATH_ALIASES = SCALAR_SURFACE_PATH_ALIASES
OPTIONAL_SCALAR_RULE_SPECS = SCALAR_ENUM_SPECS


def _surface_path_spec(path: str):
    normalized_path = _canonical_surface_path(path)
    spec = SURFACE_PATH_SPECS_BY_VALUE.get(normalized_path)
    if spec is None:
        raise KeyError(f"Unknown surface path: {normalized_path}")
    return spec


def _canonical_surface_path(path: str) -> str:
    normalized_path = clean_text(path)
    if normalized_path in SURFACE_PATH_SPECS_BY_VALUE:
        return normalized_path
    return SURFACE_PATH_LOOKUP_ALIASES.get(normalized_path, normalized_path)


def _rule_field_text(rule: StyleBibleRuleBase, field_name: str) -> str:
    return clean_text(getattr(rule, field_name, ""))


def _build_rule_item_for_path(
    *,
    path: str,
    rule_id: str,
    text: str,
    reasoning_ref: str,
    evidence_refs: Iterable[str],
    anti_pattern_codes: Iterable[str],
    trigger: str = "",
    constraint: str = "",
    query_feature_matcher: str = "",
    route_target_action: str = "",
    forbidden_action: str = "",
    correction_guideline: str = "",
) -> StyleBibleRuleBase:
    spec = _surface_path_spec(path)
    model_type = FINAL_RULE_TYPE_BY_ROW_MODEL[spec.row_model]
    payload: dict[str, Any] = {
        "rule_id": clean_text(rule_id),
        "text": clean_text(text),
        "_reasoning_ref": clean_text(reasoning_ref),
        "evidence_refs": _unique_strings(evidence_refs),
        "anti_pattern_codes": _normalize_antipattern_codes(anti_pattern_codes),
    }
    if model_type in (NarrativeRuleItem, WorldbookFactItem):
        payload["trigger"] = clean_text(trigger)
        payload["constraint"] = clean_text(constraint)
    elif model_type is RoutingHintItem:
        payload["query_feature_matcher"] = clean_text(query_feature_matcher)
        payload["route_target_action"] = clean_text(route_target_action)
    elif model_type is NegativeRuleItem:
        payload["forbidden_action"] = clean_text(forbidden_action)
        payload["correction_guideline"] = clean_text(correction_guideline)
    elif model_type is ScalarRuleItem:
        payload["text"] = clean_text(canonicalize_scalar_value(spec.path.value, payload["text"]))
    return model_type.model_validate(payload)


LOCAL_REDUCE_DIR = "_local_reduce"
SECTION_DENSIFY_DIR = "_section_densify"
SEMANTIC_DEDUPE_AGGREGATE_FILE = "semantic_dedupe_drop_pairs_aggregate.json"


@dataclass(slots=True)
class DropTracker:
    counters: dict[str, int] = field(default_factory=dict)

    def track(self, reason: str, count: int = 1):
        self.counters[reason] = self.counters.get(reason, 0) + count

    def dump(self) -> dict[str, int]:
        return dict(self.counters)


def _determine_empty_status(tracker: DropTracker | None = None, candidate_filter_trace: dict[str, Any] | None = None) -> str:
    if candidate_filter_trace and candidate_filter_trace.get("semantic_dedupe_drop_count", 0) > 0:
        return "candidate_filtered_by_semantic_dedupe"
    if candidate_filter_trace and candidate_filter_trace.get("slot_mismatch_drop_count", 0) > 0:
        return "candidate_filtered_by_slot_mismatch"
    if not tracker or not tracker.counters:
        return "filtered_empty"
    
    max_reason = max(tracker.counters.items(), key=lambda x: x[1])[0]
    return f"filtered_{max_reason}"


@dataclass(slots=True)
class StyleBibleReduceResult:
    output_path: Path
    reasoning_path: Path
    export_flat_path: Path
    reduce_trace_path: Path
    record: dict[str, Any]
    reasoning_record: dict[str, Any]
    export_flat_record: dict[str, Any]
    reduce_trace: dict[str, Any]
    request_metrics: dict[str, Any]
    usage_metadata: dict[str, Any]
    reduced_item_ids: set[str]
    reduced_chapter_ids: set[str]
    reduced_refs: set[str]
    reduce_mode: str = "hierarchical"
    prompt_name: str = "style_bible_local_reduce.md"
    local_artifact_root: Path | None = None
    failed_bucket_ids: list[str] = field(default_factory=list)
    skipped_sparse_bucket_ids: list[str] = field(default_factory=list)
    critical_bucket_ids: list[str] = field(default_factory=list)
    degraded_success: bool = False
    assembler_conflicts: list[dict[str, Any]] = field(default_factory=list)
    semantic_reconcile_sections: list[str] = field(default_factory=list)


class StyleBibleReduceGuardrailError(RuntimeError):
    pass


class CriticalBucketReduceError(RuntimeError):
    pass


@dataclass(slots=True)
class LocalReduceArtifact:
    bucket_id: str
    memo_id: str
    batch_ids: list[str]
    output_dir: Path
    final_result: StyleBibleResultV2
    partial_record: dict[str, Any]
    reasoning_bundle: StyleBibleReasoningBundle
    reasoning_record: dict[str, Any]
    reduce_trace: dict[str, Any]
    request_metrics: dict[str, Any]
    usage_metadata: dict[str, Any]
    reduced_refs: set[str]
    grounding_ref_pool: set[str] = field(default_factory=set)
    sparse: bool = False
    assembler_conflicts: list[StyleBibleAssemblerConflict] = field(default_factory=list)
    preflight_decision: dict[str, Any] = field(default_factory=dict)
    repair_passes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LocalReducePreflightDecision:
    skip: bool
    reason: str
    candidate_count: int
    grounding_ref_count: int
    batch_memo_count: int
    item_count: int


@dataclass(frozen=True, slots=True)
class SectionGap:
    path: str
    gap_type: str
    actual_count: int
    target_count: int
    deficit: int


@dataclass(frozen=True, slots=True)
class SectionDensifyRequest:
    path: str
    actual_count: int
    target_count: int
    deficit: int
    path_target: SectionPathTarget


@dataclass(slots=True)
class GlobalMergeAssembly:
    final_result: StyleBibleResultV2
    reasoning_bundle: StyleBibleReasoningBundle
    assembler_conflicts: list[StyleBibleAssemblerConflict]
    rule_lineage_records: list[StyleBibleRuleLineageEntry]
    merge_events: list[StyleBibleMergeEvent]


def _unique_strings(values: Iterable[Any], *, limit: int | None = None) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if limit is not None and len(results) >= limit:
            break
    return results


def _normalize_text_key(value: str) -> str:
    return re.sub(r"\s+", "", clean_text(value))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", clean_text(value).lower()).strip("_")
    return slug or "rule"


def _bucket_scoped_identifier(prefix: str, candidate: str, fallback: str) -> str:
    normalized_prefix = clean_text(prefix)
    normalized_candidate = clean_text(candidate)
    normalized_fallback = clean_text(fallback)
    if not normalized_prefix:
        return normalized_candidate or normalized_fallback
    if normalized_candidate.startswith(f"{normalized_prefix}__"):
        return normalized_candidate
    suffix = normalized_candidate or normalized_fallback
    return f"{normalized_prefix}__{suffix}" if suffix else normalized_prefix


def _normalize_antipattern_codes(values: Iterable[Any]) -> list[str]:
    codes = _unique_strings(values)
    meaningful_codes = [code for code in codes if code != "none"]
    return meaningful_codes or ["none"]


def _load_bucket_memo_payloads(bucket_memos: Iterable[StyleBibleBucketMemo | dict[str, Any]]) -> list[StyleBibleBucketMemo]:
    payloads: list[StyleBibleBucketMemo] = []
    for payload in bucket_memos:
        if isinstance(payload, StyleBibleBucketMemo):
            payloads.append(payload)
            continue
        payloads.append(StyleBibleBucketMemo.model_validate(payload))
    return payloads


def load_style_bible_bucket_memos(memo_dir: str | Path) -> list[StyleBibleBucketMemo]:
    memo_path = Path(memo_dir).resolve()
    if not memo_path.exists():
        raise FileNotFoundError(f"Bucket memo directory not found: {memo_path}")
    payloads: list[StyleBibleBucketMemo] = []
    for path in sorted(memo_path.glob("*.json")):
        payloads.append(StyleBibleBucketMemo.model_validate(read_json(path)))
    if not payloads:
        raise FileNotFoundError(f"No bucket memo JSON files found in {memo_path}")
    return payloads


def _load_latest_request_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    rows = [row for row in read_jsonl(path) if isinstance(row, dict)]
    return dict(rows[-1]) if rows else {}


def _fallback_usage_metadata_from_request_metrics(request_metrics: dict[str, Any], *, stage: str) -> dict[str, Any]:
    raw_usage = request_metrics.get("usage_metadata", {})
    if not isinstance(raw_usage, dict):
        raw_usage = {}
    prompt_tokens = _usage_tokens(raw_usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_tokens(raw_usage, "output_tokens", "completion_tokens")
    total_tokens = _usage_tokens(raw_usage, "total_tokens") or (prompt_tokens + output_tokens)
    cached_tokens = _extract_cached_tokens(raw_usage)
    return {
        "stage": clean_text(request_metrics.get("stage")) or stage,
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        "ttft_seconds": round(_first_chunk_seconds(request_metrics) or 0.0, 3),
        "selected_antipattern_codes": _unique_strings(request_metrics.get("selected_antipattern_codes", [])),
        "anti_pattern_token_budget": int(request_metrics.get("anti_pattern_token_budget", 0) or 0),
        "anti_pattern_token_estimate": int(request_metrics.get("anti_pattern_token_estimate", 0) or 0),
        "raw_usage_metadata": raw_usage,
    }


def _load_local_reduce_artifact_from_dir(
    *,
    bucket_memo: StyleBibleBucketMemo,
    output_dir: Path,
    source_bundle: dict[str, Any],
) -> LocalReduceArtifact:
    output_dir = Path(output_dir).resolve()
    style_id_hint = clean_text(source_bundle.get("style_bible_id_hint"))
    scope_hint = clean_text(source_bundle.get("scope_hint"))
    summary_path = output_dir / "local_reduce_summary.json"
    final_path = output_dir / "local_final.json"
    partial_path = output_dir / "local_partial.json"
    reasoning_path = output_dir / "local_reasoning.json"
    reduce_trace_path = output_dir / "local_reduce_trace.json"

    summary_record = read_json(summary_path) if summary_path.exists() else {}
    if not isinstance(summary_record, dict):
        summary_record = {}
    request_metrics = summary_record.get("request_metrics", {})
    if not isinstance(request_metrics, dict) or not request_metrics:
        request_metrics = _load_latest_request_metrics(output_dir / "request_metrics.jsonl")
    usage_metadata = summary_record.get("usage_metadata", {})
    if not isinstance(usage_metadata, dict) or not usage_metadata:
        usage_metadata = _fallback_usage_metadata_from_request_metrics(
            request_metrics if isinstance(request_metrics, dict) else {},
            stage="style_bible_local_reduce",
        )

    partial_record = (
        read_json(partial_path)
        if partial_path.exists()
        else _empty_local_partial_record(style_id_hint=style_id_hint, scope_hint=scope_hint)
    )
    if not isinstance(partial_record, dict):
        partial_record = _empty_local_partial_record(style_id_hint=style_id_hint, scope_hint=scope_hint)

    final_payload = read_json(final_path) if final_path.exists() else {}
    if isinstance(final_payload, dict) and final_payload:
        final_result = StyleBibleResultV2.model_validate(final_payload)
    else:
        final_result = StyleBibleResultV2(style_id=style_id_hint, scope=scope_hint)

    reasoning_payload = read_json(reasoning_path) if reasoning_path.exists() else {}
    if isinstance(reasoning_payload, dict) and reasoning_payload:
        reasoning_bundle = StyleBibleReasoningBundle.model_validate(reasoning_payload)
        reasoning_record = dict(reasoning_payload)
    else:
        reasoning_bundle = StyleBibleReasoningBundle(
            reasoning_version="style-bible-reasoning-v2",
            style_id=style_id_hint,
            scope=scope_hint,
            entries=[],
        )
        reasoning_record = reasoning_bundle.model_dump(mode="json")

    reduce_trace = read_json(reduce_trace_path) if reduce_trace_path.exists() else {}
    if not isinstance(reduce_trace, dict):
        reduce_trace = {}
    assembler_conflicts = [
        StyleBibleAssemblerConflict.model_validate(row)
        for row in reduce_trace.get("assembler_conflicts", [])
        if isinstance(row, dict)
    ]
    reduced_refs = _collect_reduced_refs(final_result, reasoning_bundle=reasoning_bundle)
    status = clean_text(summary_record.get("status")) or ("sparse" if bool(reduce_trace.get("sparse")) else "success")
    return LocalReduceArtifact(
        bucket_id=clean_text(summary_record.get("bucket_id")) or clean_text(bucket_memo.bucket_id),
        memo_id=clean_text(summary_record.get("memo_id")) or clean_text(bucket_memo.memo_id),
        batch_ids=_unique_strings(summary_record.get("batch_ids", []) or _batch_id_pool([bucket_memo])),
        output_dir=output_dir,
        final_result=final_result,
        partial_record=partial_record,
        reasoning_bundle=reasoning_bundle,
        reasoning_record=reasoning_record,
        reduce_trace=reduce_trace,
        request_metrics=request_metrics if isinstance(request_metrics, dict) else {},
        usage_metadata=usage_metadata,
        reduced_refs=reduced_refs,
        grounding_ref_pool=set(_grounding_ref_pool([bucket_memo])),
        sparse=(status == "sparse"),
        assembler_conflicts=assembler_conflicts,
        preflight_decision=dict(reduce_trace.get("preflight", {})) if isinstance(reduce_trace.get("preflight", {}), dict) else {},
        repair_passes=list(summary_record.get("repair_passes", []) or reduce_trace.get("repair_passes", []) or []),
    )


def _synthesize_repair_request(
    *,
    base_artifact: LocalReduceArtifact,
    repair_artifact: LocalReduceArtifact,
    section_targets: StyleBibleSectionTargets,
) -> dict[str, Any]:
    requested_paths = [
        path
        for path in [*LIST_RULE_PATHS, *OPTIONAL_RULE_PATHS]
        if _count_rule_path_items(repair_artifact.final_result, path) > 0
    ]
    missing_scalar_paths = [
        path
        for path in OPTIONAL_RULE_PATHS
        if _count_rule_path_items(base_artifact.final_result, path) <= 0
        and _count_rule_path_items(repair_artifact.final_result, path) > 0
    ]
    underfilled_paths: list[dict[str, Any]] = []
    for path in LIST_RULE_PATHS:
        repair_count = _count_rule_path_items(repair_artifact.final_result, path)
        if repair_count <= 0:
            continue
        actual_count = _count_rule_path_items(base_artifact.final_result, path)
        target_count = max(int(section_targets.minimums.get(path, 0) or 0), actual_count + repair_count)
        underfilled_paths.append(
            {
                "path": path,
                "actual_count": int(actual_count),
                "target_count": int(target_count),
                "deficit": max(int(target_count) - int(actual_count), 0),
            }
        )
    return {
        "mode": "repair",
        "requested_paths": _unique_strings(requested_paths),
        "missing_scalar_paths": _unique_strings(missing_scalar_paths),
        "underfilled_paths": underfilled_paths,
    }


def _load_resumable_local_reduce_artifacts(
    *,
    memo_payloads: list[StyleBibleBucketMemo],
    source_bundle: dict[str, Any],
    local_artifact_root: Path,
    section_targets: StyleBibleSectionTargets,
) -> tuple[list[LocalReduceArtifact], list[LocalReduceArtifact], list[str], list[str]]:
    observed_local_artifacts: list[LocalReduceArtifact] = []
    local_artifacts: list[LocalReduceArtifact] = []
    failed_bucket_ids: list[str] = []
    skipped_sparse_bucket_ids: list[str] = []
    for bucket_memo in memo_payloads:
        bucket_id = clean_text(bucket_memo.bucket_id)
        bucket_output_dir = local_artifact_root / bucket_id
        summary_path = bucket_output_dir / "local_reduce_summary.json"
        final_path = bucket_output_dir / "local_final.json"
        reasoning_path = bucket_output_dir / "local_reasoning.json"
        if not summary_path.exists() or not final_path.exists() or not reasoning_path.exists():
            failed_bucket_ids.append(bucket_id)
            continue

        try:
            artifact = _load_local_reduce_artifact_from_dir(
                bucket_memo=bucket_memo,
                output_dir=bucket_output_dir,
                source_bundle=source_bundle,
            )
            repair_history_present = bool(artifact.repair_passes)
            repair_root = bucket_output_dir / "_repair_passes"
            if repair_root.exists() and not repair_history_present:
                for repair_dir in sorted(path for path in repair_root.glob("pass_*") if path.is_dir()):
                    repair_final_path = repair_dir / "local_final.json"
                    repair_reasoning_path = repair_dir / "local_reasoning.json"
                    if not repair_final_path.exists() or not repair_reasoning_path.exists():
                        continue
                    repair_artifact = _load_local_reduce_artifact_from_dir(
                        bucket_memo=bucket_memo,
                        output_dir=repair_dir,
                        source_bundle=source_bundle,
                    )
                    repair_summary_path = repair_dir / "local_reduce_summary.json"
                    repair_summary = read_json(repair_summary_path) if repair_summary_path.exists() else {}
                    if not isinstance(repair_summary, dict):
                        repair_summary = {}
                    repair_request = {}
                    summary_request_metrics = repair_summary.get("request_metrics", {})
                    if isinstance(summary_request_metrics, dict):
                        repair_request = dict(summary_request_metrics.get("repair_request", {}) or {})
                    if not repair_request:
                        repair_request = _synthesize_repair_request(
                            base_artifact=artifact,
                            repair_artifact=repair_artifact,
                            section_targets=section_targets,
                        )
                    artifact = _merge_local_artifact_with_repair(
                        artifact,
                        repair_artifact,
                        repair_request=repair_request,
                    )
        except Exception as exc:  # noqa: BLE001
            _write_failed_local_reduce_summary(bucket_output_dir, bucket_id=bucket_id, exc=exc)
            failed_bucket_ids.append(bucket_id)
            continue
        observed_local_artifacts.append(artifact)
        if artifact.sparse:
            skipped_sparse_bucket_ids.append(bucket_id)
            continue
        local_artifacts.append(artifact)
    return observed_local_artifacts, local_artifacts, failed_bucket_ids, skipped_sparse_bucket_ids


def _grounding_ref_pool(bucket_memos: list[StyleBibleBucketMemo]) -> list[str]:
    return _unique_strings(
        clean_text(ref)
        for memo in bucket_memos
        for ref in (
            *memo.allowed_refs,
            *(candidate_ref for candidate in memo.rule_candidates for candidate_ref in candidate.evidence_refs),
            *(batch_ref for batch_memo in memo.batch_memos for batch_ref in batch_memo.allowed_refs),
            *(
                candidate_ref
                for batch_memo in memo.batch_memos
                for candidate in batch_memo.rule_candidates
                for candidate_ref in candidate.evidence_refs
            ),
        )
        if clean_text(ref)
    )


def _memo_id_pool(bucket_memos: list[StyleBibleBucketMemo]) -> list[str]:
    return _unique_strings(
        memo_id
        for memo in bucket_memos
        for memo_id in (
            memo.memo_id,
            *(batch_memo.memo_id for batch_memo in memo.batch_memos),
        )
    )


def _batch_id_pool(bucket_memos: list[StyleBibleBucketMemo]) -> list[str]:
    return _unique_strings(
        batch_memo.batch_id
        for memo in bucket_memos
        for batch_memo in memo.batch_memos
    )


def _bucket_item_count(bucket_memo: StyleBibleBucketMemo) -> int:
    return len(
        _unique_strings(
            [
                *bucket_memo.item_ids,
                *(item_id for batch_memo in bucket_memo.batch_memos for item_id in batch_memo.item_ids),
            ]
        )
    )


def _bucket_item_ids(bucket_memo: StyleBibleBucketMemo) -> set[str]:
    return set(
        _unique_strings(
            [
                *bucket_memo.item_ids,
                *(item_id for batch_memo in bucket_memo.batch_memos for item_id in batch_memo.item_ids),
            ]
        )
    )


def _bucket_chapter_ids(bucket_memo: StyleBibleBucketMemo) -> set[str]:
    return set(
        _unique_strings(
            [
                *bucket_memo.chapter_ids,
                *(chapter_id for batch_memo in bucket_memo.batch_memos for chapter_id in batch_memo.chapter_ids),
            ]
        )
    )


def _compact_scalar_candidate_rows(rows: Any, *, path: str = "") -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized_path = canonical_scalar_surface_path(path)
    merged_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = clean_text(row.get("value"))
        if not value or value == "unspecified":
            continue
        canonical_value = canonicalize_scalar_value(normalized_path, value) if normalized_path else value
        canonical_value = clean_text(canonical_value)
        if not canonical_value or canonical_value == "unspecified":
            continue
        entry = merged_rows.setdefault(
            canonical_value,
            {
                "value": canonical_value,
                "count": 0,
                "source_refs": [],
            },
        )
        entry["count"] += max(int(row.get("count", 0) or 0), 1)
        for source_ref in _unique_strings(row.get("source_refs", []), limit=8):
            if source_ref not in entry["source_refs"] and len(entry["source_refs"]) < 4:
                entry["source_refs"].append(source_ref)
    return sorted(
        merged_rows.values(),
        key=lambda item: (-int(item["count"]), clean_text(item["value"])),
    )


def _normalize_scalar_lookup_token(value: str) -> str:
    lowered = clean_text(value).casefold()
    if not lowered:
        return ""
    return re.sub(r"[\s\-_/.]+", "", lowered)


def _scalar_lookup_in_text(text: str, alias: str) -> bool:
    normalized_text = clean_text(text).casefold()
    normalized_alias = clean_text(alias).casefold()
    if not normalized_text or not normalized_alias:
        return False
    if re.fullmatch(r"[0-9a-z_]+", normalized_alias):
        return bool(re.search(rf"(?<![0-9a-z_]){re.escape(normalized_alias)}(?![0-9a-z_])", normalized_text))
    compact_text = _normalize_scalar_lookup_token(normalized_text)
    compact_alias = _normalize_scalar_lookup_token(normalized_alias)
    return bool(compact_alias) and compact_alias in compact_text


def _filtered_bucket_style_window_samples(
    source_bundle: dict[str, Any],
    *,
    bucket_memo: StyleBibleBucketMemo,
    limit: int = 4,
) -> list[dict[str, Any]]:
    item_ids = _bucket_item_ids(bucket_memo)
    chapter_ids = _bucket_chapter_ids(bucket_memo)
    rows = source_bundle.get("style_window_samples", [])
    if not isinstance(rows, list):
        return []
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        window_id = clean_text(row.get("window_id"))
        row_chapter_ids = {
            chapter_id
            for chapter_id in _unique_strings(row.get("chapter_ids", []))
            if chapter_id
        }
        matches_item = window_id in item_ids if item_ids else False
        matches_chapter = bool(row_chapter_ids & chapter_ids) if chapter_ids else False
        if not matches_item and not matches_chapter:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _filtered_bucket_scene_signal_samples(
    source_bundle: dict[str, Any],
    *,
    bucket_memo: StyleBibleBucketMemo,
    limit: int = 4,
) -> list[dict[str, Any]]:
    item_ids = _bucket_item_ids(bucket_memo)
    chapter_ids = _bucket_chapter_ids(bucket_memo)
    rows = source_bundle.get("scene_signal_samples", [])
    if not isinstance(rows, list):
        return []
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scene_id = clean_text(row.get("scene_id"))
        chapter_id = clean_text(row.get("chapter_id"))
        scene_ref = f"scene:{scene_id}" if scene_id else ""
        matches_item = scene_ref in item_ids if item_ids else False
        matches_chapter = chapter_id in chapter_ids if chapter_ids else False
        if not matches_item and not matches_chapter:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _chapter_id_from_source_ref(source_ref: str) -> str:
    normalized_ref = clean_text(source_ref)
    if not normalized_ref:
        return ""
    if normalized_ref.startswith("chapter:"):
        return clean_text(normalized_ref.split(":", 1)[1])
    payload = normalized_ref
    if normalized_ref.startswith("scene:"):
        payload = normalized_ref.split(":", 1)[1]
    elif normalized_ref.startswith("scene_"):
        payload = normalized_ref.split("scene_", 1)[1]
    head = clean_text(payload.split("_", 1)[0])
    return head if head.isdigit() else ""


def _worldbook_atom_refs(row: dict[str, Any]) -> set[str]:
    refs = set(_unique_strings(row.get("grounding_refs", []), limit=12))
    source_ref = clean_text(row.get("source_ref"))
    if source_ref:
        refs.add(source_ref)
    return {ref for ref in refs if ref}


def _select_worldbook_atom_candidates(
    rows: Any,
    *,
    item_ids: set[str] | None = None,
    chapter_ids: set[str] | None = None,
    evidence_refs: set[str] | None = None,
    limit: int = 18,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    scoped_item_ids = {clean_text(item_id) for item_id in (item_ids or set()) if clean_text(item_id)}
    scoped_chapter_ids = {clean_text(chapter_id) for chapter_id in (chapter_ids or set()) if clean_text(chapter_id)}
    scoped_evidence_refs = {clean_text(ref) for ref in (evidence_refs or set()) if clean_text(ref)}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        atom_id = clean_text(row.get("atom_id"))
        atom_text = clean_text(row.get("text"))
        if not atom_text:
            continue
        atom_refs = _worldbook_atom_refs(row)
        atom_chapter_id = clean_text(row.get("chapter_id"))
        ref_chapter_ids = {
            chapter_id
            for chapter_id in (_chapter_id_from_source_ref(ref) for ref in atom_refs)
            if chapter_id
        }
        matches_item = bool(scoped_item_ids and atom_refs & scoped_item_ids)
        matches_evidence = bool(scoped_evidence_refs and atom_refs & scoped_evidence_refs)
        matches_chapter = bool(
            scoped_chapter_ids
            and (
                atom_chapter_id in scoped_chapter_ids
                or bool(ref_chapter_ids & scoped_chapter_ids)
            )
        )
        if scoped_evidence_refs:
            if not matches_evidence and not matches_chapter:
                continue
        elif not matches_item and not matches_chapter:
            continue
        dedupe_key = atom_id or _normalize_text_key(atom_text)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        selected.append(dict(row))
        if len(selected) >= limit:
            break
    return selected


def _filtered_bucket_worldbook_atom_candidates(
    source_bundle: dict[str, Any],
    *,
    bucket_memo: StyleBibleBucketMemo,
    limit: int = 18,
) -> list[dict[str, Any]]:
    return _select_worldbook_atom_candidates(
        source_bundle.get("worldbook_atom_candidates", []),
        item_ids=_bucket_item_ids(bucket_memo),
        chapter_ids=_bucket_chapter_ids(bucket_memo),
        limit=limit,
    )


def _filtered_densify_worldbook_atom_candidates(
    source_bundle: dict[str, Any],
    *,
    request: SectionDensifyRequest,
    selected_entries: list[StyleBibleReasoningEntry],
    limit: int = 18,
) -> list[dict[str, Any]]:
    if not clean_text(request.path).startswith("worldbook_binding."):
        return []
    evidence_refs = {
        clean_text(ref)
        for entry in selected_entries
        for ref in entry.evidence_refs
        if clean_text(ref)
    }
    chapter_ids = {
        chapter_id
        for chapter_id in (_chapter_id_from_source_ref(ref) for ref in evidence_refs)
        if chapter_id
    }
    return _select_worldbook_atom_candidates(
        source_bundle.get("worldbook_atom_candidates", []),
        chapter_ids=chapter_ids,
        evidence_refs=evidence_refs,
        limit=limit,
    )


def _build_section_signal_context(
    source_bundle: dict[str, Any],
    *,
    bucket_memo: StyleBibleBucketMemo,
) -> dict[str, Any]:
    global_style_signals = source_bundle.get("global_style_signals", {})
    scalar_contracts = {}
    if isinstance(global_style_signals, dict):
        scalar_payload = global_style_signals.get("scalar_contracts", {})
        if isinstance(scalar_payload, dict):
            scalar_contracts = {
                (
                    f"narrative_system.{key}"
                    if key in {"perspective", "distance", "temporality"}
                    else f"voice_contract.{key}"
                ): _compact_scalar_candidate_rows(
                    value,
                    path=(
                        f"narrative_system.{key}"
                        if key in {"perspective", "distance", "temporality"}
                        else f"voice_contract.{key}"
                    ),
                )
                for key, value in scalar_payload.items()
                if clean_text(key)
            }
    style_window_samples = _filtered_bucket_style_window_samples(
        source_bundle,
        bucket_memo=bucket_memo,
    )
    scene_signal_samples = _filtered_bucket_scene_signal_samples(
        source_bundle,
        bucket_memo=bucket_memo,
    )
    return {
        "bucket_item_ids": sorted(_bucket_item_ids(bucket_memo)),
        "bucket_chapter_ids": sorted(_bucket_chapter_ids(bucket_memo)),
        "global_scalar_contract_candidates": scalar_contracts,
        "style_window_samples": style_window_samples,
        "scene_signal_samples": scene_signal_samples,
    }


def _evaluate_local_reduce_preflight(bucket_memo: StyleBibleBucketMemo) -> LocalReducePreflightDecision:
    candidate_count = len(bucket_memo.rule_candidates)
    grounding_ref_count = len(_grounding_ref_pool([bucket_memo]))
    batch_memo_count = len(bucket_memo.batch_memos)
    item_count = _bucket_item_count(bucket_memo)
    skip = candidate_count <= 0 and grounding_ref_count <= 0 and batch_memo_count <= 0
    return LocalReducePreflightDecision(
        skip=skip,
        reason="empty_bucket_without_candidates_or_grounding" if skip else "ready",
        candidate_count=candidate_count,
        grounding_ref_count=grounding_ref_count,
        batch_memo_count=batch_memo_count,
        item_count=item_count,
    )


def _build_bucket_reduce_bundle(
    *,
    source_bundle: dict[str, Any],
    bucket_memo: StyleBibleBucketMemo,
) -> dict[str, Any]:
    grounding_ref_pool = _grounding_ref_pool([bucket_memo])
    return {
        "style_bible_id_hint": clean_text(source_bundle.get("style_bible_id_hint")),
        "scope_hint": clean_text(source_bundle.get("scope_hint")),
        "story_node_scope": source_bundle.get("story_node_scope", {}),
        "corpus_stats": source_bundle.get("corpus_stats", {}),
        "sampling": source_bundle.get("sampling", {}),
        "global_style_signals": source_bundle.get("global_style_signals", {}),
        "fact_signal_summary": source_bundle.get("fact_signal_summary", {}),
        "section_signal_context": _build_section_signal_context(
            source_bundle,
            bucket_memo=bucket_memo,
        ),
        "bucket_memo_summary": {
            "bucket_id": clean_text(bucket_memo.bucket_id),
            "candidate_count": len(bucket_memo.rule_candidates),
            "grounding_ref_count": len(grounding_ref_pool),
            "memo_ref_count": len(grounding_ref_pool),
        },
        "grounding_ref_pool": grounding_ref_pool,
        "memo_ref_pool": grounding_ref_pool,
        "bucket_memo": bucket_memo.model_dump(mode="json", by_alias=True),
    }


def _usage_tokens(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _nested_usage_tokens(usage: dict[str, Any], *path: str) -> int:
    current: Any = usage
    for part in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(part)
    if isinstance(current, (int, float)):
        return int(current)
    return 0


def _extract_cached_tokens(usage: dict[str, Any]) -> int:
    return (
        _nested_usage_tokens(usage, "prompt_tokens_details", "cached_tokens")
        or _nested_usage_tokens(usage, "input_tokens_details", "cached_tokens")
        or 0
    )


def _first_chunk_seconds(request_metrics: dict[str, Any]) -> float | None:
    attempts = request_metrics.get("attempts", [])
    if not isinstance(attempts, list):
        return None
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        value = attempt.get("first_chunk_seconds")
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    return None


def _sanitize_reasoning_bundle(
    reasoning: StyleBibleReasoningBundle,
    *,
    style_id_hint: str,
    scope_hint: str,
    memo_ref_pool: set[str],
    reasoning_id_prefix: str = "",
    tracker: DropTracker | None = None,
) -> StyleBibleReasoningBundle:
    rows: list[StyleBibleReasoningEntry] = []
    index_by_key: dict[str, int] = {}
    used_ids: set[str] = set()
    for entry in reasoning.entries:
        claim = clean_text(entry.claim) or clean_text(entry.mechanism_inference) or clean_text(entry.observed_commonality)
        refs = [ref for ref in _unique_strings(entry.evidence_refs) if ref in memo_ref_pool]
        if not claim or not refs:
            if tracker:
                if not claim:
                    tracker.track("reasoning_missing_claim")
                if not refs:
                    tracker.track("reasoning_missing_refs")
            continue
        key = _normalize_text_key(claim)
        if key in index_by_key:
            current = rows[index_by_key[key]]
            current.evidence_refs = _unique_strings([*current.evidence_refs, *refs])
            current.axis_ids = _unique_strings([*current.axis_ids, *entry.axis_ids])
            current.anti_pattern_codes = _normalize_antipattern_codes(
                [*current.anti_pattern_codes, *entry.anti_pattern_codes]
            )
            if not clean_text(current.downstream_constraint):
                current.downstream_constraint = clean_text(entry.downstream_constraint)
            continue
        reasoning_id = _bucket_scoped_identifier(
            reasoning_id_prefix,
            clean_text(entry.reasoning_id),
            f"reasoning_{len(rows) + 1:02d}",
        )
        while reasoning_id in used_ids:
            reasoning_id = _bucket_scoped_identifier(
                reasoning_id_prefix,
                f"{clean_text(entry.reasoning_id) or f'reasoning_{len(rows) + 1:02d}'}_{len(used_ids) + 1:02d}",
                f"reasoning_{len(rows) + 1:02d}_{len(used_ids) + 1:02d}",
            )
        used_ids.add(reasoning_id)
        rows.append(
            StyleBibleReasoningEntry(
                reasoning_id=reasoning_id,
                bucket_id=clean_text(entry.bucket_id),
                axis_ids=_unique_strings(entry.axis_ids),
                claim=claim,
                observed_commonality=clean_text(entry.observed_commonality) or claim,
                mechanism_inference=clean_text(entry.mechanism_inference) or claim,
                downstream_constraint=clean_text(entry.downstream_constraint),
                evidence_refs=refs,
                anti_pattern_codes=_normalize_antipattern_codes(entry.anti_pattern_codes),
            )
        )
        index_by_key[key] = len(rows) - 1

    return StyleBibleReasoningBundle(
        reasoning_version=clean_text(reasoning.reasoning_version) or "style-bible-reasoning-v2",
        style_id=clean_text(reasoning.style_id) or style_id_hint,
        scope=clean_text(reasoning.scope) or scope_hint,
        entries=rows,
    )


def _sanitize_local_reasoning_bundle(
    reasoning: StyleBibleReasoningBundle,
    *,
    bucket_id: str,
    style_id_hint: str,
    scope_hint: str,
    memo_ref_pool: set[str],
) -> StyleBibleReasoningBundle:
    return _sanitize_reasoning_bundle(
        reasoning,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        memo_ref_pool=memo_ref_pool,
        reasoning_id_prefix=clean_text(bucket_id),
    )


def _reasoning_lookup(
    reasoning_bundle: StyleBibleReasoningBundle,
) -> tuple[dict[str, StyleBibleReasoningEntry], dict[str, str]]:
    by_id = {entry.reasoning_id: entry for entry in reasoning_bundle.entries if clean_text(entry.reasoning_id)}
    by_text_key: dict[str, str] = {}
    for entry in reasoning_bundle.entries:
        for candidate in (entry.claim, entry.mechanism_inference, entry.downstream_constraint):
            key = _normalize_text_key(candidate)
            if key and key not in by_text_key:
                by_text_key[key] = entry.reasoning_id
    return by_id, by_text_key


def _coerce_rule_item(value: Any, *, path: str = "") -> StyleBibleRuleBase | None:
    canonical_path = _canonical_surface_path(path) or None
    return coerce_style_bible_rule_item(value, path=canonical_path)


def _infer_reasoning_ref_from_evidence_refs(
    *,
    rule_refs: list[str],
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
) -> str:
    candidate_refs = {ref for ref in _unique_strings(rule_refs) if ref}
    if not candidate_refs:
        return ""
    best_reasoning_ref = ""
    best_overlap = 0
    best_reasoning_size = 0
    for reasoning_ref, reasoning_entry in reasoning_by_id.items():
        overlap = len(candidate_refs.intersection(reasoning_entry.evidence_refs))
        if overlap <= 0:
            continue
        reasoning_size = len(reasoning_entry.evidence_refs)
        if overlap > best_overlap or (overlap == best_overlap and reasoning_size > best_reasoning_size):
            best_reasoning_ref = reasoning_ref
            best_overlap = overlap
            best_reasoning_size = reasoning_size
    return best_reasoning_ref


def _sanitize_reduce_cross_validation(
    rows: list[StyleBibleReduceCrossValidationStep],
    *,
    memo_id_pool: set[str],
    memo_ref_pool: set[str],
) -> list[StyleBibleReduceCrossValidationStep]:
    sanitized_rows: list[StyleBibleReduceCrossValidationStep] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for row in rows:
        source_memo_ids = [memo_id for memo_id in _unique_strings(row.source_memo_ids) if memo_id in memo_id_pool]
        matched_evidence_refs = [
            ref
            for ref in _unique_strings(row.matched_evidence_refs)
            if ref in memo_ref_pool
        ]
        mechanism = clean_text(row.extracted_common_mechanism)
        if not source_memo_ids or not matched_evidence_refs or not mechanism:
            continue
        key = (
            _normalize_text_key(mechanism),
            tuple(source_memo_ids),
            tuple(matched_evidence_refs),
        )
        if key in seen:
            continue
        seen.add(key)
        sanitized_rows.append(
            StyleBibleReduceCrossValidationStep(
                synthesis_step=clean_text(row.synthesis_step) or "合并近似规则",
                source_memo_ids=source_memo_ids,
                extracted_common_mechanism=mechanism,
                matched_evidence_refs=matched_evidence_refs,
            )
        )
    return sanitized_rows[:24]


def _sanitize_rule_item(
    value: Any,
    *,
    path: str,
    item_index: int,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
    reasoning_by_text_key: dict[str, str],
    memo_ref_pool: set[str],
    bucket_id_prefix: str = "",
    tracker: DropTracker | None = None,
) -> StyleBibleRuleBase | None:
    item = _coerce_rule_item(value, path=path)
    if item is None:
        if tracker:
            tracker.track(f"{path}_rule_coerce_failed")
        return None
    text = clean_text(item.text)
    if not text:
        if tracker:
            tracker.track(f"{path}_rule_missing_text")
        return None
    candidate_refs = [ref for ref in _unique_strings(item.evidence_refs) if ref in memo_ref_pool]
    reasoning_ref = clean_text(item.reasoning_ref)
    if bucket_id_prefix and reasoning_ref not in reasoning_by_id:
        scoped_reasoning_ref = _bucket_scoped_identifier(bucket_id_prefix, reasoning_ref, reasoning_ref)
        if scoped_reasoning_ref in reasoning_by_id:
            reasoning_ref = scoped_reasoning_ref
    if reasoning_ref not in reasoning_by_id:
        reasoning_ref = reasoning_by_text_key.get(_normalize_text_key(text), "")
    if reasoning_ref not in reasoning_by_id:
        reasoning_ref = _infer_reasoning_ref_from_evidence_refs(
            rule_refs=candidate_refs,
            reasoning_by_id=reasoning_by_id,
        )
    if reasoning_ref not in reasoning_by_id:
        if tracker:
            tracker.track(f"{path}_rule_lost_reasoning_ref")
        return None
    reasoning_entry = reasoning_by_id[reasoning_ref]
    refs = [ref for ref in candidate_refs if ref in reasoning_entry.evidence_refs]
    if not refs:
        refs = list(reasoning_entry.evidence_refs[:4])
    if not refs:
        if tracker:
            tracker.track(f"{path}_rule_lost_evidence_refs")
        return None
    rule_id = _bucket_scoped_identifier(
        bucket_id_prefix,
        clean_text(item.rule_id),
        f"{_slugify(path)}_rule_{item_index:02d}",
    )
    return _build_rule_item_for_path(
        path=path,
        rule_id=rule_id,
        text=text,
        trigger=_rule_field_text(item, "trigger"),
        constraint=_rule_field_text(item, "constraint"),
        query_feature_matcher=_rule_field_text(item, "query_feature_matcher"),
        route_target_action=_rule_field_text(item, "route_target_action"),
        forbidden_action=_rule_field_text(item, "forbidden_action"),
        correction_guideline=_rule_field_text(item, "correction_guideline"),
        reasoning_ref=reasoning_ref,
        evidence_refs=refs,
        anti_pattern_codes=item.anti_pattern_codes,
    )


def _sanitize_rule_list(
    values: list[Any],
    *,
    path: str,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
    reasoning_by_text_key: dict[str, str],
    memo_ref_pool: set[str],
    bucket_id_prefix: str = "",
    tracker: DropTracker | None = None,
) -> list[StyleBibleRuleBase]:
    rows: list[StyleBibleRuleBase] = []
    seen: set[str] = set()
    for raw_value in values:
        row = _sanitize_rule_item(
            raw_value,
            path=path,
            item_index=len(rows) + 1,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_id_prefix,
            tracker=tracker,
        )
        if row is None:
            continue
        key = _normalize_text_key(row.text)
        if key in seen:
            if tracker:
                tracker.track(f"{path}_rule_duplicate_text")
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _resolve_optional_scalar_spec(path: str) -> OptionalScalarRuleSpec | None:
    canonical_path = canonical_scalar_surface_path(path)
    return scalar_enum_spec_for_path(canonical_path)


def _extract_optional_scalar_value(text: str, *, spec: OptionalScalarRuleSpec) -> str:
    normalized_text = clean_text(text)
    if not normalized_text:
        return ""
    for token, canonical_value in scalar_value_lookup_rows(spec.path.value):
        if _scalar_lookup_in_text(normalized_text, token):
            return canonical_value
    return ""


def _resolve_optional_scalar_value(rule: StyleBibleRuleBase, *, spec: OptionalScalarRuleSpec) -> str:
    for candidate in (
        rule.text,
        _rule_field_text(rule, "constraint"),
        _rule_field_text(rule, "trigger"),
        _rule_field_text(rule, "query_feature_matcher"),
        _rule_field_text(rule, "route_target_action"),
        _rule_field_text(rule, "forbidden_action"),
        _rule_field_text(rule, "correction_guideline"),
    ):
        value = _extract_optional_scalar_value(candidate, spec=spec)
        if value:
            return value
    if spec.default_when_missing:
        return spec.default_value
    return ""


def _normalize_optional_scalar_rule(rule: StyleBibleRuleBase | None, *, path: str) -> ScalarRuleItem | None:
    if rule is None:
        return None
    spec = _resolve_optional_scalar_spec(path)
    if spec is None:
        return (
            rule
            if isinstance(rule, ScalarRuleItem)
            else ScalarRuleItem.model_validate(
                {
                    "rule_id": clean_text(rule.rule_id),
                    "text": clean_text(rule.text),
                    "_reasoning_ref": clean_text(rule.reasoning_ref),
                    "evidence_refs": _unique_strings(rule.evidence_refs),
                    "anti_pattern_codes": _normalize_antipattern_codes(rule.anti_pattern_codes),
                }
            )
        )
    scalar_value = _resolve_optional_scalar_value(rule, spec=spec)
    if not scalar_value:
        return (
            rule
            if isinstance(rule, ScalarRuleItem)
            else ScalarRuleItem.model_validate(
                {
                    "rule_id": clean_text(rule.rule_id),
                    "text": clean_text(rule.text),
                    "_reasoning_ref": clean_text(rule.reasoning_ref),
                    "evidence_refs": _unique_strings(rule.evidence_refs),
                    "anti_pattern_codes": _normalize_antipattern_codes(rule.anti_pattern_codes),
                }
            )
        )

    return ScalarRuleItem.model_validate(
        {
            "rule_id": clean_text(rule.rule_id),
            "text": clean_text(canonicalize_scalar_value(path, scalar_value)),
            "_reasoning_ref": clean_text(rule.reasoning_ref),
            "evidence_refs": _unique_strings(rule.evidence_refs),
            "anti_pattern_codes": _normalize_antipattern_codes(rule.anti_pattern_codes),
        }
    )


def _normalize_optional_scalar_rules(final_result: StyleBibleResultV2) -> StyleBibleResultV2:
    final_result.narrative_system.perspective = _normalize_optional_scalar_rule(
        final_result.narrative_system.perspective,
        path="narrative_system.perspective",
    )
    final_result.narrative_system.distance = _normalize_optional_scalar_rule(
        final_result.narrative_system.distance,
        path="narrative_system.distance",
    )
    final_result.narrative_system.temporality = _normalize_optional_scalar_rule(
        final_result.narrative_system.temporality,
        path="narrative_system.temporality",
    )
    final_result.voice_contract.narrator_voice = _normalize_optional_scalar_rule(
        final_result.voice_contract.narrator_voice,
        path="voice_contract.narrator_voice",
    )
    final_result.voice_contract.inner_monologue_mode = _normalize_optional_scalar_rule(
        final_result.voice_contract.inner_monologue_mode,
        path="voice_contract.inner_monologue_mode",
    )
    return final_result


def _sanitize_optional_rule(
    value: Any,
    *,
    path: str,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
    reasoning_by_text_key: dict[str, str],
    memo_ref_pool: set[str],
    bucket_id_prefix: str = "",
    tracker: DropTracker | None = None,
) -> ScalarRuleItem | None:
    return _normalize_optional_scalar_rule(
        _sanitize_rule_item(
            value,
            path=path,
            item_index=1,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_id_prefix,
            tracker=tracker,
        ),
        path=path,
    )


def _iter_final_rule_items(final_result: StyleBibleResultV2) -> list[StyleBibleRuleBase]:
    items: list[StyleBibleRuleBase] = []
    items.extend(final_result.narrative_system.engine)
    if final_result.narrative_system.perspective is not None:
        items.append(final_result.narrative_system.perspective)
    if final_result.narrative_system.distance is not None:
        items.append(final_result.narrative_system.distance)
    if final_result.narrative_system.temporality is not None:
        items.append(final_result.narrative_system.temporality)
    items.extend(final_result.narrative_system.pacing_rules)
    items.extend(final_result.narrative_system.plot_node_logic)
    items.extend(final_result.expression_system.description_rules)
    items.extend(final_result.expression_system.dialogue_rules)
    items.extend(final_result.expression_system.characterization_rules)
    items.extend(final_result.expression_system.sensory_rules)
    items.extend(final_result.aesthetics_system.core_axes)
    items.extend(final_result.aesthetics_system.pressure_axes)
    items.extend(final_result.aesthetics_system.humor_recipe)
    items.extend(final_result.aesthetics_system.satire_targets)
    items.extend(final_result.aesthetics_system.nonstandard_xianxia_rules)
    if final_result.voice_contract.narrator_voice is not None:
        items.append(final_result.voice_contract.narrator_voice)
    if final_result.voice_contract.inner_monologue_mode is not None:
        items.append(final_result.voice_contract.inner_monologue_mode)
    items.extend(final_result.voice_contract.register_mix)
    items.extend(final_result.voice_contract.negative_pitfalls)
    items.extend(final_result.character_arc_rules)
    items.extend(final_result.worldbook_binding.rag_worthy)
    items.extend(final_result.worldbook_binding.worldbook_worthy)
    items.extend(final_result.worldbook_binding.routing_hints)
    items.extend(final_result.negative_rules)
    return items


def _build_supporting_evidence(
    final_result: StyleBibleResultV2,
    *,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
) -> list[StyleBibleEvidence]:
    candidates = _collect_global_supporting_evidence_candidates(
        final_result,
        reasoning_by_id=reasoning_by_id,
    )
    return _trim_global_evidence(
        candidates,
        critical_buckets=[],
        soft_cap=18,
        hard_cap=18,
    )


def _collect_global_supporting_evidence_candidates(
    final_result: StyleBibleResultV2,
    *,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
) -> list[dict[str, Any]]:
    existing_map: dict[str, StyleBibleEvidence] = {}
    for row in final_result.supporting_evidence:
        claim = clean_text(row.claim)
        if claim:
            existing_map[_normalize_text_key(claim)] = StyleBibleEvidence(
                claim=claim,
                evidence_text=clean_text(row.evidence_text),
                source_ref=clean_text(row.source_ref),
            )

    rule_support_counts: dict[str, int] = {}
    for rule in _iter_final_rule_items(final_result):
        reasoning_ref = clean_text(rule.reasoning_ref)
        if reasoning_ref:
            rule_support_counts[reasoning_ref] = rule_support_counts.get(reasoning_ref, 0) + 1

    rows: list[dict[str, Any]] = []
    seen_reasoning_ids: set[str] = set()
    for rule in _iter_final_rule_items(final_result):
        reasoning_ref = clean_text(rule.reasoning_ref)
        if reasoning_ref in seen_reasoning_ids or reasoning_ref not in reasoning_by_id:
            continue
        seen_reasoning_ids.add(reasoning_ref)
        reasoning_entry = reasoning_by_id[reasoning_ref]
        existing = existing_map.get(_normalize_text_key(reasoning_entry.claim))
        source_ref = clean_text(existing.source_ref) if existing else ""
        if source_ref not in reasoning_entry.evidence_refs:
            source_ref = reasoning_entry.evidence_refs[0] if reasoning_entry.evidence_refs else ""
        if not source_ref:
            continue
        evidence_text = clean_text(existing.evidence_text) if existing else ""
        if not evidence_text:
            evidence_text = clean_text(reasoning_entry.downstream_constraint) or clean_text(reasoning_entry.mechanism_inference)
        rows.append(
            {
                "bucket_id": clean_text(reasoning_entry.bucket_id),
                "axis_ids": _unique_strings(reasoning_entry.axis_ids),
                "support_count": int(rule_support_counts.get(reasoning_ref, 0) or 0),
                "reasoning_ref_count": len(_unique_strings(reasoning_entry.evidence_refs)),
                "evidence": StyleBibleEvidence(
                    claim=clean_text(existing.claim) if existing and clean_text(existing.claim) else reasoning_entry.claim,
                    evidence_text=evidence_text,
                    source_ref=source_ref,
                ),
            }
        )
    return rows


def _trim_global_evidence(
    candidates: list[dict[str, Any]],
    *,
    critical_buckets: Iterable[str],
    soft_cap: int,
    hard_cap: int,
) -> list[StyleBibleEvidence]:
    if not candidates:
        return []
    critical_bucket_set = {clean_text(bucket_id) for bucket_id in critical_buckets if clean_text(bucket_id)}
    resolved_hard_cap = max(int(hard_cap or 0), 1)
    resolved_soft_cap = min(max(int(soft_cap or 0), 1), resolved_hard_cap)

    ranked_candidates = sorted(
        candidates,
        key=lambda candidate: (
            -int(clean_text(candidate.get("bucket_id")) in critical_bucket_set),
            -int(candidate.get("support_count", 0) or 0),
            -len(candidate.get("axis_ids", [])),
            -int(candidate.get("reasoning_ref_count", 0) or 0),
            clean_text(candidate.get("bucket_id")),
            _normalize_text_key(candidate.get("evidence", StyleBibleEvidence()).claim),
            clean_text(candidate.get("evidence", StyleBibleEvidence()).source_ref),
        ),
    )

    deduped_candidates: list[dict[str, Any]] = []
    seen_candidate_keys: set[tuple[str, str]] = set()
    for candidate in ranked_candidates:
        evidence = candidate.get("evidence")
        if not isinstance(evidence, StyleBibleEvidence):
            continue
        candidate_key = (_normalize_text_key(evidence.claim), clean_text(evidence.source_ref))
        if candidate_key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate_key)
        deduped_candidates.append(candidate)

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    selected_buckets: set[str] = set()

    def select_candidate(candidate: dict[str, Any]) -> None:
        evidence = candidate.get("evidence")
        if not isinstance(evidence, StyleBibleEvidence):
            return
        candidate_key = (_normalize_text_key(evidence.claim), clean_text(evidence.source_ref))
        if candidate_key in selected_keys:
            return
        selected.append(candidate)
        selected_keys.add(candidate_key)
        bucket_id = clean_text(candidate.get("bucket_id"))
        if bucket_id:
            selected_buckets.add(bucket_id)

    for bucket_id in sorted(critical_bucket_set):
        for candidate in deduped_candidates:
            if clean_text(candidate.get("bucket_id")) == bucket_id:
                select_candidate(candidate)
                break

    for candidate in deduped_candidates:
        if len(selected) >= resolved_soft_cap:
            break
        select_candidate(candidate)

    for candidate in deduped_candidates:
        if len(selected) >= resolved_hard_cap:
            break
        bucket_id = clean_text(candidate.get("bucket_id"))
        if bucket_id and bucket_id not in selected_buckets:
            select_candidate(candidate)

    for candidate in deduped_candidates:
        if len(selected) >= resolved_hard_cap:
            break
        select_candidate(candidate)

    return [candidate["evidence"] for candidate in selected[:resolved_hard_cap]]


def _sanitize_style_bible_result_sections(
    final_result: StyleBibleResultV2,
    *,
    style_id_hint: str,
    scope_hint: str,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
    bucket_id_prefix: str = "",
    tracker: DropTracker | None = None,
) -> StyleBibleResultV2:
    reasoning_by_id, reasoning_by_text_key = _reasoning_lookup(reasoning_bundle)
    final_result.style_id = clean_text(final_result.style_id) or style_id_hint
    final_result.scope = clean_text(final_result.scope) or scope_hint

    final_result.narrative_system.engine = _sanitize_rule_list(
        list(final_result.narrative_system.engine),
        path="narrative_system_engine",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.narrative_system.perspective = _sanitize_optional_rule(
        final_result.narrative_system.perspective,
        path="narrative_system_perspective",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.narrative_system.distance = _sanitize_optional_rule(
        final_result.narrative_system.distance,
        path="narrative_system_distance",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.narrative_system.temporality = _sanitize_optional_rule(
        final_result.narrative_system.temporality,
        path="narrative_system_temporality",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.narrative_system.pacing_rules = _sanitize_rule_list(
        list(final_result.narrative_system.pacing_rules),
        path="narrative_system_pacing_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.narrative_system.plot_node_logic = _sanitize_rule_list(
        list(final_result.narrative_system.plot_node_logic),
        path="narrative_system_plot_node_logic",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.expression_system.description_rules = _sanitize_rule_list(
        list(final_result.expression_system.description_rules),
        path="expression_system_description_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.expression_system.dialogue_rules = _sanitize_rule_list(
        list(final_result.expression_system.dialogue_rules),
        path="expression_system_dialogue_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.expression_system.characterization_rules = _sanitize_rule_list(
        list(final_result.expression_system.characterization_rules),
        path="expression_system_characterization_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.expression_system.sensory_rules = _sanitize_rule_list(
        list(final_result.expression_system.sensory_rules),
        path="expression_system_sensory_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.aesthetics_system.core_axes = _sanitize_rule_list(
        list(final_result.aesthetics_system.core_axes),
        path="aesthetics_system_core_axes",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.aesthetics_system.pressure_axes = _sanitize_rule_list(
        list(final_result.aesthetics_system.pressure_axes),
        path="aesthetics_system_pressure_axes",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.aesthetics_system.humor_recipe = _sanitize_rule_list(
        list(final_result.aesthetics_system.humor_recipe),
        path="aesthetics_system_humor_recipe",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.aesthetics_system.satire_targets = _sanitize_rule_list(
        list(final_result.aesthetics_system.satire_targets),
        path="aesthetics_system_satire_targets",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.aesthetics_system.nonstandard_xianxia_rules = _sanitize_rule_list(
        list(final_result.aesthetics_system.nonstandard_xianxia_rules),
        path="aesthetics_system_nonstandard_xianxia_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.voice_contract.narrator_voice = _sanitize_optional_rule(
        final_result.voice_contract.narrator_voice,
        path="voice_contract_narrator_voice",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.voice_contract.inner_monologue_mode = _sanitize_optional_rule(
        final_result.voice_contract.inner_monologue_mode,
        path="voice_contract_inner_monologue_mode",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.voice_contract.register_mix = _sanitize_rule_list(
        list(final_result.voice_contract.register_mix),
        path="voice_contract_register_mix",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.voice_contract.negative_pitfalls = _sanitize_rule_list(
        list(final_result.voice_contract.negative_pitfalls),
        path="voice_contract_negative_pitfalls",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.character_arc_rules = _sanitize_rule_list(
        list(final_result.character_arc_rules),
        path="character_arc_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.worldbook_binding.rag_worthy = _sanitize_rule_list(
        list(final_result.worldbook_binding.rag_worthy),
        path="worldbook_binding_rag_worthy",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.worldbook_binding.worldbook_worthy = _sanitize_rule_list(
        list(final_result.worldbook_binding.worldbook_worthy),
        path="worldbook_binding_worldbook_worthy",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.worldbook_binding.routing_hints = _sanitize_rule_list(
        list(final_result.worldbook_binding.routing_hints),
        path="worldbook_binding_routing_hints",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    final_result.negative_rules = _sanitize_rule_list(
        list(final_result.negative_rules),
        path="negative_rules",
        reasoning_by_id=reasoning_by_id,
        reasoning_by_text_key=reasoning_by_text_key,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=bucket_id_prefix,
        tracker=tracker,
    )
    return _normalize_optional_scalar_rules(final_result)


def _sanitize_style_bible_result(
    final_result: StyleBibleResultV2,
    *,
    style_id_hint: str,
    scope_hint: str,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
) -> StyleBibleResultV2:
    final_result = _sanitize_style_bible_result_sections(
        final_result,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=memo_ref_pool,
    )
    reasoning_by_id, _ = _reasoning_lookup(reasoning_bundle)
    final_result = _normalize_optional_scalar_rules(final_result)
    final_result.supporting_evidence = _build_supporting_evidence(final_result, reasoning_by_id=reasoning_by_id)
    return final_result


def _sanitize_local_style_bible_result(
    final_result: StyleBibleResultV2,
    *,
    bucket_id: str,
    style_id_hint: str,
    scope_hint: str,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
    tracker: DropTracker | None = None,
) -> StyleBibleResultV2:
    final_result = _sanitize_style_bible_result_sections(
        final_result,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=memo_ref_pool,
        bucket_id_prefix=clean_text(bucket_id),
        tracker=tracker,
    )
    final_result.supporting_evidence = []
    return final_result


def _assert_local_reduce_output_valid(
    final_result: StyleBibleResultV2,
    *,
    bucket_id: str,
    reasoning_bundle: StyleBibleReasoningBundle,
) -> None:
    rule_items = _iter_final_rule_items(final_result)
    reduced_refs = {
        ref
        for rule in rule_items
        for ref in rule.evidence_refs
        if clean_text(ref)
    }
    if reasoning_bundle.entries and rule_items and reduced_refs:
        return
    raise StyleBibleReduceGuardrailError(
        f"Local reducer produced an empty or ungrounded partial result for bucket={clean_text(bucket_id)}. "
        f"reasoning_entry_count={len(reasoning_bundle.entries)}, "
        f"rule_count={len(rule_items)}, "
        f"reduced_ref_count={len(reduced_refs)}."
    )


def _build_reduce_trace(
    reasoning_bundle: StyleBibleReasoningBundle,
    grounding_ref_pool: set[str],
    *,
    rule_lineage_map: Iterable[StyleBibleRuleLineageEntry] = (),
    merge_events: Iterable[StyleBibleMergeEvent] = (),
    final_result: StyleBibleResultV2 | None = None,
    runtime_flags: StyleBibleRuntimeFlags | None = None,
) -> dict[str, Any]:
    resolved_flags = runtime_flags or load_style_bible_runtime_flags()
    evidence_map = [
        StyleBibleReduceTraceEntry(
            claim_id=entry.reasoning_id,
            claim=entry.claim,
            evidence_refs=[ref for ref in entry.evidence_refs if ref in grounding_ref_pool],
        ).model_dump(mode="json")
        for entry in reasoning_bundle.entries
        if entry.evidence_refs
    ]
    reduce_trace = {
        "style_id": reasoning_bundle.style_id,
        "scope": reasoning_bundle.scope,
        "evidence_map": evidence_map,
        "grounding_ref_pool": sorted(grounding_ref_pool),
        "memo_ref_pool": sorted(grounding_ref_pool),
        "rule_lineage_map": [row.model_dump(mode="json") for row in rule_lineage_map],
        "merge_events": [row.model_dump(mode="json") for row in merge_events],
        "feature_flags": resolved_flags.as_dict(),
        "final_decision_source": "hierarchical_reducer",
    }
    return reduce_trace


def _build_export_flat(final_record: dict[str, Any]) -> dict[str, Any]:
    flat_payload = style_bible_payload_to_flat(final_record)
    flat_model = StyleBibleResult.model_validate(flat_payload)
    return flat_model.model_dump(mode="json")


def _value_at_rule_path(root: Any, path: str) -> Any:
    current = root
    for segment in path.split("."):
        current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _set_rule_path_value(root: Any, path: str, value: Any) -> None:
    current = root
    segments = path.split(".")
    for segment in segments[:-1]:
        current = getattr(current, segment)
    setattr(current, segments[-1], value)


def _count_rule_path_items(root: Any, path: str) -> int:
    value = _value_at_rule_path(root, path)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, StyleBibleRuleBase):
        return 1 if clean_text(value.text) else 0
    return 0


def _iter_path_rule_rows(root: Any, path: str) -> list[StyleBibleRuleBase]:
    value = _value_at_rule_path(root, path)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, StyleBibleRuleBase)]
    if isinstance(value, StyleBibleRuleBase):
        return [value]
    return []


def _rule_embedding_text(rule: StyleBibleRuleBase, *, path: str = "") -> str:
    parts = [
        clean_text(path),
        clean_text(rule.text),
        _rule_field_text(rule, "trigger"),
        _rule_field_text(rule, "constraint"),
        _rule_field_text(rule, "query_feature_matcher"),
        _rule_field_text(rule, "route_target_action"),
        _rule_field_text(rule, "forbidden_action"),
        _rule_field_text(rule, "correction_guideline"),
    ]
    return "\n".join(part for part in parts if part)


def _reasoning_entry_embedding_text(entry: StyleBibleReasoningEntry) -> str:
    parts = [
        clean_text(entry.bucket_id),
        " | ".join(_unique_strings(entry.axis_ids)),
        clean_text(entry.claim),
        clean_text(entry.observed_commonality),
        clean_text(entry.mechanism_inference),
        clean_text(entry.downstream_constraint),
    ]
    return "\n".join(part for part in parts if part)


def _slot_embedding_text(
    slot: SectionSlotSpec,
    *,
    path: str = "",
    downstream_shape: str = "",
) -> str:
    parts = [
        clean_text(path),
        clean_text(slot.slot_id),
        clean_text(slot.label),
        clean_text(slot.cue),
        clean_text(slot.canonical_description),
        clean_text(slot.downstream_shape) or clean_text(downstream_shape),
    ]
    return "\n".join(part for part in parts if part)


def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_vector = [float(value) for value in left]
    right_vector = [float(value) for value in right]
    if not left_vector or not right_vector or len(left_vector) != len(right_vector):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left_vector))
    right_norm = math.sqrt(sum(value * value for value in right_vector))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    dot = sum(left_value * right_value for left_value, right_value in zip(left_vector, right_vector, strict=True))
    return round(dot / (left_norm * right_norm), 4)


def _combine_semantic_scores(vector_score: float, cue_score: float) -> float:
    return round(max(float(vector_score or 0.0), float(cue_score or 0.0)), 4)


def _evidence_overlap_score(shared_count: int, total_count: int) -> float:
    if int(total_count or 0) <= 0:
        return 0.0
    return round(max(int(shared_count or 0), 0) / max(int(total_count), 1), 4)


def _repair_request_path_sort_key(
    path: str,
    *,
    gap_by_path: dict[str, SectionGap],
    bucket_targets: Any,
    source_order: dict[str, int],
) -> tuple[int, int, int, int, str]:
    gap = gap_by_path[path]
    is_missing_scalar = gap.gap_type == "missing_scalar" or path in set(bucket_targets.scalar_paths)
    is_control_plane_priority = path in REPAIR_CONTROL_PLANE_PRIORITY_PATHS
    return (
        0 if is_missing_scalar else 1,
        0 if is_control_plane_priority else 1,
        -int(gap.deficit),
        int(source_order.get(path, 10**6)),
        clean_text(path),
    )


def _densify_path_group_rank(path: str) -> int:
    normalized_path = clean_text(path)
    if normalized_path.startswith("narrative_system."):
        return 0
    if normalized_path.startswith("expression_system."):
        return 1
    if normalized_path.startswith("aesthetics_system."):
        return 2
    if normalized_path.startswith("voice_contract."):
        return 3
    if normalized_path == "character_arc_rules":
        return 4
    if normalized_path == "negative_rules":
        return 5
    if normalized_path.startswith("worldbook_binding."):
        return 6
    return 7


def _slot_evidence_ref_pools(
    *,
    missing_slots: Iterable[SectionSlotSpec],
    retrieved_reasoning_entries: Iterable[dict[str, Any]] | None,
) -> dict[str, set[str]]:
    pools = {
        clean_text(slot.slot_id): set()
        for slot in missing_slots
        if clean_text(slot.slot_id)
    }
    if not pools:
        return {}
    for row in retrieved_reasoning_entries or []:
        if not isinstance(row, dict):
            continue
        evidence_refs = {
            clean_text(ref)
            for ref in row.get("evidence_refs", [])
            if clean_text(ref)
        }
        matched_slot_ids = _unique_strings(row.get("matched_slot_ids", []))
        for slot_id in matched_slot_ids:
            if slot_id in pools:
                pools[slot_id].update(evidence_refs)
    return pools


def _collect_rule_reasoning_and_evidence_refs(
    *,
    rules: Iterable[StyleBibleRuleBase],
) -> tuple[set[str], set[str]]:
    reasoning_ids: set[str] = set()
    evidence_refs: set[str] = set()
    for rule in rules:
        reasoning_ref = clean_text(rule.reasoning_ref)
        if reasoning_ref:
            reasoning_ids.add(reasoning_ref)
        for ref in rule.evidence_refs:
            cleaned_ref = clean_text(ref)
            if cleaned_ref:
                evidence_refs.add(cleaned_ref)
    return reasoning_ids, evidence_refs


def _prune_reasoning_bundle_to_rules(
    reasoning_bundle: StyleBibleReasoningBundle,
    *,
    rules: Iterable[StyleBibleRuleBase],
) -> StyleBibleReasoningBundle:
    allowed_reasoning_ids = {
        clean_text(rule.reasoning_ref)
        for rule in rules
        if clean_text(rule.reasoning_ref)
    }
    entries = [
        entry.model_copy(deep=True)
        for entry in reasoning_bundle.entries
        if clean_text(entry.reasoning_id) in allowed_reasoning_ids
    ]
    return StyleBibleReasoningBundle(
        reasoning_version=clean_text(reasoning_bundle.reasoning_version),
        style_id=clean_text(reasoning_bundle.style_id),
        scope=clean_text(reasoning_bundle.scope),
        entries=entries,
    )


def _sanitize_rule_rows_for_path(
    partial_output: StyleBibleLocalReducerOutput,
    *,
    target_path: str,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
    bucket_id_prefix: str,
    tracker: DropTracker | None = None,
) -> list[StyleBibleRuleBase]:
    reasoning_by_id, reasoning_by_text_key = _reasoning_lookup(reasoning_bundle)
    sanitized_rows: list[StyleBibleRuleBase] = []
    normalized_target_path = clean_text(target_path)
    for item_index, row in enumerate(partial_output.final.rule_rows, start=1):
        sanitized = _sanitize_local_rule_row(
            row,
            item_index=item_index,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_id_prefix,
            tracker=tracker,
        )
        if sanitized is None:
            continue
        path, rule = sanitized
        if clean_text(path) != normalized_target_path:
            if tracker:
                tracker.track(f"{path}_rule_path_mismatch")
            continue
        sanitized_rows.append(rule)
    return sanitized_rows


def _rule_merge_keys(rule: StyleBibleRuleBase, *, path: str) -> list[str]:
    spec = _surface_path_spec(path)
    keys: list[str] = []
    text_key = _normalize_text_key(rule.text)
    if text_key:
        keys.append(f"text:{text_key}")
    if spec.rule_family in {"constraint", "scalar"}:
        trigger_key = _normalize_text_key(_rule_field_text(rule, "trigger"))
        constraint_key = _normalize_text_key(_rule_field_text(rule, "constraint"))
        if trigger_key and constraint_key:
            keys.append(f"{spec.rule_family}:{trigger_key}|{constraint_key}")
    if spec.rule_family == "routing_hint":
        matcher_key = _normalize_text_key(_rule_field_text(rule, "query_feature_matcher"))
        action_key = _normalize_text_key(_rule_field_text(rule, "route_target_action"))
        if matcher_key and action_key:
            keys.append(f"routing:{matcher_key}|{action_key}")
    if spec.rule_family == "negative":
        forbidden_key = _normalize_text_key(_rule_field_text(rule, "forbidden_action"))
        correction_key = _normalize_text_key(_rule_field_text(rule, "correction_guideline"))
        if forbidden_key and correction_key:
            keys.append(f"negative:{forbidden_key}|{correction_key}")
    return keys or [f"rule_id:{clean_text(rule.rule_id) or id(rule)}"]


_JUDGE_SHAPE_TRIGGER_CUES = ("当", "如果", "出现", "遇到", "凡是", "涉及")
_JUDGE_SHAPE_ACTION_CUES = ("使用", "保持", "避免", "不要", "优先", "通过", "让", "把", "将", "必须", "应当", "先", "再", "需要", "应")
_JUDGE_SHAPE_ROUTING_CUES = ("路由到", "路由至", "进入", "归到")
_JUDGE_SHAPE_WORLDBOOK_ANCHORS = ("机构", "规则", "门槛", "资格", "资源", "制度", "节点", "世界书", "合同", "检查点", "收费", "票据")
_JUDGE_SHAPE_ENGLISH_PATTERN = re.compile(r"^(When |If |Route to |Store the |Must |Do not )", re.IGNORECASE)
_JUDGE_SHAPE_KEYWORD_STUFF_SPLIT = re.compile(r"[、,，/|；;：:\s]+")


def _judge_shape_score(rule: StyleBibleRuleBase, *, path: str = "") -> float:
    """Local scoring for Judge V2 shape friendliness. Returns 0.0-1.0.

    Prefers rules with Chinese trigger/action cues, proper routing
    structure, worldbook anchors, and evidence refs. Penalises
    English template sentences and keyword stuffing.
    """
    text = clean_text(getattr(rule, "text", ""))
    trigger = clean_text(getattr(rule, "trigger", ""))
    constraint = clean_text(getattr(rule, "constraint", ""))
    combined = f"{text} {trigger} {constraint}"

    score = 0.5  # baseline

    # Positive: Chinese trigger cues in leading position
    if any(cue in combined[:10] for cue in _JUDGE_SHAPE_TRIGGER_CUES):
        score += 0.15
    # Positive: Chinese action cues anywhere
    if any(cue in combined for cue in _JUDGE_SHAPE_ACTION_CUES):
        score += 0.1
    # Positive: routing-specific patterns
    if "routing" in path:
        matcher = clean_text(getattr(rule, "query_feature_matcher", ""))
        action = clean_text(getattr(rule, "route_target_action", ""))
        if any(cue in action for cue in _JUDGE_SHAPE_ROUTING_CUES):
            score += 0.15
        if any(cue in matcher for cue in _JUDGE_SHAPE_TRIGGER_CUES):
            score += 0.1
    # Positive: worldbook/rag anchors
    if "worldbook" in path or "rag" in path:
        if any(anchor in combined for anchor in _JUDGE_SHAPE_WORLDBOOK_ANCHORS):
            score += 0.15
    # Positive: evidence refs present
    refs = getattr(rule, "evidence_refs", [])
    if refs and any(clean_text(r) for r in refs):
        score += 0.05

    # Negative: English template sentence
    if _JUDGE_SHAPE_ENGLISH_PATTERN.search(text):
        score -= 0.3
    # Negative: keyword stuffing (≥4 comma-separated chunks, no action cue)
    chunks = [p for p in _JUDGE_SHAPE_KEYWORD_STUFF_SPLIT.split(text) if clean_text(p)]
    if len(chunks) >= 4 and not any(cue in text for cue in _JUDGE_SHAPE_ACTION_CUES):
        score -= 0.15

    return max(0.0, min(1.0, round(score, 4)))


def _rule_candidate_priority(
    rule: StyleBibleRuleBase,
    *,
    bucket_id: str,
    critical_buckets: set[str],
    bucket_order: dict[str, int],
    path: str = "",
) -> tuple[int, int, int, int, int, str, str]:
    structured_score = sum(
        1
        for value in (
            _rule_field_text(rule, "trigger"),
            _rule_field_text(rule, "constraint"),
            _rule_field_text(rule, "query_feature_matcher"),
            _rule_field_text(rule, "route_target_action"),
            _rule_field_text(rule, "forbidden_action"),
            _rule_field_text(rule, "correction_guideline"),
        )
        if clean_text(value)
    )
    # Quantise judge_shape_score to a discrete bucket (0-10) for stable sorting.
    judge_shape = int(_judge_shape_score(rule, path=path) * 10)
    return (
        -int(clean_text(bucket_id) in critical_buckets),
        -len(_unique_strings(rule.evidence_refs)),
        -structured_score,
        -judge_shape,
        int(bucket_order.get(clean_text(bucket_id), 10**6)),
        clean_text(rule.rule_id),
        _normalize_text_key(rule.text),
    )


def _merge_reasoning_bundles(
    local_artifacts: list[LocalReduceArtifact],
    *,
    style_id_hint: str,
    scope_hint: str,
) -> StyleBibleReasoningBundle:
    rows: list[StyleBibleReasoningEntry] = []
    seen_ids: set[str] = set()
    for artifact in local_artifacts:
        for entry in artifact.reasoning_bundle.entries:
            reasoning_id = clean_text(entry.reasoning_id)
            if not reasoning_id or reasoning_id in seen_ids:
                continue
            seen_ids.add(reasoning_id)
            rows.append(entry.model_copy(deep=True))
    return StyleBibleReasoningBundle(
        reasoning_version="style-bible-reasoning-v2",
        style_id=clean_text(style_id_hint),
        scope=clean_text(scope_hint),
        entries=rows,
    )


def _sort_rule_candidates(
    candidates: list[tuple[StyleBibleRuleBase, str]],
    *,
    critical_buckets: set[str],
    bucket_order: dict[str, int],
    path: str = "",
) -> list[tuple[StyleBibleRuleBase, str]]:
    return sorted(
        candidates,
        key=lambda item: _rule_candidate_priority(
            item[0],
            bucket_id=item[1],
            critical_buckets=critical_buckets,
            bucket_order=bucket_order,
            path=path,
        ),
    )


def _path_merge_keys(rule: StyleBibleRuleBase, *, path: str) -> list[str]:
    spec = _surface_path_spec(path)
    if spec.merge_strategy == "rule_dedupe_aggressive":
        keys = [
            f"{field_name}:{_normalize_text_key(getattr(rule, field_name, ''))}"
            for field_name in spec.aggressive_group_fields
            if _normalize_text_key(getattr(rule, field_name, ""))
        ]
        if keys:
            return keys
    return _rule_merge_keys(rule, path=path)


def _group_rule_candidates(
    candidates: list[tuple[StyleBibleRuleBase, str]],
    *,
    path: str,
) -> list[tuple[str, list[tuple[StyleBibleRuleBase, str]]]]:
    groups: list[list[tuple[StyleBibleRuleBase, str]]] = []
    group_keys: list[str] = []
    alias_to_group: dict[str, int] = {}
    for candidate in candidates:
        rule, _bucket_id = candidate
        aliases = _path_merge_keys(rule, path=path)
        group_index: int | None = None
        for alias in aliases:
            if alias in alias_to_group:
                group_index = alias_to_group[alias]
                break
        if group_index is None:
            groups.append([candidate])
            group_keys.append(aliases[0] if aliases else f"rule_id::{clean_text(rule.rule_id)}")
            group_index = len(groups) - 1
        else:
            groups[group_index].append(candidate)
        for alias in aliases:
            alias_to_group.setdefault(alias, group_index)
    return [(group_keys[index], group) for index, group in enumerate(groups)]


def _distinct_group_field_values(
    group: list[tuple[StyleBibleRuleBase, str]],
    *,
    field_name: str,
) -> list[str]:
    values: dict[str, str] = {}
    for rule, _bucket_id in group:
        cleaned = clean_text(getattr(rule, field_name, ""))
        if cleaned:
            values.setdefault(_normalize_text_key(cleaned), cleaned)
    return list(values.values())


def _merge_group_rule_item(group: list[tuple[StyleBibleRuleBase, str]]) -> StyleBibleRuleBase:
    best_rule = group[0][0].model_copy(deep=True)
    best_rule.evidence_refs = _unique_strings(
        ref
        for rule, _bucket_id in group
        for ref in rule.evidence_refs
    )
    best_rule.anti_pattern_codes = _normalize_antipattern_codes(
        code
        for rule, _bucket_id in group
        for code in rule.anti_pattern_codes
    )
    return best_rule


def _build_assembler_conflict(
    *,
    path: str,
    conflict_key: str,
    group: list[tuple[StyleBibleRuleBase, str]],
    resolution: str,
    note: str,
    kept_rule_id: str = "",
) -> StyleBibleAssemblerConflict:
    return StyleBibleAssemblerConflict(
        surface_path=clean_text(path),
        conflict_key=clean_text(conflict_key),
        resolution=clean_text(resolution),
        bucket_ids=_unique_strings(bucket_id for _rule, bucket_id in group),
        kept_rule_id=clean_text(kept_rule_id),
        dropped_rule_ids=_unique_strings(
            rule.rule_id
            for rule, _bucket_id in group
            if clean_text(rule.rule_id) and clean_text(rule.rule_id) != clean_text(kept_rule_id)
        ),
        note=clean_text(note),
    )


def _build_rule_lineage_entry(
    *,
    path: str,
    kept_rule: StyleBibleRuleBase,
    kept_bucket_id: str,
    group: list[tuple[StyleBibleRuleBase, str]],
    source_kind: str,
) -> StyleBibleRuleLineageEntry:
    origin_rule_ids = _unique_strings(rule.rule_id for rule, _bucket_id in group)
    kept_rule_id = clean_text(kept_rule.rule_id)
    return StyleBibleRuleLineageEntry(
        final_rule_id=kept_rule_id,
        surface_path=clean_text(path),
        kept_bucket_id=clean_text(kept_bucket_id),
        source_bucket_ids=_unique_strings(bucket_id for _rule, bucket_id in group),
        source_kind=clean_text(source_kind),
        reasoning_ref=clean_text(kept_rule.reasoning_ref),
        merged_evidence_refs=_unique_strings(kept_rule.evidence_refs),
        origin_rule_ids=origin_rule_ids,
        conflict_history=[
            rule_id
            for rule_id in origin_rule_ids
            if rule_id and rule_id != kept_rule_id
        ],
    )


def _build_merge_event(
    *,
    path: str,
    merge_strategy: str,
    group_key: str,
    group: list[tuple[StyleBibleRuleBase, str]],
    resolution: str,
    note: str,
    kept_rule_id: str = "",
    kept_bucket_id: str = "",
) -> StyleBibleMergeEvent:
    origin_rule_ids = _unique_strings(rule.rule_id for rule, _bucket_id in group)
    normalized_kept_rule_id = clean_text(kept_rule_id)
    dropped_rule_ids = [
        rule_id
        for rule_id in origin_rule_ids
        if rule_id and rule_id != normalized_kept_rule_id
    ]
    if not normalized_kept_rule_id:
        dropped_rule_ids = origin_rule_ids
    return StyleBibleMergeEvent(
        surface_path=clean_text(path),
        merge_strategy=clean_text(merge_strategy),
        group_key=clean_text(group_key),
        kept_rule_id=normalized_kept_rule_id,
        kept_bucket_id=clean_text(kept_bucket_id),
        source_bucket_ids=_unique_strings(bucket_id for _rule, bucket_id in group),
        origin_rule_ids=origin_rule_ids,
        dropped_rule_ids=dropped_rule_ids,
        resolution=clean_text(resolution),
        note=clean_text(note),
    )


def _assemble_path_value_from_candidates(
    *,
    path: str,
    candidates: list[tuple[StyleBibleRuleBase, str]],
    critical_buckets: set[str],
    bucket_order: dict[str, int],
    minimum_items: int = 0,
    conflict_records: list[StyleBibleAssemblerConflict] | None = None,
    rule_lineage_records: list[StyleBibleRuleLineageEntry] | None = None,
    merge_events: list[StyleBibleMergeEvent] | None = None,
) -> list[StyleBibleRuleBase] | StyleBibleRuleBase | None:
    spec = _surface_path_spec(path)
    ordered_candidates = _sort_rule_candidates(
        candidates,
        critical_buckets=critical_buckets,
        bucket_order=bucket_order,
        path=path,
    )
    if spec.cardinality == "scalar":
        if not ordered_candidates:
            return None
        kept_rule, kept_bucket_id = ordered_candidates[0]
        selected_rule = kept_rule.model_copy(deep=True)
        if rule_lineage_records is not None:
            rule_lineage_records.append(
                _build_rule_lineage_entry(
                    path=path,
                    kept_rule=selected_rule,
                    kept_bucket_id=kept_bucket_id,
                    group=ordered_candidates,
                    source_kind="model_generated" if len(ordered_candidates) == 1 else "scalar_pick_one",
                )
            )
        if merge_events is not None and len(ordered_candidates) > 1:
            merge_events.append(
                _build_merge_event(
                    path=path,
                    merge_strategy=spec.merge_strategy,
                    group_key=path,
                    group=ordered_candidates,
                    resolution="pick_best_scalar",
                    note="Selected the highest-priority scalar candidate and dropped lower-priority variants.",
                    kept_rule_id=selected_rule.rule_id,
                    kept_bucket_id=kept_bucket_id,
                )
            )
        return selected_rule

    if not ordered_candidates:
        return []

    if spec.merge_strategy == "append_capped":
        rows: list[StyleBibleRuleBase] = []
        seen_aliases: set[str] = set()
        alias_owner: dict[str, str] = {}
        for rule, bucket_id in ordered_candidates:
            aliases = _path_merge_keys(rule, path=path)
            duplicate_alias = next((alias for alias in aliases if alias in seen_aliases), "")
            if duplicate_alias:
                if merge_events is not None:
                    merge_events.append(
                        _build_merge_event(
                            path=path,
                            merge_strategy=spec.merge_strategy,
                            group_key=duplicate_alias,
                            group=[(rule, bucket_id)],
                            resolution="dedupe_skip",
                            note=f"Skipped append candidate because alias `{duplicate_alias}` was already kept.",
                            kept_rule_id=alias_owner.get(duplicate_alias, ""),
                        )
                    )
                continue
            if len(rows) >= spec.max_items:
                if merge_events is not None:
                    merge_events.append(
                        _build_merge_event(
                            path=path,
                            merge_strategy=spec.merge_strategy,
                            group_key=clean_text(rule.rule_id) or _normalize_text_key(rule.text),
                            group=[(rule, bucket_id)],
                            resolution="cap_drop",
                            note=f"Skipped append candidate after reaching cap={spec.max_items}.",
                        )
                    )
                continue
            copied_rule = rule.model_copy(deep=True)
            rows.append(copied_rule)
            if rule_lineage_records is not None:
                rule_lineage_records.append(
                    _build_rule_lineage_entry(
                        path=path,
                        kept_rule=copied_rule,
                        kept_bucket_id=bucket_id,
                        group=[(rule, bucket_id)],
                        source_kind="model_generated",
                    )
                )
            seen_aliases.update(aliases)
            for alias in aliases:
                alias_owner.setdefault(alias, clean_text(copied_rule.rule_id))
            if len(rows) >= spec.max_items:
                break
        return rows

    merged_rows: list[dict[str, Any]] = []
    for group_key, group in _group_rule_candidates(ordered_candidates, path=path):
        if spec.merge_strategy == "rule_dedupe_aggressive" and spec.conflict_field:
            conflict_values = _distinct_group_field_values(group, field_name=spec.conflict_field)
            if len(conflict_values) > 1 and spec.conflict_policy == "drop_group":
                conflict_note = (
                    f"Conflicting {spec.conflict_field} values: "
                    + " | ".join(conflict_values)
                )
                if conflict_records is not None:
                    conflict_records.append(
                        _build_assembler_conflict(
                            path=path,
                            conflict_key=group_key,
                            group=group,
                            resolution="drop_group",
                            note=conflict_note,
                        )
                    )
                if merge_events is not None:
                    merge_events.append(
                        _build_merge_event(
                            path=path,
                            merge_strategy=spec.merge_strategy,
                            group_key=group_key,
                            group=group,
                            resolution="drop_group",
                            note=conflict_note,
                        )
                    )
                continue
        ordered_group = _sort_rule_candidates(
            group,
            critical_buckets=critical_buckets,
            bucket_order=bucket_order,
            path=path,
        )
        merged_rule = _merge_group_rule_item(ordered_group)
        priority = _rule_candidate_priority(
            ordered_group[0][0],
            bucket_id=ordered_group[0][1],
            critical_buckets=critical_buckets,
            bucket_order=bucket_order,
            path=path,
        )
        merged_rows.append(
            {
                "rule": merged_rule,
                "priority": priority,
                "group_key": group_key,
                "group": ordered_group,
            }
        )

    merged_rows.sort(key=lambda item: item["priority"])
    selected_rows = merged_rows[: spec.max_items]
    dropped_rows = merged_rows[spec.max_items :]
    minimum_keep = min(max(int(minimum_items or 0), 0), int(spec.max_items))
    if minimum_keep and len(selected_rows) < minimum_keep:
        selected_rule_texts = {
            _normalize_text_key(row["rule"].text)
            for row in selected_rows
            if _normalize_text_key(row["rule"].text)
        }
        for row in list(selected_rows):
            if len(selected_rows) >= minimum_keep:
                break
            for rule, bucket_id in row["group"][1:]:
                if len(selected_rows) >= minimum_keep:
                    break
                text_key = _normalize_text_key(rule.text)
                if text_key and text_key in selected_rule_texts:
                    continue
                copied_rule = rule.model_copy(deep=True)
                selected_rows.append(
                    {
                        "rule": copied_rule,
                        "priority": _rule_candidate_priority(
                            rule,
                            bucket_id=bucket_id,
                            critical_buckets=critical_buckets,
                            bucket_order=bucket_order,
                        ),
                        "group_key": clean_text(rule.rule_id) or text_key,
                        "group": [(rule, bucket_id)],
                    }
                )
                if text_key:
                    selected_rule_texts.add(text_key)
                if merge_events is not None:
                    merge_events.append(
                        _build_merge_event(
                            path=path,
                            merge_strategy=spec.merge_strategy,
                            group_key=clean_text(rule.rule_id) or text_key,
                            group=[(rule, bucket_id)],
                            resolution="minimum_keep_split",
                            note=(
                                "Kept a distinct candidate from a merge group because the path "
                                f"minimum={minimum_keep} was not yet satisfied."
                            ),
                            kept_rule_id=copied_rule.rule_id,
                            kept_bucket_id=bucket_id,
                        )
                    )
        selected_rows = sorted(selected_rows, key=lambda item: item["priority"])[: spec.max_items]

    if rule_lineage_records is not None:
        for row in selected_rows:
            group = row["group"]
            kept_bucket_id = clean_text(group[0][1]) if group else ""
            rule_lineage_records.append(
                _build_rule_lineage_entry(
                    path=path,
                    kept_rule=row["rule"],
                    kept_bucket_id=kept_bucket_id,
                    group=group,
                    source_kind="model_generated" if len(group) == 1 else "assembler_merged",
                )
            )
    if merge_events is not None:
        for row in selected_rows:
            group = row["group"]
            if len(group) <= 1:
                continue
            kept_bucket_id = clean_text(group[0][1]) if group else ""
            merge_events.append(
                _build_merge_event(
                    path=path,
                    merge_strategy=spec.merge_strategy,
                    group_key=row["group_key"],
                    group=group,
                    resolution="merge_group",
                    note="Merged same-path rule candidates into one final grounded rule.",
                    kept_rule_id=row["rule"].rule_id,
                    kept_bucket_id=kept_bucket_id,
                )
            )
        for row in dropped_rows:
            merge_events.append(
                _build_merge_event(
                    path=path,
                    merge_strategy=spec.merge_strategy,
                    group_key=row["group_key"],
                    group=row["group"],
                    resolution="cap_drop",
                    note=f"Dropped deduplicated rule group after reaching cap={spec.max_items}.",
                )
            )

    return [row["rule"] for row in selected_rows]


def _merge_rule_lists(
    local_artifacts: list[LocalReduceArtifact],
    *,
    path: str,
    critical_buckets: set[str],
    bucket_order: dict[str, int],
    minimum_items: int = 0,
    conflict_records: list[StyleBibleAssemblerConflict] | None = None,
    rule_lineage_records: list[StyleBibleRuleLineageEntry] | None = None,
    merge_events: list[StyleBibleMergeEvent] | None = None,
) -> list[StyleBibleRuleBase]:
    candidates: list[tuple[StyleBibleRuleBase, str]] = []
    for artifact in local_artifacts:
        values = _value_at_rule_path(artifact.final_result, path)
        if not isinstance(values, list):
            continue
        for rule in values:
            if isinstance(rule, StyleBibleRuleBase):
                candidates.append((rule, artifact.bucket_id))
    value = _assemble_path_value_from_candidates(
        path=path,
        candidates=candidates,
        critical_buckets=critical_buckets,
        bucket_order=bucket_order,
        minimum_items=minimum_items,
        conflict_records=conflict_records,
        rule_lineage_records=rule_lineage_records,
        merge_events=merge_events,
    )
    return list(value) if isinstance(value, list) else []


def _resolve_scalar_candidates(
    local_artifacts: list[LocalReduceArtifact],
    *,
    path: str,
    critical_buckets: set[str],
    bucket_order: dict[str, int],
    conflict_records: list[StyleBibleAssemblerConflict] | None = None,
    rule_lineage_records: list[StyleBibleRuleLineageEntry] | None = None,
    merge_events: list[StyleBibleMergeEvent] | None = None,
) -> StyleBibleRuleBase | None:
    candidates: list[tuple[StyleBibleRuleBase, str]] = []
    for artifact in local_artifacts:
        value = _value_at_rule_path(artifact.final_result, path)
        if isinstance(value, StyleBibleRuleBase):
            candidates.append((value, artifact.bucket_id))
    value = _assemble_path_value_from_candidates(
        path=path,
        candidates=candidates,
        critical_buckets=critical_buckets,
        bucket_order=bucket_order,
        conflict_records=conflict_records,
        rule_lineage_records=rule_lineage_records,
        merge_events=merge_events,
    )
    return value if isinstance(value, StyleBibleRuleBase) else None


def _sanitize_local_rule_row(
    row: LocalRuleRow,
    *,
    item_index: int,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
    reasoning_by_text_key: dict[str, str],
    memo_ref_pool: set[str],
    bucket_id_prefix: str,
    tracker: DropTracker | None = None,
) -> tuple[str, StyleBibleRuleBase] | None:
    path = clean_text(row.surface_path)
    if not path:
        return None
    if path in OPTIONAL_RULE_PATHS:
        rule = _sanitize_optional_rule(
            row,
            path=path,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_id_prefix,
            tracker=tracker,
        )
    else:
        rule = _sanitize_rule_item(
            row,
            path=path,
            item_index=item_index,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_id_prefix,
            tracker=tracker,
        )
    if rule is None:
        return None
    return path, rule


def _assemble_local_partial_result(
    partial_output: StyleBibleLocalReducerOutput,
    *,
    bucket_id: str,
    style_id_hint: str,
    scope_hint: str,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
    tracker: DropTracker | None = None,
) -> tuple[StyleBibleResultV2, list[StyleBibleAssemblerConflict]]:
    reasoning_by_id, reasoning_by_text_key = _reasoning_lookup(reasoning_bundle)
    bucket_key = clean_text(bucket_id)
    bucket_order = {bucket_key: 0}
    candidates_by_path: dict[str, list[tuple[StyleBibleRuleBase, str]]] = {
        path: []
        for path in SURFACE_PATH_SPECS_BY_VALUE
    }

    for item_index, row in enumerate(partial_output.final.rule_rows, start=1):
        sanitized_row = _sanitize_local_rule_row(
            row,
            item_index=item_index,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_key,
            tracker=tracker,
        )
        if sanitized_row is None:
            continue
        path, rule = sanitized_row
        candidates_by_path.setdefault(path, []).append((rule, bucket_key))

    assembler_conflicts: list[StyleBibleAssemblerConflict] = []
    final_result = StyleBibleResultV2(
        style_id=style_id_hint,
        scope=scope_hint,
    )
    for path in LIST_RULE_PATHS:
        _set_rule_path_value(
            final_result,
            path,
            _assemble_path_value_from_candidates(
                path=path,
                candidates=candidates_by_path.get(path, []),
                critical_buckets=set(),
                bucket_order=bucket_order,
                conflict_records=assembler_conflicts,
            ),
        )
    for path in OPTIONAL_RULE_PATHS:
        _set_rule_path_value(
            final_result,
            path,
            _assemble_path_value_from_candidates(
                path=path,
                candidates=candidates_by_path.get(path, []),
                critical_buckets=set(),
                bucket_order=bucket_order,
                conflict_records=assembler_conflicts,
            ),
        )
    final_result = _sanitize_local_style_bible_result(
        final_result,
        bucket_id=bucket_key,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=memo_ref_pool,
    )
    final_result.metadata.degradation_status.mode = "degraded" if assembler_conflicts else "complete"
    final_result.metadata.degradation_status.assembler_conflicts = [row.model_copy(deep=True) for row in assembler_conflicts]
    return final_result, assembler_conflicts


def _build_global_reduce_trace(
    reasoning_bundle: StyleBibleReasoningBundle,
    grounding_ref_pool: set[str],
    *,
    final_result: StyleBibleResultV2,
    local_artifacts: list[LocalReduceArtifact],
    failed_bucket_ids: Iterable[str],
    skipped_sparse_bucket_ids: Iterable[str],
    critical_bucket_ids: Iterable[str],
    degraded_success: bool,
    assembler_conflicts: Iterable[StyleBibleAssemblerConflict],
    semantic_reconcile_sections: Iterable[str],
    rule_lineage_map: Iterable[StyleBibleRuleLineageEntry],
    merge_events: Iterable[StyleBibleMergeEvent],
) -> dict[str, Any]:
    reduce_trace = _build_reduce_trace(
        reasoning_bundle,
        grounding_ref_pool,
        rule_lineage_map=rule_lineage_map,
        merge_events=merge_events,
        final_result=final_result,
    )
    reduce_trace.update(
        {
            "reduce_mode": "hierarchical",
            "failed_bucket_ids": sorted(_unique_strings(failed_bucket_ids)),
            "skipped_sparse_bucket_ids": sorted(_unique_strings(skipped_sparse_bucket_ids)),
            "critical_bucket_ids": sorted(_unique_strings(critical_bucket_ids)),
            "degraded_success": bool(degraded_success),
            "assembler_conflicts": [
                row.model_dump(mode="json")
                for row in assembler_conflicts
            ],
            "semantic_reconcile_sections": sorted(_unique_strings(semantic_reconcile_sections)),
            "local_reduces": [
                {
                    "bucket_id": artifact.bucket_id,
                    "memo_id": artifact.memo_id,
                    "batch_ids": list(artifact.batch_ids),
                    "output_dir": str(artifact.output_dir.resolve()),
                    "grounding_ref_pool": sorted(artifact.grounding_ref_pool),
                    "reasoning_entry_count": len(artifact.reasoning_bundle.entries),
                    "rule_count": len(_iter_final_rule_items(artifact.final_result)),
                    "reduced_ref_count": len(artifact.reduced_refs),
                    "sparse": bool(artifact.sparse),
                    "assembler_conflict_count": len(artifact.assembler_conflicts),
                    "preflight": dict(artifact.preflight_decision),
                    "repair_pass_count": len(artifact.repair_passes),
                    "repair_passes": list(artifact.repair_passes),
                }
                for artifact in local_artifacts
            ],
        }
    )
    return reduce_trace


def _finalize_merged_style_bible_result(
    final_result: StyleBibleResultV2,
    *,
    reasoning_bundle: StyleBibleReasoningBundle,
    style_id_hint: str,
    scope_hint: str,
    memo_ref_pool: set[str],
    critical_buckets: Iterable[str],
    supporting_evidence_soft_cap: int,
    supporting_evidence_hard_cap: int,
) -> StyleBibleResultV2:
    sanitized_result = _sanitize_style_bible_result_sections(
        final_result,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=memo_ref_pool,
    )
    sanitized_result = _normalize_optional_scalar_rules(sanitized_result)
    reasoning_by_id, _ = _reasoning_lookup(reasoning_bundle)
    evidence_candidates = _collect_global_supporting_evidence_candidates(
        sanitized_result,
        reasoning_by_id=reasoning_by_id,
    )
    sanitized_result.supporting_evidence = _trim_global_evidence(
        evidence_candidates,
        critical_buckets=critical_buckets,
        soft_cap=supporting_evidence_soft_cap,
        hard_cap=supporting_evidence_hard_cap,
    )
    return sanitized_result


def _collect_reduced_refs(
    final_result: StyleBibleResultV2,
    *,
    reasoning_bundle: StyleBibleReasoningBundle,
) -> set[str]:
    reduced_refs = {
        *[row.source_ref for row in final_result.supporting_evidence if clean_text(row.source_ref)],
        *[
            ref
            for entry in reasoning_bundle.entries
            for ref in entry.evidence_refs
            if clean_text(ref)
        ],
        *[
            ref
            for rule in _iter_final_rule_items(final_result)
            for ref in rule.evidence_refs
            if clean_text(ref)
        ],
    }
    return {clean_text(ref) for ref in reduced_refs if clean_text(ref)}


def _summarize_ttft_seconds(values: Iterable[Any]) -> dict[str, Any]:
    rows = [float(value) for value in values if isinstance(value, (int, float)) and float(value) > 0]
    if not rows:
        return {"count": 0, "min_seconds": 0.0, "max_seconds": 0.0, "avg_seconds": 0.0}
    return {
        "count": len(rows),
        "min_seconds": round(min(rows), 3),
        "max_seconds": round(max(rows), 3),
        "avg_seconds": round(sum(rows) / len(rows), 3),
    }


def _build_response_usage_and_request_metrics(
    response: Any,
    assembly: Any,
    *,
    stage: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    first_chunk_seconds = _first_chunk_seconds(response.request_metrics)
    prompt_tokens = _usage_tokens(response.usage_metadata, "input_tokens", "prompt_tokens")
    output_tokens = _usage_tokens(response.usage_metadata, "output_tokens", "completion_tokens")
    total_tokens = _usage_tokens(response.usage_metadata, "total_tokens") or (prompt_tokens + output_tokens)
    cached_tokens = _extract_cached_tokens(response.usage_metadata)
    usage_metadata = {
        "stage": stage,
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        "ttft_seconds": round(first_chunk_seconds, 3) if first_chunk_seconds is not None else 0.0,
        "selected_antipattern_codes": assembly.selected_antipattern_codes,
        "anti_pattern_token_budget": assembly.anti_pattern_token_budget,
        "anti_pattern_token_estimate": assembly.anti_pattern_token_estimate,
        "raw_usage_metadata": response.usage_metadata,
    }
    request_metrics = dict(response.request_metrics)
    request_metrics["stage"] = stage
    request_metrics["selected_antipattern_codes"] = assembly.selected_antipattern_codes
    request_metrics["anti_pattern_token_budget"] = assembly.anti_pattern_token_budget
    request_metrics["anti_pattern_token_estimate"] = assembly.anti_pattern_token_estimate
    return request_metrics, usage_metadata


def _write_failed_local_reduce_summary(bucket_output_dir: Path, *, bucket_id: str, exc: Exception) -> None:
    write_json(
        bucket_output_dir / "local_reduce_summary.json",
        {
            "status": "failed",
            "bucket_id": clean_text(bucket_id),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        },
    )


def _empty_local_partial_record(*, style_id_hint: str, scope_hint: str) -> dict[str, Any]:
    return {
        "reasoning": {
            "reasoning_version": "style-bible-reasoning-v2",
            "style_id": clean_text(style_id_hint),
            "scope": clean_text(scope_hint),
            "entries": [],
        },
        "final": {
            "style_id": clean_text(style_id_hint),
            "scope": clean_text(scope_hint),
            "rule_rows": [],
        },
    }


def _build_sparse_local_reduce_artifact(
    *,
    bucket_memo: StyleBibleBucketMemo,
    output_dir: Path,
    style_id_hint: str,
    scope_hint: str,
    grounding_ref_pool: set[str],
    reasoning_bundle: StyleBibleReasoningBundle,
    partial_record: dict[str, Any],
    request_metrics: dict[str, Any],
    usage_metadata: dict[str, Any],
    preflight_decision: LocalReducePreflightDecision | None = None,
) -> LocalReduceArtifact:
    final_result = StyleBibleResultV2(style_id=style_id_hint, scope=scope_hint)
    final_result.metadata.degradation_status.mode = "degraded"
    final_result.metadata.degradation_status.skipped_sparse_buckets = [clean_text(bucket_memo.bucket_id)]
    record = final_result.model_dump(mode="json", by_alias=True)
    reasoning_record = reasoning_bundle.model_dump(mode="json")
    reduce_trace = _build_reduce_trace(
        reasoning_bundle,
        grounding_ref_pool,
        final_result=final_result,
    )
    reduce_trace["sparse"] = True
    decision_payload: dict[str, Any] = {}
    if preflight_decision is not None:
        decision_payload = {
            "skip": bool(preflight_decision.skip),
            "reason": clean_text(preflight_decision.reason),
            "candidate_count": int(preflight_decision.candidate_count),
            "grounding_ref_count": int(preflight_decision.grounding_ref_count),
            "batch_memo_count": int(preflight_decision.batch_memo_count),
            "item_count": int(preflight_decision.item_count),
        }
        reduce_trace["preflight"] = decision_payload

    write_json(output_dir / "local_partial.json", partial_record)
    write_json(output_dir / "local_final.json", record)
    write_json(output_dir / "local_reasoning.json", reasoning_record)
    write_json(output_dir / "local_reduce_trace.json", reduce_trace)
    write_json(
        output_dir / "local_reduce_summary.json",
        {
            "status": "sparse",
            "bucket_id": clean_text(bucket_memo.bucket_id),
            "memo_id": clean_text(bucket_memo.memo_id),
            "batch_ids": _batch_id_pool([bucket_memo]),
            "style_id": clean_text(record.get("style_id")),
            "scope": clean_text(record.get("scope")),
            "reasoning_entry_count": len(reasoning_bundle.entries),
            "rule_count": 0,
            "reduced_ref_count": 0,
            "grounding_ref_count": len(grounding_ref_pool),
            "preflight_skip": bool(preflight_decision is not None),
            "sparse_reason": (
                clean_text(preflight_decision.reason)
                if preflight_decision is not None
                else "model_returned_empty_partial"
            ),
            "request_metrics": request_metrics,
            "usage_metadata": usage_metadata,
        },
    )
    return LocalReduceArtifact(
        bucket_id=clean_text(bucket_memo.bucket_id),
        memo_id=clean_text(bucket_memo.memo_id),
        batch_ids=_batch_id_pool([bucket_memo]),
        output_dir=output_dir,
        final_result=final_result,
        partial_record=partial_record,
        reasoning_bundle=reasoning_bundle,
        reasoning_record=reasoning_record,
        reduce_trace=reduce_trace,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        reduced_refs=set(),
        grounding_ref_pool=set(grounding_ref_pool),
        sparse=True,
        preflight_decision=decision_payload,
    )


def _run_local_reduce(
    config: StableProjectConfig,
    *,
    source_bundle: dict[str, Any],
    bucket_memo: StyleBibleBucketMemo,
    output_dir: str | Path,
    section_targets: StyleBibleSectionTargets | None = None,
    repair_request: dict[str, Any] | None = None,
    request_key_suffix: str = "",
) -> LocalReduceArtifact:
    bucket_output_dir = ensure_dir(output_dir)
    local_reduce_bundle = _build_bucket_reduce_bundle(
        source_bundle=source_bundle,
        bucket_memo=bucket_memo,
    )
    grounding_ref_pool = set(_grounding_ref_pool([bucket_memo]))
    memo_id_pool = set(_memo_id_pool([bucket_memo]))
    batch_ids = _batch_id_pool([bucket_memo])
    assembly = assemble_local_reducer_prompt(
        prompt_dir=config.prompt_dir,
        bucket_id=bucket_memo.bucket_id,
        axis_focus=bucket_memo.axis_focus,
        local_reduce_bundle=local_reduce_bundle,
        section_targets=(
            section_targets.targets_for_bucket(bucket_memo.bucket_id).as_prompt_payload()
            if section_targets is not None
            else None
        ),
        path_targets=(
            [path_target.as_prompt_payload() for path_target in section_targets.path_targets.values()]
            if section_targets is not None
            else None
        ),
        repair_request=repair_request,
    )
    client = StableOpenAICompatibleStructuredClient(config, artifacts_dir=bucket_output_dir)
    response = client.generate_structured(
        request_key=f"style_bible_local_reduce__{clean_text(bucket_memo.bucket_id)}{clean_text(request_key_suffix)}",
        model_name=config.model.style_bible_model or config.model.style_model,
        response_model=assembly.response_model,
        system_instruction=assembly.system_instruction,
        user_payload=assembly.user_payload,
        temperature=float(config.model.style_bible_temperature or config.model.style_temperature),
        max_output_tokens=int(config.model.style_bible_max_output_tokens or config.model.style_max_output_tokens),
        response_format_mode="json_schema",
        output_contract_mode="blueprint",
    )

    style_id_hint = clean_text(local_reduce_bundle.get("style_bible_id_hint"))
    scope_hint = clean_text(local_reduce_bundle.get("scope_hint"))
    tracker = DropTracker()
    reasoning_bundle = _sanitize_local_reasoning_bundle(
        response.parsed.reasoning,
        bucket_id=bucket_memo.bucket_id,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        memo_ref_pool=grounding_ref_pool,
    )
    # Re-sanitize with tracker to catch reasoning drops
    reasoning_bundle = _sanitize_reasoning_bundle(
        response.parsed.reasoning,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        memo_ref_pool=grounding_ref_pool,
        reasoning_id_prefix=clean_text(bucket_memo.bucket_id),
        tracker=tracker,
    )
    scratchpad_cross_validation = _sanitize_reduce_cross_validation(
        list(response.parsed.scratchpad_cross_validation),
        memo_id_pool=memo_id_pool,
        memo_ref_pool=grounding_ref_pool,
    )
    partial_record = response.parsed.model_dump(mode="json", by_alias=True)
    request_metrics, usage_metadata = _build_response_usage_and_request_metrics(
        response,
        assembly,
        stage="style_bible_local_reduce",
    )
    if repair_request:
        request_metrics["repair_request"] = repair_request
        request_metrics["repair_mode"] = clean_text(repair_request.get("mode")) or "default"
        usage_metadata["repair_request"] = repair_request
        usage_metadata["repair_mode"] = clean_text(repair_request.get("mode")) or "default"
    reasoning_record = reasoning_bundle.model_dump(mode="json")
    if scratchpad_cross_validation:
        reasoning_record["_scratchpad_cross_validation"] = [
            row.model_dump(mode="json")
            for row in scratchpad_cross_validation
        ]
    if scratchpad_cross_validation:
        partial_record["_scratchpad_cross_validation"] = [
            row.model_dump(mode="json")
            for row in scratchpad_cross_validation
        ]

    if not response.parsed.final.rule_rows and not reasoning_bundle.entries:
        return _build_sparse_local_reduce_artifact(
            bucket_memo=bucket_memo,
            output_dir=bucket_output_dir,
            style_id_hint=style_id_hint,
            scope_hint=scope_hint,
            grounding_ref_pool=grounding_ref_pool,
            reasoning_bundle=reasoning_bundle,
            partial_record=partial_record,
            request_metrics=request_metrics,
            usage_metadata=usage_metadata,
        )

    final_result, assembler_conflicts = _assemble_local_partial_result(
        response.parsed,
        bucket_id=bucket_memo.bucket_id,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=grounding_ref_pool,
        tracker=tracker,
    )
    _assert_local_reduce_output_valid(
        final_result,
        bucket_id=bucket_memo.bucket_id,
        reasoning_bundle=reasoning_bundle,
    )
    record = final_result.model_dump(mode="json", by_alias=True)
    reduce_trace = _build_reduce_trace(
        reasoning_bundle,
        grounding_ref_pool,
        final_result=final_result,
    )
    reduce_trace["assembler_conflicts"] = [row.model_dump(mode="json") for row in assembler_conflicts]
    reduce_trace["drop_stats"] = tracker.dump()
    reduced_refs = _collect_reduced_refs(
        final_result,
        reasoning_bundle=reasoning_bundle,
    )

    write_json(bucket_output_dir / "local_partial.json", partial_record)
    write_json(bucket_output_dir / "local_final.json", record)
    write_json(bucket_output_dir / "local_reasoning.json", reasoning_record)
    write_json(bucket_output_dir / "local_reduce_trace.json", reduce_trace)
    write_json(
        bucket_output_dir / "local_reduce_summary.json",
        {
            "status": "success" if len(_iter_final_rule_items(final_result)) > 0 else _determine_empty_status(tracker),
            "bucket_id": clean_text(bucket_memo.bucket_id),
            "memo_id": clean_text(bucket_memo.memo_id),
            "batch_ids": batch_ids,
            "style_id": clean_text(record.get("style_id")),
            "scope": clean_text(record.get("scope")),
            "reasoning_entry_count": len(reasoning_bundle.entries),
            "rule_count": len(_iter_final_rule_items(final_result)),
            "reduced_ref_count": len(reduced_refs),
            "grounding_ref_count": len(grounding_ref_pool),
            "assembler_conflict_count": len(assembler_conflicts),
            "drop_stats": tracker.dump(),
            "request_metrics": request_metrics,
            "usage_metadata": usage_metadata,
        },
    )
    return LocalReduceArtifact(
        bucket_id=clean_text(bucket_memo.bucket_id),
        memo_id=clean_text(bucket_memo.memo_id),
        batch_ids=batch_ids,
        output_dir=bucket_output_dir,
        final_result=final_result,
        partial_record=partial_record,
        reasoning_bundle=reasoning_bundle,
        reasoning_record=reasoning_record,
        reduce_trace=reduce_trace,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        reduced_refs=reduced_refs,
        grounding_ref_pool=grounding_ref_pool,
        assembler_conflicts=assembler_conflicts,
    )


def _assemble_global_merge(
    *,
    local_artifacts: list[LocalReduceArtifact],
    memo_payloads: list[StyleBibleBucketMemo],
    style_id_hint: str,
    scope_hint: str,
    critical_bucket_ids: list[str],
    supporting_evidence_soft_cap: int,
    supporting_evidence_hard_cap: int,
    section_minimums: dict[str, int] | None = None,
) -> GlobalMergeAssembly:
    critical_bucket_set = set(critical_bucket_ids)
    bucket_order = {
        clean_text(bucket_memo.bucket_id): index
        for index, bucket_memo in enumerate(memo_payloads)
    }
    reasoning_bundle = _merge_reasoning_bundles(
        local_artifacts,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
    )
    assembler_conflicts = [
        row.model_copy(deep=True)
        for artifact in local_artifacts
        for row in artifact.assembler_conflicts
    ]
    rule_lineage_records: list[StyleBibleRuleLineageEntry] = []
    merge_events: list[StyleBibleMergeEvent] = []
    merged_result = StyleBibleResultV2(
        style_id=style_id_hint,
        scope=scope_hint,
    )
    for path in LIST_RULE_PATHS:
        _set_rule_path_value(
            merged_result,
            path,
            _merge_rule_lists(
                local_artifacts,
                path=path,
                critical_buckets=critical_bucket_set,
                bucket_order=bucket_order,
                minimum_items=int((section_minimums or {}).get(path, 0) or 0),
                conflict_records=assembler_conflicts,
                rule_lineage_records=rule_lineage_records,
                merge_events=merge_events,
            ),
        )
    for path in OPTIONAL_RULE_PATHS:
        _set_rule_path_value(
            merged_result,
            path,
            _resolve_scalar_candidates(
                local_artifacts,
                path=path,
                critical_buckets=critical_bucket_set,
                bucket_order=bucket_order,
                conflict_records=assembler_conflicts,
                rule_lineage_records=rule_lineage_records,
                merge_events=merge_events,
            ),
        )
    memo_ref_pool = set(_grounding_ref_pool(memo_payloads))
    final_result = _finalize_merged_style_bible_result(
        merged_result,
        reasoning_bundle=reasoning_bundle,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        memo_ref_pool=memo_ref_pool,
        critical_buckets=critical_bucket_ids,
        supporting_evidence_soft_cap=int(supporting_evidence_soft_cap),
        supporting_evidence_hard_cap=int(supporting_evidence_hard_cap),
    )
    return GlobalMergeAssembly(
        final_result=final_result,
        reasoning_bundle=reasoning_bundle,
        assembler_conflicts=assembler_conflicts,
        rule_lineage_records=rule_lineage_records,
        merge_events=merge_events,
    )


def _compute_section_gaps(
    final_result: StyleBibleResultV2,
    *,
    section_targets: StyleBibleSectionTargets,
) -> list[SectionGap]:
    gaps: list[SectionGap] = []
    for path in section_targets.required_scalars:
        if path not in OPTIONAL_RULE_PATHS:
            continue
        actual_count = _count_rule_path_items(final_result, path)
        if actual_count > 0:
            continue
        gaps.append(
            SectionGap(
                path=path,
                gap_type="missing_scalar",
                actual_count=actual_count,
                target_count=1,
                deficit=1,
            )
        )
    for path, target_count in section_targets.minimums.items():
        if path not in LIST_RULE_PATHS:
            continue
        actual_count = _count_rule_path_items(final_result, path)
        if actual_count >= int(target_count):
            continue
        gaps.append(
            SectionGap(
                path=path,
                gap_type="underfilled_list",
                actual_count=actual_count,
                target_count=int(target_count),
                deficit=max(int(target_count) - actual_count, 0),
            )
        )
    gaps.sort(
        key=lambda gap: (
            0 if gap.gap_type == "missing_scalar" else 1,
            -int(gap.deficit),
            gap.path,
        )
    )
    return gaps


def _existing_rows_for_paths(
    final_result: StyleBibleResultV2,
    *,
    paths: Iterable[str],
    limit: int = 16,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for rule in _iter_path_rule_rows(final_result, path):
            rows.append(
                {
                    "path": clean_text(path),
                    "rule_id": clean_text(rule.rule_id),
                    "text": clean_text(rule.text),
                    "trigger": _rule_field_text(rule, "trigger"),
                    "constraint": _rule_field_text(rule, "constraint"),
                    "query_feature_matcher": _rule_field_text(rule, "query_feature_matcher"),
                    "route_target_action": _rule_field_text(rule, "route_target_action"),
                    "forbidden_action": _rule_field_text(rule, "forbidden_action"),
                    "correction_guideline": _rule_field_text(rule, "correction_guideline"),
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def _target_scalar_candidates(
    source_bundle: dict[str, Any],
    *,
    requested_paths: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    global_style_signals = source_bundle.get("global_style_signals", {})
    scalar_payload = global_style_signals.get("scalar_contracts", {}) if isinstance(global_style_signals, dict) else {}
    if not isinstance(scalar_payload, dict):
        return {}
    key_by_path = {
        "narrative_system.perspective": "perspective",
        "narrative_system.distance": "distance",
        "narrative_system.temporality": "temporality",
        "voice_contract.narrator_voice": "narrator_voice",
        "voice_contract.inner_monologue_mode": "inner_monologue_mode",
    }
    candidates: dict[str, list[dict[str, Any]]] = {}
    requested_path_set = {
        canonical_scalar_surface_path(path)
        for path in requested_paths
        if clean_text(path)
    }
    for path, scalar_key in key_by_path.items():
        if path not in requested_path_set:
            continue
        rows = _compact_scalar_candidate_rows(
            scalar_payload.get(scalar_key, []),
            path=path,
        )
        if rows:
            candidates[path] = rows
    return candidates


def _enum_hints_for_paths(paths: Iterable[str]) -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {}
    for path in paths:
        normalized_path = canonical_scalar_surface_path(path)
        spec = _resolve_optional_scalar_spec(normalized_path)
        if spec is None:
            continue
        hints[normalized_path] = list(spec.allowed_values)
    return hints


def _enum_aliases_for_paths(paths: Iterable[str]) -> dict[str, dict[str, str]]:
    aliases: dict[str, dict[str, str]] = {}
    for path in paths:
        normalized_path = canonical_scalar_surface_path(path)
        alias_map = scalar_value_aliases_for_path(normalized_path)
        if alias_map:
            aliases[normalized_path] = alias_map
    return aliases


def _select_repair_requests(
    *,
    local_artifacts: list[LocalReduceArtifact],
    section_targets: StyleBibleSectionTargets,
    gaps: list[SectionGap],
    bucket_order: dict[str, int],
    source_bundle: dict[str, Any],
) -> list[tuple[LocalReduceArtifact, dict[str, Any]]]:
    if not gaps:
        return []
    gap_by_path = {gap.path: gap for gap in gaps}
    candidates: list[tuple[tuple[int, int, int, int, str], LocalReduceArtifact, dict[str, Any]]] = []
    for artifact in local_artifacts:
        bucket_targets = section_targets.targets_for_bucket(artifact.bucket_id)
        source_order = {
            path: index
            for index, path in enumerate(bucket_targets.repair_paths)
        }
        candidate_paths = [
            path
            for path in bucket_targets.repair_paths
            if path in gap_by_path
        ]
        requested_paths = sorted(
            candidate_paths,
            key=lambda path: _repair_request_path_sort_key(
                path,
                gap_by_path=gap_by_path,
                bucket_targets=bucket_targets,
                source_order=source_order,
            ),
        )[: int(section_targets.max_paths_per_bucket)]
        if not requested_paths:
            continue
        missing_scalar_paths = [
            path
            for path in requested_paths
            if gap_by_path[path].gap_type == "missing_scalar"
        ]
        underfilled_paths = [
            {
                "path": gap.path,
                "actual_count": gap.actual_count,
                "target_count": gap.target_count,
                "deficit": gap.deficit,
            }
            for gap in gaps
            if gap.path in requested_paths and gap.gap_type == "underfilled_list"
        ]
        repair_request = {
            "mode": "repair",
            "requested_paths": requested_paths,
            "missing_scalar_paths": missing_scalar_paths,
            "underfilled_paths": underfilled_paths,
            "existing_rows": _existing_rows_for_paths(artifact.final_result, paths=requested_paths),
            "bucket_path_counts": {
                path: _count_rule_path_items(artifact.final_result, path)
                for path in requested_paths
            },
            "target_scalar_candidates": _target_scalar_candidates(
                source_bundle,
                requested_paths=requested_paths,
            ),
            "enum_hints": _enum_hints_for_paths(requested_paths),
            "enum_aliases": _enum_aliases_for_paths(requested_paths),
            "notes": list(bucket_targets.prompt_hints),
        }
        score = (
            -len(missing_scalar_paths),
            -sum(gap_by_path[path].deficit for path in requested_paths),
            int(bucket_targets.repair_priority),
            int(bucket_order.get(clean_text(artifact.bucket_id), 10**6)),
            clean_text(artifact.bucket_id),
        )
        candidates.append((score, artifact, repair_request))
    candidates.sort(key=lambda item: item[0])
    return [
        (artifact, repair_request)
        for _score, artifact, repair_request in candidates[: int(section_targets.max_buckets_per_round)]
    ]


def _select_section_densify_requests(
    *,
    final_result: StyleBibleResultV2,
    section_targets: StyleBibleSectionTargets,
) -> list[SectionDensifyRequest]:
    if not section_targets.densify_enabled or not section_targets.path_targets:
        return []
    requests: list[SectionDensifyRequest] = []
    for path, path_target in section_targets.path_targets.items():
        if not path_target.enabled or path not in LIST_RULE_PATHS:
            continue
        target_count = max(int(path_target.target_count), int(section_targets.minimums.get(path, 0)))
        actual_count = _count_rule_path_items(final_result, path)
        deficit = max(target_count - actual_count, 0)
        if deficit <= 0:
            continue
        requests.append(
            SectionDensifyRequest(
                path=clean_text(path),
                actual_count=actual_count,
                target_count=target_count,
                deficit=deficit,
                path_target=path_target,
            )
        )
    requests.sort(
        key=lambda request: (
            -int(request.deficit),
            _densify_path_group_rank(request.path),
            request.path,
        )
    )
    return requests[: int(section_targets.densify_max_paths_per_round)]


def _score_slot_against_text(
    slot: SectionSlotSpec,
    *,
    text: str,
    slot_vector: list[float] | None = None,
    text_vector: list[float] | None = None,
) -> tuple[float, float, float]:
    vector_score = _cosine_similarity(slot_vector or [], text_vector or [])
    semantic_anchor_score = vector_score if clean_text(text) else 0.0
    return _combine_semantic_scores(vector_score, semantic_anchor_score), vector_score, semantic_anchor_score


def _compute_missing_slots(
    *,
    existing_rows: list[StyleBibleRuleBase],
    path_target: SectionPathTarget,
    embedding_client: StableOpenAICompatibleEmbeddingClient,
    request_key_prefix: str,
) -> tuple[list[SectionSlotSpec], list[dict[str, Any]]]:
    if not path_target.slot_specs:
        return [], []

    slot_texts = [
        _slot_embedding_text(slot, path=path_target.path, downstream_shape=path_target.downstream_shape)
        for slot in path_target.slot_specs
    ]
    slot_embedding_response = embedding_client.embed_texts(
        request_key=f"{request_key_prefix}__slot_specs",
        texts=slot_texts,
    )
    existing_texts = [
        _rule_embedding_text(row, path=path_target.path)
        for row in existing_rows
    ]
    existing_vectors: list[list[float]] = []
    if existing_texts:
        existing_embedding_response = embedding_client.embed_texts(
            request_key=f"{request_key_prefix}__existing_rows",
            texts=existing_texts,
        )
        existing_vectors = existing_embedding_response.vectors

    missing_slots: list[SectionSlotSpec] = []
    coverage_trace: list[dict[str, Any]] = []
    for slot, slot_vector in zip(path_target.slot_specs, slot_embedding_response.vectors, strict=True):
        best_rule_id = ""
        best_text = ""
        best_score = 0.0
        best_vector_score = 0.0
        best_cue_score = 0.0
        for existing_row, existing_vector, existing_text in zip(
            existing_rows,
            existing_vectors,
            existing_texts,
            strict=True,
        ):
            combined_score, vector_score, cue_score = _score_slot_against_text(
                slot,
                text=existing_text,
                slot_vector=slot_vector,
                text_vector=existing_vector,
            )
            if combined_score <= best_score:
                continue
            best_rule_id = clean_text(existing_row.rule_id)
            best_text = clean_text(existing_row.text)
            best_score = combined_score
            best_vector_score = vector_score
            best_cue_score = cue_score
        covered = bool(best_score >= float(path_target.slot_match_threshold))
        if not covered:
            missing_slots.append(slot)
        coverage_trace.append(
            {
                "slot_id": slot.slot_id,
                "covered": covered,
                "best_rule_id": best_rule_id,
                "best_rule_text": best_text,
                "best_score": best_score,
                "best_vector_score": best_vector_score,
                "best_cue_score": best_cue_score,
                "semantic_slot_score": best_vector_score,
                "cue_score": best_cue_score,
                "combined_score": best_score,
                "evidence_overlap_score": 0.0,
                "slot_match_threshold": float(path_target.slot_match_threshold),
            }
        )
    return missing_slots, coverage_trace


def _select_count_expansion_slots(
    path_target: SectionPathTarget,
    *,
    slot_coverage_trace: list[dict[str, Any]],
    deficit: int,
) -> list[SectionSlotSpec]:
    if int(deficit or 0) <= 0 or not path_target.slot_specs:
        return []
    score_by_slot_id = {
        clean_text(row.get("slot_id")): float(row.get("best_score", 0.0) or 0.0)
        for row in slot_coverage_trace
        if isinstance(row, dict) and clean_text(row.get("slot_id"))
    }
    ranked_slots = sorted(
        path_target.slot_specs,
        key=lambda slot: (
            float(score_by_slot_id.get(clean_text(slot.slot_id), 0.0)),
            clean_text(slot.slot_id),
        ),
    )
    limit = max(min(int(deficit), max(int(path_target.max_new_rows), 1)), 1)
    return list(ranked_slots[:limit])


def _retrieve_reasoning_entries_for_slots(
    *,
    reasoning_entries: list[StyleBibleReasoningEntry],
    path_target: SectionPathTarget,
    missing_slots: list[SectionSlotSpec],
    embedding_client: StableOpenAICompatibleEmbeddingClient,
    request_key_prefix: str,
    burned_reasoning_ids: set[str] | None = None,
    burned_evidence_refs: set[str] | None = None,
) -> tuple[list[StyleBibleReasoningEntry], set[str], list[dict[str, Any]], dict[str, Any]]:
    if not missing_slots:
        return [], set(), [], {"candidate_count": 0, "selected_reasoning_ids": []}

    burned_reasoning_ids = {
        clean_text(reasoning_id)
        for reasoning_id in (burned_reasoning_ids or set())
        if clean_text(reasoning_id)
    }
    burned_evidence_refs = {
        clean_text(ref)
        for ref in (burned_evidence_refs or set())
        if clean_text(ref)
    }
    candidate_entries = [
        entry.model_copy(deep=True)
        for entry in reasoning_entries
        if any(clean_text(ref) for ref in entry.evidence_refs)
    ]
    if path_target.bucket_allowlist:
        filtered_entries = [
            entry
            for entry in candidate_entries
            if clean_text(entry.bucket_id) in set(path_target.bucket_allowlist)
        ]
        if filtered_entries:
            candidate_entries = filtered_entries
    burned_candidate_count = 0
    if candidate_entries and (burned_reasoning_ids or burned_evidence_refs):
        filtered_candidates: list[StyleBibleReasoningEntry] = []
        for entry in candidate_entries:
            reasoning_id = clean_text(entry.reasoning_id)
            entry_evidence_refs = {
                clean_text(ref)
                for ref in entry.evidence_refs
                if clean_text(ref)
            }
            if reasoning_id and reasoning_id in burned_reasoning_ids:
                burned_candidate_count += 1
                continue
            if (
                burned_evidence_refs
                and entry_evidence_refs
                and entry_evidence_refs.issubset(burned_evidence_refs)
            ):
                burned_candidate_count += 1
                continue
            filtered_candidates.append(entry)
        candidate_entries = filtered_candidates
    if not candidate_entries:
        return (
            [],
            set(),
            [],
            {
                "candidate_count": 0,
                "selected_reasoning_ids": [],
                "burned_reasoning_id_count": len(burned_reasoning_ids),
                "burned_evidence_ref_count": len(burned_evidence_refs),
                "burned_candidate_count": burned_candidate_count,
            },
        )

    slot_texts = [
        _slot_embedding_text(slot, path=path_target.path, downstream_shape=path_target.downstream_shape)
        for slot in missing_slots
    ]
    slot_embedding_response = embedding_client.embed_texts(
        request_key=f"{request_key_prefix}__slot_queries",
        texts=slot_texts,
    )
    entry_texts = [_reasoning_entry_embedding_text(entry) for entry in candidate_entries]
    entry_embedding_response = embedding_client.embed_texts(
        request_key=f"{request_key_prefix}__reasoning_entries",
        texts=entry_texts,
    )

    scored_rows: list[dict[str, Any]] = []
    for entry, entry_vector, entry_text in zip(
        candidate_entries,
        entry_embedding_response.vectors,
        entry_texts,
        strict=True,
    ):
        slot_scores: list[dict[str, Any]] = []
        for slot, slot_vector in zip(missing_slots, slot_embedding_response.vectors, strict=True):
            combined_score, vector_score, cue_score = _score_slot_against_text(
                slot,
                text=entry_text,
                slot_vector=slot_vector,
                text_vector=entry_vector,
            )
            slot_scores.append(
                {
                    "slot_id": slot.slot_id,
                    "combined_score": combined_score,
                    "vector_score": vector_score,
                    "cue_score": cue_score,
                }
            )
        slot_scores.sort(key=lambda row: (-float(row["combined_score"]), row["slot_id"]))
        best_row = slot_scores[0] if slot_scores else {
            "slot_id": "",
            "combined_score": 0.0,
            "vector_score": 0.0,
            "cue_score": 0.0,
        }
        matched_slot_ids = [
            row["slot_id"]
            for row in slot_scores
            if float(row["combined_score"]) >= max(float(path_target.slot_match_threshold) - 0.1, 0.55)
        ]
        if not matched_slot_ids and clean_text(best_row.get("slot_id")):
            matched_slot_ids = [clean_text(best_row.get("slot_id"))]
        evidence_ref_pool = {
            clean_text(ref)
            for ref in entry.evidence_refs
            if clean_text(ref)
        }
        burned_overlap_count = len(evidence_ref_pool & burned_evidence_refs)
        new_evidence_count = len(evidence_ref_pool - burned_evidence_refs)
        scored_rows.append(
            {
                "entry": entry,
                "best_score": float(best_row.get("combined_score", 0.0) or 0.0),
                "semantic_slot_score": float(best_row.get("vector_score", 0.0) or 0.0),
                "cue_score": float(best_row.get("cue_score", 0.0) or 0.0),
                "combined_score": float(best_row.get("combined_score", 0.0) or 0.0),
                "matched_slot_ids": matched_slot_ids,
                "new_evidence_count": new_evidence_count,
                "burned_overlap_count": burned_overlap_count,
                "evidence_overlap_score": _evidence_overlap_score(
                    burned_overlap_count,
                    len(evidence_ref_pool),
                ),
            }
        )

    scored_rows.sort(
        key=lambda row: (
            -float(row["best_score"]),
            -int(row["new_evidence_count"]),
            int(row["burned_overlap_count"]),
            -len(row["entry"].evidence_refs),
            clean_text(row["entry"].reasoning_id),
        )
    )
    selected_rows = scored_rows[: max(int(path_target.retrieval_top_k), 1)]
    selected_entries = [row["entry"] for row in selected_rows if float(row["best_score"]) > 0]
    grounding_ref_pool = {
        clean_text(ref)
        for entry in selected_entries
        for ref in entry.evidence_refs
        if clean_text(ref)
    }
    retrieved_payload = [
        {
            "reasoning_id": clean_text(row["entry"].reasoning_id),
            "bucket_id": clean_text(row["entry"].bucket_id),
            "axis_ids": _unique_strings(row["entry"].axis_ids),
            "claim": clean_text(row["entry"].claim),
            "observed_commonality": clean_text(row["entry"].observed_commonality),
            "mechanism_inference": clean_text(row["entry"].mechanism_inference),
            "downstream_constraint": clean_text(row["entry"].downstream_constraint),
            "evidence_refs": _unique_strings(row["entry"].evidence_refs),
            "retrieval_score": round(float(row["best_score"]), 4),
            "semantic_slot_score": round(float(row["semantic_slot_score"]), 4),
            "cue_score": round(float(row["cue_score"]), 4),
            "combined_score": round(float(row["combined_score"]), 4),
            "evidence_overlap_score": round(float(row["evidence_overlap_score"]), 4),
            "matched_slot_ids": _unique_strings(row["matched_slot_ids"]),
        }
        for row in selected_rows
        if clean_text(row["entry"].reasoning_id)
    ]
    retrieval_trace = {
        "candidate_count": len(candidate_entries),
        "selected_reasoning_ids": [
            clean_text(entry.reasoning_id)
            for entry in selected_entries
            if clean_text(entry.reasoning_id)
        ],
        "burned_reasoning_id_count": len(burned_reasoning_ids),
        "burned_evidence_ref_count": len(burned_evidence_refs),
        "burned_candidate_count": burned_candidate_count,
        "slot_query_request_metrics": slot_embedding_response.request_metrics,
        "entry_request_metrics": entry_embedding_response.request_metrics,
        "slot_query_usage_metadata": slot_embedding_response.usage_metadata,
        "entry_usage_metadata": entry_embedding_response.usage_metadata,
        "selected_rows": [
            {
                "reasoning_id": clean_text(row["entry"].reasoning_id),
                "matched_slot_ids": _unique_strings(row["matched_slot_ids"]),
                "semantic_slot_score": round(float(row["semantic_slot_score"]), 4),
                "cue_score": round(float(row["cue_score"]), 4),
                "combined_score": round(float(row["combined_score"]), 4),
                "evidence_overlap_score": round(float(row["evidence_overlap_score"]), 4),
                "new_evidence_count": int(row["new_evidence_count"]),
                "burned_overlap_count": int(row["burned_overlap_count"]),
            }
            for row in selected_rows
            if clean_text(row["entry"].reasoning_id)
        ],
    }
    return selected_entries, grounding_ref_pool, retrieved_payload, retrieval_trace


def _build_section_densify_bundle(
    *,
    source_bundle: dict[str, Any],
    reasoning_bundle: StyleBibleReasoningBundle,
    final_result: StyleBibleResultV2,
    request: SectionDensifyRequest,
    missing_slots: list[SectionSlotSpec],
    embedding_client: StableOpenAICompatibleEmbeddingClient,
    request_key_prefix: str,
    burned_reasoning_ids: set[str] | None = None,
    burned_evidence_refs: set[str] | None = None,
) -> tuple[dict[str, Any], set[str], dict[str, Any]]:
    selected_entries, grounding_ref_pool, retrieved_payload, retrieval_trace = _retrieve_reasoning_entries_for_slots(
        reasoning_entries=list(reasoning_bundle.entries),
        path_target=request.path_target,
        missing_slots=missing_slots,
        embedding_client=embedding_client,
        request_key_prefix=request_key_prefix,
        burned_reasoning_ids=burned_reasoning_ids,
        burned_evidence_refs=burned_evidence_refs,
    )
    bundle = {
        "style_bible_id_hint": clean_text(source_bundle.get("style_bible_id_hint")),
        "scope_hint": clean_text(source_bundle.get("scope_hint")),
        "target_path": request.path,
        "target_gap": {
            "actual_count": int(request.actual_count),
            "target_count": int(request.target_count),
            "deficit": int(request.deficit),
        },
        "existing_rows": _existing_rows_for_paths(final_result, paths=[request.path], limit=24),
        "missing_slots": [slot.as_prompt_payload() for slot in missing_slots],
        "retrieved_reasoning_entries": retrieved_payload,
        "grounding_ref_pool": sorted(grounding_ref_pool),
        "source_bucket_ids": _unique_strings(entry.bucket_id for entry in selected_entries),
        "burned_reasoning_ids": sorted(
            {
                clean_text(reasoning_id)
                for reasoning_id in (burned_reasoning_ids or set())
                if clean_text(reasoning_id)
            }
        ),
        "burned_evidence_refs": sorted(
            {
                clean_text(ref)
                for ref in (burned_evidence_refs or set())
                if clean_text(ref)
            }
        ),
        "notes": list(request.path_target.prompt_hints),
    }
    return bundle, grounding_ref_pool, retrieval_trace


def _filter_section_densify_candidates(
    *,
    candidate_rows: list[StyleBibleRuleBase],
    existing_rows: list[StyleBibleRuleBase],
    missing_slots: list[SectionSlotSpec],
    path_target: SectionPathTarget,
    max_keep: int,
    embedding_client: StableOpenAICompatibleEmbeddingClient,
    request_key_prefix: str,
    retrieved_reasoning_entries: list[dict[str, Any]] | None = None,
) -> tuple[list[StyleBibleRuleBase], dict[str, Any]]:
    if not candidate_rows or max_keep <= 0:
        return [], {"candidates": [], "kept_rule_ids": [], "semantic_dedupe_drops": []}

    candidate_texts = [_rule_embedding_text(row, path=path_target.path) for row in candidate_rows]
    candidate_embedding_response = embedding_client.embed_texts(
        request_key=f"{request_key_prefix}__candidate_rows",
        texts=candidate_texts,
    )
    existing_texts = [_rule_embedding_text(row, path=path_target.path) for row in existing_rows]
    existing_vectors: list[list[float]] = []
    existing_request_metrics: dict[str, Any] = {}
    existing_usage_metadata: dict[str, Any] = {}
    if existing_texts:
        existing_embedding_response = embedding_client.embed_texts(
            request_key=f"{request_key_prefix}__existing_rows",
            texts=existing_texts,
        )
        existing_vectors = existing_embedding_response.vectors
        existing_request_metrics = existing_embedding_response.request_metrics
        existing_usage_metadata = existing_embedding_response.usage_metadata

    slot_vectors: list[list[float]] = []
    slot_request_metrics: dict[str, Any] = {}
    slot_usage_metadata: dict[str, Any] = {}
    if missing_slots:
        slot_texts = [
            _slot_embedding_text(slot, path=path_target.path, downstream_shape=path_target.downstream_shape)
            for slot in missing_slots
        ]
        slot_embedding_response = embedding_client.embed_texts(
            request_key=f"{request_key_prefix}__slot_specs",
            texts=slot_texts,
        )
        slot_vectors = slot_embedding_response.vectors
        slot_request_metrics = slot_embedding_response.request_metrics
        slot_usage_metadata = slot_embedding_response.usage_metadata
    slot_specs_by_id = {
        clean_text(slot.slot_id): slot
        for slot in missing_slots
        if clean_text(slot.slot_id)
    }
    slot_evidence_ref_pools = _slot_evidence_ref_pools(
        missing_slots=missing_slots,
        retrieved_reasoning_entries=retrieved_reasoning_entries,
    )

    annotated_candidates: list[dict[str, Any]] = []
    for rule, candidate_vector, candidate_text in zip(
        candidate_rows,
        candidate_embedding_response.vectors,
        candidate_texts,
        strict=True,
    ):
        best_existing_score = 0.0
        best_existing_rule_id = ""
        best_existing_rule_text = ""
        for existing_row, existing_vector, existing_text in zip(
            existing_rows,
            existing_vectors,
            existing_texts,
            strict=True,
        ):
            semantic_score = _combine_semantic_scores(
                _cosine_similarity(candidate_vector, existing_vector),
                1.0 if _normalize_text_key(candidate_text) == _normalize_text_key(existing_text) else 0.0,
            )
            if semantic_score <= best_existing_score:
                continue
            best_existing_score = semantic_score
            best_existing_rule_id = clean_text(existing_row.rule_id)
            best_existing_rule_text = clean_text(existing_row.text)

        best_slot_id = ""
        best_slot_score = 0.0
        best_slot_vector_score = 0.0
        best_slot_cue_score = 0.0
        for slot, slot_vector in zip(missing_slots, slot_vectors, strict=True):
            combined_score, vector_score, cue_score = _score_slot_against_text(
                slot,
                text=candidate_text,
                slot_vector=slot_vector,
                text_vector=candidate_vector,
            )
            if combined_score <= best_slot_score:
                continue
            best_slot_id = clean_text(slot.slot_id)
            best_slot_score = combined_score
            best_slot_vector_score = vector_score
            best_slot_cue_score = cue_score
        candidate_evidence_refs = {
            clean_text(ref)
            for ref in rule.evidence_refs
            if clean_text(ref)
        }
        best_slot_spec = slot_specs_by_id.get(best_slot_id)
        fresh_slot_evidence_required = bool(best_slot_spec and best_slot_spec.fresh_evidence_required)
        fresh_slot_evidence_hit = True
        evidence_overlap_count = 0
        if fresh_slot_evidence_required:
            evidence_overlap_count = len(candidate_evidence_refs & slot_evidence_ref_pools.get(best_slot_id, set()))
            fresh_slot_evidence_hit = bool(evidence_overlap_count)
        evidence_overlap_score = _evidence_overlap_score(
            evidence_overlap_count,
            len(candidate_evidence_refs),
        )
        gray_keep_eligible = bool(
            missing_slots
            and best_slot_id
            and float(path_target.soft_slot_match_floor) <= float(best_slot_score) < float(path_target.slot_match_threshold)
            and fresh_slot_evidence_hit
        )

        annotated_candidates.append(
            {
                "rule": rule,
                "vector": candidate_vector,
                "best_slot_id": best_slot_id,
                "best_slot_score": round(best_slot_score, 4),
                "best_existing_score": round(best_existing_score, 4),
                "best_existing_rule_id": best_existing_rule_id,
                "best_existing_rule_text": best_existing_rule_text,
                "semantic_slot_score": round(best_slot_vector_score, 4),
                "cue_score": round(best_slot_cue_score, 4),
                "combined_score": round(best_slot_score, 4),
                "evidence_overlap_score": evidence_overlap_score,
                "fresh_slot_evidence_required": fresh_slot_evidence_required,
                "fresh_slot_evidence_hit": fresh_slot_evidence_hit,
                "gray_keep_eligible": gray_keep_eligible,
            }
        )

    annotated_candidates.sort(
        key=lambda row: (
            -float(row["best_slot_score"]),
            -len(row["rule"].evidence_refs),
            clean_text(row["rule"].rule_id),
        )
    )
    kept_rows: list[StyleBibleRuleBase] = []
    kept_vectors: list[list[float]] = []
    used_slot_ids: set[str] = set()
    gray_keep_count = 0
    filter_trace_rows: list[dict[str, Any]] = []
    semantic_dedupe_drops: list[dict[str, Any]] = []
    for row in annotated_candidates:
        status = "keep"
        keep_status = "keep"
        semantic_match_rule_id = ""
        semantic_match_rule_text = ""
        semantic_match_source = ""
        semantic_match_score = 0.0
        if missing_slots and float(row["best_slot_score"]) < float(path_target.slot_match_threshold):
            if not bool(row["gray_keep_eligible"]) or gray_keep_count >= int(path_target.max_gray_keep):
                status = "drop_slot_miss"
            else:
                keep_status = "keep_gray_slot"
        if status == "keep" and clean_text(row["best_slot_id"]) and clean_text(row["best_slot_id"]) in used_slot_ids:
            status = "drop_slot_duplicate"
        elif status == "keep" and float(row["best_existing_score"]) >= float(path_target.dedupe_threshold):
            status = "drop_semantic_duplicate_existing"
            semantic_match_rule_id = clean_text(row["best_existing_rule_id"])
            semantic_match_rule_text = clean_text(row["best_existing_rule_text"])
            semantic_match_source = "existing_rows"
            semantic_match_score = float(row["best_existing_score"])
        elif status == "keep":
            duplicate_new_score = 0.0
            duplicate_new_rule_id = ""
            duplicate_new_rule_text = ""
            for kept_rule, kept_vector in zip(kept_rows, kept_vectors, strict=True):
                semantic_score = _cosine_similarity(row["vector"], kept_vector)
                if semantic_score <= duplicate_new_score:
                    continue
                duplicate_new_score = semantic_score
                duplicate_new_rule_id = clean_text(kept_rule.rule_id)
                duplicate_new_rule_text = clean_text(kept_rule.text)
            if duplicate_new_score >= float(path_target.dedupe_threshold):
                status = "drop_semantic_duplicate_new"
                semantic_match_rule_id = duplicate_new_rule_id
                semantic_match_rule_text = duplicate_new_rule_text
                semantic_match_source = "kept_candidates"
                semantic_match_score = duplicate_new_score
            elif len(kept_rows) >= int(max_keep):
                status = "drop_cap"
        if status == "keep":
            status = keep_status
        if status in {"keep", "keep_gray_slot"}:
            kept_rows.append(row["rule"].model_copy(deep=True))
            kept_vectors.append(list(row["vector"]))
            if clean_text(row["best_slot_id"]):
                used_slot_ids.add(clean_text(row["best_slot_id"]))
            if status == "keep_gray_slot":
                gray_keep_count += 1
        elif status in {"drop_semantic_duplicate_existing", "drop_semantic_duplicate_new"}:
            semantic_dedupe_drops.append(
                {
                    "dropped_rule_id": clean_text(row["rule"].rule_id),
                    "dropped_rule_text": clean_text(row["rule"].text),
                    "drop_reason": status,
                    "matched_rule_id": semantic_match_rule_id,
                    "matched_rule_text": semantic_match_rule_text,
                    "matched_rule_source": semantic_match_source,
                    "semantic_score": round(float(semantic_match_score), 4),
                    "dedupe_threshold": float(path_target.dedupe_threshold),
                    "best_slot_id": clean_text(row["best_slot_id"]),
                    "best_slot_score": float(row["best_slot_score"]),
                    "semantic_slot_score": float(row["semantic_slot_score"]),
                    "cue_score": float(row["cue_score"]),
                    "combined_score": float(row["combined_score"]),
                    "evidence_overlap_score": float(row["evidence_overlap_score"]),
                }
            )
        filter_trace_rows.append(
            {
                "rule_id": clean_text(row["rule"].rule_id),
                "status": status,
                "best_slot_id": clean_text(row["best_slot_id"]),
                "best_slot_score": float(row["best_slot_score"]),
                "best_existing_score": float(row["best_existing_score"]),
                "semantic_slot_score": float(row["semantic_slot_score"]),
                "cue_score": float(row["cue_score"]),
                "combined_score": float(row["combined_score"]),
                "evidence_overlap_score": float(row["evidence_overlap_score"]),
                "fresh_slot_evidence_required": bool(row["fresh_slot_evidence_required"]),
                "fresh_slot_evidence_hit": bool(row["fresh_slot_evidence_hit"]),
                "gray_keep_eligible": bool(row["gray_keep_eligible"]),
                "slot_match_threshold": float(path_target.slot_match_threshold),
                "soft_slot_match_floor": float(path_target.soft_slot_match_floor),
                "semantic_match_rule_id": semantic_match_rule_id,
                "semantic_match_source": semantic_match_source,
                "semantic_match_score": round(float(semantic_match_score), 4),
            }
        )

    filter_trace = {
        "candidates": filter_trace_rows,
        "kept_rule_ids": [clean_text(rule.rule_id) for rule in kept_rows if clean_text(rule.rule_id)],
        "semantic_dedupe_drops": semantic_dedupe_drops,
        "semantic_dedupe_drop_count": len(semantic_dedupe_drops),
        "gray_keep_count": gray_keep_count,
        "candidate_request_metrics": candidate_embedding_response.request_metrics,
        "existing_request_metrics": existing_request_metrics,
        "slot_request_metrics": slot_request_metrics,
        "candidate_usage_metadata": candidate_embedding_response.usage_metadata,
        "existing_usage_metadata": existing_usage_metadata,
        "slot_usage_metadata": slot_usage_metadata,
    }
    return kept_rows, filter_trace


def _write_section_densify_outputs(
    output_dir: Path,
    *,
    summary_record: dict[str, Any],
    partial_record: dict[str, Any] | None = None,
    final_record: dict[str, Any] | None = None,
    reasoning_record: dict[str, Any] | None = None,
    reduce_trace: dict[str, Any] | None = None,
    semantic_dedupe_pairs: list[dict[str, Any]] | None = None,
) -> None:
    output_dir = ensure_dir(output_dir)
    if partial_record is not None:
        write_json(output_dir / "section_densify_partial.json", partial_record)
    if final_record is not None:
        write_json(output_dir / "section_densify_final.json", final_record)
    if reasoning_record is not None:
        write_json(output_dir / "section_densify_reasoning.json", reasoning_record)
    if reduce_trace is not None:
        write_json(output_dir / "section_densify_trace.json", reduce_trace)
    if semantic_dedupe_pairs is not None:
        write_json(output_dir / "semantic_dedupe_drop_pairs.json", semantic_dedupe_pairs)
    write_json(output_dir / "section_densify_summary.json", summary_record)


def _run_section_densify_pass(
    config: StableProjectConfig,
    *,
    source_bundle: dict[str, Any],
    request: SectionDensifyRequest,
    missing_slots: list[SectionSlotSpec],
    slot_coverage_trace: list[dict[str, Any]],
    existing_rows: list[StyleBibleRuleBase],
    densify_bundle: dict[str, Any],
    grounding_ref_pool: set[str],
    retrieval_trace: dict[str, Any],
    embedding_client: StableOpenAICompatibleEmbeddingClient,
    output_dir: Path,
    round_index: int,
) -> tuple[LocalReduceArtifact | None, dict[str, Any]]:
    output_dir = ensure_dir(output_dir)
    assembly = assemble_section_densify_prompt(
        prompt_dir=config.prompt_dir,
        target_path=request.path,
        path_target=request.path_target.as_prompt_payload(),
        densify_bundle=densify_bundle,
    )
    request_metrics: dict[str, Any] = {}
    usage_metadata: dict[str, Any] = {}
    partial_record: dict[str, Any] | None = None
    try:
        client = StableOpenAICompatibleStructuredClient(config, artifacts_dir=output_dir)
        response = client.generate_structured(
            request_key=f"style_bible_section_densify__{_slugify(request.path)}__{round_index + 1:02d}",
            model_name=config.model.style_bible_model or config.model.style_model,
            response_model=assembly.response_model,
            system_instruction=assembly.system_instruction,
            user_payload=assembly.user_payload,
            temperature=float(config.model.style_bible_temperature or config.model.style_temperature),
            max_output_tokens=int(config.model.style_bible_max_output_tokens or config.model.style_max_output_tokens),
            response_format_mode="json_schema",
            output_contract_mode="blueprint",
        )
        request_metrics, usage_metadata = _build_response_usage_and_request_metrics(
            response,
            assembly,
            stage="style_bible_section_densify",
        )
        partial_record = response.parsed.model_dump(mode="json", by_alias=True)
        densify_bucket_id = f"section_densify__{_slugify(request.path)}"
        reasoning_bundle = _sanitize_reasoning_bundle(
            response.parsed.reasoning,
            style_id_hint=clean_text(source_bundle.get("style_bible_id_hint")),
            scope_hint=clean_text(source_bundle.get("scope_hint")),
            memo_ref_pool=grounding_ref_pool,
            reasoning_id_prefix=densify_bucket_id,
        )
        candidate_rows = _sanitize_rule_rows_for_path(
            response.parsed,
            target_path=request.path,
            reasoning_bundle=reasoning_bundle,
            memo_ref_pool=grounding_ref_pool,
            bucket_id_prefix=densify_bucket_id,
        )
        max_keep = min(
            int(request.deficit),
            int(request.path_target.max_new_rows or request.deficit),
            len(missing_slots) or int(request.deficit),
        )
        kept_rows, candidate_filter_trace = _filter_section_densify_candidates(
            candidate_rows=candidate_rows,
            existing_rows=existing_rows,
            missing_slots=missing_slots,
            path_target=request.path_target,
            max_keep=max_keep,
            embedding_client=embedding_client,
            request_key_prefix=f"section_densify__{_slugify(request.path)}__{round_index + 1:02d}",
            retrieved_reasoning_entries=densify_bundle.get("retrieved_reasoning_entries", []),
        )
        reasoning_bundle = _prune_reasoning_bundle_to_rules(reasoning_bundle, rules=kept_rows)
        final_result = StyleBibleResultV2(
            style_id=clean_text(source_bundle.get("style_bible_id_hint")),
            scope=clean_text(source_bundle.get("scope_hint")),
        )
        _set_rule_path_value(final_result, request.path, [row.model_copy(deep=True) for row in kept_rows])
        final_record = final_result.model_dump(mode="json", by_alias=True)
        reasoning_record = reasoning_bundle.model_dump(mode="json")
        reduce_trace = _build_reduce_trace(
            reasoning_bundle,
            grounding_ref_pool,
            final_result=final_result,
        )
        reduce_trace["target_path"] = request.path
        reduce_trace["slot_coverage"] = slot_coverage_trace
        reduce_trace["retrieval"] = retrieval_trace
        reduce_trace["candidate_filter"] = candidate_filter_trace
        reduced_refs = _collect_reduced_refs(final_result, reasoning_bundle=reasoning_bundle)
        request_metrics["target_path"] = request.path
        request_metrics["slot_coverage"] = slot_coverage_trace
        request_metrics["retrieval"] = retrieval_trace
        request_metrics["candidate_filter"] = candidate_filter_trace
        summary_record = {
            "status": "success" if kept_rows else _determine_empty_status(tracker, candidate_filter_trace),            "target_path": request.path,
            "actual_count": int(request.actual_count),
            "target_count": int(request.target_count),
            "deficit": int(request.deficit),
            "missing_slot_ids": [slot.slot_id for slot in missing_slots],
            "kept_rule_count": len(kept_rows),
            "retrieved_reasoning_count": len(densify_bundle.get("retrieved_reasoning_entries", [])),
            "semantic_dedupe_drop_count": int(candidate_filter_trace.get("semantic_dedupe_drop_count", 0) or 0),
            "gray_keep_count": int(candidate_filter_trace.get("gray_keep_count", 0) or 0),
            "request_metrics": request_metrics,
            "usage_metadata": usage_metadata,
            "slot_coverage": slot_coverage_trace,
            "retrieval": retrieval_trace,
            "candidate_filter": candidate_filter_trace,
            "output_dir": str(output_dir.resolve()),
        }
        _write_section_densify_outputs(
            output_dir,
            summary_record=summary_record,
            partial_record=partial_record,
            final_record=final_record,
            reasoning_record=reasoning_record,
            reduce_trace=reduce_trace,
            semantic_dedupe_pairs=candidate_filter_trace.get("semantic_dedupe_drops", []),
        )
        if not kept_rows:
            return None, summary_record
        artifact = LocalReduceArtifact(
            bucket_id=f"section_densify__{_slugify(request.path)}",
            memo_id=f"section_densify::{request.path}",
            batch_ids=_unique_strings(densify_bundle.get("source_bucket_ids", [])),
            output_dir=output_dir,
            final_result=final_result,
            partial_record=partial_record or {},
            reasoning_bundle=reasoning_bundle,
            reasoning_record=reasoning_record,
            reduce_trace=reduce_trace,
            request_metrics=request_metrics,
            usage_metadata=usage_metadata,
            reduced_refs=reduced_refs,
            grounding_ref_pool=set(grounding_ref_pool),
        )
        return artifact, summary_record
    except Exception as exc:
        summary_record = {
            "status": "request_failed",
            "target_path": request.path,
            "actual_count": int(request.actual_count),
            "target_count": int(request.target_count),
            "deficit": int(request.deficit),
            "missing_slot_ids": [slot.slot_id for slot in missing_slots],
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "request_metrics": request_metrics,
            "usage_metadata": usage_metadata,
            "slot_coverage": slot_coverage_trace,
            "retrieval": retrieval_trace,
            "output_dir": str(output_dir.resolve()),
        }
        _write_section_densify_outputs(output_dir, summary_record=summary_record, partial_record=partial_record)
        return None, summary_record


def _run_section_densify_passes(
    config: StableProjectConfig,
    *,
    source_bundle: dict[str, Any],
    memo_payloads: list[StyleBibleBucketMemo],
    local_artifacts: list[LocalReduceArtifact],
    merge_assembly: GlobalMergeAssembly,
    section_targets: StyleBibleSectionTargets,
    output_dir: Path,
) -> tuple[list[LocalReduceArtifact], list[dict[str, Any]]]:
    if (
        not local_artifacts
        or not section_targets.densify_enabled
        or int(section_targets.densify_max_rounds) <= 0
        or not section_targets.path_targets
    ):
        return [], []
    embedding_config = getattr(config, "embedding", None)
    if embedding_config is None or not bool(getattr(embedding_config, "enabled", False)):
        return [], []
    if not clean_text(getattr(embedding_config, "model", "")):
        return [], []

    reports: list[dict[str, Any]] = []
    try:
        embedding_client = StableOpenAICompatibleEmbeddingClient(config, artifacts_dir=ensure_dir(output_dir))
    except Exception as exc:
        reports.append(
            {
                "status": "embedding_unavailable",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        return [], reports

    densify_artifacts: list[LocalReduceArtifact] = []
    current_merge = merge_assembly
    reduce_config = config.style_bible_reduce
    critical_bucket_ids = sorted(_unique_strings(reduce_config.critical_buckets))
    burned_reasoning_ids_by_path: dict[str, set[str]] = {}
    burned_evidence_refs_by_path: dict[str, set[str]] = {}
    for round_index in range(int(section_targets.densify_max_rounds)):
        requests = _select_section_densify_requests(
            final_result=current_merge.final_result,
            section_targets=section_targets,
        )
        if not requests:
            break
        round_artifacts: list[LocalReduceArtifact] = []
        progress_made = False
        for request in requests:
            request_output_dir = ensure_dir(output_dir / _slugify(request.path) / f"pass_{round_index + 1:02d}")
            existing_rows = _iter_path_rule_rows(current_merge.final_result, request.path)
            try:
                missing_slots, slot_coverage_trace = _compute_missing_slots(
                    existing_rows=existing_rows,
                    path_target=request.path_target,
                    embedding_client=embedding_client,
                    request_key_prefix=f"section_densify__{_slugify(request.path)}__{round_index + 1:02d}",
                )
            except Exception as exc:
                report = {
                    "status": "slot_coverage_failed",
                    "target_path": request.path,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "output_dir": str(request_output_dir.resolve()),
                }
                _write_section_densify_outputs(request_output_dir, summary_record=report)
                reports.append(report)
                continue

            count_expansion_slots: list[SectionSlotSpec] = []
            if request.path_target.slot_specs and not missing_slots and request.deficit > 0:
                count_expansion_slots = _select_count_expansion_slots(
                    request.path_target,
                    slot_coverage_trace=slot_coverage_trace,
                    deficit=request.deficit,
                )
            active_slots = missing_slots or count_expansion_slots
            if request.path_target.slot_specs and not active_slots:
                report = {
                    "status": "skipped_slots_full",
                    "target_path": request.path,
                    "actual_count": int(request.actual_count),
                    "target_count": int(request.target_count),
                    "deficit": int(request.deficit),
                    "missing_slot_ids": [],
                    "slot_coverage": slot_coverage_trace,
                    "output_dir": str(request_output_dir.resolve()),
                }
                _write_section_densify_outputs(request_output_dir, summary_record=report)
                reports.append(report)
                continue

            try:
                densify_bundle, grounding_ref_pool, retrieval_trace = _build_section_densify_bundle(
                    source_bundle=source_bundle,
                    reasoning_bundle=current_merge.reasoning_bundle,
                    final_result=current_merge.final_result,
                    request=request,
                    missing_slots=active_slots,
                    embedding_client=embedding_client,
                    request_key_prefix=f"section_densify__{_slugify(request.path)}__{round_index + 1:02d}",
                    burned_reasoning_ids=burned_reasoning_ids_by_path.get(request.path, set()),
                    burned_evidence_refs=burned_evidence_refs_by_path.get(request.path, set()),
                )
            except Exception as exc:
                report = {
                    "status": "retrieval_failed",
                    "target_path": request.path,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "slot_coverage": slot_coverage_trace,
                    "output_dir": str(request_output_dir.resolve()),
                }
                _write_section_densify_outputs(request_output_dir, summary_record=report)
                reports.append(report)
                continue

            if not densify_bundle.get("retrieved_reasoning_entries"):
                report = {
                    "status": "skipped_no_retrieval",
                    "target_path": request.path,
                    "actual_count": int(request.actual_count),
                    "target_count": int(request.target_count),
                    "deficit": int(request.deficit),
                    "missing_slot_ids": [slot.slot_id for slot in missing_slots],
                    "slot_coverage": slot_coverage_trace,
                    "retrieval": retrieval_trace,
                    "burned_reasoning_id_count": len(burned_reasoning_ids_by_path.get(request.path, set())),
                    "burned_evidence_ref_count": len(burned_evidence_refs_by_path.get(request.path, set())),
                    "output_dir": str(request_output_dir.resolve()),
                }
                _write_section_densify_outputs(request_output_dir, summary_record=report)
                reports.append(report)
                continue

            artifact, report = _run_section_densify_pass(
                config,
                source_bundle=source_bundle,
                request=request,
                missing_slots=active_slots,
                slot_coverage_trace=slot_coverage_trace,
                existing_rows=existing_rows,
                densify_bundle=densify_bundle,
                grounding_ref_pool=grounding_ref_pool,
                retrieval_trace=retrieval_trace,
                embedding_client=embedding_client,
                output_dir=request_output_dir,
                round_index=round_index,
            )
            if count_expansion_slots:
                report["slot_generation_mode"] = "count_expansion"
                report["expansion_slot_ids"] = [slot.slot_id for slot in count_expansion_slots]
            reports.append(report)
            if artifact is None:
                continue
            burned_reasoning_ids, burned_evidence_refs = _collect_rule_reasoning_and_evidence_refs(
                rules=_iter_path_rule_rows(artifact.final_result, request.path)
            )
            if burned_reasoning_ids:
                burned_reasoning_ids_by_path.setdefault(request.path, set()).update(burned_reasoning_ids)
            if burned_evidence_refs:
                burned_evidence_refs_by_path.setdefault(request.path, set()).update(burned_evidence_refs)
            round_artifacts.append(artifact)
            progress_made = True

        if not progress_made:
            break
        densify_artifacts.extend(round_artifacts)
        current_merge = _assemble_global_merge(
            local_artifacts=[*local_artifacts, *densify_artifacts],
            memo_payloads=memo_payloads,
            style_id_hint=clean_text(source_bundle.get("style_bible_id_hint")),
            scope_hint=clean_text(source_bundle.get("scope_hint")),
            critical_bucket_ids=critical_bucket_ids,
            supporting_evidence_soft_cap=int(reduce_config.supporting_evidence_soft_cap),
            supporting_evidence_hard_cap=int(reduce_config.supporting_evidence_hard_cap),
            section_minimums=section_targets.minimums,
        )
    return densify_artifacts, reports


def _merge_local_request_metrics(
    base_metrics: dict[str, Any],
    repair_metrics: dict[str, Any],
    *,
    repair_passes: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(base_metrics)
    attempts: list[dict[str, Any]] = []
    for metrics in (base_metrics, repair_metrics):
        rows = metrics.get("attempts", [])
        if isinstance(rows, list):
            attempts.extend(row for row in rows if isinstance(row, dict))
    merged["attempts"] = attempts
    merged["total_elapsed_seconds"] = round(
        float(base_metrics.get("total_elapsed_seconds", 0.0) or 0.0)
        + float(repair_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
        3,
    )
    merged["response_chars"] = int(base_metrics.get("response_chars", 0) or 0) + int(
        repair_metrics.get("response_chars", 0) or 0
    )
    merged["selected_antipattern_codes"] = _unique_strings(
        [
            *base_metrics.get("selected_antipattern_codes", []),
            *repair_metrics.get("selected_antipattern_codes", []),
        ]
    )
    merged["repair_used"] = True
    merged["repair_pass_count"] = len(repair_passes)
    merged["repair_passes"] = repair_passes
    return merged


def _merge_local_usage_metadata(
    base_usage: dict[str, Any],
    repair_usage: dict[str, Any],
    *,
    repair_passes: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_tokens = int(base_usage.get("prompt_tokens", 0) or 0) + int(repair_usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(base_usage.get("output_tokens", 0) or 0) + int(repair_usage.get("output_tokens", 0) or 0)
    total_tokens = int(base_usage.get("total_tokens", 0) or 0) + int(repair_usage.get("total_tokens", 0) or 0)
    cached_tokens = int(base_usage.get("cached_tokens", 0) or 0) + int(repair_usage.get("cached_tokens", 0) or 0)
    ttft_summary = _summarize_ttft_seconds(
        [
            base_usage.get("ttft_seconds", 0.0),
            repair_usage.get("ttft_seconds", 0.0),
        ]
    )
    merged = dict(base_usage)
    merged["cached_tokens"] = cached_tokens
    merged["prompt_tokens"] = prompt_tokens
    merged["input_tokens"] = prompt_tokens
    merged["output_tokens"] = output_tokens
    merged["total_tokens"] = total_tokens
    merged["overall_cache_hit_ratio"] = round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0
    merged["ttft_seconds"] = float(ttft_summary.get("avg_seconds", 0.0) or 0.0)
    merged["ttft_summary"] = ttft_summary
    merged["selected_antipattern_codes"] = _unique_strings(
        [
            *base_usage.get("selected_antipattern_codes", []),
            *repair_usage.get("selected_antipattern_codes", []),
        ]
    )
    merged["repair_used"] = True
    merged["repair_pass_count"] = len(repair_passes)
    merged["repair_passes"] = repair_passes
    merged["repair_usage_metadata"] = [
        repair_usage,
    ]
    return merged


def _rewrite_local_artifact_outputs(artifact: LocalReduceArtifact) -> None:
    record = artifact.final_result.model_dump(mode="json", by_alias=True)
    reasoning_record = artifact.reasoning_bundle.model_dump(mode="json")
    reduce_trace = dict(artifact.reduce_trace)
    write_json(artifact.output_dir / "local_final.json", record)
    write_json(artifact.output_dir / "local_reasoning.json", reasoning_record)
    write_json(artifact.output_dir / "local_reduce_trace.json", reduce_trace)
    write_json(
        artifact.output_dir / "local_reduce_summary.json",
        {
            "status": "success",
            "bucket_id": artifact.bucket_id,
            "memo_id": artifact.memo_id,
            "batch_ids": list(artifact.batch_ids),
            "style_id": clean_text(record.get("style_id")),
            "scope": clean_text(record.get("scope")),
            "reasoning_entry_count": len(artifact.reasoning_bundle.entries),
            "rule_count": len(_iter_final_rule_items(artifact.final_result)),
            "reduced_ref_count": len(artifact.reduced_refs),
            "grounding_ref_count": len(artifact.grounding_ref_pool),
            "assembler_conflict_count": len(artifact.assembler_conflicts),
            "request_metrics": artifact.request_metrics,
            "usage_metadata": artifact.usage_metadata,
            "repair_pass_count": len(artifact.repair_passes),
            "repair_passes": artifact.repair_passes,
        },
    )


def _merge_local_artifact_with_repair(
    base_artifact: LocalReduceArtifact,
    repair_artifact: LocalReduceArtifact,
    *,
    repair_request: dict[str, Any],
) -> LocalReduceArtifact:
    bucket_order = {clean_text(base_artifact.bucket_id): 0}
    grounding_ref_pool = set(base_artifact.grounding_ref_pool) | set(repair_artifact.grounding_ref_pool)
    reasoning_bundle = _merge_reasoning_bundles(
        [base_artifact, repair_artifact],
        style_id_hint=clean_text(base_artifact.final_result.style_id) or clean_text(repair_artifact.final_result.style_id),
        scope_hint=clean_text(base_artifact.final_result.scope) or clean_text(repair_artifact.final_result.scope),
    )
    assembler_conflicts = [
        row.model_copy(deep=True)
        for row in [*base_artifact.assembler_conflicts, *repair_artifact.assembler_conflicts]
    ]
    merged_result = StyleBibleResultV2(
        style_id=clean_text(base_artifact.final_result.style_id) or clean_text(repair_artifact.final_result.style_id),
        scope=clean_text(base_artifact.final_result.scope) or clean_text(repair_artifact.final_result.scope),
    )
    for path in LIST_RULE_PATHS:
        _set_rule_path_value(
            merged_result,
            path,
            _merge_rule_lists(
                [base_artifact, repair_artifact],
                path=path,
                critical_buckets=set(),
                bucket_order=bucket_order,
                conflict_records=assembler_conflicts,
            ),
        )
    for path in OPTIONAL_RULE_PATHS:
        _set_rule_path_value(
            merged_result,
            path,
            _resolve_scalar_candidates(
                [base_artifact, repair_artifact],
                path=path,
                critical_buckets=set(),
                bucket_order=bucket_order,
                conflict_records=assembler_conflicts,
            ),
        )
    final_result = _sanitize_local_style_bible_result(
        merged_result,
        bucket_id=base_artifact.bucket_id,
        style_id_hint=clean_text(merged_result.style_id),
        scope_hint=clean_text(merged_result.scope),
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=grounding_ref_pool,
    )
    final_result.metadata.degradation_status.mode = "degraded" if assembler_conflicts else "complete"
    final_result.metadata.degradation_status.assembler_conflicts = [
        row.model_copy(deep=True)
        for row in assembler_conflicts
    ]
    reduced_refs = _collect_reduced_refs(
        final_result,
        reasoning_bundle=reasoning_bundle,
    )
    repair_passes = [
        *base_artifact.repair_passes,
        {
            "mode": clean_text(repair_request.get("mode")) or "repair",
            "requested_paths": _unique_strings(repair_request.get("requested_paths", [])),
            "missing_scalar_paths": _unique_strings(repair_request.get("missing_scalar_paths", [])),
            "underfilled_paths": repair_request.get("underfilled_paths", []),
            "status": "sparse" if repair_artifact.sparse else "success",
            "output_dir": str(repair_artifact.output_dir.resolve()),
            "rule_count": len(_iter_final_rule_items(repair_artifact.final_result)),
            "reasoning_entry_count": len(repair_artifact.reasoning_bundle.entries),
        },
    ]
    reduce_trace = _build_reduce_trace(
        reasoning_bundle,
        grounding_ref_pool,
        final_result=final_result,
    )
    reduce_trace["assembler_conflicts"] = [row.model_dump(mode="json") for row in assembler_conflicts]
    reduce_trace["repair_passes"] = repair_passes
    merged_artifact = LocalReduceArtifact(
        bucket_id=base_artifact.bucket_id,
        memo_id=base_artifact.memo_id,
        batch_ids=list(base_artifact.batch_ids),
        output_dir=base_artifact.output_dir,
        final_result=final_result,
        partial_record=base_artifact.partial_record,
        reasoning_bundle=reasoning_bundle,
        reasoning_record=reasoning_bundle.model_dump(mode="json"),
        reduce_trace=reduce_trace,
        request_metrics=_merge_local_request_metrics(
            base_artifact.request_metrics,
            repair_artifact.request_metrics,
            repair_passes=repair_passes,
        ),
        usage_metadata=_merge_local_usage_metadata(
            base_artifact.usage_metadata,
            repair_artifact.usage_metadata,
            repair_passes=repair_passes,
        ),
        reduced_refs=reduced_refs,
        grounding_ref_pool=grounding_ref_pool,
        sparse=False,
        assembler_conflicts=assembler_conflicts,
        preflight_decision=dict(base_artifact.preflight_decision),
        repair_passes=repair_passes,
    )
    _rewrite_local_artifact_outputs(merged_artifact)
    return merged_artifact


def _run_section_repair_passes(
    config: StableProjectConfig,
    *,
    source_bundle: dict[str, Any],
    memo_payloads: list[StyleBibleBucketMemo],
    local_artifacts: list[LocalReduceArtifact],
    section_targets: StyleBibleSectionTargets,
) -> list[LocalReduceArtifact]:
    if not local_artifacts or section_targets.repair_max_rounds <= 0:
        return local_artifacts
    bucket_order = {
        clean_text(bucket_memo.bucket_id): index
        for index, bucket_memo in enumerate(memo_payloads)
    }
    bucket_memo_by_id = {
        clean_text(bucket_memo.bucket_id): bucket_memo
        for bucket_memo in memo_payloads
    }
    artifact_by_bucket_id = {
        clean_text(artifact.bucket_id): artifact
        for artifact in local_artifacts
    }
    critical_bucket_ids = sorted(_unique_strings(config.style_bible_reduce.critical_buckets))
    reduce_config = config.style_bible_reduce
    for round_index in range(int(section_targets.repair_max_rounds)):
        current_artifacts = list(artifact_by_bucket_id.values())
        merge_assembly = _assemble_global_merge(
            local_artifacts=current_artifacts,
            memo_payloads=memo_payloads,
            style_id_hint=clean_text(source_bundle.get("style_bible_id_hint")),
            scope_hint=clean_text(source_bundle.get("scope_hint")),
            critical_bucket_ids=critical_bucket_ids,
            supporting_evidence_soft_cap=int(reduce_config.supporting_evidence_soft_cap),
            supporting_evidence_hard_cap=int(reduce_config.supporting_evidence_hard_cap),
            section_minimums=section_targets.minimums,
        )
        gaps = _compute_section_gaps(
            merge_assembly.final_result,
            section_targets=section_targets,
        )
        if not gaps:
            break
        repair_requests = _select_repair_requests(
            local_artifacts=current_artifacts,
            section_targets=section_targets,
            gaps=gaps,
            bucket_order=bucket_order,
            source_bundle=source_bundle,
        )
        if not repair_requests:
            break
        repair_made_progress = False
        for base_artifact, repair_request in repair_requests:
            bucket_id = clean_text(base_artifact.bucket_id)
            bucket_memo = bucket_memo_by_id.get(bucket_id)
            if bucket_memo is None:
                continue
            repair_output_dir = ensure_dir(base_artifact.output_dir / "_repair_passes" / f"pass_{round_index + 1:02d}")
            try:
                repair_artifact = _run_local_reduce(
                    config,
                    source_bundle=source_bundle,
                    bucket_memo=bucket_memo,
                    output_dir=repair_output_dir,
                    section_targets=section_targets,
                    repair_request=repair_request,
                    request_key_suffix=f"__repair_{round_index + 1:02d}",
                )
            except Exception as exc:
                _write_failed_local_reduce_summary(repair_output_dir, bucket_id=bucket_id, exc=exc)
                continue
            artifact_by_bucket_id[bucket_id] = _merge_local_artifact_with_repair(
                artifact_by_bucket_id[bucket_id],
                repair_artifact,
                repair_request=repair_request,
            )
            if not repair_artifact.sparse and _iter_final_rule_items(repair_artifact.final_result):
                repair_made_progress = True
        if not repair_made_progress:
            break
    return list(artifact_by_bucket_id.values())


def _aggregate_local_reduce_metrics(
    local_artifacts: list[LocalReduceArtifact],
    *,
    failed_bucket_ids: list[str],
    skipped_sparse_bucket_ids: list[str],
    critical_bucket_ids: list[str],
    local_reduce_concurrency: int,
    assembler_conflicts: list[StyleBibleAssemblerConflict],
    semantic_reconcile_sections: list[str],
    supporting_evidence_final_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    successful_artifacts = [artifact for artifact in local_artifacts if not artifact.sparse]
    ttft_values = [artifact.usage_metadata.get("ttft_seconds", 0.0) for artifact in local_artifacts]
    ttft_summary = _summarize_ttft_seconds(ttft_values)
    prompt_tokens = sum(_usage_tokens(artifact.usage_metadata, "prompt_tokens", "input_tokens") for artifact in local_artifacts)
    output_tokens = sum(_usage_tokens(artifact.usage_metadata, "output_tokens", "completion_tokens") for artifact in local_artifacts)
    total_tokens = sum(_usage_tokens(artifact.usage_metadata, "total_tokens") for artifact in local_artifacts)
    cached_tokens = sum(_usage_tokens(artifact.usage_metadata, "cached_tokens") for artifact in local_artifacts)
    repair_pass_count = sum(len(artifact.repair_passes) for artifact in local_artifacts)
    repair_used_count = sum(1 for artifact in local_artifacts if artifact.repair_passes)
    request_metrics = {
        "stage": "style_bible_reduce",
        "reduce_mode": "hierarchical",
        "local_reduce_concurrency": int(local_reduce_concurrency),
        "local_reduce_success_count": len(successful_artifacts),
        "local_reduce_failure_count": len(failed_bucket_ids),
        "local_reduce_sparse_count": len(skipped_sparse_bucket_ids),
        "failed_bucket_ids": sorted(_unique_strings(failed_bucket_ids)),
        "skipped_sparse_bucket_ids": sorted(_unique_strings(skipped_sparse_bucket_ids)),
        "critical_bucket_ids": sorted(_unique_strings(critical_bucket_ids)),
        "degraded_success": bool(failed_bucket_ids or skipped_sparse_bucket_ids or assembler_conflicts),
        "assembler_conflict_count": len(assembler_conflicts),
        "repair_pass_count": int(repair_pass_count),
        "repair_used_bucket_count": int(repair_used_count),
        "semantic_reconcile_sections": sorted(_unique_strings(semantic_reconcile_sections)),
        "supporting_evidence_final_count": int(supporting_evidence_final_count),
        "total_elapsed_seconds": round(
            sum(float(artifact.request_metrics.get("total_elapsed_seconds", 0.0) or 0.0) for artifact in local_artifacts),
            3,
        ),
        "response_chars": sum(int(artifact.request_metrics.get("response_chars", 0) or 0) for artifact in local_artifacts),
        "per_bucket": [
            {
                "bucket_id": artifact.bucket_id,
                "memo_id": artifact.memo_id,
                "batch_ids": list(artifact.batch_ids),
                "reasoning_entry_count": len(artifact.reasoning_bundle.entries),
                "rule_count": len(_iter_final_rule_items(artifact.final_result)),
                "reduced_ref_count": len(artifact.reduced_refs),
                "grounding_ref_count": len(artifact.grounding_ref_pool),
                "ttft_seconds": float(artifact.usage_metadata.get("ttft_seconds", 0.0) or 0.0),
                "selected_antipattern_codes": artifact.request_metrics.get("selected_antipattern_codes", []),
                "assembler_conflict_count": len(artifact.assembler_conflicts),
                "sparse": bool(artifact.sparse),
                "preflight_skip": bool(artifact.preflight_decision),
                "repair_pass_count": len(artifact.repair_passes),
                "repair_used": bool(artifact.repair_passes),
            }
            for artifact in local_artifacts
        ],
        "ttft_summary": ttft_summary,
    }
    usage_metadata = {
        "stage": "style_bible_reduce",
        "reduce_mode": "hierarchical",
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        "ttft_seconds": float(ttft_summary.get("avg_seconds", 0.0) or 0.0),
        "ttft_summary": ttft_summary,
        "repair_pass_count": int(repair_pass_count),
        "local_reduces": [artifact.usage_metadata for artifact in local_artifacts],
    }
    return request_metrics, usage_metadata


def _apply_section_densify_metrics(
    request_metrics: dict[str, Any],
    usage_metadata: dict[str, Any],
    *,
    reports: list[dict[str, Any]],
) -> None:
    valid_reports = [report for report in reports if isinstance(report, dict)]
    success_reports = [report for report in valid_reports if clean_text(report.get("status")) == "success"]
    request_metrics["section_densify_attempt_count"] = len(valid_reports)
    request_metrics["section_densify_success_count"] = len(success_reports)
    request_metrics["section_densify_paths"] = sorted(
        _unique_strings(
            report.get("target_path")
            for report in success_reports
            if clean_text(report.get("target_path"))
        )
    )
    request_metrics["section_densify"] = valid_reports
    request_metrics["total_elapsed_seconds"] = round(
        float(request_metrics.get("total_elapsed_seconds", 0.0) or 0.0)
        + sum(
            float(((report.get("request_metrics") or {}).get("total_elapsed_seconds", 0.0) or 0.0))
            for report in valid_reports
        ),
        3,
    )
    request_metrics["response_chars"] = int(request_metrics.get("response_chars", 0) or 0) + sum(
        int(((report.get("request_metrics") or {}).get("response_chars", 0) or 0))
        for report in valid_reports
    )

    prompt_tokens = int(usage_metadata.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage_metadata.get("output_tokens", 0) or 0)
    total_tokens = int(usage_metadata.get("total_tokens", 0) or 0)
    cached_tokens = int(usage_metadata.get("cached_tokens", 0) or 0)
    prompt_tokens += sum(_usage_tokens((report.get("usage_metadata") or {}), "prompt_tokens", "input_tokens") for report in valid_reports)
    output_tokens += sum(_usage_tokens((report.get("usage_metadata") or {}), "output_tokens", "completion_tokens") for report in valid_reports)
    total_tokens += sum(_usage_tokens((report.get("usage_metadata") or {}), "total_tokens") for report in valid_reports)
    cached_tokens += sum(_usage_tokens((report.get("usage_metadata") or {}), "cached_tokens") for report in valid_reports)
    usage_metadata["prompt_tokens"] = prompt_tokens
    usage_metadata["input_tokens"] = prompt_tokens
    usage_metadata["output_tokens"] = output_tokens
    usage_metadata["total_tokens"] = total_tokens
    usage_metadata["cached_tokens"] = cached_tokens
    usage_metadata["overall_cache_hit_ratio"] = round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0
    usage_metadata["section_densify_attempt_count"] = len(valid_reports)
    usage_metadata["section_densify_success_count"] = len(success_reports)
    usage_metadata["section_densify"] = [report.get("usage_metadata", {}) for report in valid_reports]


def _prepare_section_densify_output_dir(output_path: Path) -> Path:
    section_output_dir = ensure_dir(output_path / SECTION_DENSIFY_DIR)
    output_root = output_path.resolve()
    for child in list(section_output_dir.iterdir()):
        if child.name == "_request_cache":
            continue
        try:
            child.resolve().relative_to(output_root)
        except ValueError as exc:
            raise ValueError(f"Refusing to clear densify artifact outside output root: {child}") from exc
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return ensure_dir(section_output_dir)


def _write_semantic_dedupe_drop_pair_aggregate(output_path: Path) -> dict[str, Any]:
    section_root = output_path / SECTION_DENSIFY_DIR
    pair_files = sorted(section_root.rglob("semantic_dedupe_drop_pairs.json")) if section_root.exists() else []
    drop_reasons: dict[str, int] = {}
    drop_pairs_by_file: dict[str, list[dict[str, Any]]] = {}
    pairs: list[dict[str, Any]] = []
    for path in pair_files:
        payload = read_json(path)
        if not isinstance(payload, list):
            continue
        relative_path = str(path.relative_to(output_path))
        normalized_pairs: list[dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            normalized = dict(row)
            normalized["source_file"] = relative_path
            normalized_pairs.append(normalized)
            pairs.append(normalized)
            drop_reason = clean_text(
                row.get("drop_reason")
                or row.get("reason")
                or row.get("status")
            )
            if drop_reason:
                drop_reasons[drop_reason] = int(drop_reasons.get(drop_reason, 0) or 0) + 1
        drop_pairs_by_file[relative_path] = normalized_pairs
    aggregate_record = {
        "run_root": str(output_path.resolve()),
        "search_root": str(section_root.resolve()),
        "pair_file_count": len(pair_files),
        "drop_pair_count": len(pairs),
        "drop_reasons": drop_reasons,
        "drop_pairs_by_file": drop_pairs_by_file,
        "pairs": pairs,
    }
    write_json(output_path / SEMANTIC_DEDUPE_AGGREGATE_FILE, aggregate_record)
    return aggregate_record


def _build_reduce_guardrail_summary(
    final_result: StyleBibleResultV2,
    *,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
) -> dict[str, Any]:
    list_rule_counts: dict[str, int] = {}
    optional_rule_counts: dict[str, int] = {}
    total_rule_count = 0

    for path in LIST_RULE_PATHS:
        value = _value_at_rule_path(final_result, path)
        count = len(value) if isinstance(value, list) else 0
        list_rule_counts[path] = count
        total_rule_count += count

    for path in OPTIONAL_RULE_PATHS:
        value = _value_at_rule_path(final_result, path)
        count = 1 if value is not None else 0
        optional_rule_counts[path] = count
        total_rule_count += count

    reduced_refs = {
        ref
        for rule in _iter_final_rule_items(final_result)
        for ref in rule.evidence_refs
        if clean_text(ref)
    }
    return {
        "reasoning_entry_count": len(reasoning_bundle.entries),
        "supporting_evidence_count": len(final_result.supporting_evidence),
        "total_rule_count": total_rule_count,
        "reduced_ref_count": len(reduced_refs),
        "memo_ref_pool_count": len(memo_ref_pool),
        "list_rule_counts": list_rule_counts,
        "optional_rule_counts": optional_rule_counts,
    }


def _assert_reduce_output_not_empty(
    final_result: StyleBibleResultV2,
    *,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
) -> None:
    summary = _build_reduce_guardrail_summary(
        final_result,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=memo_ref_pool,
    )
    if (
        summary["reasoning_entry_count"] > 0
        and summary["total_rule_count"] > 0
        and summary["supporting_evidence_count"] > 0
        and summary["reduced_ref_count"] > 0
    ):
        return
    raise StyleBibleReduceGuardrailError(
        "Reducer guardrail intercepted an empty or ungrounded final result; "
        f"refusing to write style_bible_final.json. "
        f"reasoning_entry_count={summary['reasoning_entry_count']}, "
        f"total_rule_count={summary['total_rule_count']}, "
        f"supporting_evidence_count={summary['supporting_evidence_count']}, "
        f"reduced_ref_count={summary['reduced_ref_count']}, "
        f"memo_ref_pool_count={summary['memo_ref_pool_count']}."
    )


def _complete_hierarchical_reduce_from_local_artifacts(
    config: StableProjectConfig,
    *,
    source_bundle: dict[str, Any],
    memo_payloads: list[StyleBibleBucketMemo],
    output_path: Path,
    observed_local_artifacts: list[LocalReduceArtifact],
    local_artifacts: list[LocalReduceArtifact],
    failed_bucket_ids: list[str],
    skipped_sparse_bucket_ids: list[str],
    section_targets: StyleBibleSectionTargets,
) -> StyleBibleReduceResult:
    reduce_config = config.style_bible_reduce
    grounding_ref_pool = set(_grounding_ref_pool(memo_payloads))
    style_id_hint = clean_text(source_bundle.get("style_bible_id_hint"))
    scope_hint = clean_text(source_bundle.get("scope_hint"))
    critical_bucket_ids = sorted(_unique_strings(reduce_config.critical_buckets))
    pre_densify_merge = _assemble_global_merge(
        local_artifacts=local_artifacts,
        memo_payloads=memo_payloads,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        critical_bucket_ids=critical_bucket_ids,
        supporting_evidence_soft_cap=int(reduce_config.supporting_evidence_soft_cap),
        supporting_evidence_hard_cap=int(reduce_config.supporting_evidence_hard_cap),
        section_minimums=section_targets.minimums,
    )
    section_densify_artifacts, section_densify_reports = _run_section_densify_passes(
        config,
        source_bundle=source_bundle,
        memo_payloads=memo_payloads,
        local_artifacts=local_artifacts,
        merge_assembly=pre_densify_merge,
        section_targets=section_targets,
        output_dir=_prepare_section_densify_output_dir(output_path),
    )
    semantic_reconcile_sections = sorted(
        _unique_strings(
            report.get("target_path")
            for report in section_densify_reports
            if clean_text(report.get("status")) == "success" and clean_text(report.get("target_path"))
        )
    )
    merge_assembly = _assemble_global_merge(
        local_artifacts=[*local_artifacts, *section_densify_artifacts],
        memo_payloads=memo_payloads,
        style_id_hint=style_id_hint,
        scope_hint=scope_hint,
        critical_bucket_ids=critical_bucket_ids,
        supporting_evidence_soft_cap=int(reduce_config.supporting_evidence_soft_cap),
        supporting_evidence_hard_cap=int(reduce_config.supporting_evidence_hard_cap),
        section_minimums=section_targets.minimums,
    )
    final_result = merge_assembly.final_result
    reasoning_bundle = merge_assembly.reasoning_bundle
    assembler_conflicts = merge_assembly.assembler_conflicts
    rule_lineage_records = merge_assembly.rule_lineage_records
    merge_events = merge_assembly.merge_events
    _assert_reduce_output_not_empty(
        final_result,
        reasoning_bundle=reasoning_bundle,
        memo_ref_pool=grounding_ref_pool,
    )
    final_result.metadata.degradation_status.mode = (
        "degraded"
        if failed_bucket_ids or skipped_sparse_bucket_ids or assembler_conflicts
        else "complete"
    )
    final_result.metadata.degradation_status.skipped_sparse_buckets = sorted(
        _unique_strings(skipped_sparse_bucket_ids)
    )
    final_result.metadata.degradation_status.failed_bucket_ids = sorted(_unique_strings(failed_bucket_ids))
    final_result.metadata.degradation_status.assembler_conflicts = [
        row.model_copy(deep=True)
        for row in assembler_conflicts
    ]

    reasoning_record = reasoning_bundle.model_dump(mode="json")
    record = final_result.model_dump(mode="json", by_alias=True)
    export_flat_record = _build_export_flat(record)
    reduce_trace = _build_global_reduce_trace(
        reasoning_bundle,
        grounding_ref_pool,
        final_result=final_result,
        local_artifacts=observed_local_artifacts,
        failed_bucket_ids=failed_bucket_ids,
        skipped_sparse_bucket_ids=skipped_sparse_bucket_ids,
        critical_bucket_ids=critical_bucket_ids,
        degraded_success=bool(failed_bucket_ids or skipped_sparse_bucket_ids or assembler_conflicts),
        assembler_conflicts=assembler_conflicts,
        semantic_reconcile_sections=semantic_reconcile_sections,
        rule_lineage_map=rule_lineage_records,
        merge_events=merge_events,
    )
    reduce_trace["section_densify"] = section_densify_reports
    semantic_dedupe_aggregate = _write_semantic_dedupe_drop_pair_aggregate(output_path)
    reduce_trace["semantic_dedupe_drop_pairs_aggregate"] = {
        "pair_file_count": int(semantic_dedupe_aggregate.get("pair_file_count", 0) or 0),
        "drop_pair_count": int(semantic_dedupe_aggregate.get("drop_pair_count", 0) or 0),
        "drop_reasons": dict(semantic_dedupe_aggregate.get("drop_reasons", {})),
        "aggregate_path": str((output_path / SEMANTIC_DEDUPE_AGGREGATE_FILE).resolve()),
    }

    output_file = output_path / "style_bible_final.json"
    reasoning_path = output_path / REASONING_FILE
    export_flat_path = output_path / EXPORT_FLAT_FILE
    reduce_trace_path = output_path / REDUCE_TRACE_FILE
    source_bundle_path = output_path / "style_bible_source_bundle.json"
    bucket_memo_dir = ensure_dir(output_path / "bucket_memos")
    write_json(source_bundle_path, source_bundle)
    for memo in memo_payloads:
        write_json(bucket_memo_dir / f"{clean_text(memo.bucket_id)}.json", memo.model_dump(mode="json"))
    write_json(output_file, record)
    write_json(reasoning_path, reasoning_record)
    write_json(export_flat_path, export_flat_record)
    write_json(reduce_trace_path, reduce_trace)

    request_metrics, usage_metadata = _aggregate_local_reduce_metrics(
        observed_local_artifacts,
        failed_bucket_ids=failed_bucket_ids,
        skipped_sparse_bucket_ids=skipped_sparse_bucket_ids,
        critical_bucket_ids=critical_bucket_ids,
        local_reduce_concurrency=1,
        assembler_conflicts=assembler_conflicts,
        semantic_reconcile_sections=semantic_reconcile_sections,
        supporting_evidence_final_count=len(final_result.supporting_evidence),
    )
    _apply_section_densify_metrics(
        request_metrics,
        usage_metadata,
        reports=section_densify_reports,
    )
    reduced_refs = _collect_reduced_refs(
        final_result,
        reasoning_bundle=reasoning_bundle,
    )
    return StyleBibleReduceResult(
        output_path=output_file,
        reasoning_path=reasoning_path,
        export_flat_path=export_flat_path,
        reduce_trace_path=reduce_trace_path,
        record=record,
        reasoning_record=reasoning_record,
        export_flat_record=export_flat_record,
        reduce_trace=reduce_trace,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        reduced_item_ids=set(),
        reduced_chapter_ids=set(),
        reduced_refs=reduced_refs,
        reduce_mode="hierarchical",
        prompt_name="style_bible_local_reduce.md",
        local_artifact_root=ensure_dir(output_path / LOCAL_REDUCE_DIR),
        failed_bucket_ids=sorted(_unique_strings(failed_bucket_ids)),
        skipped_sparse_bucket_ids=sorted(_unique_strings(skipped_sparse_bucket_ids)),
        critical_bucket_ids=critical_bucket_ids,
        degraded_success=bool(failed_bucket_ids or skipped_sparse_bucket_ids or assembler_conflicts),
        assembler_conflicts=[row.model_dump(mode="json") for row in assembler_conflicts],
        semantic_reconcile_sections=semantic_reconcile_sections,
    )


def _resume_style_bible_hierarchical_from_bucket_memos(
    config: StableProjectConfig,
    source_bundle: dict[str, Any],
    bucket_memos: Iterable[StyleBibleBucketMemo | dict[str, Any]],
    output_dir: str | Path,
) -> StyleBibleReduceResult:
    output_path = ensure_dir(output_dir)
    memo_payloads = sorted(_load_bucket_memo_payloads(bucket_memos), key=lambda memo: clean_text(memo.bucket_id))
    if not memo_payloads:
        raise ValueError("No bucket memos were provided for hierarchical reduce resume.")

    section_targets = load_style_bible_section_targets()
    local_artifact_root = ensure_dir(output_path / LOCAL_REDUCE_DIR)
    observed_local_artifacts, local_artifacts, failed_bucket_ids, skipped_sparse_bucket_ids = _load_resumable_local_reduce_artifacts(
        memo_payloads=memo_payloads,
        source_bundle=source_bundle,
        local_artifact_root=local_artifact_root,
        section_targets=section_targets,
    )
    reduce_config = config.style_bible_reduce
    critical_bucket_ids = sorted(_unique_strings(reduce_config.critical_buckets))
    critical_bucket_set = set(critical_bucket_ids)
    unresolved_bucket_ids = set(failed_bucket_ids) | set(skipped_sparse_bucket_ids)
    if unresolved_bucket_ids:
        observed_by_bucket_id = {
            clean_text(artifact.bucket_id): artifact
            for artifact in observed_local_artifacts
            if clean_text(artifact.bucket_id)
        }
        local_by_bucket_id = {
            clean_text(artifact.bucket_id): artifact
            for artifact in local_artifacts
            if clean_text(artifact.bucket_id) and not artifact.sparse
        }
        rerun_failed_bucket_ids: list[str] = []
        rerun_sparse_bucket_ids: list[str] = []
        grounding_ref_pool = set(_grounding_ref_pool(memo_payloads))
        style_id_hint = clean_text(source_bundle.get("style_bible_id_hint"))
        scope_hint = clean_text(source_bundle.get("scope_hint"))

        for bucket_memo in memo_payloads:
            bucket_id = clean_text(bucket_memo.bucket_id)
            if bucket_id not in unresolved_bucket_ids:
                continue
            bucket_output_dir = ensure_dir(local_artifact_root / bucket_id)
            metrics_path = bucket_output_dir / "request_metrics.jsonl"
            cache_dir = bucket_output_dir / "_request_cache"
            if metrics_path.exists():
                metrics_path.unlink()
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            preflight_decision = _evaluate_local_reduce_preflight(bucket_memo)
            if preflight_decision.skip:
                artifact = _build_sparse_local_reduce_artifact(
                    bucket_memo=bucket_memo,
                    output_dir=bucket_output_dir,
                    style_id_hint=style_id_hint,
                    scope_hint=scope_hint,
                    grounding_ref_pool=set(),
                    reasoning_bundle=StyleBibleReasoningBundle(
                        reasoning_version="style-bible-reasoning-v2",
                        style_id=style_id_hint,
                        scope=scope_hint,
                        entries=[],
                    ),
                    partial_record=_empty_local_partial_record(
                        style_id_hint=style_id_hint,
                        scope_hint=scope_hint,
                    ),
                    request_metrics={
                        "stage": "style_bible_local_reduce",
                        "total_elapsed_seconds": 0.0,
                        "response_chars": 0,
                        "selected_antipattern_codes": [],
                        "preflight_skip": True,
                    },
                    usage_metadata={
                        "stage": "style_bible_local_reduce",
                        "cached_tokens": 0,
                        "prompt_tokens": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "overall_cache_hit_ratio": 0.0,
                        "ttft_seconds": 0.0,
                        "raw_usage_metadata": {},
                        "preflight_skip": True,
                    },
                    preflight_decision=preflight_decision,
                )
            else:
                try:
                    artifact = _run_local_reduce(
                        config,
                        source_bundle=source_bundle,
                        bucket_memo=bucket_memo,
                        output_dir=bucket_output_dir,
                        section_targets=section_targets,
                        request_key_suffix="__resume_rerun",
                    )
                except Exception as exc:
                    _write_failed_local_reduce_summary(bucket_output_dir, bucket_id=bucket_id, exc=exc)
                    observed_by_bucket_id.pop(bucket_id, None)
                    local_by_bucket_id.pop(bucket_id, None)
                    rerun_failed_bucket_ids.append(bucket_id)
                    continue
            observed_by_bucket_id[bucket_id] = artifact
            local_by_bucket_id.pop(bucket_id, None)
            if artifact.sparse:
                rerun_sparse_bucket_ids.append(bucket_id)
                continue
            local_by_bucket_id[bucket_id] = artifact

        failed_bucket_ids = sorted(
            _unique_strings(
                [
                    *(bucket_id for bucket_id in failed_bucket_ids if bucket_id not in unresolved_bucket_ids),
                    *rerun_failed_bucket_ids,
                ]
            )
        )
        skipped_sparse_bucket_ids = sorted(
            _unique_strings(
                [
                    *(bucket_id for bucket_id in skipped_sparse_bucket_ids if bucket_id not in unresolved_bucket_ids),
                    *rerun_sparse_bucket_ids,
                ]
            )
        )
        observed_local_artifacts = []
        local_artifacts = []
        for bucket_memo in memo_payloads:
            bucket_id = clean_text(bucket_memo.bucket_id)
            observed_artifact = observed_by_bucket_id.get(bucket_id)
            if observed_artifact is not None:
                observed_local_artifacts.append(observed_artifact)
            local_artifact = local_by_bucket_id.get(bucket_id)
            if local_artifact is not None:
                local_artifacts.append(local_artifact)

    if not local_artifacts:
        raise StyleBibleReduceGuardrailError("Hierarchical reducer resume found no successful local reduce artifacts.")

    for bucket_id in failed_bucket_ids:
        if bucket_id in critical_bucket_set:
            raise CriticalBucketReduceError(f"Critical bucket local reduce is missing or failed during resume: {bucket_id}")
    for bucket_id in skipped_sparse_bucket_ids:
        if bucket_id in critical_bucket_set:
            raise CriticalBucketReduceError(f"Critical bucket local reduce is sparse during resume: {bucket_id}")
    failed_ratio = len(failed_bucket_ids) / max(len(memo_payloads), 1)
    if len(failed_bucket_ids) > int(reduce_config.max_failed_bucket_count) or failed_ratio > float(
        reduce_config.max_failed_bucket_ratio
    ):
        raise StyleBibleReduceGuardrailError(
            "Hierarchical reducer resume aborted after resumed local bucket failures exceeded the configured threshold. "
            f"failed_bucket_ids={sorted(_unique_strings(failed_bucket_ids))}, "
            f"max_failed_bucket_count={int(reduce_config.max_failed_bucket_count)}, "
            f"max_failed_bucket_ratio={float(reduce_config.max_failed_bucket_ratio):.3f}."
        )
    if unresolved_bucket_ids:
        local_artifacts = _run_section_repair_passes(
            config,
            source_bundle=source_bundle,
            memo_payloads=memo_payloads,
            local_artifacts=local_artifacts,
            section_targets=section_targets,
        )
        artifact_by_bucket_id = {
            clean_text(artifact.bucket_id): artifact
            for artifact in local_artifacts
        }
        observed_local_artifacts = [
            artifact if artifact.sparse else artifact_by_bucket_id.get(clean_text(artifact.bucket_id), artifact)
            for artifact in observed_local_artifacts
        ]
    return _complete_hierarchical_reduce_from_local_artifacts(
        config,
        source_bundle=source_bundle,
        memo_payloads=memo_payloads,
        output_path=output_path,
        observed_local_artifacts=observed_local_artifacts,
        local_artifacts=local_artifacts,
        failed_bucket_ids=failed_bucket_ids,
        skipped_sparse_bucket_ids=skipped_sparse_bucket_ids,
        section_targets=section_targets,
    )


def _reduce_style_bible_hierarchical_from_bucket_memos(
    config: StableProjectConfig,
    source_bundle: dict[str, Any],
    bucket_memos: Iterable[StyleBibleBucketMemo | dict[str, Any]],
    output_dir: str | Path,
) -> StyleBibleReduceResult:
    output_path = ensure_dir(output_dir)
    memo_payloads = sorted(_load_bucket_memo_payloads(bucket_memos), key=lambda memo: clean_text(memo.bucket_id))
    if not memo_payloads:
        raise ValueError("No bucket memos were provided for hierarchical reduce.")

    reduce_config = config.style_bible_reduce
    section_targets = load_style_bible_section_targets()
    critical_bucket_ids = sorted(_unique_strings(reduce_config.critical_buckets))
    critical_bucket_set = set(critical_bucket_ids)
    local_artifact_root = ensure_dir(output_path / LOCAL_REDUCE_DIR)
    observed_local_artifacts: list[LocalReduceArtifact] = []
    local_artifacts: list[LocalReduceArtifact] = []
    failed_bucket_ids: list[str] = []
    skipped_sparse_bucket_ids: list[str] = []
    grounding_ref_pool = set(_grounding_ref_pool(memo_payloads))
    style_id_hint = clean_text(source_bundle.get("style_bible_id_hint"))
    scope_hint = clean_text(source_bundle.get("scope_hint"))

    for bucket_memo in memo_payloads:
        bucket_id = clean_text(bucket_memo.bucket_id)
        bucket_output_dir = ensure_dir(local_artifact_root / bucket_id)
        preflight_decision = _evaluate_local_reduce_preflight(bucket_memo)
        if preflight_decision.skip:
            artifact = _build_sparse_local_reduce_artifact(
                bucket_memo=bucket_memo,
                output_dir=bucket_output_dir,
                style_id_hint=style_id_hint,
                scope_hint=scope_hint,
                grounding_ref_pool=set(),
                reasoning_bundle=StyleBibleReasoningBundle(
                    reasoning_version="style-bible-reasoning-v2",
                    style_id=style_id_hint,
                    scope=scope_hint,
                    entries=[],
                ),
                partial_record=_empty_local_partial_record(
                    style_id_hint=style_id_hint,
                    scope_hint=scope_hint,
                ),
                request_metrics={
                    "stage": "style_bible_local_reduce",
                    "total_elapsed_seconds": 0.0,
                    "response_chars": 0,
                    "selected_antipattern_codes": [],
                    "preflight_skip": True,
                },
                usage_metadata={
                    "stage": "style_bible_local_reduce",
                    "cached_tokens": 0,
                    "prompt_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "overall_cache_hit_ratio": 0.0,
                    "ttft_seconds": 0.0,
                    "raw_usage_metadata": {},
                    "preflight_skip": True,
                },
                preflight_decision=preflight_decision,
            )
        else:
            try:
                artifact = _run_local_reduce(
                    config,
                    source_bundle=source_bundle,
                    bucket_memo=bucket_memo,
                    output_dir=bucket_output_dir,
                    section_targets=section_targets,
                )
            except Exception as exc:
                _write_failed_local_reduce_summary(bucket_output_dir, bucket_id=bucket_id, exc=exc)
                if bucket_id in critical_bucket_set:
                    raise CriticalBucketReduceError(f"Critical bucket local reduce failed: {bucket_id}") from exc
                failed_bucket_ids.append(bucket_id)
                failed_ratio = len(failed_bucket_ids) / max(len(memo_payloads), 1)
                if len(failed_bucket_ids) > int(reduce_config.max_failed_bucket_count) or failed_ratio > float(
                    reduce_config.max_failed_bucket_ratio
                ):
                    raise StyleBibleReduceGuardrailError(
                        "Hierarchical reducer aborted after non-critical bucket failures exceeded the configured threshold. "
                        f"failed_bucket_ids={sorted(_unique_strings(failed_bucket_ids))}, "
                        f"max_failed_bucket_count={int(reduce_config.max_failed_bucket_count)}, "
                        f"max_failed_bucket_ratio={float(reduce_config.max_failed_bucket_ratio):.3f}."
                    ) from exc
                continue
        observed_local_artifacts.append(artifact)
        if artifact.sparse:
            if bucket_id in critical_bucket_set:
                raise CriticalBucketReduceError(f"Critical bucket local reduce produced a sparse result: {bucket_id}")
            skipped_sparse_bucket_ids.append(bucket_id)
            continue
        local_artifacts.append(artifact)

    if not local_artifacts:
        raise StyleBibleReduceGuardrailError("Hierarchical reducer produced no successful local reduce artifacts.")

    local_artifacts = _run_section_repair_passes(
        config,
        source_bundle=source_bundle,
        memo_payloads=memo_payloads,
        local_artifacts=local_artifacts,
        section_targets=section_targets,
    )
    artifact_by_bucket_id = {
        clean_text(artifact.bucket_id): artifact
        for artifact in local_artifacts
    }
    observed_local_artifacts = [
        artifact if artifact.sparse else artifact_by_bucket_id.get(clean_text(artifact.bucket_id), artifact)
        for artifact in observed_local_artifacts
    ]
    return _complete_hierarchical_reduce_from_local_artifacts(
        config,
        source_bundle=source_bundle,
        memo_payloads=memo_payloads,
        output_path=output_path,
        observed_local_artifacts=observed_local_artifacts,
        local_artifacts=local_artifacts,
        failed_bucket_ids=failed_bucket_ids,
        skipped_sparse_bucket_ids=skipped_sparse_bucket_ids,
        section_targets=section_targets,
    )


def reduce_style_bible_from_bucket_memos(
    config: StableProjectConfig,
    source_bundle: dict[str, Any],
    bucket_memos: Iterable[StyleBibleBucketMemo | dict[str, Any]],
    output_dir: str | Path,
    *,
    resume_local_reduce: bool = False,
) -> StyleBibleReduceResult:
    reduce_mode = clean_text(config.style_bible_reduce.mode) or "hierarchical"
    if reduce_mode != "hierarchical":
        raise ValueError(
            "style_bible_reduce.mode must be `hierarchical`; "
            "style bible v2 only supports local partial reduce + Python assembler merge."
        )
    if resume_local_reduce:
        return _resume_style_bible_hierarchical_from_bucket_memos(
            config,
            source_bundle,
            bucket_memos,
            output_dir,
        )
    return _reduce_style_bible_hierarchical_from_bucket_memos(
        config,
        source_bundle,
        bucket_memos,
        output_dir,
    )
