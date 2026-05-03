from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient, StructuredGenerationError
from novel_pipeline_stable.config import StableProjectConfig
from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json
from novel_pipeline_stable.models import (
    StyleBibleBatchPlan,
    StyleBibleResultV2,
    StyleBibleRoutedIndex,
)
from novel_pipeline_stable.monitoring import RunTracker
from novel_pipeline_stable.prompting import load_prompt
from novel_pipeline_stable.style_bible_bucket_builder import build_style_bible_bucket_memos
from novel_pipeline_stable.style_bible_batching import plan_style_bible_batches_with_debug
from novel_pipeline_stable.style_bible_contracts import (
    BATCHING_MODE_BUCKET_AFFINITY_V3,
    BATCH_PLAN_FILE,
    BUCKET_MEMO_DIR,
    COVERAGE_REPORT_FILE,
    PLANNER_DEBUG_REPORT_FILE,
    REDUCE_TRACE_FILE,
    ROUTED_INDEX_FILE,
    ROUTING_MODE_SIGNAL_FUSION_V2,
    SAMPLING_REPORT_FILE,
    infer_sampling_mode,
)
from novel_pipeline_stable.style_bible_inputs import (
    StyleBibleInputBundle,
    build_scope_hint as shared_build_scope_hint,
    load_style_bible_inputs,
)
from novel_pipeline_stable.style_bible_surface_specs import (
    canonicalize_scalar_value,
    scalar_enum_spec_for_path,
    scalar_value_lookup_rows,
)
from novel_pipeline_stable.style_bible_reduction import reduce_style_bible_from_bucket_memos
from novel_pipeline_stable.style_bible_router import build_style_bible_sampling_report, route_style_bible_inputs
from novel_pipeline_stable.style_bible_runtime_flags import load_style_bible_runtime_flags
from novel_pipeline_stable.style_eval_contract import (
    RUN_MANIFEST_FILE,
    build_style_bible_run_manifest,
    build_style_id,
    file_sha256,
    sha256_payload,
    utc_iso_from_timestamp,
    utc_now_iso,
)

RELATIONSHIP_PATTERN_LIMIT = 12
SIGNAL_SUMMARY_LIMIT = 12


@dataclass(slots=True)
class StyleBibleBuildResult:
    output_path: Path
    reasoning_path: Path | None
    export_flat_path: Path | None
    source_bundle_path: Path
    routed_index_path: Path
    batch_plan_path: Path
    sampling_report_path: Path
    bucket_memo_dir_path: Path | None
    reduce_trace_path: Path | None
    record: dict[str, Any]
    request_metrics: dict[str, Any]
    usage_metadata: dict[str, Any]
    source_bundle: dict[str, Any]
    routed_index: dict[str, Any]
    batch_plan: dict[str, Any]
    sampling_report: dict[str, Any]
    reduce_trace: dict[str, Any] | None = None
    sampling_mode: str = ""
    routing_mode: str = ""
    batching_mode: str = ""
    story_node_scope: dict[str, Any] | None = None
    run_manifest_path: Path | None = None
    run_manifest: dict[str, Any] | None = None


@dataclass(slots=True)
class StyleBiblePhaseArtifacts:
    scope_hint: str
    story_node_scope: dict[str, Any] | None
    source_bundle: dict[str, Any]
    source_bundle_path: Path
    routed_index: dict[str, Any]
    routed_index_path: Path
    batch_plan: dict[str, Any]
    batch_plan_path: Path
    sampling_report: dict[str, Any]
    sampling_report_path: Path
    sampling_mode: str
    routing_mode: str
    batching_mode: str


@dataclass(slots=True)
class StyleBibleResumeValidation:
    valid: bool
    reason: str = ""
    run_manifest: dict[str, Any] | None = None
    legacy_without_fingerprint: bool = False


def _style_bible_config_fingerprint_payload(
    config: StableProjectConfig,
    *,
    max_style_windows: int,
    max_scene_samples: int,
    max_plot_nodes: int,
    max_chapter_summaries: int,
    max_entity_samples: int,
    routing_rules_config: str | Path | None,
    batching_rules_config: str | Path | None,
    bucket_build_concurrency: int | None,
) -> dict[str, Any]:
    routing_path = Path(routing_rules_config).resolve() if routing_rules_config else None
    batching_path = Path(batching_rules_config).resolve() if batching_rules_config else None
    return {
        "api_route": config.model.api_route,
        "model": _style_bible_model_name(config),
        "temperature": config.model.style_bible_temperature
        if config.model.style_bible_temperature is not None
        else config.model.style_temperature,
        "max_output_tokens": config.model.style_bible_max_output_tokens
        if config.model.style_bible_max_output_tokens is not None
        else config.model.style_max_output_tokens,
        "response_format": config.model.response_format,
        "max_style_windows": int(max_style_windows),
        "max_scene_samples": int(max_scene_samples),
        "max_plot_nodes": int(max_plot_nodes),
        "max_chapter_summaries": int(max_chapter_summaries),
        "max_entity_samples": int(max_entity_samples),
        "routing_rules_config": str(routing_path) if routing_path else "",
        "routing_rules_sha256": file_sha256(routing_path) if routing_path else "",
        "batching_rules_config": str(batching_path) if batching_path else "",
        "batching_rules_sha256": file_sha256(batching_path) if batching_path else "",
        "bucket_build_concurrency": int(bucket_build_concurrency or 0),
    }


def _style_bible_artifact_fingerprint(
    config: StableProjectConfig,
    *,
    source_bundle: dict[str, Any],
    max_style_windows: int,
    max_scene_samples: int,
    max_plot_nodes: int,
    max_chapter_summaries: int,
    max_entity_samples: int,
    routing_rules_config: str | Path | None,
    batching_rules_config: str | Path | None,
    bucket_build_concurrency: int | None,
) -> dict[str, Any]:
    payload = {
        "version": "artifact-fingerprint-v1",
        "kind": "style_bible",
        "input_sha256": sha256_payload(source_bundle),
        "config_sha256": sha256_payload(
            _style_bible_config_fingerprint_payload(
                config,
                max_style_windows=max_style_windows,
                max_scene_samples=max_scene_samples,
                max_plot_nodes=max_plot_nodes,
                max_chapter_summaries=max_chapter_summaries,
                max_entity_samples=max_entity_samples,
                routing_rules_config=routing_rules_config,
                batching_rules_config=batching_rules_config,
                bucket_build_concurrency=bucket_build_concurrency,
            )
        ),
    }
    payload["sha256"] = sha256_payload(payload)
    return payload


def _selection_limits_payload(
    *,
    max_style_windows: int,
    max_scene_samples: int,
    max_plot_nodes: int,
    max_chapter_summaries: int,
    max_entity_samples: int,
) -> dict[str, int]:
    return {
        "max_style_windows": int(max_style_windows),
        "max_scene_samples": int(max_scene_samples),
        "max_plot_nodes": int(max_plot_nodes),
        "max_chapter_summaries": int(max_chapter_summaries),
        "max_entity_samples": int(max_entity_samples),
    }


def _resolve_selection_limit(limit: int, available: int) -> int:
    resolved_available = max(int(available or 0), 0)
    if resolved_available <= 0:
        return 0
    requested_limit = int(limit or 0)
    if requested_limit <= 0:
        return resolved_available
    return min(requested_limit, resolved_available)


def _sampling_count(source_bundle: dict[str, Any], sample_key: str, field: str = "selected") -> int:
    sampling = source_bundle.get("sampling", {})
    if not isinstance(sampling, dict):
        return 0
    sample_row = sampling.get(sample_key, {})
    if not isinstance(sample_row, dict):
        return 0
    value = sample_row.get(field)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _selection_limits_from_source_bundle(source_bundle: dict[str, Any]) -> dict[str, int]:
    return _selection_limits_payload(
        max_style_windows=_sampling_count(source_bundle, "style_window_samples"),
        max_scene_samples=_sampling_count(source_bundle, "scene_signal_samples"),
        max_plot_nodes=_sampling_count(source_bundle, "plot_node_samples"),
        max_chapter_summaries=_sampling_count(source_bundle, "chapter_summaries"),
        max_entity_samples=_sampling_count(source_bundle, "entity_samples"),
    )


def _selected_refs_from_source_bundle(source_bundle: dict[str, Any]) -> tuple[set[str], set[str]]:
    selected_item_ids: set[str] = set()
    selected_chapter_ids: set[str] = set()

    for row in source_bundle.get("scene_signal_samples", []):
        if not isinstance(row, dict):
            continue
        scene_id = _clean_text(row.get("scene_id"))
        if scene_id:
            selected_item_ids.add(f"scene:{scene_id}")

    for row in source_bundle.get("style_window_samples", []):
        if not isinstance(row, dict):
            continue
        window_id = _clean_text(row.get("window_id"))
        if window_id:
            selected_item_ids.add(window_id)

    for row in source_bundle.get("chapter_summaries", []):
        if not isinstance(row, dict):
            continue
        chapter_id = _clean_text(row.get("chapter_id"))
        if chapter_id:
            selected_chapter_ids.add(chapter_id)

    return selected_item_ids, selected_chapter_ids


def _selected_values_from_source_bundle(source_bundle: dict[str, Any], sample_key: str, field: str) -> set[str]:
    selected_values: set[str] = set()
    for row in source_bundle.get(sample_key, []):
        if not isinstance(row, dict):
            continue
        value = _clean_text(row.get(field))
        if value:
            selected_values.add(value)
    return selected_values


def _filter_style_bible_inputs_to_sampling_scope(
    inputs: StyleBibleInputBundle,
    source_bundle: dict[str, Any],
) -> StyleBibleInputBundle:
    selected_scene_ids = _selected_values_from_source_bundle(source_bundle, "scene_signal_samples", "scene_id")
    selected_window_ids = _selected_values_from_source_bundle(source_bundle, "style_window_samples", "window_id")
    selected_chapter_ids = _selected_values_from_source_bundle(source_bundle, "chapter_summaries", "chapter_id")
    selected_plot_node_ids = _selected_values_from_source_bundle(source_bundle, "plot_node_samples", "node_id")
    selected_entity_ids = _selected_values_from_source_bundle(source_bundle, "entity_samples", "entity_id")

    if selected_scene_ids:
        fact_rows = [
            row
            for row in inputs.fact_rows
            if _clean_text(row.get("scene_id")) in selected_scene_ids
        ]
    else:
        fact_rows = list(inputs.fact_rows)

    if selected_window_ids:
        style_rows = [
            row
            for row in inputs.style_rows
            if _clean_text(row.get("window_id")) in selected_window_ids
        ]
    else:
        style_rows = list(inputs.style_rows)

    chapter_scope: set[str] = set(selected_chapter_ids)
    for row in fact_rows:
        chapter_id = _clean_text(row.get("chapter_id"))
        if chapter_id:
            chapter_scope.add(chapter_id)
    for row in style_rows:
        for chapter_id in row.get("chapter_ids", []):
            cleaned_chapter_id = _clean_text(chapter_id)
            if cleaned_chapter_id:
                chapter_scope.add(cleaned_chapter_id)

    if chapter_scope:
        chapter_rows = [
            row
            for row in inputs.chapter_rows
            if _clean_text(row.get("chapter_id")) in chapter_scope
        ]
    else:
        chapter_rows = list(inputs.chapter_rows)

    if selected_plot_node_ids or chapter_scope:
        plot_rows = [
            row
            for row in inputs.plot_rows
            if _clean_text(row.get("node_id")) in selected_plot_node_ids
            or _clean_text(row.get("chapter_id")) in chapter_scope
        ]
    else:
        plot_rows = list(inputs.plot_rows)

    if selected_entity_ids or chapter_scope:
        entity_rows = [
            row
            for row in inputs.entity_rows
            if _clean_text(row.get("entity_id")) in selected_entity_ids
            or _clean_text(row.get("first_seen_chapter")) in chapter_scope
        ]
    else:
        entity_rows = list(inputs.entity_rows)

    return StyleBibleInputBundle(
        fact_rows=fact_rows,
        style_rows=style_rows,
        chapter_rows=chapter_rows,
        plot_rows=plot_rows,
        entity_rows=entity_rows,
        canon_index=inputs.canon_index,
        style_index=inputs.style_index,
        story_node_scope=inputs.story_node_scope,
    )


def _sampled_input_scope_payload(
    *,
    original_inputs: StyleBibleInputBundle,
    filtered_inputs: StyleBibleInputBundle,
    source_bundle: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scope_hint": _clean_text(source_bundle.get("scope_hint")),
        "selected_refs": {
            "scene_ids": sorted(_selected_values_from_source_bundle(source_bundle, "scene_signal_samples", "scene_id")),
            "style_window_ids": sorted(_selected_values_from_source_bundle(source_bundle, "style_window_samples", "window_id")),
            "chapter_ids": sorted(_selected_values_from_source_bundle(source_bundle, "chapter_summaries", "chapter_id")),
            "plot_node_ids": sorted(_selected_values_from_source_bundle(source_bundle, "plot_node_samples", "node_id")),
            "entity_ids": sorted(_selected_values_from_source_bundle(source_bundle, "entity_samples", "entity_id")),
        },
        "counts": {
            "original": {
                "scene_count": len(original_inputs.fact_rows),
                "style_window_count": len(original_inputs.style_rows),
                "chapter_count": len(original_inputs.chapter_rows),
                "plot_node_count": len(original_inputs.plot_rows),
                "entity_count": len(original_inputs.entity_rows),
            },
            "filtered": {
                "scene_count": len(filtered_inputs.fact_rows),
                "style_window_count": len(filtered_inputs.style_rows),
                "chapter_count": len(filtered_inputs.chapter_rows),
                "plot_node_count": len(filtered_inputs.plot_rows),
                "entity_count": len(filtered_inputs.entity_rows),
            },
        },
    }


