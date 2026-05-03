from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
from novel_pipeline_stable.config import GatewayConfig, StableProjectConfig
from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_text
from novel_pipeline_stable.models import (
    StyleBibleBatch,
    StyleBibleBatchPlan,
    StyleBibleBucketBatchMemo,
    StyleBibleBucketMemo,
    StyleBibleBucketRuleCandidate,
    StyleBibleBucketScratchpadStep,
    StyleBibleRoutedIndex,
    StyleBibleRoutedItem,
)
from novel_pipeline_stable.style_bible_contracts import BUCKET_MEMO_DIR, STYLE_BIBLE_BUCKET_MEMO_VERSION
from novel_pipeline_stable.style_bible_inputs import (
    StyleBibleInputBundle,
    chapter_sort_key,
    clean_text,
    load_style_bible_inputs,
)
from novel_pipeline_stable.style_bible_prompt_assembler import assemble_bucket_synthesis_prompt


DEFAULT_BUCKET_BUILD_CONCURRENCY = 6
MIN_BUCKET_BUILD_CONCURRENCY = 4
MAX_BUCKET_BUILD_CONCURRENCY = 7


@dataclass(slots=True)
class BatchMemoTask:
    batch: StyleBibleBatch
    prompt_bundle_xml: str
    allowed_refs: list[str]
    prompt_bundle_path: Path
    system_instruction: str
    user_payload: dict[str, Any]
    selected_antipattern_codes: list[str] = field(default_factory=list)
    anti_pattern_token_budget: int = 0
    anti_pattern_token_estimate: int = 0