def _batched_refs_from_batch_plan(batch_plan: dict[str, Any]) -> tuple[set[str], set[str]]:
    batched_item_ids: set[str] = set()
    batched_chapter_ids: set[str] = set()
    for batch in batch_plan.get("batches", []):
        if not isinstance(batch, dict):
            continue
        for item_id in batch.get("item_ids", []):
            cleaned_item_id = _clean_text(item_id)
            if cleaned_item_id:
                batched_item_ids.add(cleaned_item_id)
        for chapter_id in batch.get("chapter_ids", []):
            cleaned_chapter_id = _clean_text(chapter_id)
            if cleaned_chapter_id:
                batched_chapter_ids.add(cleaned_chapter_id)
    return batched_item_ids, batched_chapter_ids


def _refs_to_item_and_chapter_scope(
    routed_index: dict[str, Any],
    refs: set[str],
) -> tuple[set[str], set[str]]:
    item_ids: set[str] = set()
    chapter_ids: set[str] = set()
    for row in routed_index.get("items", []):
        if not isinstance(row, dict):
            continue
        item_id = _clean_text(row.get("item_id"))
        source_ref = _clean_text(row.get("source_ref"))
        if item_id not in refs and source_ref not in refs:
            continue
        if item_id:
            item_ids.add(item_id)
        for chapter_id in row.get("chapter_ids", []):
            cleaned_chapter_id = _clean_text(chapter_id)
            if cleaned_chapter_id:
                chapter_ids.add(cleaned_chapter_id)
    return item_ids, chapter_ids


def _usage_counter_direct(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _nested_usage_counter_direct(usage: dict[str, Any], *path: str) -> int:
    current: Any = usage
    for part in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(part)
    if isinstance(current, (int, float)):
        return int(current)
    return 0


def _usage_rollup_payload(usage: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    candidate_payloads: list[dict[str, Any]] = [usage]
    raw_usage = usage.get("raw_usage_metadata")
    if isinstance(raw_usage, dict):
        candidate_payloads.append(raw_usage)
    for candidate in candidate_payloads:
        source_usage = candidate.get("source_usage_metadata")
        if not isinstance(source_usage, dict):
            continue
        prompt_tokens = _usage_counter_direct(candidate, "prompt_tokens", "input_tokens")
        output_tokens = _usage_counter_direct(candidate, "output_tokens", "completion_tokens")
        total_tokens = _usage_counter_direct(candidate, "total_tokens")
        cached_tokens = _nested_usage_counter_direct(candidate, "prompt_tokens_details", "cached_tokens") or _nested_usage_counter_direct(
            candidate,
            "input_tokens_details",
            "cached_tokens",
        )
        if prompt_tokens or output_tokens or total_tokens or cached_tokens:
            continue
        return source_usage
    return usage


def _usage_counter(usage: dict[str, Any], *keys: str) -> int:
    value = _usage_counter_direct(usage, *keys)
    if value:
        return value
    rollup = _usage_rollup_payload(usage)
    if rollup is usage:
        return 0
    return _usage_counter_direct(rollup, *keys)


def _nested_usage_counter(usage: dict[str, Any], *path: str) -> int:
    value = _nested_usage_counter_direct(usage, *path)
    if value:
        return value
    rollup = _usage_rollup_payload(usage)
    if rollup is usage:
        return 0
    return _nested_usage_counter_direct(rollup, *path)


def _combine_cache_metrics(
    bucket_usage: dict[str, Any],
    reduce_usage: dict[str, Any],
) -> dict[str, Any]:
    prompt_tokens = _usage_counter(bucket_usage, "prompt_tokens", "input_tokens") + _usage_counter(
        reduce_usage, "prompt_tokens", "input_tokens"
    )
    output_tokens = _usage_counter(bucket_usage, "output_tokens", "completion_tokens") + _usage_counter(
        reduce_usage, "output_tokens", "completion_tokens"
    )
    total_tokens = _usage_counter(bucket_usage, "total_tokens") + _usage_counter(reduce_usage, "total_tokens")
    cached_tokens = _usage_counter(bucket_usage, "cached_tokens") + _usage_counter(reduce_usage, "cached_tokens")
    return {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        "memo_stage": bucket_usage,
        "reduce_stage": reduce_usage,
    }


def _combine_ttft_summary(
    bucket_metrics: dict[str, Any],
    reduce_usage: dict[str, Any],
) -> dict[str, Any]:
    bucket_ttft = bucket_metrics.get("ttft_summary", {}) if isinstance(bucket_metrics.get("ttft_summary"), dict) else {}
    reducer_ttft = float(reduce_usage.get("ttft_seconds", 0.0) or 0.0)
    return {
        "bucket_stage": bucket_ttft,
        "reduce_stage": {
            "ttft_seconds": round(reducer_ttft, 3),
        },
    }


def _sampling_mode_from_source_bundle(source_bundle: dict[str, Any]) -> str:
    style_selected = _sampling_count(source_bundle, "style_window_samples", "selected")
    scene_selected = _sampling_count(source_bundle, "scene_signal_samples", "selected")
    style_available = _sampling_count(source_bundle, "style_window_samples", "available")
    scene_available = _sampling_count(source_bundle, "scene_signal_samples", "available")
    return infer_sampling_mode(
        max_style_windows=style_selected,
        max_scene_samples=scene_selected,
        available_style_windows=style_available,
        available_scene_samples=scene_available,
    )


def _prepare_style_bible_phase01_artifacts(
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: Path,
    *,
    scope_label: str | None = None,
    max_style_windows: int = 0,
    max_scene_samples: int = 0,
    max_plot_nodes: int = 0,
    max_chapter_summaries: int = 0,
    max_entity_samples: int = 0,
    routing_rules_config: str | Path | None = None,
    batching_rules_config: str | Path | None = None,
) -> StyleBiblePhaseArtifacts:
    inputs = load_style_bible_inputs(facts_dir, style_dir, canon_dir)
    chapter_ids = [_clean_text(row.get("chapter_id")) for row in inputs.chapter_rows if _clean_text(row.get("chapter_id"))]
    scope_hint = scope_label or shared_build_scope_hint(chapter_ids, story_node_scope=inputs.story_node_scope)
    source_bundle = build_style_bible_source_bundle(
        facts_dir,
        style_dir,
        canon_dir,
        output_dir=output_dir,
        scope_label=scope_hint,
        max_style_windows=max_style_windows,
        max_scene_samples=max_scene_samples,
        max_plot_nodes=max_plot_nodes,
        max_chapter_summaries=max_chapter_summaries,
        max_entity_samples=max_entity_samples,
        inputs=inputs,
    )
    source_bundle_path = output_dir / "style_bible_source_bundle.json"
    write_json(source_bundle_path, source_bundle)

    filtered_inputs = _filter_style_bible_inputs_to_sampling_scope(inputs, source_bundle)
    write_json(
        output_dir / "sampled_input_scope.json",
        _sampled_input_scope_payload(
            original_inputs=inputs,
            filtered_inputs=filtered_inputs,
            source_bundle=source_bundle,
        ),
    )

    runtime_flags = load_style_bible_runtime_flags()
    routed_index_model = route_style_bible_inputs(
        filtered_inputs,
        scope_hint=scope_hint,
        rules_config=routing_rules_config,
        runtime_flags=runtime_flags,
    )
    routed_index = routed_index_model.model_dump(mode="json")
    routed_index_path = output_dir / ROUTED_INDEX_FILE
    write_json(routed_index_path, routed_index)

    batch_plan_model, planner_debug_report = plan_style_bible_batches_with_debug(
        routed_index_model,
        rules_config=batching_rules_config,
    )
    batch_plan_model.source_routed_index_file = str(routed_index_path.resolve())
    batch_plan = batch_plan_model.model_dump(mode="json")
    batch_plan_path = output_dir / BATCH_PLAN_FILE
    write_json(batch_plan_path, batch_plan)
    planner_debug_report["source_routed_index_file"] = str(routed_index_path.resolve())
    planner_debug_report["batch_plan_file"] = str(batch_plan_path.resolve())
    write_json(output_dir / PLANNER_DEBUG_REPORT_FILE, planner_debug_report)

    selected_item_ids, selected_chapter_ids = _selected_refs_from_source_bundle(source_bundle)
    batched_item_ids, batched_chapter_ids = _batched_refs_from_batch_plan(batch_plan)
    selection_limits = _selection_limits_from_source_bundle(source_bundle)
    sampling_mode = _sampling_mode_from_source_bundle(source_bundle)
    sampling_report_model = build_style_bible_sampling_report(
        routed_index_model,
        selected_item_ids=selected_item_ids,
        selected_chapter_ids=selected_chapter_ids,
        batched_item_ids=batched_item_ids,
        batched_chapter_ids=batched_chapter_ids,
        selection_limits=selection_limits,
        sampling_mode=sampling_mode,
        routing_mode=ROUTING_MODE_SIGNAL_FUSION_V2,
        batching_mode=BATCHING_MODE_BUCKET_AFFINITY_V3,
        batch_plan=batch_plan_model,
    )
    sampling_report = sampling_report_model.model_dump(mode="json")
    sampling_report_path = output_dir / SAMPLING_REPORT_FILE
    write_json(sampling_report_path, sampling_report)
    write_json(output_dir / COVERAGE_REPORT_FILE, sampling_report)

    return StyleBiblePhaseArtifacts(
        scope_hint=scope_hint,
        story_node_scope=inputs.story_node_scope,
        source_bundle=source_bundle,
        source_bundle_path=source_bundle_path,
        routed_index=routed_index,
        routed_index_path=routed_index_path,
        batch_plan=batch_plan,
        batch_plan_path=batch_plan_path,
        sampling_report=sampling_report,
        sampling_report_path=sampling_report_path,
        sampling_mode=sampling_mode,
        routing_mode=ROUTING_MODE_SIGNAL_FUSION_V2,
        batching_mode=BATCHING_MODE_BUCKET_AFFINITY_V3,
    )


def _load_existing_rows(path: Path, *, resume: bool) -> list[dict]:
    if not resume or not path.exists():
        return []
    payload = read_json(path)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _write_tracking_files(
    manifest_path: Path,
    manifest_by_key: dict[str, dict],
    failures_path: Path,
    failures_by_key: dict[str, dict],
) -> None:
    write_json(manifest_path, list(manifest_by_key.values()))
    write_json(failures_path, list(failures_by_key.values()))


def _extract_request_metrics(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, StructuredGenerationError):
        return exc.request_metrics
    return {}


def _chapter_sort_key(value: Any) -> tuple[int, Any]:
    text = str(value or "").strip()
    if not text:
        return (2, "")
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _validate_existing_style_bible_resume(
    config: StableProjectConfig,
    *,
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: Path,
    final_output_path: Path,
    max_style_windows: int,
    max_scene_samples: int,
    max_plot_nodes: int,
    max_chapter_summaries: int,
    max_entity_samples: int,
    routing_rules_config: str | Path | None,
    batching_rules_config: str | Path | None,
    bucket_build_concurrency: int | None,
) -> StyleBibleResumeValidation:
    source_bundle_path = output_dir / "style_bible_source_bundle.json"
    if not source_bundle_path.exists():
        return StyleBibleResumeValidation(False, "missing_source_bundle")
    try:
        style_bible_payload = read_json(final_output_path)
        source_bundle = read_json(source_bundle_path)
    except Exception as exc:  # noqa: BLE001
        return StyleBibleResumeValidation(False, f"invalid_json:{type(exc).__name__}")
    if not isinstance(style_bible_payload, dict):
        return StyleBibleResumeValidation(False, "style_bible_not_object")
    if not isinstance(source_bundle, dict):
        return StyleBibleResumeValidation(False, "source_bundle_not_object")
    try:
        StyleBibleResultV2.model_validate(style_bible_payload)
    except Exception as exc:  # noqa: BLE001
        return StyleBibleResumeValidation(False, f"schema_invalid:{type(exc).__name__}")

    expected_fingerprint = _style_bible_artifact_fingerprint(
        config,
        source_bundle=source_bundle,
        max_style_windows=max_style_windows,
        max_scene_samples=max_scene_samples,
        max_plot_nodes=max_plot_nodes,
        max_chapter_summaries=max_chapter_summaries,
        max_entity_samples=max_entity_samples,
        routing_rules_config=routing_rules_config,
        batching_rules_config=batching_rules_config,
        bucket_build_concurrency=bucket_build_concurrency,
    )
    legacy_without_fingerprint = False
    artifact_fingerprint = style_bible_payload.get("artifact_fingerprint")
    if artifact_fingerprint:
        if artifact_fingerprint != expected_fingerprint:
            return StyleBibleResumeValidation(False, "artifact_fingerprint_mismatch")
    else:
        legacy_without_fingerprint = True

    run_manifest_path = output_dir / RUN_MANIFEST_FILE
    if not run_manifest_path.exists():
        return StyleBibleResumeValidation(True, "legacy_without_run_manifest", None, True)
    try:
        run_manifest = read_json(run_manifest_path)
    except Exception as exc:  # noqa: BLE001
        return StyleBibleResumeValidation(False, f"run_manifest_invalid_json:{type(exc).__name__}")
    if not isinstance(run_manifest, dict):
        return StyleBibleResumeValidation(False, "run_manifest_not_object")
    input_dirs = run_manifest.get("input_dirs", {})
    if not isinstance(input_dirs, dict):
        return StyleBibleResumeValidation(False, "run_manifest_missing_input_dirs")
    expected_dirs = {
        "facts_dir": str(Path(facts_dir).resolve()),
        "style_dir": str(Path(style_dir).resolve()),
        "canon_dir": str(Path(canon_dir).resolve()),
    }
    for key, expected in expected_dirs.items():
        if _clean_text(input_dirs.get(key)) != expected:
            return StyleBibleResumeValidation(False, f"run_manifest_{key}_mismatch")
    hashes = run_manifest.get("hashes", {})
    if not isinstance(hashes, dict):
        return StyleBibleResumeValidation(False, "run_manifest_missing_hashes")
    if _clean_text(hashes.get("style_bible_sha256")) != sha256_payload(style_bible_payload):
        return StyleBibleResumeValidation(False, "style_bible_hash_mismatch")
    if _clean_text(hashes.get("source_bundle_sha256")) != sha256_payload(source_bundle):
        return StyleBibleResumeValidation(False, "source_bundle_hash_mismatch")
    return StyleBibleResumeValidation(True, run_manifest=run_manifest, legacy_without_fingerprint=legacy_without_fingerprint)


def _load_story_node_scope(canon_dir: str | Path) -> dict[str, Any] | None:
    scope_path = Path(canon_dir).resolve() / "story_node_scope.json"
    if not scope_path.exists():
        return None
    payload = read_json(scope_path)
    if not isinstance(payload, dict):
        raise ValueError(f"Story node scope file must be a JSON object: {scope_path}")

    start_chapter = _clean_text(payload.get("start_chapter"))
    end_chapter = _clean_text(payload.get("end_chapter"))
    if not start_chapter or not end_chapter:
        raise ValueError(f"Story node scope file is missing start/end chapter fields: {scope_path}")

    return {
        "scope_type": _clean_text(payload.get("scope_type")) or "story_node",
        "node_id": _clean_text(payload.get("node_id")),
        "label": _clean_text(payload.get("label")),
        "start_chapter": start_chapter,
        "end_chapter": end_chapter,
        "dominant_layer": payload.get("dominant_layer"),
        "dominant_layer_label": _clean_text(payload.get("dominant_layer_label")),
        "manifest_path": _clean_text(payload.get("manifest_path")),
        "user_notes": _clean_text(payload.get("user_notes")),
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    return slug or "style_bible"


def _compact_strings(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if len(results) >= limit:
            break
    return results


def _extract_nested_evidence_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    nested = payload.get("evidence")
    if isinstance(nested, dict):
        return _clean_text(nested.get("evidence_text"))
    return _clean_text(payload.get("evidence_text"))


def _evenly_sample(rows: list[dict], limit: int) -> list[dict]:
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[0]]

    max_index = len(rows) - 1
    selected_indices: list[int] = []
    for offset in range(limit):
        index = round(offset * max_index / (limit - 1))
        if selected_indices and index <= selected_indices[-1]:
            index = min(selected_indices[-1] + 1, max_index)
        selected_indices.append(index)
    return [rows[index] for index in selected_indices]


def _limit_with_per_group(
    rows: list[dict],
    *,
    limit: int,
    group_key: str,
    per_group_limit: int,
) -> list[dict]:
    if limit <= 0 or len(rows) <= limit:
        return list(rows)

    selected: list[dict] = []
    group_counts: dict[str, int] = defaultdict(int)
    overflow: list[dict] = []
    for row in rows:
        group_value = _clean_text(row.get(group_key))
        if per_group_limit > 0 and group_value and group_counts[group_value] >= per_group_limit:
            overflow.append(row)
            continue
        selected.append(row)
        if group_value:
            group_counts[group_value] += 1
        if len(selected) >= limit:
            return selected

    if len(selected) >= limit:
        return selected[:limit]

    for row in overflow:
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _top_items(
    rows: list[dict],
    *,
    value_getter,
    source_ref_getter,
    limit: int,
) -> list[dict]:
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_ref = _clean_text(source_ref_getter(row))
        for raw_value in value_getter(row):
            cleaned = _clean_text(raw_value)
            if not cleaned:
                continue
            entry = counts.setdefault(cleaned, {"value": cleaned, "count": 0, "source_refs": []})
            entry["count"] += 1
            if source_ref and source_ref not in entry["source_refs"] and len(entry["source_refs"]) < 4:
                entry["source_refs"].append(source_ref)
    ordered = sorted(counts.values(), key=lambda item: (-int(item["count"]), str(item["value"])))
    return ordered[:limit]


def _truncate_text(value: Any, *, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."


def _style_rule_values(row: dict[str, Any], field_name: str, *, key: str = "mechanism_label") -> list[str]:
    values = row.get(field_name, [])
    if not isinstance(values, list):
        return []
    return [
        _clean_text(item.get(key))
        for item in values
        if isinstance(item, dict) and _clean_text(item.get(key))
    ]


def _style_hint_values(row: dict[str, Any], field_names: tuple[str, ...], *, key: str) -> list[str]:
    values: list[str] = []
    for field_name in field_names:
        field_values = row.get(field_name, [])
        if not isinstance(field_values, list):
            continue
        values.extend(
            _clean_text(item.get(key))
            for item in field_values
            if isinstance(item, dict) and _clean_text(item.get(key))
        )
    return values


def _style_rule_candidate_texts(row: dict[str, Any], field_name: str) -> list[str]:
    values = row.get(field_name, [])
    if not isinstance(values, list):
        return []
    results: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        joined = " ".join(
            _clean_text(item.get(key))
            for key in ("mechanism_label", "execution_logic", "trigger", "constraint")
            if _clean_text(item.get(key))
        )
        if joined:
            results.append(joined)
    return results


def _normalize_scalar_lookup_token(value: Any) -> str:
    lowered = _clean_text(value).casefold()
    if not lowered:
        return ""
    return re.sub(r"[\s\-_/.]+", "", lowered)


def _scalar_lookup_in_text(text: Any, alias: str) -> bool:
    normalized_text = _clean_text(text).casefold()
    normalized_alias = _clean_text(alias).casefold()
    if not normalized_text or not normalized_alias:
        return False
    if re.fullmatch(r"[0-9a-z_]+", normalized_alias):
        return bool(re.search(rf"(?<![0-9a-z_]){re.escape(normalized_alias)}(?![0-9a-z_])", normalized_text))
    compact_text = _normalize_scalar_lookup_token(normalized_text)
    compact_alias = _normalize_scalar_lookup_token(normalized_alias)
    return bool(compact_alias) and compact_alias in compact_text


def _extract_canonical_scalar_candidate(path: str, value: Any) -> str:
    direct = canonicalize_scalar_value(path, value)
    spec = scalar_enum_spec_for_path(path)
    if spec is None:
        return _clean_text(direct)
    if direct in set(spec.allowed_values):
        return direct
    text = _clean_text(value)
    for token, canonical in scalar_value_lookup_rows(path):
        if _scalar_lookup_in_text(text, token):
            return canonical
    return _clean_text(direct)


def _canonical_scalar_summary_rows(
    style_rows: list[dict],
    *,
    path: str,
    value_getter,
) -> list[dict[str, Any]]:
    spec = scalar_enum_spec_for_path(path)
    if spec is None:
        return []
    allowed_values = set(spec.allowed_values)
    counts: dict[str, dict[str, Any]] = {}
    for row in style_rows:
        source_ref = _clean_text(row.get("window_id"))
        for raw_value in value_getter(row):
            canonical_value = _extract_canonical_scalar_candidate(path, raw_value)
            canonical_value = _clean_text(canonical_value)
            if canonical_value not in allowed_values or canonical_value == "unspecified":
                continue
            entry = counts.setdefault(canonical_value, {"value": canonical_value, "count": 0, "source_refs": []})
            entry["count"] += 1
            if source_ref and source_ref not in entry["source_refs"] and len(entry["source_refs"]) < 4:
                entry["source_refs"].append(source_ref)
    ordered = sorted(counts.values(), key=lambda item: (-int(item["count"]), str(item["value"])))
    return ordered[:SIGNAL_SUMMARY_LIMIT]


def _build_scalar_contract_summary(style_rows: list[dict]) -> dict[str, list[dict]]:
    return {
        "perspective": _canonical_scalar_summary_rows(
            style_rows,
            path="narrative_system.perspective",
            value_getter=lambda row: [_clean_text((row.get("scalar_contracts", {}) or {}).get("perspective"))],
        ),
        "distance": _canonical_scalar_summary_rows(
            style_rows,
            path="narrative_system.distance",
            value_getter=lambda row: [_clean_text((row.get("scalar_contracts", {}) or {}).get("distance"))],
        ),
        "temporality": _canonical_scalar_summary_rows(
            style_rows,
            path="narrative_system.temporality",
            value_getter=lambda row: [_clean_text((row.get("scalar_contracts", {}) or {}).get("temporality"))],
        ),
        "inner_monologue_mode": _canonical_scalar_summary_rows(
            style_rows,
            path="voice_contract.inner_monologue_mode",
            value_getter=lambda row: [_clean_text((row.get("scalar_contracts", {}) or {}).get("inner_monologue_mode"))],
        ),
        "narrator_voice": _canonical_scalar_summary_rows(
            style_rows,
            path="voice_contract.narrator_voice",
            value_getter=lambda row: _style_rule_candidate_texts(row, "narrator_voice_rules"),
        ),
    }


def _build_style_signal_summary(style_rows: list[dict]) -> dict[str, Any]:
    return {
        "surface_markers": _top_items(
            style_rows,
            value_getter=lambda row: row.get("surface_markers", []),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "narrative_engine_labels": _top_items(
            style_rows,
            value_getter=lambda row: _style_rule_values(row, "narrative_engine_rules"),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "dialogue_rule_labels": _top_items(
            style_rows,
            value_getter=lambda row: _style_rule_values(row, "dialogue_rules"),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "humor_rule_labels": _top_items(
            style_rows,
            value_getter=lambda row: _style_rule_values(row, "humor_rules"),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "satire_rule_labels": _top_items(
            style_rows,
            value_getter=lambda row: _style_rule_values(row, "satire_rules"),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "nonstandard_xianxia_labels": _top_items(
            style_rows,
            value_getter=lambda row: _style_rule_values(row, "nonstandard_xianxia_rules"),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "routing_target_actions": _top_items(
            style_rows,
            value_getter=lambda row: _style_hint_values(
                row,
                ("rag_candidates", "worldbook_candidates", "routing_hints"),
                key="route_target_action",
            ),
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "axis_hints": _top_items(
            style_rows,
            value_getter=lambda row: [
                *_style_hint_values(row, ("routing_hints",), key="axis_id"),
                *[
                    _clean_text(item.get("axis_id"))
                    for item in row.get("axis_hints", [])
                    if isinstance(item, dict) and _clean_text(item.get("axis_id"))
                ],
            ],
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "bucket_hints": _top_items(
            style_rows,
            value_getter=lambda row: [
                *_style_hint_values(row, ("routing_hints",), key="bucket_id"),
                *[
                    _clean_text(item.get("bucket_id"))
                    for item in row.get("bucket_hints", [])
                    if isinstance(item, dict) and _clean_text(item.get("bucket_id"))
                ],
            ],
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "negative_pitfalls": _top_items(
            style_rows,
            value_getter=lambda row: [
                _clean_text(item.get("forbidden_action"))
                for item in row.get("negative_pitfalls", [])
                if isinstance(item, dict) and _clean_text(item.get("forbidden_action"))
            ],
            source_ref_getter=lambda row: row.get("window_id", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "scalar_contracts": _build_scalar_contract_summary(style_rows),
    }


def _build_fact_signal_summary(fact_rows: list[dict]) -> dict[str, list[dict]]:
    style_marker_rows = [
        {
            "source_ref": f"scene:{_clean_text(row.get('scene_id'))}",
            "values": [item.get("marker", "") for item in row.get("style_markers", []) if isinstance(item, dict)],
        }
        for row in fact_rows
    ]
    power_note_rows = [
        {
            "source_ref": f"scene:{_clean_text(row.get('scene_id'))}",
            "values": [item.get("topic", "") for item in row.get("power_system_notes", []) if isinstance(item, dict)],
        }
        for row in fact_rows
    ]
    relationship_rows = [
        {
            "source_ref": f"scene:{_clean_text(row.get('scene_id'))}",
            "values": [item.get("relation", "") for item in row.get("relationship_changes", []) if isinstance(item, dict)],
        }
        for row in fact_rows
    ]
    return {
        "scene_style_markers": _top_items(
            style_marker_rows,
            value_getter=lambda row: row.get("values", []),
            source_ref_getter=lambda row: row.get("source_ref", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "power_system_topics": _top_items(
            power_note_rows,
            value_getter=lambda row: row.get("values", []),
            source_ref_getter=lambda row: row.get("source_ref", ""),
            limit=SIGNAL_SUMMARY_LIMIT,
        ),
        "relationship_patterns": _top_items(
            relationship_rows,
            value_getter=lambda row: row.get("values", []),
            source_ref_getter=lambda row: row.get("source_ref", ""),
            limit=RELATIONSHIP_PATTERN_LIMIT,
        ),
    }


def _scene_source_ref(scene_id: Any) -> str:
    normalized_scene_id = _clean_text(scene_id)
    if not normalized_scene_id:
        return ""
    if normalized_scene_id.startswith("scene:"):
        return normalized_scene_id
    if normalized_scene_id.startswith("scene_"):
        return f"scene:{normalized_scene_id.split('scene_', 1)[1]}"
    return f"scene:{normalized_scene_id}"


def _normalize_scene_refs(values: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(values, list):
        return []
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        source_ref = _scene_source_ref(value)
        if not source_ref or source_ref in seen:
            continue
        seen.add(source_ref)
        results.append(source_ref)
        if len(results) >= limit:
            break
    return results


def _append_worldbook_atom(
    results: list[dict[str, Any]],
    seen_keys: set[str],
    atom: dict[str, Any],
) -> None:
    atom_text = _clean_text(atom.get("text"))
    if not atom_text:
        return
    atom_id = _clean_text(atom.get("atom_id")) or _slugify(atom_text)
    dedupe_key = atom_id or atom_text
    if dedupe_key in seen_keys:
        return
    seen_keys.add(dedupe_key)
    results.append(
        {
            "atom_id": atom_id,
            "atom_type": _clean_text(atom.get("atom_type")),
            "source_family": _clean_text(atom.get("source_family")),
            "stability": _clean_text(atom.get("stability")) or "scene_grounded",
            "chapter_id": _clean_text(atom.get("chapter_id")),
            "scene_id": _clean_text(atom.get("scene_id")),
            "source_ref": _clean_text(atom.get("source_ref")),
            "grounding_refs": _compact_strings(atom.get("grounding_refs"), limit=8),
            "tags": _compact_strings(atom.get("tags"), limit=8),
            "text": _truncate_text(atom_text, limit=240),
        }
    )


def _build_worldbook_atom_candidates(
    fact_rows: list[dict],
    plot_rows: list[dict],
    entity_rows: list[dict],
    chapter_rows: list[dict],
) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for row in fact_rows:
        chapter_id = _clean_text(row.get("chapter_id"))
        scene_id = _clean_text(row.get("scene_id"))
        source_ref = _scene_source_ref(scene_id)
        grounding_refs = [source_ref] if source_ref else []

        for index, item in enumerate(row.get("facts", [])[:3], start=1):
            if not isinstance(item, dict):
                continue
            subject = _clean_text(item.get("subject"))
            predicate = _clean_text(item.get("predicate"))
            obj = _clean_text(item.get("object"))
            atom_text = _clean_text(f"【事实】{subject}{predicate}{obj}")
            _append_worldbook_atom(
                atoms,
                seen_keys,
                {
                    "atom_id": f"fact__{scene_id or chapter_id}__{index:02d}",
                    "atom_type": "fact",
                    "source_family": "facts",
                    "stability": "scene_grounded",
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    "source_ref": source_ref,
                    "grounding_refs": grounding_refs,
                    "tags": [subject, predicate, obj],
                    "text": atom_text,
                },
            )

        for index, item in enumerate(row.get("events", [])[:2], start=1):
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"))
            summary = _clean_text(item.get("summary"))
            _append_worldbook_atom(
                atoms,
                seen_keys,
                {
                    "atom_id": f"event__{scene_id or chapter_id}__{index:02d}",
                    "atom_type": "event",
                    "source_family": "facts",
                    "stability": "scene_grounded",
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    "source_ref": source_ref,
                    "grounding_refs": grounding_refs,
                    "tags": [
                        name,
                        *_compact_strings(item.get("participants"), limit=4),
                        _clean_text(item.get("location")),
                    ],
                    "text": _clean_text(f"【事件】{name}：{summary}"),
                },
            )

        for index, item in enumerate(row.get("relationship_changes", [])[:2], start=1):
            if not isinstance(item, dict):
                continue
            source_name = _clean_text(item.get("source"))
            target_name = _clean_text(item.get("target"))
            relation = _clean_text(item.get("relation"))
            change = _clean_text(item.get("change"))
            _append_worldbook_atom(
                atoms,
                seen_keys,
                {
                    "atom_id": f"relation__{scene_id or chapter_id}__{index:02d}",
                    "atom_type": "relationship_change",
                    "source_family": "facts",
                    "stability": "scene_grounded",
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    "source_ref": source_ref,
                    "grounding_refs": grounding_refs,
                    "tags": [source_name, target_name, relation],
                    "text": _clean_text(f"【关系变化】{source_name}与{target_name}的{relation}发生变化：{change}"),
                },
            )

        for index, item in enumerate(row.get("power_system_notes", [])[:2], start=1):
            if not isinstance(item, dict):
                continue
            topic = _clean_text(item.get("topic"))
            note = _clean_text(item.get("note"))
            _append_worldbook_atom(
                atoms,
                seen_keys,
                {
                    "atom_id": f"power__{scene_id or chapter_id}__{index:02d}",
                    "atom_type": "power_rule",
                    "source_family": "facts",
                    "stability": "scene_grounded",
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    "source_ref": source_ref,
                    "grounding_refs": grounding_refs,
                    "tags": [topic],
                    "text": _clean_text(f"【规则】{topic}：{note}"),
                },
            )

    for row in plot_rows:
        node_id = _clean_text(row.get("node_id"))
        chapter_id = _clean_text(row.get("chapter_id"))
        _append_worldbook_atom(
            atoms,
            seen_keys,
            {
                "atom_id": f"plot__{node_id or chapter_id}",
                "atom_type": "plot_node",
                "source_family": "plot",
                "stability": "canon_stable",
                "chapter_id": chapter_id,
                "scene_id": "",
                "source_ref": f"plot_node:{node_id}" if node_id else f"chapter:{chapter_id}",
                "grounding_refs": _normalize_scene_refs(row.get("scene_ids"), limit=6),
                "tags": [
                    _clean_text(row.get("title")),
                    *_compact_strings(row.get("event_names"), limit=4),
                    *_compact_strings(row.get("participants"), limit=4),
                    *_compact_strings(row.get("locations"), limit=3),
                ],
                "text": _clean_text(f"【情节点】{_clean_text(row.get('title'))}：{_clean_text(row.get('summary'))}"),
            },
        )

    for row in entity_rows:
        entity_id = _clean_text(row.get("entity_id"))
        name = _clean_text(row.get("name"))
        entity_type = _clean_text(row.get("entity_type"))
        notes = _compact_strings(row.get("notes"), limit=3)
        aliases = _compact_strings(row.get("aliases"), limit=3)
        summary_parts = [f"【实体】{name}（{entity_type or 'other'}）"]
        if notes:
            summary_parts.append("；".join(notes))
        else:
            first_seen = _clean_text(row.get("first_seen_chapter"))
            if first_seen:
                summary_parts.append(f"首次出现于第{first_seen}章。")
        _append_worldbook_atom(
            atoms,
            seen_keys,
            {
                "atom_id": f"entity__{entity_id or _slugify(name)}",
                "atom_type": "entity",
                "source_family": "entities",
                "stability": "canon_stable",
                "chapter_id": _clean_text(row.get("first_seen_chapter")),
                "scene_id": "",
                "source_ref": f"entity:{entity_id}" if entity_id else f"entity:{_slugify(name)}",
                "grounding_refs": _normalize_scene_refs(row.get("supporting_scene_ids"), limit=6),
                "tags": [name, *aliases, entity_type],
                "text": "".join(part for part in summary_parts if part),
            },
        )

    for row in chapter_rows:
        chapter_id = _clean_text(row.get("chapter_id"))
        chapter_title = _clean_text(row.get("chapter_title"))
        scene_summaries = _compact_strings(row.get("scene_summaries"), limit=2)
        open_questions = _compact_strings(row.get("open_questions"), limit=2)
        chapter_text = f"【章节摘要】{chapter_title or chapter_id}：{'；'.join(scene_summaries)}"
        if open_questions:
            chapter_text = f"{chapter_text}；待解问题：{'；'.join(open_questions)}"
        _append_worldbook_atom(
            atoms,
            seen_keys,
            {
                "atom_id": f"chapter__{chapter_id}",
                "atom_type": "chapter_summary",
                "source_family": "canon",
                "stability": "canon_stable",
                "chapter_id": chapter_id,
                "scene_id": "",
                "source_ref": f"chapter:{chapter_id}",
                "grounding_refs": [],
                "tags": [chapter_title, *open_questions],
                "text": chapter_text,
            },
        )

    return atoms


def _compact_style_window(row: dict) -> dict[str, Any]:
    def compact_rules(field_name: str, *, limit: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in row.get(field_name, []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "mechanism_label": _truncate_text(item.get("mechanism_label"), limit=40),
                    "execution_logic": _truncate_text(item.get("execution_logic"), limit=180),
                    "trigger": _truncate_text(item.get("trigger"), limit=96),
                    "constraint": _truncate_text(item.get("constraint"), limit=96),
                    "evidence_ids": _compact_strings(item.get("evidence_ids"), limit=4),
                }
            )
            if len(results) >= limit:
                break
        return results

    def compact_hints(field_name: str, *, limit: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in row.get(field_name, []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "axis_id": _clean_text(item.get("axis_id")),
                    "bucket_id": _clean_text(item.get("bucket_id")),
                    "query_feature_matcher": _truncate_text(item.get("query_feature_matcher"), limit=120),
                    "route_target_action": _truncate_text(item.get("route_target_action"), limit=120),
                    "evidence_ids": _compact_strings(item.get("evidence_ids"), limit=4),
                }
            )
            if len(results) >= limit:
                break
        return results

    def compact_negative_pitfalls(*, limit: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in row.get("negative_pitfalls", []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "forbidden_action": _truncate_text(item.get("forbidden_action"), limit=120),
                    "correction_guideline": _truncate_text(item.get("correction_guideline"), limit=120),
                    "evidence_ids": _compact_strings(item.get("evidence_ids"), limit=4),
                }
            )
            if len(results) >= limit:
                break
        return results

    def compact_evidence_rows(*, limit: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in row.get("evidence_index", []):
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "evidence_id": _clean_text(item.get("evidence_id")),
                    "source_ref": _clean_text(item.get("source_ref")),
                    "quote": _truncate_text(item.get("quote"), limit=140),
                }
            )
            if len(results) >= limit:
                break
        return results

    scalar_contracts = row.get("scalar_contracts", {})
    compact_scalar_contracts = {}
    if isinstance(scalar_contracts, dict):
        compact_scalar_contracts = {
            key: _clean_text(scalar_contracts.get(key))
            for key in ("perspective", "distance", "temporality", "inner_monologue_mode")
            if _clean_text(scalar_contracts.get(key)) and _clean_text(scalar_contracts.get(key)) != "unspecified"
        }

    payload = {
        "schema_version": _clean_text(row.get("schema_version")),
        "window_id": _clean_text(row.get("window_id")),
        "chapter_ids": _compact_strings(row.get("chapter_ids"), limit=12),
        "surface_markers": _compact_strings(row.get("surface_markers"), limit=6),
        "narrative_engine_rules": compact_rules("narrative_engine_rules", limit=3),
        "pacing_rules": compact_rules("pacing_rules", limit=2),
        "plot_node_logic_rules": compact_rules("plot_node_logic_rules", limit=2),
        "description_rules": compact_rules("description_rules", limit=2),
        "dialogue_rules": compact_rules("dialogue_rules", limit=2),
        "characterization_rules": compact_rules("characterization_rules", limit=2),
        "humor_rules": compact_rules("humor_rules", limit=2),
        "satire_rules": compact_rules("satire_rules", limit=2),
        "nonstandard_xianxia_rules": compact_rules("nonstandard_xianxia_rules", limit=2),
        "narrator_voice_rules": compact_rules("narrator_voice_rules", limit=2),
        "register_mix_rules": compact_rules("register_mix_rules", limit=2),
        "rag_candidates": compact_hints("rag_candidates", limit=2),
        "worldbook_candidates": compact_hints("worldbook_candidates", limit=2),
        "routing_hints": compact_hints("routing_hints", limit=3),
        "negative_pitfalls": compact_negative_pitfalls(limit=2),
        "axis_hints": [
            {
                "axis_id": _clean_text(item.get("axis_id")),
                "evidence_ids": _compact_strings(item.get("evidence_ids"), limit=4),
            }
            for item in row.get("axis_hints", [])
            if isinstance(item, dict) and _clean_text(item.get("axis_id"))
        ][:4],
        "bucket_hints": [
            {
                "bucket_id": _clean_text(item.get("bucket_id")),
                "evidence_ids": _compact_strings(item.get("evidence_ids"), limit=4),
            }
            for item in row.get("bucket_hints", [])
            if isinstance(item, dict) and _clean_text(item.get("bucket_id"))
        ][:4],
        "evidence_index": compact_evidence_rows(limit=4),
    }
    if compact_scalar_contracts:
        payload["scalar_contracts"] = compact_scalar_contracts
    source_titles = _compact_strings(row.get("source_chapter_titles"), limit=4)
    if source_titles:
        payload["source_chapter_titles"] = source_titles
    return payload


def _compact_scene_signal(row: dict) -> dict[str, Any]:
    payload = {
        "chapter_id": _clean_text(row.get("chapter_id")),
        "scene_id": _clean_text(row.get("scene_id")),
        "scene_summary": _clean_text(row.get("scene_summary")),
        "relationship_changes": [],
        "power_system_notes": [],
        "style_markers": [],
        "open_questions": _compact_strings(row.get("open_questions"), limit=3),
    }
    for item in row.get("relationship_changes", []):
        if not isinstance(item, dict):
            continue
        payload["relationship_changes"].append(
            {
                "source": _clean_text(item.get("source")),
                "target": _clean_text(item.get("target")),
                "relation": _clean_text(item.get("relation")),
                "change": _clean_text(item.get("change")),
                "evidence_text": _extract_nested_evidence_text(item),
            }
        )
        if len(payload["relationship_changes"]) >= 3:
            break
    for item in row.get("power_system_notes", []):
        if not isinstance(item, dict):
            continue
        payload["power_system_notes"].append(
            {
                "topic": _clean_text(item.get("topic")),
                "note": _clean_text(item.get("note")),
                "evidence_text": _extract_nested_evidence_text(item),
            }
        )
        if len(payload["power_system_notes"]) >= 3:
            break
    for item in row.get("style_markers", []):
        if not isinstance(item, dict):
            continue
        payload["style_markers"].append(
            {
                "marker": _clean_text(item.get("marker")),
                "explanation": _clean_text(item.get("explanation")),
                "evidence_text": _extract_nested_evidence_text(item),
            }
        )
        if len(payload["style_markers"]) >= 3:
            break
    return payload


def _compact_plot_node(row: dict) -> dict[str, Any]:
    return {
        "node_id": _clean_text(row.get("node_id")),
        "chapter_id": _clean_text(row.get("chapter_id")),
        "title": _clean_text(row.get("title")),
        "summary": _clean_text(row.get("summary")),
        "event_names": _compact_strings(row.get("event_names"), limit=4),
        "participants": _compact_strings(row.get("participants"), limit=6),
        "locations": _compact_strings(row.get("locations"), limit=4),
        "plot_relevance_hint": _clean_text(row.get("plot_relevance_hint")),
        "open_questions": _compact_strings(row.get("open_questions"), limit=3),
    }


def _compact_chapter_summary(row: dict) -> dict[str, Any]:
    return {
        "chapter_id": _clean_text(row.get("chapter_id")),
        "chapter_title": _clean_text(row.get("chapter_title")),
        "scene_count": int(row.get("scene_count", 0) or 0),
        "scene_summaries": _compact_strings(row.get("scene_summaries"), limit=3),
        "open_questions": _compact_strings(row.get("open_questions"), limit=3),
    }


def _compact_entity(row: dict) -> dict[str, Any]:
    return {
        "entity_id": _clean_text(row.get("entity_id")),
        "name": _clean_text(row.get("name")),
        "entity_type": _clean_text(row.get("entity_type")),
        "aliases": _compact_strings(row.get("aliases"), limit=4),
        "first_seen_chapter": _clean_text(row.get("first_seen_chapter")),
        "supporting_scene_count": len(row.get("supporting_scene_ids", [])) if isinstance(row.get("supporting_scene_ids"), list) else 0,
        "notes": _compact_strings(row.get("notes"), limit=3),
    }


def _select_scene_samples(fact_rows: list[dict], *, limit: int) -> list[dict]:
    def score(row: dict) -> tuple[int, tuple[int, Any], str]:
        relationship_count = len(row.get("relationship_changes", []))
        power_count = len(row.get("power_system_notes", []))
        style_count = len(row.get("style_markers", []))
        question_count = len(row.get("open_questions", []))
        scene_score = (style_count * 4) + (power_count * 3) + (relationship_count * 2) + question_count
        return (-scene_score, _chapter_sort_key(row.get("chapter_id")), _clean_text(row.get("scene_id")))

    ranked = sorted(fact_rows, key=score)
    selected = _limit_with_per_group(ranked, limit=limit, group_key="chapter_id", per_group_limit=2)
    return [_compact_scene_signal(row) for row in selected]


def _select_style_samples(style_rows: list[dict], *, limit: int) -> list[dict]:
    ordered = sorted(style_rows, key=lambda row: (_chapter_sort_key(row.get("chapter_ids", [""])[0] if isinstance(row.get("chapter_ids"), list) and row.get("chapter_ids") else ""), _clean_text(row.get("window_id"))))
    return [_compact_style_window(row) for row in _evenly_sample(ordered, limit)]


def _select_chapter_samples(chapter_rows: list[dict], *, limit: int) -> list[dict]:
    ordered = sorted(chapter_rows, key=lambda row: (_chapter_sort_key(row.get("chapter_id")), _clean_text(row.get("chapter_title"))))
    return [_compact_chapter_summary(row) for row in _evenly_sample(ordered, limit)]


def _select_plot_node_samples(plot_rows: list[dict], *, limit: int) -> list[dict]:
    relevance_order = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(
        plot_rows,
        key=lambda row: (
            relevance_order.get(_clean_text(row.get("plot_relevance_hint")).lower(), 9),
            _chapter_sort_key(row.get("chapter_id")),
            _clean_text(row.get("node_id")),
        ),
    )
    if limit <= 0:
        return [_compact_plot_node(row) for row in ordered]
    return [_compact_plot_node(row) for row in ordered[:limit]]


def _select_entity_samples(entity_rows: list[dict], *, limit: int) -> list[dict]:
    type_order = {"character": 0, "faction": 1, "location": 2}
    ordered = sorted(
        entity_rows,
        key=lambda row: (
            type_order.get(_clean_text(row.get("entity_type")), 9),
            -len(row.get("supporting_scene_ids", [])) if isinstance(row.get("supporting_scene_ids"), list) else 0,
            _chapter_sort_key(row.get("first_seen_chapter")),
            _clean_text(row.get("name")),
        ),
    )
    if limit <= 0:
        return [_compact_entity(row) for row in ordered]
    selected = _limit_with_per_group(ordered, limit=limit, group_key="entity_type", per_group_limit=max(limit // 2, 1))
    return [_compact_entity(row) for row in selected]


def _load_required_rows(
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    inputs = load_style_bible_inputs(facts_dir, style_dir, canon_dir)
    return (
        inputs.fact_rows,
        inputs.style_rows,
        inputs.chapter_rows,
        inputs.plot_rows,
        inputs.entity_rows,
        inputs.canon_index,
        inputs.style_index,
        inputs.story_node_scope,
    )


def _build_scope_hint(chapter_ids: list[str], *, story_node_scope: dict[str, Any] | None = None) -> str:
    if story_node_scope:
        start_chapter = _clean_text(story_node_scope.get("start_chapter"))
        end_chapter = _clean_text(story_node_scope.get("end_chapter"))
        label = _clean_text(story_node_scope.get("label"))
        if label and start_chapter and end_chapter:
            return f"{label} ({start_chapter}-{end_chapter}) / facts+style+canon 联合蒸馏"
        if start_chapter and end_chapter:
            return f"第{start_chapter}-{end_chapter}章 / facts+style+canon 联合蒸馏"
    if not chapter_ids:
        return "facts+style+canon 联合蒸馏"
    ordered = sorted({_clean_text(chapter_id) for chapter_id in chapter_ids if _clean_text(chapter_id)}, key=_chapter_sort_key)
    if not ordered:
        return "facts+style+canon 联合蒸馏"
    return f"第{ordered[0]}-{ordered[-1]}章 / facts+style+canon 联合蒸馏"


def _build_style_index_summary(style_index: dict[str, Any]) -> dict[str, Any]:
    def sorted_rows(mapping: Any) -> list[dict[str, Any]]:
        if not isinstance(mapping, dict):
            return []
        rows = [
            {"value": _clean_text(key), "count": int(value or 0)}
            for key, value in mapping.items()
            if _clean_text(key)
        ]
        rows.sort(key=lambda item: (-item["count"], item["value"]))
        return rows[:SIGNAL_SUMMARY_LIMIT]

    def sorted_scalar_rows(path: str, mapping: Any) -> list[dict[str, Any]]:
        spec = scalar_enum_spec_for_path(path)
        if spec is None or not isinstance(mapping, dict):
            return []
        allowed_values = set(spec.allowed_values)
        merged_counts: dict[str, int] = defaultdict(int)
        for key, value in mapping.items():
            canonical_value = canonicalize_scalar_value(path, key)
            canonical_value = _clean_text(canonical_value)
            if canonical_value not in allowed_values:
                continue
            merged_counts[canonical_value] += int(value or 0)
        rows = [
            {"value": value, "count": count}
            for value, count in merged_counts.items()
            if value
        ]
        rows.sort(key=lambda item: (-item["count"], item["value"]))
        return rows[:SIGNAL_SUMMARY_LIMIT]

    scalar_contract_counts = style_index.get("scalar_contract_counts", {})
    scalar_contract_summary = {}
    if isinstance(scalar_contract_counts, dict):
        path_by_key = {
            "perspective": "narrative_system.perspective",
            "distance": "narrative_system.distance",
            "temporality": "narrative_system.temporality",
            "inner_monologue_mode": "voice_contract.inner_monologue_mode",
        }
        scalar_contract_summary = {
            key: sorted_scalar_rows(path_by_key.get(key, ""), value)
            for key, value in scalar_contract_counts.items()
            if isinstance(value, dict) and path_by_key.get(key, "")
        }

    return {
        "window_count": int(style_index.get("window_count", 0) or 0),
        "top_axis_hints": sorted_rows(style_index.get("axis_hint_counts", {})),
        "top_bucket_hints": sorted_rows(style_index.get("bucket_hint_counts", {})),
        "top_routing_targets": sorted_rows(style_index.get("routing_target_counts", {})),
        "top_mechanism_labels": sorted_rows(style_index.get("mechanism_label_counts", {})),
        "top_negative_pitfalls": sorted_rows(style_index.get("negative_pitfall_counts", {})),
        "scalar_contracts": scalar_contract_summary,
    }


def build_style_bible_source_bundle(
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    *,
    output_dir: str | Path,
    scope_label: str | None = None,
    max_style_windows: int = 0,
    max_scene_samples: int = 0,
    max_plot_nodes: int = 0,
    max_chapter_summaries: int = 0,
    max_entity_samples: int = 0,
    inputs: StyleBibleInputBundle | None = None,
) -> dict[str, Any]:
    if inputs is None:
        fact_rows, style_rows, chapter_rows, plot_rows, entity_rows, canon_index, style_index, story_node_scope = _load_required_rows(
            facts_dir,
            style_dir,
            canon_dir,
        )
    else:
        fact_rows = inputs.fact_rows
        style_rows = inputs.style_rows
        chapter_rows = inputs.chapter_rows
        plot_rows = inputs.plot_rows
        entity_rows = inputs.entity_rows
        canon_index = inputs.canon_index
        style_index = inputs.style_index
        story_node_scope = inputs.story_node_scope
    chapter_ids = [row.get("chapter_id", "") for row in chapter_rows]
    scope_hint = scope_label or shared_build_scope_hint(chapter_ids, story_node_scope=story_node_scope)
    resolved_chapter_limit = _resolve_selection_limit(max_chapter_summaries, len(chapter_rows))
    resolved_scene_limit = _resolve_selection_limit(max_scene_samples, len(fact_rows))
    resolved_style_limit = _resolve_selection_limit(max_style_windows, len(style_rows))
    resolved_plot_limit = _resolve_selection_limit(max_plot_nodes, len(plot_rows))
    resolved_entity_limit = _resolve_selection_limit(max_entity_samples, len(entity_rows))

    bundle = {
        "style_bible_id_hint": build_style_id(Path(output_dir).resolve(), story_node_scope=story_node_scope or {}),
        "scope_hint": scope_hint,
        "story_node_scope": story_node_scope or {},
        "corpus_stats": {
            "chapter_count": len(chapter_rows),
            "scene_count": len(fact_rows),
            "style_window_count": len(style_rows),
            "plot_node_count": len(plot_rows),
            "entity_count": len(entity_rows),
            "canon_index": canon_index,
            "style_index": _build_style_index_summary(style_index),
        },
        "sampling": {
            "chapter_summaries": {"available": len(chapter_rows), "selected": resolved_chapter_limit},
            "scene_signal_samples": {"available": len(fact_rows), "selected": resolved_scene_limit},
            "style_window_samples": {"available": len(style_rows), "selected": resolved_style_limit},
            "plot_node_samples": {"available": len(plot_rows), "selected": resolved_plot_limit},
            "entity_samples": {"available": len(entity_rows), "selected": resolved_entity_limit},
        },
        "global_style_signals": _build_style_signal_summary(style_rows),
        "fact_signal_summary": _build_fact_signal_summary(fact_rows),
        "chapter_summaries": _select_chapter_samples(chapter_rows, limit=resolved_chapter_limit),
        "scene_signal_samples": _select_scene_samples(fact_rows, limit=resolved_scene_limit),
        "style_window_samples": _select_style_samples(style_rows, limit=resolved_style_limit),
        "plot_node_samples": _select_plot_node_samples(plot_rows, limit=resolved_plot_limit),
        "entity_samples": _select_entity_samples(entity_rows, limit=resolved_entity_limit),
        "worldbook_atom_candidates": _build_worldbook_atom_candidates(
            fact_rows,
            plot_rows,
            entity_rows,
            chapter_rows,
        ),
    }
    return bundle


def _style_bible_model_name(config: StableProjectConfig) -> str:
    return config.model.style_bible_model or config.model.style_model


def _style_bible_temperature(config: StableProjectConfig) -> float:
    if config.model.style_bible_temperature is None:
        return config.model.style_temperature
    return float(config.model.style_bible_temperature)


def _style_bible_max_output_tokens(config: StableProjectConfig) -> int:
    if config.model.style_bible_max_output_tokens is None:
        return int(config.model.style_max_output_tokens)
    return int(config.model.style_bible_max_output_tokens)


def _mapping_has_content(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


def _request_first_chunk_seconds(request_metrics: dict[str, Any]) -> float | None:
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


def _load_latest_request_metrics_row(metrics_path: Path) -> dict[str, Any]:
    if not metrics_path.exists():
        return {}
    rows = read_jsonl(metrics_path)
    for row in reversed(rows):
        if isinstance(row, dict):
            return row
    return {}


def _recover_bucket_candidate_map(memo_dir: Path) -> tuple[dict[str, int], set[str]]:
    candidate_count_by_batch: dict[str, int] = {}
    memoed_refs: set[str] = set()
    if not memo_dir.exists():
        return candidate_count_by_batch, memoed_refs
    for path in sorted(memo_dir.glob("*.json")):
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        for batch_memo in payload.get("batch_memos", []):
            if not isinstance(batch_memo, dict):
                continue
            batch_id = _clean_text(batch_memo.get("batch_id"))
            rule_candidates = batch_memo.get("rule_candidates", [])
            if batch_id:
                candidate_count_by_batch[batch_id] = len(rule_candidates) if isinstance(rule_candidates, list) else 0
            if not isinstance(rule_candidates, list):
                continue
            for candidate in rule_candidates:
                if not isinstance(candidate, dict):
                    continue
                for ref in candidate.get("evidence_refs", []):
                    cleaned_ref = _clean_text(ref)
                    if cleaned_ref:
                        memoed_refs.add(cleaned_ref)
    return candidate_count_by_batch, memoed_refs


def _recover_bucket_ttft_summary(per_batch_rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured_rows = [
        {
            "bucket_id": _clean_text(row.get("bucket_id")),
            "batch_id": _clean_text(row.get("batch_id")),
            "planner_rank": int(row.get("planner_rank", 0) or 0),
            "first_chunk_seconds": float(row.get("first_chunk_seconds", 0.0) or 0.0),
        }
        for row in per_batch_rows
        if isinstance(row.get("first_chunk_seconds"), (int, float)) and float(row.get("first_chunk_seconds", 0.0) or 0.0) > 0
    ]
    if not measured_rows:
        return {
            "measured_batch_count": 0,
            "overall_avg_ttft_seconds": 0.0,
            "by_bucket": [],
        }

    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in measured_rows:
        by_bucket[row["bucket_id"]].append(row)

    bucket_rows: list[dict[str, Any]] = []
    for bucket_id, bucket_entries in sorted(by_bucket.items()):
        ordered_entries = sorted(
            bucket_entries,
            key=lambda row: (
                int(row.get("planner_rank", 0) or 0),
                _clean_text(row.get("batch_id")),
            ),
        )
        first_seconds = float(ordered_entries[0]["first_chunk_seconds"])
        followup_values = [float(row["first_chunk_seconds"]) for row in ordered_entries[1:]]
        followup_avg = round(sum(followup_values) / len(followup_values), 3) if followup_values else 0.0
        drop_ratio = round((first_seconds - followup_avg) / first_seconds, 4) if first_seconds > 0 and followup_values else 0.0
        bucket_rows.append(
            {
                "bucket_id": bucket_id,
                "batch_count": len(ordered_entries),
                "first_batch_ttft_seconds": round(first_seconds, 3),
                "followup_avg_ttft_seconds": followup_avg,
                "followup_drop_ratio": drop_ratio,
            }
        )

    return {
        "measured_batch_count": len(measured_rows),
        "overall_avg_ttft_seconds": round(
            sum(float(row["first_chunk_seconds"]) for row in measured_rows) / len(measured_rows),
            3,
        ),
        "by_bucket": bucket_rows,
    }


def _recover_bucket_stage_metrics(output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    request_root = output_dir / "_bucket_requests"
    if not request_root.exists():
        return {}, {}

    batch_plan_payload = read_json(output_dir / BATCH_PLAN_FILE) if (output_dir / BATCH_PLAN_FILE).exists() else {}
    planner_rank_by_batch: dict[str, int] = {}
    bucket_id_by_batch: dict[str, str] = {}
    min_rank_by_bucket: dict[str, int] = {}
    if isinstance(batch_plan_payload, dict):
        for batch in batch_plan_payload.get("batches", []):
            if not isinstance(batch, dict):
                continue
            batch_id = _clean_text(batch.get("batch_id"))
            bucket_id = _clean_text(batch.get("bucket_id"))
            planner_rank = int(batch.get("planner_rank", 0) or 0)
            if batch_id:
                planner_rank_by_batch[batch_id] = planner_rank
                bucket_id_by_batch[batch_id] = bucket_id
            if bucket_id and planner_rank:
                current_min_rank = min_rank_by_bucket.get(bucket_id)
                if current_min_rank is None or planner_rank < current_min_rank:
                    min_rank_by_bucket[bucket_id] = planner_rank

    candidate_count_by_batch, memoed_refs = _recover_bucket_candidate_map(output_dir / BUCKET_MEMO_DIR)
    usage_rows: list[dict[str, Any]] = []
    gateway_labels: set[str] = set()
    resumed_bucket_ids: set[str] = set()
    per_batch_rows: list[dict[str, Any]] = []

    for metrics_path in sorted(request_root.glob("*/request_metrics.jsonl")):
        batch_id = metrics_path.parent.name
        request_metrics = _load_latest_request_metrics_row(metrics_path)
        if not request_metrics:
            continue
        bucket_id = bucket_id_by_batch.get(batch_id) or batch_id.partition("__")[0]
        planner_rank = int(planner_rank_by_batch.get(batch_id, 0) or 0)
        first_chunk_seconds = _request_first_chunk_seconds(request_metrics)
        gateway_label = _clean_text(request_metrics.get("gateway_label"))
        if gateway_label:
            gateway_labels.add(gateway_label)
        if bucket_id:
            resumed_bucket_ids.add(bucket_id)
        usage = request_metrics.get("usage_metadata", {})
        if isinstance(usage, dict):
            usage_rows.append(usage)
        per_batch_rows.append(
            {
                "batch_id": batch_id,
                "bucket_id": bucket_id,
                "candidate_count": int(candidate_count_by_batch.get(batch_id, 0) or 0),
                "warmup_batch": bool(bucket_id and planner_rank and planner_rank == min_rank_by_bucket.get(bucket_id)),
                "cache_hit": bool(request_metrics.get("cache_hit")),
                "response_chars": int(request_metrics.get("response_chars", 0) or 0),
                "total_elapsed_seconds": float(request_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
                "first_chunk_seconds": round(first_chunk_seconds, 3) if first_chunk_seconds is not None else None,
                "planner_rank": planner_rank,
                "cache_affinity_key": bucket_id,
                "worker_slot": int(request_metrics.get("worker_slot", -1) or -1),
                "gateway_label": gateway_label,
                "selected_antipattern_codes": [
                    _clean_text(item)
                    for item in request_metrics.get("selected_antipattern_codes", [])
                    if _clean_text(item)
                ]
                if isinstance(request_metrics.get("selected_antipattern_codes"), list)
                else [],
                "anti_pattern_token_estimate": int(request_metrics.get("anti_pattern_token_estimate", 0) or 0),
                "resumed_existing": True,
            }
        )

    if not per_batch_rows:
        return {}, {}

    prompt_tokens = sum(_usage_counter(row, "input_tokens", "prompt_tokens") for row in usage_rows)
    output_tokens = sum(_usage_counter(row, "output_tokens", "completion_tokens") for row in usage_rows)
    total_tokens = sum(
        _usage_counter(row, "total_tokens")
        or (_usage_counter(row, "input_tokens", "prompt_tokens") + _usage_counter(row, "output_tokens", "completion_tokens"))
        for row in usage_rows
    )
    cached_tokens = sum(
        _nested_usage_counter(row, "prompt_tokens_details", "cached_tokens")
        or _nested_usage_counter(row, "input_tokens_details", "cached_tokens")
        for row in usage_rows
    )
    ttft_summary = _recover_bucket_ttft_summary(per_batch_rows)

    request_metrics = {
        "stage": "bucket_memo_synthesis",
        "max_concurrency": len(gateway_labels),
        "warmup_enabled": True,
        "batch_count": len(per_batch_rows),
        "completed_batch_count": len(per_batch_rows),
        "empty_batch_count": len([row for row in per_batch_rows if int(row.get("candidate_count", 0) or 0) <= 0]),
        "warmup_batch_count": len([row for row in per_batch_rows if bool(row.get("warmup_batch"))]),
        "bucket_count": len(resumed_bucket_ids),
        "candidate_count": sum(int(row.get("candidate_count", 0) or 0) for row in per_batch_rows),
        "memoed_ref_count": len(memoed_refs),
        "response_chars": sum(int(row.get("response_chars", 0) or 0) for row in per_batch_rows),
        "total_elapsed_seconds": round(
            sum(float(row.get("total_elapsed_seconds", 0.0) or 0.0) for row in per_batch_rows),
            3,
        ),
        "resumed_bucket_count": len(resumed_bucket_ids),
        "resumed_bucket_ids": sorted(resumed_bucket_ids),
        "worker_assignments": [],
        "per_batch": per_batch_rows,
        "ttft_summary": ttft_summary,
    }
    usage_metadata = {
        "stage": "bucket_memo_synthesis",
        "response_count": len(usage_rows),
        "warmup_batch_count": len([row for row in per_batch_rows if bool(row.get("warmup_batch"))]),
        "cache_hit_count": sum(1 for row in per_batch_rows if bool(row.get("cache_hit"))),
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        "ttft_summary": ttft_summary,
    }
    return request_metrics, usage_metadata


def _recover_reduce_stage_metrics(output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    request_metrics = _load_latest_request_metrics_row(output_dir / "_reduce_request" / "request_metrics.jsonl")
    if request_metrics:
        raw_usage_metadata = request_metrics.get("usage_metadata", {})
        usage_payload = raw_usage_metadata if isinstance(raw_usage_metadata, dict) else {}
        prompt_tokens = _usage_counter(usage_payload, "input_tokens", "prompt_tokens")
        output_tokens = _usage_counter(usage_payload, "output_tokens", "completion_tokens")
        total_tokens = _usage_counter(usage_payload, "total_tokens") or (prompt_tokens + output_tokens)
        cached_tokens = _nested_usage_counter(usage_payload, "prompt_tokens_details", "cached_tokens") or _nested_usage_counter(
            usage_payload,
            "input_tokens_details",
            "cached_tokens",
        )
        first_chunk_seconds = _request_first_chunk_seconds(request_metrics)
        usage_metadata = {
            "stage": "style_bible_reduce",
            "cached_tokens": cached_tokens,
            "prompt_tokens": prompt_tokens,
            "input_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
            "ttft_seconds": round(first_chunk_seconds, 3) if first_chunk_seconds is not None else 0.0,
            "selected_antipattern_codes": [
                _clean_text(item)
                for item in request_metrics.get("selected_antipattern_codes", [])
                if _clean_text(item)
            ]
            if isinstance(request_metrics.get("selected_antipattern_codes"), list)
            else [],
            "anti_pattern_token_budget": int(request_metrics.get("anti_pattern_token_budget", 0) or 0),
            "anti_pattern_token_estimate": int(request_metrics.get("anti_pattern_token_estimate", 0) or 0),
            "raw_usage_metadata": usage_payload,
        }
        return request_metrics, usage_metadata

    local_reduce_root = output_dir / "_local_reduce"
    if not local_reduce_root.exists():
        return {}, {}

    per_bucket_rows: list[dict[str, Any]] = []
    usage_rows: list[dict[str, Any]] = []
    for metrics_path in sorted(local_reduce_root.glob("*/request_metrics.jsonl")):
        local_request_metrics = _load_latest_request_metrics_row(metrics_path)
        if not local_request_metrics:
            continue
        bucket_id = metrics_path.parent.name
        first_chunk_seconds = _request_first_chunk_seconds(local_request_metrics)
        raw_usage_metadata = local_request_metrics.get("usage_metadata", {})
        usage_payload = raw_usage_metadata if isinstance(raw_usage_metadata, dict) else {}
        if usage_payload:
            usage_rows.append(usage_payload)
        per_bucket_rows.append(
            {
                "bucket_id": bucket_id,
                "batch_id": bucket_id,
                "planner_rank": 0,
                "first_chunk_seconds": round(first_chunk_seconds, 3) if first_chunk_seconds is not None else None,
                "response_chars": int(local_request_metrics.get("response_chars", 0) or 0),
                "total_elapsed_seconds": float(local_request_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
                "selected_antipattern_codes": [
                    _clean_text(item)
                    for item in local_request_metrics.get("selected_antipattern_codes", [])
                    if _clean_text(item)
                ]
                if isinstance(local_request_metrics.get("selected_antipattern_codes"), list)
                else [],
            }
        )

    if not per_bucket_rows:
        return {}, {}

    prompt_tokens = sum(_usage_counter(row, "input_tokens", "prompt_tokens") for row in usage_rows)
    output_tokens = sum(_usage_counter(row, "output_tokens", "completion_tokens") for row in usage_rows)
    total_tokens = sum(
        _usage_counter(row, "total_tokens")
        or (_usage_counter(row, "input_tokens", "prompt_tokens") + _usage_counter(row, "output_tokens", "completion_tokens"))
        for row in usage_rows
    )
    cached_tokens = sum(
        _nested_usage_counter(row, "prompt_tokens_details", "cached_tokens")
        or _nested_usage_counter(row, "input_tokens_details", "cached_tokens")
        for row in usage_rows
    )
    ttft_summary = _recover_bucket_ttft_summary(per_bucket_rows)
    reduce_trace = read_json(output_dir / REDUCE_TRACE_FILE) if (output_dir / REDUCE_TRACE_FILE).exists() else {}
    reduce_trace_payload = reduce_trace if isinstance(reduce_trace, dict) else {}

    recovered_request_metrics = {
        "stage": "style_bible_reduce",
        "reduce_mode": _clean_text(reduce_trace_payload.get("reduce_mode")) or "hierarchical",
        "local_reduce_success_count": len(per_bucket_rows),
        "local_reduce_failure_count": len(reduce_trace_payload.get("failed_bucket_ids", []))
        if isinstance(reduce_trace_payload.get("failed_bucket_ids"), list)
        else 0,
        "failed_bucket_ids": reduce_trace_payload.get("failed_bucket_ids", [])
        if isinstance(reduce_trace_payload.get("failed_bucket_ids"), list)
        else [],
        "critical_bucket_ids": reduce_trace_payload.get("critical_bucket_ids", [])
        if isinstance(reduce_trace_payload.get("critical_bucket_ids"), list)
        else [],
        "degraded_success": bool(reduce_trace_payload.get("degraded_success")),
        "semantic_reconcile_sections": reduce_trace_payload.get("semantic_reconcile_sections", [])
        if isinstance(reduce_trace_payload.get("semantic_reconcile_sections"), list)
        else [],
        "total_elapsed_seconds": round(
            sum(float(row.get("total_elapsed_seconds", 0.0) or 0.0) for row in per_bucket_rows),
            3,
        ),
        "response_chars": sum(int(row.get("response_chars", 0) or 0) for row in per_bucket_rows),
        "per_bucket": per_bucket_rows,
        "ttft_summary": ttft_summary,
    }
    recovered_usage_metadata = {
        "stage": "style_bible_reduce",
        "reduce_mode": _clean_text(reduce_trace_payload.get("reduce_mode")) or "hierarchical",
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "overall_cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        "ttft_seconds": float(ttft_summary.get("overall_avg_ttft_seconds", 0.0) or 0.0),
        "ttft_summary": ttft_summary,
    }
    return recovered_request_metrics, recovered_usage_metadata


def _recover_request_and_usage_metadata(
    *,
    output_dir: Path,
    manifest_row: dict[str, Any] | None,
    existing_run_manifest: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_request_metrics = dict(manifest_row.get("request_metrics", {})) if isinstance(manifest_row, dict) and isinstance(manifest_row.get("request_metrics"), dict) else {}
    manifest_usage_metadata = dict(manifest_row.get("usage_metadata", {})) if isinstance(manifest_row, dict) and isinstance(manifest_row.get("usage_metadata"), dict) else {}
    existing_request_metrics = (
        dict(existing_run_manifest.get("request_metrics", {}))
        if isinstance(existing_run_manifest, dict) and isinstance(existing_run_manifest.get("request_metrics"), dict)
        else {}
    )
    existing_usage_metadata = (
        dict(existing_run_manifest.get("usage_metadata", {}))
        if isinstance(existing_run_manifest, dict) and isinstance(existing_run_manifest.get("usage_metadata"), dict)
        else {}
    )

    if _mapping_has_content(existing_request_metrics) and _mapping_has_content(existing_usage_metadata):
        return existing_request_metrics, existing_usage_metadata
    if _mapping_has_content(manifest_request_metrics) and _mapping_has_content(manifest_usage_metadata):
        return manifest_request_metrics, manifest_usage_metadata

    bucket_request_metrics, bucket_usage_metadata = _recover_bucket_stage_metrics(output_dir)
    reduce_request_metrics, reduce_usage_metadata = _recover_reduce_stage_metrics(output_dir)

    recovered_request_metrics: dict[str, Any] = {}
    recovered_usage_metadata: dict[str, Any] = {}
    if _mapping_has_content(bucket_request_metrics) or _mapping_has_content(reduce_request_metrics):
        cache_metrics = _combine_cache_metrics(bucket_usage_metadata, reduce_usage_metadata)
        ttft_summary = _combine_ttft_summary(bucket_request_metrics, reduce_usage_metadata)
        recovered_request_metrics = {
            "total_elapsed_seconds": round(
                float(bucket_request_metrics.get("total_elapsed_seconds", 0.0) or 0.0)
                + float(reduce_request_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
                3,
            ),
            "response_chars": int(bucket_request_metrics.get("response_chars", 0) or 0)
            + int(reduce_request_metrics.get("response_chars", 0) or 0),
            "overall_cache_hit_ratio": cache_metrics.get("overall_cache_hit_ratio", 0.0),
            "cache_metrics": cache_metrics,
            "ttft_summary": ttft_summary,
            "memo_stage": bucket_request_metrics,
            "reduce_stage": reduce_request_metrics,
        }
        recovered_usage_metadata = {
            "prompt_tokens": cache_metrics.get("prompt_tokens", 0),
            "input_tokens": cache_metrics.get("prompt_tokens", 0),
            "output_tokens": cache_metrics.get("output_tokens", 0),
            "total_tokens": cache_metrics.get("total_tokens", 0),
            "cached_tokens": cache_metrics.get("cached_tokens", 0),
            "overall_cache_hit_ratio": cache_metrics.get("overall_cache_hit_ratio", 0.0),
            "ttft_summary": ttft_summary,
            "memo_stage": bucket_usage_metadata,
            "reduce_stage": reduce_usage_metadata,
        }

    final_request_metrics = recovered_request_metrics
    final_usage_metadata = recovered_usage_metadata
    if not _mapping_has_content(final_request_metrics):
        final_request_metrics = existing_request_metrics or manifest_request_metrics
    if not _mapping_has_content(final_usage_metadata):
        final_usage_metadata = existing_usage_metadata or manifest_usage_metadata
    return final_request_metrics, final_usage_metadata


def _backfill_existing_run_manifest(
    config: StableProjectConfig,
    *,
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: Path,
    story_node_scope: dict[str, Any] | None,
    manifest_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    source_bundle_path = output_dir / "style_bible_source_bundle.json"
    style_bible_path = output_dir / "style_bible_final.json"
    routed_index_path = output_dir / ROUTED_INDEX_FILE
    batch_plan_path = output_dir / BATCH_PLAN_FILE
    sampling_report_path = output_dir / SAMPLING_REPORT_FILE
    bucket_memo_dir = output_dir / BUCKET_MEMO_DIR
    local_reduce_dir = output_dir / "_local_reduce"
    reasoning_path = output_dir / "style_bible_reasoning.json"
    export_flat_path = output_dir / "style_bible_export_flat.json"
    reduce_trace_path = output_dir / REDUCE_TRACE_FILE
    if not source_bundle_path.exists() or not style_bible_path.exists():
        return None

    existing_run_manifest_path = output_dir / RUN_MANIFEST_FILE
    existing_run_manifest = read_json(existing_run_manifest_path) if existing_run_manifest_path.exists() else {}
    if not isinstance(existing_run_manifest, dict):
        existing_run_manifest = {}
    source_bundle = read_json(source_bundle_path)
    style_bible_payload = read_json(style_bible_path)
    if not isinstance(source_bundle, dict) or not isinstance(style_bible_payload, dict):
        return None

    scope = story_node_scope or (source_bundle.get("story_node_scope") if isinstance(source_bundle.get("story_node_scope"), dict) else {})
    built_at = utc_iso_from_timestamp(style_bible_path.stat().st_mtime)
    style_id = build_style_id(output_dir, story_node_scope=scope if isinstance(scope, dict) else {})
    payload_changed = False
    if _clean_text(style_bible_payload.get("style_id")) != style_id:
        style_bible_payload["style_id"] = style_id
        payload_changed = True
    if not _clean_text(style_bible_payload.get("scope")):
        style_bible_payload["scope"] = _clean_text(source_bundle.get("scope_hint"))
        payload_changed = True
    if payload_changed:
        write_json(style_bible_path, style_bible_payload)
    request_metrics, usage_metadata = _recover_request_and_usage_metadata(
        output_dir=output_dir,
        manifest_row=manifest_row,
        existing_run_manifest=existing_run_manifest,
    )

    run_manifest = build_style_bible_run_manifest(
        project_root=config.project_root,
        config_path=config.config_path,
        prompt_dir=config.prompt_dir,
        facts_dir=facts_dir,
        style_dir=style_dir,
        canon_dir=canon_dir,
        output_dir=output_dir,
        source_bundle_path=source_bundle_path,
        style_bible_path=style_bible_path,
        style_bible_payload=style_bible_payload,
        source_bundle=source_bundle,
        model_name=_style_bible_model_name(config),
        built_at=built_at,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        story_node_scope=scope if isinstance(scope, dict) else None,
        sampling_mode=_sampling_mode_from_source_bundle(source_bundle),
        routing_mode=ROUTING_MODE_SIGNAL_FUSION_V2 if routed_index_path.exists() else "",
        batching_mode=BATCHING_MODE_BUCKET_AFFINITY_V3 if batch_plan_path.exists() else "",
        sampling_report_path=sampling_report_path if sampling_report_path.exists() else None,
        routed_index_path=routed_index_path if routed_index_path.exists() else None,
        batch_plan_path=batch_plan_path if batch_plan_path.exists() else None,
        prompt_name="style_bible_local_reduce.md",
        extra_output_files={
            **({"bucket_memo_dir": bucket_memo_dir} if bucket_memo_dir.exists() else {}),
            **({"local_reduce_dir": local_reduce_dir} if local_reduce_dir.exists() else {}),
            **({"reasoning_file": reasoning_path} if reasoning_path.exists() else {}),
            **({"export_flat_file": export_flat_path} if export_flat_path.exists() else {}),
            **({"reduce_trace_file": reduce_trace_path} if reduce_trace_path.exists() else {}),
        },
        extra_hashes={
            **({"reasoning_sha256": file_sha256(reasoning_path)} if reasoning_path.exists() else {}),
            **({"export_flat_sha256": file_sha256(export_flat_path)} if export_flat_path.exists() else {}),
            **({"reduce_trace_sha256": file_sha256(reduce_trace_path)} if reduce_trace_path.exists() else {}),
        },
        backfilled_from_existing_output=True,
    )
    write_json(output_dir / RUN_MANIFEST_FILE, run_manifest)
    return run_manifest


def build_style_bible(
    config: StableProjectConfig,
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: str | Path,
    *,
    scope_label: str | None = None,
    max_style_windows: int = 0,
    max_scene_samples: int = 0,
    max_plot_nodes: int = 0,
    max_chapter_summaries: int = 0,
    max_entity_samples: int = 0,
    routing_rules_config: str | Path | None = None,
    batching_rules_config: str | Path | None = None,
    bucket_build_concurrency: int | None = None,
    resume: bool = False,
) -> StyleBibleBuildResult:
    output_path = ensure_dir(output_dir)
    phase_artifacts = _prepare_style_bible_phase01_artifacts(
        facts_dir,
        style_dir,
        canon_dir,
        output_path,
        scope_label=scope_label,
        max_style_windows=max_style_windows,
        max_scene_samples=max_scene_samples,
        max_plot_nodes=max_plot_nodes,
        max_chapter_summaries=max_chapter_summaries,
        max_entity_samples=max_entity_samples,
        routing_rules_config=routing_rules_config,
        batching_rules_config=batching_rules_config,
    )
    source_bundle = phase_artifacts.source_bundle
    source_bundle_path = phase_artifacts.source_bundle_path
    story_node_scope = phase_artifacts.story_node_scope
    if isinstance(story_node_scope, dict) and story_node_scope:
        write_json(output_path / "story_node_scope.json", story_node_scope)

    routed_index_model = StyleBibleRoutedIndex.model_validate(phase_artifacts.routed_index)
    batch_plan_model = StyleBibleBatchPlan.model_validate(phase_artifacts.batch_plan)
    bucket_memo_result = build_style_bible_bucket_memos(
        config,
        facts_dir,
        style_dir,
        canon_dir,
        routed_index_model,
        batch_plan_model,
        output_path,
        max_concurrency=bucket_build_concurrency,
        resume=resume,
    )
    reduce_result = reduce_style_bible_from_bucket_memos(
        config,
        source_bundle,
        bucket_memo_result.bucket_memos,
        output_path,
        resume_local_reduce=resume,
    )
    output_file = reduce_result.output_path
    record = reduce_result.record
    style_id = build_style_id(output_path, story_node_scope=story_node_scope if isinstance(story_node_scope, dict) else {})
    record["style_id"] = style_id
    record["artifact_fingerprint"] = _style_bible_artifact_fingerprint(
        config,
        source_bundle=source_bundle,
        max_style_windows=max_style_windows,
        max_scene_samples=max_scene_samples,
        max_plot_nodes=max_plot_nodes,
        max_chapter_summaries=max_chapter_summaries,
        max_entity_samples=max_entity_samples,
        routing_rules_config=routing_rules_config,
        batching_rules_config=batching_rules_config,
        bucket_build_concurrency=bucket_build_concurrency,
    )
    reasoning_record = dict(reduce_result.reasoning_record)
    reasoning_record["style_id"] = style_id
    export_flat_record = dict(reduce_result.export_flat_record)
    export_flat_record["style_id"] = style_id
    if not _clean_text(record.get("scope")):
        record["scope"] = _clean_text(source_bundle.get("scope_hint"))
    if not _clean_text(reasoning_record.get("scope")):
        reasoning_record["scope"] = _clean_text(source_bundle.get("scope_hint"))
    if not _clean_text(export_flat_record.get("scope")):
        export_flat_record["scope"] = _clean_text(source_bundle.get("scope_hint"))
    write_json(output_file, record)
    if reduce_result.reasoning_path is not None:
        write_json(reduce_result.reasoning_path, reasoning_record)
    if reduce_result.export_flat_path is not None:
        write_json(reduce_result.export_flat_path, export_flat_record)

    reduced_item_ids, reduced_chapter_ids = _refs_to_item_and_chapter_scope(
        phase_artifacts.routed_index,
        reduce_result.reduced_refs,
    )
    selected_item_ids, selected_chapter_ids = _selected_refs_from_source_bundle(source_bundle)
    batched_item_ids, batched_chapter_ids = _batched_refs_from_batch_plan(phase_artifacts.batch_plan)
    cache_metrics = _combine_cache_metrics(bucket_memo_result.usage_metadata, reduce_result.usage_metadata)
    ttft_summary = _combine_ttft_summary(bucket_memo_result.request_metrics, reduce_result.usage_metadata)
    selection_limits = _selection_limits_from_source_bundle(source_bundle)
    sampling_report_model = build_style_bible_sampling_report(
        routed_index_model,
        selected_item_ids=selected_item_ids,
        selected_chapter_ids=selected_chapter_ids,
        batched_item_ids=batched_item_ids,
        batched_chapter_ids=batched_chapter_ids,
        memoed_item_ids=bucket_memo_result.memoed_item_ids,
        memoed_chapter_ids=bucket_memo_result.memoed_chapter_ids,
        reduced_item_ids=reduced_item_ids,
        reduced_chapter_ids=reduced_chapter_ids,
        selection_limits=selection_limits,
        sampling_mode=phase_artifacts.sampling_mode,
        routing_mode=phase_artifacts.routing_mode,
        batching_mode=phase_artifacts.batching_mode,
        batch_plan=batch_plan_model,
        cache_metrics=cache_metrics,
        ttft_summary=ttft_summary,
    )
    sampling_report = sampling_report_model.model_dump(mode="json")
    write_json(phase_artifacts.sampling_report_path, sampling_report)
    write_json(output_dir / COVERAGE_REPORT_FILE, sampling_report)

    request_metrics = {
        "total_elapsed_seconds": round(
            float(bucket_memo_result.request_metrics.get("total_elapsed_seconds", 0.0) or 0.0)
            + float(reduce_result.request_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
            3,
        ),
        "response_chars": int(bucket_memo_result.request_metrics.get("response_chars", 0) or 0)
        + int(reduce_result.request_metrics.get("response_chars", 0) or 0),
        "overall_cache_hit_ratio": cache_metrics.get("overall_cache_hit_ratio", 0.0),
        "cache_metrics": cache_metrics,
        "ttft_summary": ttft_summary,
        "reduce_mode": reduce_result.reduce_mode,
        "failed_bucket_ids": reduce_result.failed_bucket_ids,
        "critical_bucket_ids": reduce_result.critical_bucket_ids,
        "degraded_success": reduce_result.degraded_success,
        "semantic_reconcile_sections": reduce_result.semantic_reconcile_sections,
        "memo_stage": bucket_memo_result.request_metrics,
        "reduce_stage": reduce_result.request_metrics,
    }
    usage_metadata = {
        "prompt_tokens": cache_metrics.get("prompt_tokens", 0),
        "input_tokens": cache_metrics.get("prompt_tokens", 0),
        "output_tokens": cache_metrics.get("output_tokens", 0),
        "total_tokens": cache_metrics.get("total_tokens", 0),
        "cached_tokens": cache_metrics.get("cached_tokens", 0),
        "overall_cache_hit_ratio": cache_metrics.get("overall_cache_hit_ratio", 0.0),
        "ttft_summary": ttft_summary,
        "reduce_mode": reduce_result.reduce_mode,
        "memo_stage": bucket_memo_result.usage_metadata,
        "reduce_stage": reduce_result.usage_metadata,
    }
    built_at = utc_now_iso()
    run_manifest = build_style_bible_run_manifest(
        project_root=config.project_root,
        config_path=config.config_path,
        prompt_dir=config.prompt_dir,
        facts_dir=facts_dir,
        style_dir=style_dir,
        canon_dir=canon_dir,
        output_dir=output_path,
        source_bundle_path=source_bundle_path,
        style_bible_path=output_file,
        style_bible_payload=record,
        source_bundle=source_bundle,
        model_name=_style_bible_model_name(config),
        built_at=built_at,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        story_node_scope=story_node_scope if isinstance(story_node_scope, dict) and story_node_scope else None,
        sampling_mode=phase_artifacts.sampling_mode,
        routing_mode=phase_artifacts.routing_mode,
        batching_mode=phase_artifacts.batching_mode,
        sampling_report_path=phase_artifacts.sampling_report_path,
        routed_index_path=phase_artifacts.routed_index_path,
        batch_plan_path=phase_artifacts.batch_plan_path,
        prompt_name=reduce_result.prompt_name,
        extra_output_files={
            "bucket_memo_dir": bucket_memo_result.memo_dir,
            **({"local_reduce_dir": reduce_result.local_artifact_root} if reduce_result.local_artifact_root else {}),
            "reasoning_file": reduce_result.reasoning_path,
            "export_flat_file": reduce_result.export_flat_path,
            "reduce_trace_file": reduce_result.reduce_trace_path,
        },
        extra_hashes={
            "reasoning_sha256": file_sha256(reduce_result.reasoning_path),
            "export_flat_sha256": file_sha256(reduce_result.export_flat_path),
            "reduce_trace_sha256": file_sha256(reduce_result.reduce_trace_path),
        },
    )
    run_manifest_path = output_path / RUN_MANIFEST_FILE
    write_json(run_manifest_path, run_manifest)
    return StyleBibleBuildResult(
        output_path=output_file,
        reasoning_path=reduce_result.reasoning_path,
        export_flat_path=reduce_result.export_flat_path,
        source_bundle_path=source_bundle_path,
        routed_index_path=phase_artifacts.routed_index_path,
        batch_plan_path=phase_artifacts.batch_plan_path,
        sampling_report_path=phase_artifacts.sampling_report_path,
        bucket_memo_dir_path=bucket_memo_result.memo_dir,
        reduce_trace_path=reduce_result.reduce_trace_path,
        record=record,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        source_bundle=source_bundle,
        routed_index=phase_artifacts.routed_index,
        batch_plan=phase_artifacts.batch_plan,
        sampling_report=sampling_report,
        reduce_trace=reduce_result.reduce_trace,
        sampling_mode=phase_artifacts.sampling_mode,
        routing_mode=phase_artifacts.routing_mode,
        batching_mode=phase_artifacts.batching_mode,
        story_node_scope=story_node_scope if isinstance(story_node_scope, dict) and story_node_scope else None,
        run_manifest_path=run_manifest_path,
        run_manifest=run_manifest,
    )


def run_style_bible_build(
    config: StableProjectConfig,
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: str | Path,
    *,
    scope_label: str | None = None,
    max_style_windows: int = 0,
    max_scene_samples: int = 0,
    max_plot_nodes: int = 0,
    max_chapter_summaries: int = 0,
    max_entity_samples: int = 0,
    routing_rules_config: str | Path | None = None,
    batching_rules_config: str | Path | None = None,
    bucket_build_concurrency: int | None = None,
    resume: bool = False,
) -> StyleBibleBuildResult | None:
    output_path = ensure_dir(output_dir)
    story_node_scope = _load_story_node_scope(canon_dir)
    manifest_path = output_path / "manifest.json"
    failures_path = output_path / "failures.json"
    final_output_path = output_path / "style_bible_final.json"
    tracker = RunTracker(
        stage="stable-build-style-bible",
        output_dir=output_path,
        total_items=1,
        item_label="build",
        source_dir=canon_dir,
        metadata={
            "facts_dir": str(Path(facts_dir).resolve()),
            "style_dir": str(Path(style_dir).resolve()),
            "canon_dir": str(Path(canon_dir).resolve()),
            "model": _style_bible_model_name(config),
            "resume": resume,
            "max_style_windows": max_style_windows,
            "max_scene_samples": max_scene_samples,
            "max_plot_nodes": max_plot_nodes,
            "max_chapter_summaries": max_chapter_summaries,
            "max_entity_samples": max_entity_samples,
            "routing_rules_config": str(Path(routing_rules_config).resolve()) if routing_rules_config else "",
            "batching_rules_config": str(Path(batching_rules_config).resolve()) if batching_rules_config else "",
            "bucket_build_concurrency": int(bucket_build_concurrency or 0),
            "story_node_id": story_node_scope.get("node_id", "") if story_node_scope else "",
            "story_node_start_chapter": story_node_scope.get("start_chapter", "") if story_node_scope else "",
            "story_node_end_chapter": story_node_scope.get("end_chapter", "") if story_node_scope else "",
        },
    )
    manifest_by_key = {
        row["build_id"]: row
        for row in _load_existing_rows(manifest_path, resume=resume)
        if row.get("build_id")
    }
    failures_by_key = {
        row["build_id"]: row
        for row in _load_existing_rows(failures_path, resume=resume)
        if row.get("build_id")
    }

    if resume and final_output_path.exists():
        resume_validation = _validate_existing_style_bible_resume(
            config,
            facts_dir=facts_dir,
            style_dir=style_dir,
            canon_dir=canon_dir,
            output_dir=output_path,
            final_output_path=final_output_path,
            max_style_windows=max_style_windows,
            max_scene_samples=max_scene_samples,
            max_plot_nodes=max_plot_nodes,
            max_chapter_summaries=max_chapter_summaries,
            max_entity_samples=max_entity_samples,
            routing_rules_config=routing_rules_config,
            batching_rules_config=batching_rules_config,
            bucket_build_concurrency=bucket_build_concurrency,
        )
        if not resume_validation.valid:
            tracker.log(
                f"Existing style bible output will be regenerated: {resume_validation.reason}",
                level="warning",
                event="resume_invalid",
                item="style_bible",
                reason=resume_validation.reason,
            )
        else:
            if resume_validation.legacy_without_fingerprint:
                tracker.log(
                    "Skipped legacy style bible output without a complete fingerprint/run manifest.",
                    level="warning",
                    event="resume_legacy_without_fingerprint",
                    item="style_bible",
                )
            existing_row = manifest_by_key.get("style_bible", {})
            run_manifest = resume_validation.run_manifest or _backfill_existing_run_manifest(
                config,
                facts_dir=facts_dir,
                style_dir=style_dir,
                canon_dir=canon_dir,
                output_dir=output_path,
                story_node_scope=story_node_scope,
                manifest_row=existing_row if isinstance(existing_row, dict) else {},
            )
            source_bundle_path = output_path / "style_bible_source_bundle.json"
            routed_index_path = output_path / ROUTED_INDEX_FILE
            batch_plan_path = output_path / BATCH_PLAN_FILE
            sampling_report_path = output_path / SAMPLING_REPORT_FILE
            source_bundle = read_json(source_bundle_path) if source_bundle_path.exists() else {}
            routed_index = read_json(routed_index_path) if routed_index_path.exists() else {}
            batch_plan = read_json(batch_plan_path) if batch_plan_path.exists() else {}
            final_payload = read_json(final_output_path) if final_output_path.exists() else {}
            failures_by_key.pop("style_bible", None)
            manifest_by_key["style_bible"] = {
                "build_id": "style_bible",
                "output_file": final_output_path.name,
                "reasoning_file": "style_bible_reasoning.json" if (output_path / "style_bible_reasoning.json").exists() else "",
                "export_flat_file": "style_bible_export_flat.json" if (output_path / "style_bible_export_flat.json").exists() else "",
                "source_bundle_file": source_bundle_path.name if source_bundle_path.exists() else "",
                "routed_index_file": routed_index_path.name if routed_index_path.exists() else "",
                "batch_plan_file": batch_plan_path.name if batch_plan_path.exists() else "",
                "sampling_report_file": sampling_report_path.name if sampling_report_path.exists() else "",
                "bucket_memo_dir": BUCKET_MEMO_DIR if (output_path / BUCKET_MEMO_DIR).exists() else "",
                "reduce_trace_file": REDUCE_TRACE_FILE if (output_path / REDUCE_TRACE_FILE).exists() else "",
                "status": "skipped_existing",
                "story_node_scope": story_node_scope or {},
                "run_id": run_manifest.get("run_id", "") if isinstance(run_manifest, dict) else "",
                "style_id": run_manifest.get("style_id", "") if isinstance(run_manifest, dict) else "",
                "run_manifest_file": RUN_MANIFEST_FILE if isinstance(run_manifest, dict) else "",
                "scope": run_manifest.get("scope", "") if isinstance(run_manifest, dict) else "",
                "model": _style_bible_model_name(config),
                "sampling_mode": run_manifest.get("sampling_mode", "") if isinstance(run_manifest, dict) else "",
                "routing_mode": run_manifest.get("routing_mode", "") if isinstance(run_manifest, dict) else "",
                "batching_mode": run_manifest.get("batching_mode", "") if isinstance(run_manifest, dict) else "",
                "usage_metadata": run_manifest.get("usage_metadata", {}) if isinstance(run_manifest, dict) else {},
                "request_metrics": run_manifest.get("request_metrics", {}) if isinstance(run_manifest, dict) else {},
                "sampling": source_bundle.get("sampling", {}) if isinstance(source_bundle, dict) else {},
                "corpus_stats": source_bundle.get("corpus_stats", {}) if isinstance(source_bundle, dict) else {},
                "routed_coverage_summary": routed_index.get("coverage_summary", {}) if isinstance(routed_index, dict) else {},
                "batch_coverage_summary": batch_plan.get("coverage_summary", {}) if isinstance(batch_plan, dict) else {},
                "artifact_fingerprint": final_payload.get("artifact_fingerprint", {}) if isinstance(final_payload, dict) else {},
            }
            _write_tracking_files(manifest_path, manifest_by_key, failures_path, failures_by_key)
            tracker.record_skip("style_bible", f"Skipped existing output for {final_output_path.name}.", file_name=final_output_path.name)
            tracker.finish(
                "Stable style bible build skipped.",
                output_file=str(final_output_path.resolve()),
                node_id=story_node_scope.get("node_id", "") if story_node_scope else "",
            )
            return None

    try:
        result = build_style_bible(
            config,
            facts_dir,
            style_dir,
            canon_dir,
            output_path,
            scope_label=scope_label,
            max_style_windows=max_style_windows,
                max_scene_samples=max_scene_samples,
                max_plot_nodes=max_plot_nodes,
                max_chapter_summaries=max_chapter_summaries,
                max_entity_samples=max_entity_samples,
                routing_rules_config=routing_rules_config,
                batching_rules_config=batching_rules_config,
                bucket_build_concurrency=bucket_build_concurrency,
                resume=resume,
            )
        manifest_by_key["style_bible"] = {
            "build_id": "style_bible",
            "output_file": result.output_path.name,
            "reasoning_file": result.reasoning_path.name if result.reasoning_path else "",
            "export_flat_file": result.export_flat_path.name if result.export_flat_path else "",
            "source_bundle_file": result.source_bundle_path.name,
            "routed_index_file": result.routed_index_path.name,
            "batch_plan_file": result.batch_plan_path.name,
            "sampling_report_file": result.sampling_report_path.name,
            "bucket_memo_dir": result.bucket_memo_dir_path.name if result.bucket_memo_dir_path else "",
            "reduce_trace_file": result.reduce_trace_path.name if result.reduce_trace_path else "",
            "style_id": result.record.get("style_id", ""),
            "run_id": result.run_manifest.get("run_id", "") if isinstance(result.run_manifest, dict) else "",
            "run_manifest_file": result.run_manifest_path.name if result.run_manifest_path else "",
            "scope": result.record.get("scope", ""),
            "model": _style_bible_model_name(config),
            "sampling_mode": result.sampling_mode,
            "routing_mode": result.routing_mode,
            "batching_mode": result.batching_mode,
            "usage_metadata": result.usage_metadata,
            "request_metrics": result.request_metrics,
            "sampling": result.source_bundle.get("sampling", {}),
            "corpus_stats": result.source_bundle.get("corpus_stats", {}),
            "routed_coverage_summary": result.routed_index.get("coverage_summary", {}),
            "batch_coverage_summary": result.batch_plan.get("coverage_summary", {}),
            "story_node_scope": result.story_node_scope or {},
            "artifact_fingerprint": result.record.get("artifact_fingerprint", {}),
        }
        failures_by_key.pop("style_bible", None)
        _write_tracking_files(manifest_path, manifest_by_key, failures_path, failures_by_key)
        tracker.record_success(
            "style_bible",
            f"Wrote {result.output_path.name}.",
            output_file=result.output_path.name,
            source_bundle_file=result.source_bundle_path.name,
            style_id=result.record.get("style_id", ""),
            elapsed_seconds=result.request_metrics.get("total_elapsed_seconds"),
            response_chars=result.request_metrics.get("response_chars"),
            node_id=result.story_node_scope.get("node_id", "") if result.story_node_scope else "",
        )
        tracker.finish(
            "Stable style bible build completed.",
            output_file=str(result.output_path.resolve()),
            source_bundle_file=str(result.source_bundle_path.resolve()),
            node_id=result.story_node_scope.get("node_id", "") if result.story_node_scope else "",
        )
        return result
    except Exception as exc:  # noqa: BLE001
        metrics = _extract_request_metrics(exc)
        failures_by_key["style_bible"] = {
            "build_id": "style_bible",
            "output_file": final_output_path.name,
            "model": _style_bible_model_name(config),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "request_metrics": metrics,
            "story_node_scope": story_node_scope or {},
        }
        _write_tracking_files(manifest_path, manifest_by_key, failures_path, failures_by_key)
        tracker.record_failure("style_bible", f"Style bible build failed: {exc}", error_type=type(exc).__name__)
        tracker.fail_run(f"Stable style bible build aborted: {exc}", error_type=type(exc).__name__)
        raise