@dataclass(slots=True)
class BatchMemoSanitizationAudit:
    raw_scratchpad_count: int = 0
    sanitized_scratchpad_count: int = 0
    dropped_scratchpad_invalid_ref_count: int = 0
    dropped_scratchpad_empty_count: int = 0
    dropped_scratchpad_duplicate_count: int = 0
    raw_candidate_count: int = 0
    sanitized_candidate_count: int = 0
    dropped_candidate_empty_text_count: int = 0
    dropped_candidate_no_allowed_ref_count: int = 0
    merged_candidate_duplicate_count: int = 0
    pruned_candidate_invalid_ref_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def dropped_scratchpad_count(self) -> int:
        return (
            self.dropped_scratchpad_invalid_ref_count
            + self.dropped_scratchpad_empty_count
            + self.dropped_scratchpad_duplicate_count
        )

    @property
    def dropped_candidate_count(self) -> int:
        return (
            self.dropped_candidate_empty_text_count
            + self.dropped_candidate_no_allowed_ref_count
            + self.merged_candidate_duplicate_count
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_scratchpad_count": self.raw_scratchpad_count,
            "sanitized_scratchpad_count": self.sanitized_scratchpad_count,
            "dropped_scratchpad_count": self.dropped_scratchpad_count,
            "dropped_scratchpad_invalid_ref_count": self.dropped_scratchpad_invalid_ref_count,
            "dropped_scratchpad_empty_count": self.dropped_scratchpad_empty_count,
            "dropped_scratchpad_duplicate_count": self.dropped_scratchpad_duplicate_count,
            "raw_candidate_count": self.raw_candidate_count,
            "sanitized_candidate_count": self.sanitized_candidate_count,
            "dropped_candidate_count": self.dropped_candidate_count,
            "dropped_candidate_empty_text_count": self.dropped_candidate_empty_text_count,
            "dropped_candidate_no_allowed_ref_count": self.dropped_candidate_no_allowed_ref_count,
            "merged_candidate_duplicate_count": self.merged_candidate_duplicate_count,
            "pruned_candidate_invalid_ref_count": self.pruned_candidate_invalid_ref_count,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class BatchMemoExecution:
    memo: StyleBibleBucketBatchMemo
    request_metrics: dict[str, Any]
    usage_metadata: dict[str, Any]
    worker_slot: int = -1
    gateway_label: str = ""
    warmup_batch: bool = False
    selected_antipattern_codes: list[str] = field(default_factory=list)
    anti_pattern_token_estimate: int = 0
    sanitization_audit: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BucketWorkerResult:
    worker_slot: int
    gateway_label: str
    bucket_ids: list[str]
    memo_paths: list[Path]
    bucket_memos: list[StyleBibleBucketMemo]
    executions: list[BatchMemoExecution]


@dataclass(slots=True)
class BucketMemoBuildResult:
    memo_dir: Path
    prompt_bundle_dir: Path
    memo_paths: list[Path]
    bucket_memos: list[StyleBibleBucketMemo]
    batch_memos: list[StyleBibleBucketBatchMemo]
    request_metrics: dict[str, Any]
    usage_metadata: dict[str, Any]
    memoed_item_ids: set[str]
    memoed_chapter_ids: set[str]
    memoed_refs: set[str]


def _normalize_workers(max_concurrency: int | None) -> int:
    if max_concurrency is None:
        return DEFAULT_BUCKET_BUILD_CONCURRENCY
    return max(MIN_BUCKET_BUILD_CONCURRENCY, min(MAX_BUCKET_BUILD_CONCURRENCY, int(max_concurrency)))


def _effective_worker_count(worker_target: int, bucket_count: int) -> int:
    if bucket_count <= 0:
        return 0
    return max(1, min(worker_target, bucket_count))


def _gateway_pool(config: StableProjectConfig) -> list[GatewayConfig]:
    if config.gateways:
        return list(config.gateways)
    return [GatewayConfig(label="primary", api_key=config.api_key, base_url=config.base_url)]


def _pin_gateway_config(config: StableProjectConfig, gateway: GatewayConfig) -> StableProjectConfig:
    base = config.as_project_config().model_copy(deep=True)
    base.gateways = [gateway]
    base.api_key = gateway.api_key
    base.base_url = gateway.base_url
    return StableProjectConfig(
        base=base,
        stability=config.stability.model_copy(deep=True),
        config_path=config.config_path,
    )


def _contiguous_bucket_assignments(
    bucket_ids: list[str],
    *,
    worker_count: int,
) -> list[list[str]]:
    if worker_count <= 0:
        return []
    assignments: list[list[str]] = [[] for _ in range(worker_count)]
    bucket_count = len(bucket_ids)
    for index, bucket_id in enumerate(bucket_ids):
        slot = min((index * worker_count) // max(bucket_count, 1), worker_count - 1)
        assignments[slot].append(bucket_id)
    return [assignment for assignment in assignments if assignment]


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


def _quote_attr(value: Any) -> str:
    return escape(clean_text(value), {'"': "&quot;"})


def _xml_text(value: Any) -> str:
    return escape(clean_text(value))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", clean_text(value).lower()).strip("_")
    return slug or "memo"


def _normalize_text_key(value: str) -> str:
    return re.sub(r"\s+", "", clean_text(value))


def _normalize_antipattern_codes(values: Iterable[Any]) -> list[str]:
    codes = _unique_strings(values)
    meaningful_codes = [code for code in codes if code != "none"]
    return meaningful_codes or ["none"]


def _format_fact_rows(row: dict[str, Any], *, limit: int = 4) -> list[str]:
    results: list[str] = []
    for fact in row.get("facts", []):
        if not isinstance(fact, dict):
            continue
        subject = clean_text(fact.get("subject"))
        predicate = clean_text(fact.get("predicate"))
        obj = clean_text(fact.get("object"))
        parts = [part for part in (subject, predicate, obj) if part]
        if parts:
            results.append(" | ".join(parts))
        if len(results) >= limit:
            break
    return results


def _format_event_rows(row: dict[str, Any], *, limit: int = 2) -> list[str]:
    results: list[str] = []
    for event in row.get("events", []):
        if not isinstance(event, dict):
            continue
        name = clean_text(event.get("name"))
        summary = _truncate_text(event.get("summary"), limit=90)
        parts = [part for part in (name, summary) if part]
        if parts:
            results.append(" | ".join(parts))
        if len(results) >= limit:
            break
    return results


def _format_relationship_rows(row: dict[str, Any], *, limit: int = 2) -> list[str]:
    results: list[str] = []
    for change in row.get("relationship_changes", []):
        if not isinstance(change, dict):
            continue
        source = clean_text(change.get("source"))
        relation = clean_text(change.get("relation"))
        target = clean_text(change.get("target"))
        delta = _truncate_text(change.get("change"), limit=84)
        core = " -> ".join(part for part in (source, target) if part)
        parts = [part for part in (core, relation, delta) if part]
        if parts:
            results.append(" | ".join(parts))
        if len(results) >= limit:
            break
    return results


def _format_note_rows(row: dict[str, Any], *, limit: int = 3) -> list[str]:
    results: list[str] = []
    for note in row.get("power_system_notes", []):
        if not isinstance(note, dict):
            continue
        topic = clean_text(note.get("topic"))
        body = _truncate_text(note.get("note"), limit=90)
        parts = [part for part in (topic, body) if part]
        if parts:
            results.append(" | ".join(parts))
        if len(results) >= limit:
            break
    return results


def _format_marker_rows(row: dict[str, Any], *, limit: int = 3) -> list[str]:
    results: list[str] = []
    for marker in row.get("style_markers", []):
        if not isinstance(marker, dict):
            continue
        name = clean_text(marker.get("marker"))
        body = _truncate_text(marker.get("explanation"), limit=90)
        parts = [part for part in (name, body) if part]
        if parts:
            results.append(" | ".join(parts))
        if len(results) >= limit:
            break
    return results


def _format_style_values(row: dict[str, Any], field_name: str, *, limit: int, item_limit: int = 88) -> list[str]:
    values = row.get(field_name, [])
    if not isinstance(values, list):
        return []
    return [_truncate_text(value, limit=item_limit) for value in _unique_strings(values, limit=limit)]


def _truncate_text(value: Any, *, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."


def _format_style_rule_rows(row: dict[str, Any], field_name: str, *, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in row.get(field_name, []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "mechanism_label": clean_text(item.get("mechanism_label")),
                "execution_logic": _truncate_text(item.get("execution_logic"), limit=180),
                "trigger": _truncate_text(item.get("trigger"), limit=96),
                "constraint": _truncate_text(item.get("constraint"), limit=96),
                "evidence_ids": _unique_strings(item.get("evidence_ids", []), limit=4),
            }
        )
        if len(results) >= limit:
            break
    return results


def _format_style_hint_rows(row: dict[str, Any], field_name: str, *, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in row.get(field_name, []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "axis_id": clean_text(item.get("axis_id")),
                "bucket_id": clean_text(item.get("bucket_id")),
                "query_feature_matcher": _truncate_text(item.get("query_feature_matcher"), limit=120),
                "route_target_action": _truncate_text(item.get("route_target_action"), limit=120),
                "evidence_ids": _unique_strings(item.get("evidence_ids", []), limit=4),
            }
        )
        if len(results) >= limit:
            break
    return results


def _format_style_pitfall_rows(row: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in row.get("negative_pitfalls", []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "forbidden_action": _truncate_text(item.get("forbidden_action"), limit=120),
                "correction_guideline": _truncate_text(item.get("correction_guideline"), limit=120),
                "evidence_ids": _unique_strings(item.get("evidence_ids", []), limit=4),
            }
        )
        if len(results) >= limit:
            break
    return results


def _format_style_evidence_rows(row: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in row.get("evidence_index", []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "evidence_id": clean_text(item.get("evidence_id")),
                "source_ref": clean_text(item.get("source_ref")),
                "quote": _truncate_text(item.get("quote"), limit=140),
            }
        )
        if len(results) >= limit:
            break
    return results


def _metric_attrs(item: StyleBibleRoutedItem) -> str:
    metric_pairs = {
        "evidence_density": item.features.evidence_density,
        "resource_pressure_density": item.features.resource_pressure_density,
        "institution_density": item.features.institution_density,
        "body_modification_density": item.features.body_modification_density,
        "dark_humor_signal": item.features.dark_humor_signal,
        "contract_signal": item.features.contract_signal,
        "conflict_intensity": item.features.conflict_intensity,
        "voice_novelty": item.features.voice_novelty,
    }
    return " ".join(f'{key}="{value:.4f}"' for key, value in metric_pairs.items())


def _item_summary_xml(
    item: StyleBibleRoutedItem,
    *,
    scene_row: dict[str, Any] | None,
    style_row: dict[str, Any] | None,
) -> list[str]:
    lines: list[str] = []
    if item.item_type == "scene" and scene_row is not None:
        lines.append(
            "      "
            + f'<scene ref="{_quote_attr(item.source_ref or item.item_id)}" '
            + f'chapter_id="{_quote_attr(item.primary_chapter_id)}" '
            + f'axes="{_quote_attr(",".join(item.axes))}" '
            + f'batch_score="{max(item.features.evidence_density, 0.0):.4f}" '
            + f"{_metric_attrs(item)}>"
        )
        lines.append(f"        <summary>{_xml_text(_truncate_text(scene_row.get('scene_summary'), limit=180))}</summary>")
        facts = _format_fact_rows(scene_row)
        if facts:
            lines.append("        <facts>")
            for value in facts:
                lines.append(f"          <fact>{_xml_text(value)}</fact>")
            lines.append("        </facts>")
        events = _format_event_rows(scene_row)
        if events:
            lines.append("        <events>")
            for value in events:
                lines.append(f"          <event>{_xml_text(value)}</event>")
            lines.append("        </events>")
        notes = _format_note_rows(scene_row)
        if notes:
            lines.append("        <power_notes>")
            for value in notes:
                lines.append(f"          <note>{_xml_text(value)}</note>")
            lines.append("        </power_notes>")
        markers = _format_marker_rows(scene_row)
        if markers:
            lines.append("        <style_markers>")
            for value in markers:
                lines.append(f"          <marker>{_xml_text(value)}</marker>")
            lines.append("        </style_markers>")
        relationships = _format_relationship_rows(scene_row)
        if relationships:
            lines.append("        <relationships>")
            for value in relationships:
                lines.append(f"          <relationship>{_xml_text(value)}</relationship>")
            lines.append("        </relationships>")
        open_questions = _unique_strings(scene_row.get("open_questions", []), limit=2)
        if open_questions:
            lines.append("        <open_questions>")
            for value in open_questions:
                lines.append(f"          <question>{_xml_text(_truncate_text(value, limit=80))}</question>")
            lines.append("        </open_questions>")
        lines.append("      </scene>")
        return lines

    if item.item_type == "style_window" and style_row is not None:
        chapter_ids = _unique_strings(style_row.get("chapter_ids", []), limit=8)
        lines.append(
            "      "
            + f'<style_window ref="{_quote_attr(item.source_ref or item.item_id)}" '
            + f'chapter_ids="{_quote_attr(",".join(chapter_ids))}" '
            + f'axes="{_quote_attr(",".join(item.axes))}" '
            + f"{_metric_attrs(item)}>"
        )
        lines.append(f"        <summary>{_xml_text(_truncate_text(item.summary, limit=180))}</summary>")
        scalar_contracts = style_row.get("scalar_contracts", {})
        if isinstance(scalar_contracts, dict):
            scalar_rows = {
                key: clean_text(scalar_contracts.get(key))
                for key in ("perspective", "distance", "temporality", "inner_monologue_mode")
                if clean_text(scalar_contracts.get(key)) and clean_text(scalar_contracts.get(key)) != "unspecified"
            }
            if scalar_rows:
                lines.append("        <scalar_contracts>")
                for key, value in scalar_rows.items():
                    lines.append(f"          <{key}>{_xml_text(value)}</{key}>")
                lines.append("        </scalar_contracts>")
        surface_markers = _format_style_values(style_row, "surface_markers", limit=4)
        if surface_markers:
            lines.append("        <surface_markers>")
            for value in surface_markers:
                lines.append(f"          <item>{_xml_text(value)}</item>")
            lines.append("        </surface_markers>")
        for section_name, field_name, limit in (
            ("narrative_engine_rules", "narrative_engine_rules", 2),
            ("pacing_rules", "pacing_rules", 2),
            ("plot_node_logic_rules", "plot_node_logic_rules", 2),
            ("description_rules", "description_rules", 2),
            ("dialogue_rules", "dialogue_rules", 2),
            ("characterization_rules", "characterization_rules", 2),
            ("sensory_rules", "sensory_rules", 2),
            ("humor_rules", "humor_rules", 2),
            ("satire_rules", "satire_rules", 2),
            ("nonstandard_xianxia_rules", "nonstandard_xianxia_rules", 2),
            ("narrator_voice_rules", "narrator_voice_rules", 2),
            ("register_mix_rules", "register_mix_rules", 2),
        ):
            rules = _format_style_rule_rows(style_row, field_name, limit=limit)
            if not rules:
                continue
            lines.append(f"        <{section_name}>")
            for rule in rules:
                evidence_ids = ",".join(rule["evidence_ids"])
                lines.append(
                    f'          <rule label="{_quote_attr(rule["mechanism_label"])}" '
                    f'evidence_ids="{_quote_attr(evidence_ids)}">'
                )
                if rule["execution_logic"]:
                    lines.append(f"            <execution_logic>{_xml_text(rule['execution_logic'])}</execution_logic>")
                if rule["trigger"]:
                    lines.append(f"            <trigger>{_xml_text(rule['trigger'])}</trigger>")
                if rule["constraint"]:
                    lines.append(f"            <constraint>{_xml_text(rule['constraint'])}</constraint>")
                lines.append("          </rule>")
            lines.append(f"        </{section_name}>")
        for section_name, field_name, limit in (
            ("rag_candidates", "rag_candidates", 2),
            ("worldbook_candidates", "worldbook_candidates", 2),
            ("routing_hints", "routing_hints", 3),
        ):
            hints = _format_style_hint_rows(style_row, field_name, limit=limit)
            if not hints:
                continue
            lines.append(f"        <{section_name}>")
            for hint in hints:
                evidence_ids = ",".join(hint["evidence_ids"])
                lines.append(
                    f'          <hint axis_id="{_quote_attr(hint["axis_id"])}" '
                    f'bucket_id="{_quote_attr(hint["bucket_id"])}" '
                    f'evidence_ids="{_quote_attr(evidence_ids)}">'
                )
                if hint["query_feature_matcher"]:
                    lines.append(
                        f"            <query_feature_matcher>{_xml_text(hint['query_feature_matcher'])}</query_feature_matcher>"
                    )
                if hint["route_target_action"]:
                    lines.append(
                        f"            <route_target_action>{_xml_text(hint['route_target_action'])}</route_target_action>"
                    )
                lines.append("          </hint>")
            lines.append(f"        </{section_name}>")
        axis_hints = _format_style_hint_rows(style_row, "axis_hints", limit=4)
        if axis_hints:
            lines.append("        <axis_hints>")
            for hint in axis_hints:
                lines.append(
                    f'          <axis_hint axis_id="{_quote_attr(hint["axis_id"])}" '
                    f'evidence_ids="{_quote_attr(",".join(hint["evidence_ids"]))}" />'
                )
            lines.append("        </axis_hints>")
        bucket_hints = _format_style_hint_rows(style_row, "bucket_hints", limit=4)
        if bucket_hints:
            lines.append("        <bucket_hints>")
            for hint in bucket_hints:
                lines.append(
                    f'          <bucket_hint bucket_id="{_quote_attr(hint["bucket_id"])}" '
                    f'evidence_ids="{_quote_attr(",".join(hint["evidence_ids"]))}" />'
                )
            lines.append("        </bucket_hints>")
        pitfalls = _format_style_pitfall_rows(style_row, limit=2)
        if pitfalls:
            lines.append("        <negative_pitfalls>")
            for pitfall in pitfalls:
                lines.append(
                    f'          <pitfall evidence_ids="{_quote_attr(",".join(pitfall["evidence_ids"]))}">'
                )
                if pitfall["forbidden_action"]:
                    lines.append(f"            <forbidden_action>{_xml_text(pitfall['forbidden_action'])}</forbidden_action>")
                if pitfall["correction_guideline"]:
                    lines.append(
                        f"            <correction_guideline>{_xml_text(pitfall['correction_guideline'])}</correction_guideline>"
                    )
                lines.append("          </pitfall>")
            lines.append("        </negative_pitfalls>")
        evidence_rows = _format_style_evidence_rows(style_row, limit=4)
        if evidence_rows:
            lines.append("        <evidence_index>")
            for evidence in evidence_rows:
                lines.append(
                    f'          <evidence id="{_quote_attr(evidence["evidence_id"])}" '
                    f'source_ref="{_quote_attr(evidence["source_ref"])}">'
                )
                if evidence["quote"]:
                    lines.append(f"            <quote>{_xml_text(evidence['quote'])}</quote>")
                lines.append("          </evidence>")
            lines.append("        </evidence_index>")
        lines.append("      </style_window>")
        return lines

    return lines


def _lookup_maps(inputs: StyleBibleInputBundle) -> dict[str, Any]:
    scene_rows = {
        f"scene:{clean_text(row.get('scene_id'))}": row
        for row in inputs.fact_rows
        if clean_text(row.get("scene_id"))
    }
    style_rows = {
        clean_text(row.get("window_id")): row
        for row in inputs.style_rows
        if clean_text(row.get("window_id"))
    }
    chapter_rows = {
        clean_text(row.get("chapter_id")): row
        for row in inputs.chapter_rows
        if clean_text(row.get("chapter_id"))
    }
    plot_rows = {
        clean_text(row.get("node_id")): row
        for row in inputs.plot_rows
        if clean_text(row.get("node_id"))
    }
    entity_rows = {
        clean_text(row.get("entity_id")): row
        for row in inputs.entity_rows
        if clean_text(row.get("entity_id"))
    }
    return {
        "scene_rows": scene_rows,
        "style_rows": style_rows,
        "chapter_rows": chapter_rows,
        "plot_rows": plot_rows,
        "entity_rows": entity_rows,
    }


def _bucket_summary_map(batch_plan: StyleBibleBatchPlan) -> dict[str, dict[str, Any]]:
    return {
        summary.bucket_id: summary.model_dump(mode="json")
        for summary in batch_plan.bucket_summaries
    }


def _routed_item_map(routed_index: StyleBibleRoutedIndex) -> dict[str, StyleBibleRoutedItem]:
    return {item.item_id: item for item in routed_index.items}


def _bucket_catalog_map(routed_index: StyleBibleRoutedIndex) -> dict[str, dict[str, Any]]:
    return {
        row.id: row.model_dump(mode="json")
        for row in routed_index.bucket_catalog
    }


def _bucket_static_axis_focus(
    bucket_id: str,
    *,
    bucket_summary_by_id: dict[str, dict[str, Any]],
    bucket_catalog_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    summary_axes = [
        clean_text(item)
        for item in bucket_summary_by_id.get(bucket_id, {}).get("axis_ids", [])
        if clean_text(item)
    ]
    if summary_axes:
        return summary_axes
    catalog_axes = [
        clean_text(item)
        for item in bucket_catalog_by_id.get(bucket_id, {}).get("primary_axes", [])
        if clean_text(item)
    ]
    if catalog_axes:
        return catalog_axes
    # Static prompt sections must stay bucket-stable for cache reuse.
    # If we cannot resolve bucket-level axes from summary/catalog, prefer an
    # empty static axis list over leaking batch-specific axis_focus upward.
    return []


def _chapter_context_xml(batch: StyleBibleBatch, chapter_rows: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    chapter_ids = [chapter_id for chapter_id in batch.chapter_ids if chapter_id in chapter_rows][:4]
    if not chapter_ids:
        return lines
    lines.append("    <chapter_context>")
    for chapter_id in chapter_ids:
        row = chapter_rows[chapter_id]
        lines.append(
            "      "
            + f'<chapter chapter_id="{_quote_attr(chapter_id)}" '
            + f'title="{_quote_attr(_truncate_text(row.get("chapter_title"), limit=60))}" '
            + f'scene_count="{int(row.get("scene_count", 0) or 0)}">'
        )
        scene_summaries = _unique_strings(row.get("scene_summaries", []), limit=2)
        if scene_summaries:
            lines.append("        <scene_summaries>")
            for summary in scene_summaries:
                lines.append(f"          <summary>{_xml_text(_truncate_text(summary, limit=90))}</summary>")
            lines.append("        </scene_summaries>")
        lines.append("      </chapter>")
    lines.append("    </chapter_context>")
    return lines


def _support_context_xml(
    batch: StyleBibleBatch,
    *,
    plot_rows: dict[str, dict[str, Any]],
    entity_rows: dict[str, dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    plot_node_ids = _unique_strings(batch.support_refs.get("plot_node_ids", []), limit=4)
    entity_ids = _unique_strings(batch.support_refs.get("entity_ids", []), limit=6)
    if plot_node_ids:
        lines.append("    <plot_node_context>")
        for node_id in plot_node_ids:
            row = plot_rows.get(node_id, {})
            lines.append(
                "      "
                + f'<plot_node node_id="{_quote_attr(node_id)}" '
                + f'chapter_id="{_quote_attr(row.get("chapter_id"))}">'
            )
            title = _truncate_text(row.get("title"), limit=60)
            summary = _truncate_text(row.get("summary"), limit=100)
            if title:
                lines.append(f"        <title>{_xml_text(title)}</title>")
            if summary:
                lines.append(f"        <summary>{_xml_text(summary)}</summary>")
            lines.append("      </plot_node>")
        lines.append("    </plot_node_context>")
    if entity_ids:
        lines.append("    <entity_context>")
        for entity_id in entity_ids:
            row = entity_rows.get(entity_id, {})
            lines.append(
                "      "
                + f'<entity entity_id="{_quote_attr(entity_id)}" '
                + f'name="{_quote_attr(row.get("name"))}" '
                + f'entity_type="{_quote_attr(row.get("entity_type"))}" />'
            )
        lines.append("    </entity_context>")
    return lines


def _build_batch_prompt_bundle_xml(
    batch: StyleBibleBatch,
    *,
    routed_item_by_id: dict[str, StyleBibleRoutedItem],
    bucket_summary_by_id: dict[str, dict[str, Any]],
    bucket_catalog_by_id: dict[str, dict[str, Any]],
    lookup_maps: dict[str, Any],
    scope_hint: str,
) -> tuple[str, list[str]]:
    scene_rows: dict[str, dict[str, Any]] = lookup_maps["scene_rows"]
    style_rows: dict[str, dict[str, Any]] = lookup_maps["style_rows"]
    chapter_rows: dict[str, dict[str, Any]] = lookup_maps["chapter_rows"]
    plot_rows: dict[str, dict[str, Any]] = lookup_maps["plot_rows"]
    entity_rows: dict[str, dict[str, Any]] = lookup_maps["entity_rows"]

    allowed_refs = _unique_strings(item.source_ref or item.item_id for item in batch.items)
    bucket_summary = bucket_summary_by_id.get(batch.bucket_id, {})
    bucket_catalog = bucket_catalog_by_id.get(batch.bucket_id, {})

    lines: list[str] = ['<style_bible_bucket_prompt scope_hint="' + _quote_attr(scope_hint) + '">']
    lines.append(
        "  "
        + f'<bucket id="{_quote_attr(batch.bucket_id)}" '
        + f'label="{_quote_attr(batch.label)}" '
        + f'description="{_quote_attr(bucket_catalog.get("description"))}" '
        + f'axis_focus="{_quote_attr(",".join(batch.axis_focus))}">'
    )
    lines.append(
        "    "
        + f'<batch id="{_quote_attr(batch.batch_id)}" '
        + f'estimated_tokens="{int(batch.estimated_tokens)}" '
        + f'token_budget="{int(batch.token_budget)}" '
        + f'scene_count="{int(batch.scene_count)}" '
        + f'style_window_count="{int(batch.style_window_count)}" '
        + f'batch_score="{float(batch.batch_score):.4f}" '
        + f'novelty_score="{float(batch.novelty_score):.4f}" '
        + f'redundancy_penalty="{float(batch.redundancy_penalty):.4f}" />'
    )
    if bucket_summary:
        lines.append(
            "    "
            + f'<bucket_coverage selected_item_count="{int(bucket_summary.get("selected_item_count", 0) or 0)}" '
            + f'batch_count="{len(bucket_summary.get("batch_ids", []))}" '
            + f'axis_ids="{_quote_attr(",".join(_unique_strings(bucket_summary.get("axis_ids", []))))}" />'
        )
    lines.append("    <allowed_refs>")
    for ref in allowed_refs:
        lines.append(f'      <allowed_ref value="{_quote_attr(ref)}" />')
    lines.append("    </allowed_refs>")
    lines.extend(_chapter_context_xml(batch, chapter_rows))
    lines.extend(_support_context_xml(batch, plot_rows=plot_rows, entity_rows=entity_rows))
    lines.append("    <items>")
    ordered_items = sorted(
        batch.items,
        key=lambda row: (
            chapter_sort_key(row.chapter_ids[0] if row.chapter_ids else ""),
            row.item_type,
            row.item_id,
        ),
    )
    for batch_item in ordered_items:
        item = routed_item_by_id.get(batch_item.item_id)
        if item is None:
            continue
        lines.extend(
            _item_summary_xml(
                item,
                scene_row=scene_rows.get(item.item_id),
                style_row=style_rows.get(item.item_id),
            )
        )
    lines.append("    </items>")
    lines.append("  </bucket>")
    lines.append("</style_bible_bucket_prompt>")
    return "\n".join(lines) + "\n", allowed_refs


def _sanitize_batch_memo(
    memo: StyleBibleBucketBatchMemo,
    *,
    task: BatchMemoTask,
) -> tuple[StyleBibleBucketBatchMemo, BatchMemoSanitizationAudit]:
    allowed_ref_set = set(task.allowed_refs)
    audit = BatchMemoSanitizationAudit(
        raw_scratchpad_count=len(memo.scratchpad),
        raw_candidate_count=len(memo.rule_candidates),
    )
    normalized_scratchpad: list[StyleBibleBucketScratchpadStep] = []
    scratchpad_seen: set[tuple[str, str, str]] = set()
    for row in memo.scratchpad:
        target_ref = clean_text(row.target_ref)
        if target_ref not in allowed_ref_set:
            audit.dropped_scratchpad_invalid_ref_count += 1
            continue
        exact_quote = clean_text(row.exact_quote)
        structural_analysis = clean_text(row.structural_analysis)
        if not exact_quote and not structural_analysis:
            audit.dropped_scratchpad_empty_count += 1
            continue
        key = (
            target_ref,
            _normalize_text_key(exact_quote),
            _normalize_text_key(structural_analysis),
        )
        if key in scratchpad_seen:
            audit.dropped_scratchpad_duplicate_count += 1
            continue
        scratchpad_seen.add(key)
        normalized_scratchpad.append(
            StyleBibleBucketScratchpadStep(
                step=clean_text(row.step) or "1. 锁定原始证据",
                target_ref=target_ref,
                exact_quote=exact_quote,
                structural_analysis=structural_analysis,
            )
        )

    normalized_candidates: list[StyleBibleBucketRuleCandidate] = []
    candidate_index_by_key: dict[str, int] = {}

    for candidate in memo.rule_candidates:
        text = clean_text(candidate.text)
        if not text:
            audit.dropped_candidate_empty_text_count += 1
            continue
        unique_refs = _unique_strings(candidate.evidence_refs)
        audit.pruned_candidate_invalid_ref_count += sum(1 for ref in unique_refs if ref not in allowed_ref_set)
        refs = [ref for ref in unique_refs if ref in allowed_ref_set]
        if not refs:
            audit.dropped_candidate_no_allowed_ref_count += 1
            continue
        key = _normalize_text_key(text)
        if key in candidate_index_by_key:
            current = normalized_candidates[candidate_index_by_key[key]]
            current.evidence_refs = _unique_strings([*current.evidence_refs, *refs])
            current.anti_pattern_codes = _normalize_antipattern_codes(
                [*current.anti_pattern_codes, *candidate.anti_pattern_codes]
            )
            audit.merged_candidate_duplicate_count += 1
            continue
        normalized_candidates.append(
            StyleBibleBucketRuleCandidate(
                candidate_id=clean_text(candidate.candidate_id)
                or f"{_slugify(task.batch.bucket_id)}_rule_{len(normalized_candidates) + 1:02d}",
                text=text,
                trigger_condition=clean_text(candidate.trigger_condition),
                execution_action=clean_text(candidate.execution_action),
                evidence_refs=refs,
                anti_pattern_codes=_normalize_antipattern_codes(candidate.anti_pattern_codes),
            )
        )
        candidate_index_by_key[key] = len(normalized_candidates) - 1

    audit.sanitized_scratchpad_count = len(normalized_scratchpad)
    audit.sanitized_candidate_count = len(normalized_candidates)
    if audit.raw_candidate_count > 0 and audit.sanitized_candidate_count == 0 and audit.dropped_candidate_no_allowed_ref_count > 0:
        audit.warnings.append("all_rule_candidates_removed_by_allowed_refs")
    if audit.pruned_candidate_invalid_ref_count > 0:
        audit.warnings.append("invalid_evidence_refs_pruned")

    sanitized = StyleBibleBucketBatchMemo(
        scratchpad=normalized_scratchpad[:12],
        memo_id=clean_text(memo.memo_id) or f"{task.batch.batch_id}__memo",
        bucket_id=task.batch.bucket_id,
        batch_id=task.batch.batch_id,
        label=task.batch.label,
        axis_focus=_unique_strings(task.batch.axis_focus),
        chapter_ids=sorted(_unique_strings(task.batch.chapter_ids), key=chapter_sort_key),
        item_ids=_unique_strings(task.batch.item_ids),
        allowed_refs=list(task.allowed_refs),
        rule_candidates=normalized_candidates[:6],
    )
    audit.sanitized_scratchpad_count = len(sanitized.scratchpad)
    audit.sanitized_candidate_count = len(sanitized.rule_candidates)
    return sanitized, audit


def _should_retry_after_sanitization(audit: BatchMemoSanitizationAudit) -> bool:
    return (
        audit.raw_candidate_count > 0
        and audit.sanitized_candidate_count == 0
        and audit.dropped_candidate_no_allowed_ref_count > 0
    )


def _retry_guardrail_suffix(task: BatchMemoTask) -> str:
    allowed_refs = ", ".join(task.allowed_refs[:48])
    return (
        "\n\n[System Retry Guardrail]\n"
        "Previous output was blocked because evidence_refs used refs outside allowed_refs.\n"
        "You may copy only the exact ref values that already exist in the input XML allowed_refs list.\n"
        "Do not paraphrase refs. Do not invent refs. If you cannot ground a rule with allowed refs, return rule_candidates as [].\n"
        f"Allowed refs: {allowed_refs}"
    )


def _build_bucket_memo(
    bucket_id: str,
    *,
    scope_hint: str,
    story_node_scope: dict[str, Any],
    batch_memos: list[StyleBibleBucketBatchMemo],
) -> StyleBibleBucketMemo:
    label = clean_text(batch_memos[0].label) if batch_memos else bucket_id
    axis_focus = _unique_strings(axis_id for memo in batch_memos for axis_id in memo.axis_focus)
    chapter_ids = sorted(
        _unique_strings(chapter_id for memo in batch_memos for chapter_id in memo.chapter_ids),
        key=chapter_sort_key,
    )
    item_ids = _unique_strings(item_id for memo in batch_memos for item_id in memo.item_ids)
    allowed_refs = _unique_strings(ref for memo in batch_memos for ref in memo.allowed_refs)

    merged_candidates: list[StyleBibleBucketRuleCandidate] = []
    merged_index_by_key: dict[str, int] = {}
    for memo in batch_memos:
        for candidate in memo.rule_candidates:
            key = _normalize_text_key(candidate.text)
            if not key:
                continue
            if key in merged_index_by_key:
                current = merged_candidates[merged_index_by_key[key]]
                current.evidence_refs = _unique_strings([*current.evidence_refs, *candidate.evidence_refs])
                if not clean_text(current.trigger_condition):
                    current.trigger_condition = clean_text(candidate.trigger_condition)
                if not clean_text(current.execution_action):
                    current.execution_action = clean_text(candidate.execution_action)
                current.anti_pattern_codes = _normalize_antipattern_codes(
                    [*current.anti_pattern_codes, *candidate.anti_pattern_codes]
                )
                continue
            merged_candidates.append(
                StyleBibleBucketRuleCandidate(
                    candidate_id=f"{_slugify(bucket_id)}_rule_{len(merged_candidates) + 1:02d}",
                    text=clean_text(candidate.text),
                    trigger_condition=clean_text(candidate.trigger_condition),
                    execution_action=clean_text(candidate.execution_action),
                    evidence_refs=_unique_strings(candidate.evidence_refs),
                    anti_pattern_codes=_normalize_antipattern_codes(candidate.anti_pattern_codes),
                )
            )
            merged_index_by_key[key] = len(merged_candidates) - 1

    merged_candidates.sort(
        key=lambda row: (-len(row.evidence_refs), row.candidate_id, row.text),
    )
    return StyleBibleBucketMemo(
        memo_version=STYLE_BIBLE_BUCKET_MEMO_VERSION,
        memo_id=f"{bucket_id}__memo",
        bucket_id=bucket_id,
        label=label,
        scope_hint=scope_hint,
        story_node_scope=story_node_scope,
        axis_focus=axis_focus,
        chapter_ids=chapter_ids,
        item_ids=item_ids,
        allowed_refs=allowed_refs,
        coverage_summary={
            "batch_count": len(batch_memos),
            "nonempty_batch_count": len([memo for memo in batch_memos if memo.rule_candidates]),
            "candidate_count": sum(len(memo.rule_candidates) for memo in batch_memos),
            "allowed_ref_count": len(allowed_refs),
            "item_count": len(item_ids),
            "chapter_count": len(chapter_ids),
        },
        rule_candidates=merged_candidates[:12],
        batch_memos=batch_memos,
    )


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


def _load_persisted_request_metrics(output_dir: Path, batch_id: str) -> dict[str, Any]:
    metrics_path = output_dir / "_bucket_requests" / batch_id / "request_metrics.jsonl"
    if not metrics_path.exists():
        return {}
    rows = read_jsonl(metrics_path)
    for row in reversed(rows):
        if isinstance(row, dict):
            return row
    return {}


def _load_completed_request_metrics(output_dir: Path, batch_id: str) -> dict[str, Any]:
    metrics_path = output_dir / "_bucket_requests" / batch_id / "request_metrics.jsonl"
    if not metrics_path.exists():
        return {}
    rows = read_jsonl(metrics_path)
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if bool(row.get("completed")):
            return row
        cache_path = clean_text(row.get("cache_path"))
        if cache_path:
            return row
    return {}


def _persist_enriched_request_metrics(
    *,
    output_dir: Path,
    batch_id: str,
    request_metrics: dict[str, Any],
) -> None:
    metrics_dir = ensure_dir(output_dir / "_bucket_requests" / batch_id)
    metrics_path = metrics_dir / "request_metrics.jsonl"
    with metrics_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(request_metrics, ensure_ascii=False))
        handle.write("\n")


def _resolved_cached_response_path(
    *,
    output_dir: Path,
    batch_id: str,
    request_metrics: dict[str, Any],
) -> Path | None:
    cache_path_value = clean_text(request_metrics.get("cache_path"))
    if cache_path_value:
        cache_path = Path(cache_path_value)
        if not cache_path.is_absolute():
            cache_path = (output_dir / cache_path).resolve()
        if cache_path.exists():
            return cache_path

    cache_dir = output_dir / "_bucket_requests" / batch_id / "_request_cache"
    if not cache_dir.exists():
        return None
    cache_key = clean_text(request_metrics.get("cache_key"))
    if cache_key:
        candidate = cache_dir / cache_key[:2] / f"{cache_key}.json"
        if candidate.exists():
            return candidate
    cache_candidates = sorted(cache_dir.rglob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not cache_candidates:
        return None
    return cache_candidates[0]


def _restore_cached_batch_execution(
    *,
    output_dir: Path,
    batch_id: str,
) -> BatchMemoExecution | None:
    request_metrics = dict(_load_completed_request_metrics(output_dir, batch_id))
    if not request_metrics:
        return None
    cache_path = _resolved_cached_response_path(
        output_dir=output_dir,
        batch_id=batch_id,
        request_metrics=request_metrics,
    )
    if cache_path is None or not cache_path.exists():
        return None
    cache_payload = read_json(cache_path)
    if not isinstance(cache_payload, dict):
        return None
    parsed_payload = cache_payload.get("parsed_payload")
    if not isinstance(parsed_payload, dict):
        return None
    try:
        batch_memo = StyleBibleBucketBatchMemo.model_validate(parsed_payload)
    except Exception:  # noqa: BLE001
        return None
    if clean_text(batch_memo.batch_id) != clean_text(batch_id):
        return None

    request_metrics["resumed_existing"] = True
    request_metrics["resumed_partial_existing"] = True
    usage_metadata = request_metrics.get("usage_metadata", {})
    if not isinstance(usage_metadata, dict) or not usage_metadata:
        cached_usage = cache_payload.get("source_usage_metadata")
        usage_metadata = cached_usage if isinstance(cached_usage, dict) else {}

    return BatchMemoExecution(
        memo=batch_memo,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        worker_slot=int(request_metrics.get("worker_slot", -1) or -1),
        gateway_label=clean_text(request_metrics.get("gateway_label")),
        warmup_batch=bool(request_metrics.get("warmup_batch")),
        selected_antipattern_codes=[
            clean_text(item)
            for item in request_metrics.get("selected_antipattern_codes", [])
            if clean_text(item)
        ]
        if isinstance(request_metrics.get("selected_antipattern_codes"), list)
        else [],
        anti_pattern_token_estimate=int(request_metrics.get("anti_pattern_token_estimate", 0) or 0),
    )


def _restore_existing_bucket_artifacts(
    *,
    memo_path: Path,
    output_dir: Path,
) -> tuple[StyleBibleBucketMemo, list[BatchMemoExecution]]:
    bucket_memo = StyleBibleBucketMemo.model_validate(read_json(memo_path))
    executions: list[BatchMemoExecution] = []
    for batch_memo in bucket_memo.batch_memos:
        request_metrics = _load_completed_request_metrics(output_dir, batch_memo.batch_id)
        request_metrics = dict(request_metrics)
        request_metrics["resumed_existing"] = True
        executions.append(
            BatchMemoExecution(
                memo=batch_memo,
                request_metrics=request_metrics,
                usage_metadata=request_metrics.get("usage_metadata", {})
                if isinstance(request_metrics.get("usage_metadata"), dict)
                else {},
                worker_slot=-1,
                gateway_label=clean_text(request_metrics.get("gateway_label")),
                warmup_batch=bool(request_metrics.get("warmup_batch")),
                selected_antipattern_codes=[
                    clean_text(item)
                    for item in request_metrics.get("selected_antipattern_codes", [])
                    if clean_text(item)
                ]
                if isinstance(request_metrics.get("selected_antipattern_codes"), list)
                else [],
                anti_pattern_token_estimate=int(request_metrics.get("anti_pattern_token_estimate", 0) or 0),
            )
        )
    return bucket_memo, executions


def _bucket_order(batch_plan: StyleBibleBatchPlan, bucket_tasks: dict[str, list[BatchMemoTask]]) -> list[str]:
    preferred = [clean_text(item) for item in batch_plan.bucket_execution_order if clean_text(item)]
    ordered: list[str] = []
    seen: set[str] = set()
    for bucket_id in preferred:
        if bucket_id in bucket_tasks and bucket_id not in seen:
            seen.add(bucket_id)
            ordered.append(bucket_id)
    for bucket_id in sorted(bucket_tasks):
        if bucket_id not in seen:
            seen.add(bucket_id)
            ordered.append(bucket_id)
    return ordered


def _summarize_ttft(executions: list[BatchMemoExecution]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for execution in executions:
        seconds = _first_chunk_seconds(execution.request_metrics)
        if seconds is None:
            continue
        row = {
            "bucket_id": execution.memo.bucket_id,
            "batch_id": execution.memo.batch_id,
            "planner_rank": int(execution.request_metrics.get("planner_rank", execution.memo.batch_id.count("_")) or 0),
            "first_chunk_seconds": round(seconds, 3),
        }
        rows.append(row)
        by_bucket.setdefault(execution.memo.bucket_id, []).append(row)

    bucket_rows: list[dict[str, Any]] = []
    for bucket_id, bucket_entries in sorted(by_bucket.items()):
        ordered_entries = sorted(
            bucket_entries,
            key=lambda row: (
                int(row.get("planner_rank", 0) or 0),
                clean_text(row.get("batch_id")),
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

    overall_average = round(sum(float(row["first_chunk_seconds"]) for row in rows) / len(rows), 3) if rows else 0.0
    return {
        "measured_batch_count": len(rows),
        "overall_avg_ttft_seconds": overall_average,
        "by_bucket": bucket_rows,
    }


def _build_batch_stage_metrics(
    *,
    executions: list[BatchMemoExecution],
    workers: int,
    total_task_count: int,
    resumed_bucket_ids: list[str],
    worker_assignments: list[dict[str, Any]],
    started: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    usage_rows = [execution.usage_metadata for execution in executions if isinstance(execution.usage_metadata, dict)]
    prompt_tokens = sum(_usage_tokens(row, "input_tokens", "prompt_tokens") for row in usage_rows)
    output_tokens = sum(_usage_tokens(row, "output_tokens", "completion_tokens") for row in usage_rows)
    total_tokens = sum(
        _usage_tokens(row, "total_tokens")
        or (_usage_tokens(row, "input_tokens", "prompt_tokens") + _usage_tokens(row, "output_tokens", "completion_tokens"))
        for row in usage_rows
    )
    cached_tokens = sum(_extract_cached_tokens(row) for row in usage_rows)
    cache_ratio = round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0
    ttft_summary = _summarize_ttft(executions)

    per_batch_request_metrics = [
        {
            "batch_id": execution.memo.batch_id,
            "bucket_id": execution.memo.bucket_id,
            "candidate_count": len(execution.memo.rule_candidates),
            "raw_candidate_count": int(
                execution.request_metrics.get("raw_candidate_count", len(execution.memo.rule_candidates)) or 0
            ),
            "sanitized_candidate_count": int(
                execution.request_metrics.get("sanitized_candidate_count", len(execution.memo.rule_candidates)) or 0
            ),
            "dropped_candidate_count": int(execution.request_metrics.get("dropped_candidate_count", 0) or 0),
            "raw_scratchpad_count": int(
                execution.request_metrics.get("raw_scratchpad_count", len(execution.memo.scratchpad)) or 0
            ),
            "sanitized_scratchpad_count": int(
                execution.request_metrics.get("sanitized_scratchpad_count", len(execution.memo.scratchpad)) or 0
            ),
            "dropped_scratchpad_count": int(execution.request_metrics.get("dropped_scratchpad_count", 0) or 0),
            "local_retry_used": bool(execution.request_metrics.get("local_retry_used")),
            "local_retry_count": int(execution.request_metrics.get("local_retry_count", 0) or 0),
            "warmup_batch": bool(execution.warmup_batch),
            "cache_hit": bool(execution.request_metrics.get("cache_hit")),
            "response_chars": int(execution.request_metrics.get("response_chars", 0) or 0),
            "total_elapsed_seconds": float(execution.request_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
            "first_chunk_seconds": _first_chunk_seconds(execution.request_metrics),
            "planner_rank": int(execution.request_metrics.get("planner_rank", 0) or 0),
            "cache_affinity_key": clean_text(execution.request_metrics.get("cache_affinity_key")) or execution.memo.bucket_id,
            "worker_slot": execution.worker_slot,
            "gateway_label": execution.gateway_label,
            "selected_antipattern_codes": execution.selected_antipattern_codes,
            "anti_pattern_token_estimate": execution.anti_pattern_token_estimate,
            "resumed_existing": bool(execution.request_metrics.get("resumed_existing")),
        }
        for execution in executions
    ]
    request_metrics = {
        "stage": "bucket_memo_synthesis",
        "max_concurrency": workers,
        "warmup_enabled": True,
        "batch_count": total_task_count,
        "completed_batch_count": len(executions),
        "empty_batch_count": len([execution for execution in executions if not execution.memo.rule_candidates]),
        "warmup_batch_count": len([execution for execution in executions if execution.warmup_batch]),
        "bucket_count": len({execution.memo.bucket_id for execution in executions}),
        "candidate_count": sum(len(execution.memo.rule_candidates) for execution in executions),
        "raw_candidate_count": sum(int(row.get("raw_candidate_count", 0) or 0) for row in per_batch_request_metrics),
        "sanitized_candidate_count": sum(
            int(row.get("sanitized_candidate_count", 0) or 0) for row in per_batch_request_metrics
        ),
        "dropped_candidate_count": sum(
            int(row.get("dropped_candidate_count", 0) or 0) for row in per_batch_request_metrics
        ),
        "raw_scratchpad_count": sum(int(row.get("raw_scratchpad_count", 0) or 0) for row in per_batch_request_metrics),
        "sanitized_scratchpad_count": sum(
            int(row.get("sanitized_scratchpad_count", 0) or 0) for row in per_batch_request_metrics
        ),
        "dropped_scratchpad_count": sum(
            int(row.get("dropped_scratchpad_count", 0) or 0) for row in per_batch_request_metrics
        ),
        "local_retry_used_batch_count": sum(
            1 for row in per_batch_request_metrics if bool(row.get("local_retry_used"))
        ),
        "local_retry_count": sum(int(row.get("local_retry_count", 0) or 0) for row in per_batch_request_metrics),
        "memoed_ref_count": len(
            {
                ref
                for execution in executions
                for candidate in execution.memo.rule_candidates
                for ref in candidate.evidence_refs
            }
        ),
        "response_chars": sum(int(row.get("response_chars", 0) or 0) for row in per_batch_request_metrics),
        "total_elapsed_seconds": round(time.perf_counter() - started, 3),
        "resumed_bucket_count": len(resumed_bucket_ids),
        "resumed_bucket_ids": resumed_bucket_ids,
        "worker_assignments": worker_assignments,
        "per_batch": per_batch_request_metrics,
        "ttft_summary": ttft_summary,
    }
    usage_metadata = {
        "stage": "bucket_memo_synthesis",
        "response_count": len(usage_rows),
        "warmup_batch_count": len([execution for execution in executions if execution.warmup_batch]),
        "cache_hit_count": sum(1 for execution in executions if execution.request_metrics.get("cache_hit")),
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "overall_cache_hit_ratio": cache_ratio,
        "ttft_summary": ttft_summary,
    }
    return request_metrics, usage_metadata


def _run_batch_memo_task(
    config: StableProjectConfig,
    *,
    task: BatchMemoTask,
    output_dir: Path,
    worker_slot: int,
    gateway_label: str,
    warmup_batch: bool = False,
) -> BatchMemoExecution:
    client = StableOpenAICompatibleStructuredClient(
        config,
        artifacts_dir=ensure_dir(output_dir / "_bucket_requests" / task.batch.batch_id),
    )
    local_retry_history: list[dict[str, Any]] = []
    merged_usage_metadata: dict[str, Any] = {}
    total_model_elapsed_seconds = 0.0
    response: Any | None = None
    sanitized: StyleBibleBucketBatchMemo | None = None
    sanitization_audit: BatchMemoSanitizationAudit | None = None
    system_instruction = task.system_instruction

    for local_attempt in range(1, 3):
        response = client.generate_structured(
            request_key=f"bucket_memo_{task.batch.batch_id}__lr{local_attempt:02d}",
            model_name=config.model.style_bible_model or config.model.style_model,
            response_model=StyleBibleBucketBatchMemo,
            system_instruction=system_instruction,
            user_payload=task.user_payload,
            temperature=float(config.model.style_bible_temperature or config.model.style_temperature),
            max_output_tokens=int(config.model.style_bible_max_output_tokens or config.model.style_max_output_tokens),
            response_format_mode="json_schema",
            output_contract_mode="blueprint",
        )
        sanitized, sanitization_audit = _sanitize_batch_memo(response.parsed, task=task)
        merged_usage_metadata = StableOpenAICompatibleStructuredClient._merge_usage_metadata(
            merged_usage_metadata,
            response.usage_metadata,
        )
        total_model_elapsed_seconds += float(response.request_metrics.get("total_elapsed_seconds", 0.0) or 0.0)
        local_attempt_summary = {
            "local_attempt": local_attempt,
            **sanitization_audit.to_dict(),
            "request_key": response.request_metrics.get("request_key", ""),
            "total_elapsed_seconds": float(response.request_metrics.get("total_elapsed_seconds", 0.0) or 0.0),
            "cache_hit": bool(response.request_metrics.get("cache_hit")),
            "retry_triggered": False,
        }
        if local_attempt == 1 and _should_retry_after_sanitization(sanitization_audit):
            local_attempt_summary["retry_triggered"] = True
            local_attempt_summary["retry_reason"] = "all_candidates_removed_by_allowed_refs"
            local_retry_history.append(local_attempt_summary)
            system_instruction = task.system_instruction + _retry_guardrail_suffix(task)
            continue
        local_retry_history.append(local_attempt_summary)
        break

    if response is None or sanitized is None or sanitization_audit is None:
        raise RuntimeError(f"Failed to build bucket memo for batch {task.batch.batch_id}.")

    request_metrics = dict(response.request_metrics)
    request_metrics["final_model_elapsed_seconds"] = float(request_metrics.get("total_elapsed_seconds", 0.0) or 0.0)
    request_metrics["total_elapsed_seconds"] = round(total_model_elapsed_seconds, 3)
    request_metrics["usage_metadata"] = merged_usage_metadata
    request_metrics["usage_summary"] = StableOpenAICompatibleStructuredClient._usage_summary(merged_usage_metadata)
    request_metrics["local_retry_used"] = len(local_retry_history) > 1
    request_metrics["local_retry_count"] = max(len(local_retry_history) - 1, 0)
    request_metrics["local_retry_history"] = local_retry_history
    request_metrics["model_call_count"] = len(local_retry_history)
    request_metrics["sanitization_audit"] = sanitization_audit.to_dict()
    request_metrics["raw_scratchpad_count"] = int(sanitization_audit.raw_scratchpad_count)
    request_metrics["sanitized_scratchpad_count"] = int(sanitization_audit.sanitized_scratchpad_count)
    request_metrics["dropped_scratchpad_count"] = int(sanitization_audit.dropped_scratchpad_count)
    request_metrics["raw_candidate_count"] = int(sanitization_audit.raw_candidate_count)
    request_metrics["sanitized_candidate_count"] = int(sanitization_audit.sanitized_candidate_count)
    request_metrics["dropped_candidate_count"] = int(sanitization_audit.dropped_candidate_count)
    request_metrics["pruned_candidate_invalid_ref_count"] = int(sanitization_audit.pruned_candidate_invalid_ref_count)
    request_metrics["planner_rank"] = int(task.batch.planner_rank or 0)
    request_metrics["cache_affinity_key"] = clean_text(task.batch.cache_affinity_key) or task.batch.bucket_id
    request_metrics["selected_antipattern_codes"] = list(task.selected_antipattern_codes)
    request_metrics["anti_pattern_token_budget"] = int(task.anti_pattern_token_budget)
    request_metrics["anti_pattern_token_estimate"] = int(task.anti_pattern_token_estimate)
    request_metrics["worker_slot"] = int(worker_slot)
    request_metrics["gateway_label"] = clean_text(request_metrics.get("gateway_label")) or clean_text(gateway_label)
    request_metrics["warmup_batch"] = bool(warmup_batch)
    _persist_enriched_request_metrics(
        output_dir=output_dir,
        batch_id=task.batch.batch_id,
        request_metrics=request_metrics,
    )
    return BatchMemoExecution(
        memo=sanitized,
        request_metrics=request_metrics,
        usage_metadata=merged_usage_metadata,
        worker_slot=worker_slot,
        gateway_label=clean_text(request_metrics.get("gateway_label")) or clean_text(gateway_label),
        warmup_batch=bool(warmup_batch),
        selected_antipattern_codes=list(task.selected_antipattern_codes),
        anti_pattern_token_estimate=int(task.anti_pattern_token_estimate),
        sanitization_audit=sanitization_audit.to_dict(),
    )


def _run_bucket_worker(
    *,
    worker_slot: int,
    bucket_ids: list[str],
    bucket_tasks: dict[str, list[BatchMemoTask]],
    precomputed_executions_by_bucket: dict[str, list[BatchMemoExecution]] | None,
    config: StableProjectConfig,
    routed_index: StyleBibleRoutedIndex,
    memo_dir: Path,
    gateway_label: str,
) -> BucketWorkerResult:
    memo_paths: list[Path] = []
    bucket_memos: list[StyleBibleBucketMemo] = []
    executions: list[BatchMemoExecution] = []

    for bucket_id in bucket_ids:
        tasks = bucket_tasks.get(bucket_id, [])
        if not tasks:
            continue
        bucket_executions: list[BatchMemoExecution] = list((precomputed_executions_by_bucket or {}).get(bucket_id, []))
        completed_batch_ids = {execution.memo.batch_id for execution in bucket_executions}
        for task in tasks:
            if task.batch.batch_id in completed_batch_ids:
                continue
            bucket_executions.append(
                _run_batch_memo_task(
                    config,
                    task=task,
                    output_dir=memo_dir.parent,
                    worker_slot=worker_slot,
                    gateway_label=gateway_label,
                )
            )
        bucket_executions.sort(key=lambda row: row.request_metrics.get("planner_rank", 0))
        executions.extend(bucket_executions)
        bucket_memo = _build_bucket_memo(
            bucket_id,
            scope_hint=routed_index.scope_hint,
            story_node_scope=routed_index.story_node_scope,
            batch_memos=[execution.memo for execution in bucket_executions],
        )
        path = memo_dir / f"{bucket_id}.json"
        write_json(path, bucket_memo.model_dump(mode="json", by_alias=True))
        memo_paths.append(path)
        bucket_memos.append(bucket_memo)

    return BucketWorkerResult(
        worker_slot=worker_slot,
        gateway_label=gateway_label,
        bucket_ids=bucket_ids,
        memo_paths=memo_paths,
        bucket_memos=bucket_memos,
        executions=executions,
    )


def build_style_bible_bucket_memos(
    config: StableProjectConfig,
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    routed_index: StyleBibleRoutedIndex | dict[str, Any],
    batch_plan: StyleBibleBatchPlan | dict[str, Any],
    output_dir: str | Path,
    *,
    include_bucket_ids: Iterable[str] | None = None,
    max_concurrency: int | None = None,
    resume: bool = False,
) -> BucketMemoBuildResult:
    if not isinstance(routed_index, StyleBibleRoutedIndex):
        routed_index = StyleBibleRoutedIndex.model_validate(routed_index)
    if not isinstance(batch_plan, StyleBibleBatchPlan):
        batch_plan = StyleBibleBatchPlan.model_validate(batch_plan)

    requested_bucket_ids = {clean_text(bucket_id) for bucket_id in (include_bucket_ids or []) if clean_text(bucket_id)}
    if requested_bucket_ids:
        available_bucket_ids = {clean_text(batch.bucket_id) for batch in batch_plan.batches if clean_text(batch.bucket_id)}
        missing = sorted(requested_bucket_ids - available_bucket_ids)
        if missing:
            raise ValueError(f"Unknown bucket ids for memo build: {', '.join(missing)}")

    output_path = ensure_dir(output_dir)
    memo_dir = ensure_dir(output_path / BUCKET_MEMO_DIR)
    prompt_bundle_dir = ensure_dir(output_path / "bucket_prompt_bundles")
    inputs = load_style_bible_inputs(facts_dir, style_dir, canon_dir)
    lookup_maps = _lookup_maps(inputs)
    routed_item_by_id = _routed_item_map(routed_index)
    bucket_summary_by_id = _bucket_summary_map(batch_plan)
    bucket_catalog_by_id = _bucket_catalog_map(routed_index)

    bucket_tasks: dict[str, list[BatchMemoTask]] = {}
    for batch in sorted(
        batch_plan.batches,
        key=lambda row: (
            int(row.planner_rank or 0),
            row.bucket_id,
            row.batch_id,
        ),
    ):
        if requested_bucket_ids and batch.bucket_id not in requested_bucket_ids:
            continue
        prompt_bundle_xml, allowed_refs = _build_batch_prompt_bundle_xml(
            batch,
            routed_item_by_id=routed_item_by_id,
            bucket_summary_by_id=bucket_summary_by_id,
            bucket_catalog_by_id=bucket_catalog_by_id,
            lookup_maps=lookup_maps,
            scope_hint=routed_index.scope_hint,
        )
        prompt_bundle_path = prompt_bundle_dir / f"{batch.batch_id}.xml"
        write_text(prompt_bundle_path, prompt_bundle_xml)
        static_axis_focus = _bucket_static_axis_focus(
            batch.bucket_id,
            bucket_summary_by_id=bucket_summary_by_id,
            bucket_catalog_by_id=bucket_catalog_by_id,
        )
        assembly = assemble_bucket_synthesis_prompt(
            prompt_dir=config.prompt_dir,
            bucket_id=batch.bucket_id,
            axis_focus=batch.axis_focus,
            static_axis_focus=static_axis_focus,
            prompt_bundle_xml=prompt_bundle_xml,
            memo_id=f"{batch.batch_id}__memo",
            batch_id=batch.batch_id,
            label=batch.label,
            chapter_ids=batch.chapter_ids,
            item_ids=batch.item_ids,
            allowed_refs=allowed_refs,
        )
        bucket_tasks.setdefault(batch.bucket_id, []).append(
            BatchMemoTask(
                batch=batch,
                prompt_bundle_xml=prompt_bundle_xml,
                allowed_refs=allowed_refs,
                prompt_bundle_path=prompt_bundle_path,
                system_instruction=assembly.system_instruction,
                user_payload=assembly.user_payload,
                selected_antipattern_codes=assembly.selected_antipattern_codes,
                anti_pattern_token_budget=assembly.anti_pattern_token_budget,
                anti_pattern_token_estimate=assembly.anti_pattern_token_estimate,
            )
        )

    if not bucket_tasks:
        raise ValueError("No bucket memo tasks were generated from the provided batch plan.")

    total_task_count = sum(len(tasks) for tasks in bucket_tasks.values())
    started = time.perf_counter()
    ordered_bucket_ids = _bucket_order(batch_plan, bucket_tasks)
    loaded_bucket_memos: list[StyleBibleBucketMemo] = []
    loaded_executions: list[BatchMemoExecution] = []
    restored_partial_executions_by_bucket: dict[str, list[BatchMemoExecution]] = {}
    resumed_bucket_ids: list[str] = []
    active_bucket_tasks: dict[str, list[BatchMemoTask]] = {}

    for bucket_id in ordered_bucket_ids:
        memo_path = memo_dir / f"{bucket_id}.json"
        if resume and memo_path.exists():
            try:
                restored_bucket_memo, restored_executions = _restore_existing_bucket_artifacts(
                    memo_path=memo_path,
                    output_dir=output_path,
                )
            except Exception:  # noqa: BLE001
                active_bucket_tasks[bucket_id] = bucket_tasks[bucket_id]
            else:
                loaded_bucket_memos.append(restored_bucket_memo)
                loaded_executions.extend(restored_executions)
                resumed_bucket_ids.append(bucket_id)
            continue
        if resume:
            restored_partial_executions = [
                execution
                for task in bucket_tasks[bucket_id]
                if (execution := _restore_cached_batch_execution(output_dir=output_path, batch_id=task.batch.batch_id)) is not None
            ]
            if restored_partial_executions:
                restored_batch_ids = {
                    clean_text(execution.memo.batch_id)
                    for execution in restored_partial_executions
                    if clean_text(execution.memo.batch_id)
                }
                if len(restored_batch_ids) == len(bucket_tasks[bucket_id]):
                    restored_bucket_memo = _build_bucket_memo(
                        bucket_id,
                        scope_hint=routed_index.scope_hint,
                        story_node_scope=routed_index.story_node_scope,
                        batch_memos=[execution.memo for execution in restored_partial_executions],
                    )
                    write_json(memo_path, restored_bucket_memo.model_dump(mode="json", by_alias=True))
                    loaded_bucket_memos.append(restored_bucket_memo)
                    loaded_executions.extend(restored_partial_executions)
                    resumed_bucket_ids.append(bucket_id)
                    continue
                restored_partial_executions_by_bucket[bucket_id] = restored_partial_executions
                active_bucket_tasks[bucket_id] = [
                    task
                    for task in bucket_tasks[bucket_id]
                    if clean_text(task.batch.batch_id) not in restored_batch_ids
                ]
                resumed_bucket_ids.append(bucket_id)
                continue
        active_bucket_tasks[bucket_id] = bucket_tasks[bucket_id]

    built_worker_results: list[BucketWorkerResult] = []
    failures: list[tuple[str, Exception]] = []
    worker_assignments: list[dict[str, Any]] = []
    worker_target = _normalize_workers(max_concurrency)
    active_bucket_ids = [bucket_id for bucket_id in ordered_bucket_ids if bucket_id in active_bucket_tasks]
    gateway_pool = _gateway_pool(config)
    active_worker_count = _effective_worker_count(worker_target, len(active_bucket_ids))

    if active_bucket_ids:
        bucket_assignments = _contiguous_bucket_assignments(active_bucket_ids, worker_count=active_worker_count)
        bucket_assignment_map: dict[str, tuple[int, GatewayConfig, str]] = {}
        warmup_executions_by_bucket: dict[str, list[BatchMemoExecution]] = {
            bucket_id: list(restored_partial_executions_by_bucket.get(bucket_id, []))
            for bucket_id in active_bucket_ids
            if restored_partial_executions_by_bucket.get(bucket_id)
        }
        for worker_slot, assigned_bucket_ids in enumerate(bucket_assignments):
            gateway = gateway_pool[worker_slot % len(gateway_pool)]
            gateway_label = clean_text(gateway.label) or f"gateway_{(worker_slot % len(gateway_pool)) + 1}"
            worker_assignments.append(
                {
                    "worker_slot": worker_slot,
                    "gateway_label": gateway_label,
                    "bucket_ids": assigned_bucket_ids,
                    "batch_count": sum(len(active_bucket_tasks[bucket_id]) for bucket_id in assigned_bucket_ids),
                    "cache_affinity_enabled": True,
                    "warmup_enabled": True,
                    "warmup_batch_count": len(assigned_bucket_ids),
                }
            )
            for bucket_id in assigned_bucket_ids:
                bucket_assignment_map[bucket_id] = (worker_slot, gateway, gateway_label)

        # Warm each bucket with its first batch before the remaining work enters the pool.
        # The warm-up phase has its own bounded executor so we do not serialize all
        # buckets behind a single request stream.
        with ThreadPoolExecutor(max_workers=active_worker_count) as executor:
            future_map = {}
            for bucket_id in active_bucket_ids:
                tasks = active_bucket_tasks.get(bucket_id, [])
                if not tasks:
                    continue
                worker_slot, gateway, gateway_label = bucket_assignment_map[bucket_id]
                future = executor.submit(
                    _run_batch_memo_task,
                    _pin_gateway_config(config, gateway),
                    task=tasks[0],
                    output_dir=output_path,
                    worker_slot=worker_slot,
                    gateway_label=gateway_label,
                    warmup_batch=True,
                )
                future_map[future] = bucket_id
            for future in as_completed(future_map):
                bucket_id = future_map[future]
                try:
                    warmup_executions_by_bucket.setdefault(bucket_id, [])
                    warmup_executions_by_bucket[bucket_id].append(future.result())
                except Exception as exc:  # noqa: BLE001
                    failures.append((f"{bucket_id}__warmup", exc))

        if not failures:
            with ThreadPoolExecutor(max_workers=active_worker_count) as executor:
                future_map = {}
                for worker_slot, assigned_bucket_ids in enumerate(bucket_assignments):
                    gateway = gateway_pool[worker_slot % len(gateway_pool)]
                    gateway_label = clean_text(gateway.label) or f"gateway_{(worker_slot % len(gateway_pool)) + 1}"
                    future = executor.submit(
                        _run_bucket_worker,
                        worker_slot=worker_slot,
                        bucket_ids=assigned_bucket_ids,
                        bucket_tasks=active_bucket_tasks,
                        precomputed_executions_by_bucket=warmup_executions_by_bucket,
                        config=_pin_gateway_config(config, gateway),
                        routed_index=routed_index,
                        memo_dir=memo_dir,
                        gateway_label=gateway_label,
                    )
                    future_map[future] = assigned_bucket_ids
                for future in as_completed(future_map):
                    assigned_bucket_ids = future_map[future]
                    try:
                        built_worker_results.append(future.result())
                    except Exception as exc:  # noqa: BLE001
                        bucket_label = ",".join(assigned_bucket_ids[:3])
                        failures.append((bucket_label, exc))

    if failures:
        summary = "; ".join(f"{bucket_id}: {type(exc).__name__}({exc})" for bucket_id, exc in failures[:3])
        raise RuntimeError(f"Bucket memo synthesis failed for {len(failures)} worker slots. {summary}")

    batch_rank_by_id = {
        batch.batch_id: int(batch.planner_rank or 0)
        for batch in batch_plan.batches
    }
    built_bucket_memos = [memo for result in built_worker_results for memo in result.bucket_memos]
    built_executions = [execution for result in built_worker_results for execution in result.executions]
    bucket_memos = loaded_bucket_memos + built_bucket_memos
    bucket_memos.sort(key=lambda memo: ordered_bucket_ids.index(memo.bucket_id) if memo.bucket_id in ordered_bucket_ids else len(ordered_bucket_ids))
    executions = loaded_executions + built_executions
    executions.sort(
        key=lambda row: (
            batch_rank_by_id.get(row.memo.batch_id, 10**9),
            row.memo.batch_id,
        )
    )
    batch_memos = [execution.memo for execution in executions]
    memo_paths = [memo_dir / f"{memo.bucket_id}.json" for memo in bucket_memos]

    memoed_refs = {
        ref
        for memo in batch_memos
        for candidate in memo.rule_candidates
        for ref in candidate.evidence_refs
    }
    memoed_item_ids = {
        item.item_id
        for item in routed_index.items
        if item.source_ref in memoed_refs or item.item_id in memoed_refs
    }
    memoed_chapter_ids = {
        chapter_id
        for item in routed_index.items
        if item.item_id in memoed_item_ids
        for chapter_id in item.chapter_ids
        if chapter_id
    }

    request_metrics, usage_metadata = _build_batch_stage_metrics(
        executions=executions,
        workers=active_worker_count,
        total_task_count=total_task_count,
        resumed_bucket_ids=resumed_bucket_ids,
        worker_assignments=worker_assignments,
        started=started,
    )
    return BucketMemoBuildResult(
        memo_dir=memo_dir,
        prompt_bundle_dir=prompt_bundle_dir,
        memo_paths=memo_paths,
        bucket_memos=bucket_memos,
        batch_memos=batch_memos,
        request_metrics=request_metrics,
        usage_metadata=usage_metadata,
        memoed_item_ids=memoed_item_ids,
        memoed_chapter_ids=memoed_chapter_ids,
        memoed_refs=memoed_refs,
    )
