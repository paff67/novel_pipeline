from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json, write_json, write_jsonl, write_markdown, write_text
from novel_pipeline_stable.models import StyleBibleReasoningBundle, StyleBibleResult, style_bible_payload_to_flat
from novel_pipeline_stable.monitoring import RunTracker, utc_timestamp
from novel_pipeline_stable.style_bible_contracts import JUDGE_FLAT_FILE, REDUCE_TRACE_FILE
from novel_pipeline_stable.style_eval_contract import (
    EVALUATION_MANIFEST_FILE,
    RUN_MANIFEST_FILE,
    file_sha256,
    sha256_payload,
)
from novel_pipeline_stable.style_bible_runtime_flags import load_style_bible_runtime_flags


STYLE_BIBLE_FILE = "style_bible_final.json"
REASONING_FILE = "style_bible_reasoning.json"
EXPORT_FLAT_FILE = "style_bible_export_flat.json"
SOURCE_BUNDLE_FILE = "style_bible_source_bundle.json"
JUDGE_REPORT_JSON_FILE = "judge_report.json"
JUDGE_REPORT_MD_FILE = "judge_report.md"
JUDGE_ROWS_JSONL_FILE = "judge_rows.jsonl"
STYLE_EVAL_REPORT_FILE = "style_eval_report.json"
SEMANTIC_FRAGMENT_SPLIT_RE = re.compile(r"[\s,.;:!?，。；：！？、（）()【】《》“”‘’/\\|\-]+")
SEMANTIC_CONJUNCTION_SPLIT_RE = re.compile(r"(?:和|与|及|或|并且|并|以及)")
SEMANTIC_PREFIXES = (
    "当剧情把",
    "当剧情",
    "当角色",
    "当人物",
    "当主角",
    "当",
    "如果",
    "若",
    "出现",
    "关于",
    "对于",
    "角色",
    "人物",
    "剧情",
    "主角",
)
SEMANTIC_SUFFIXES = (
    "的时候",
    "时",
    "等事实规则",
    "事实规则",
    "的关键表述",
    "关键表述",
    "的叙事模板",
    "叙事模板",
    "的世界规则",
    "世界规则",
    "规则",
    "模板",
    "节点",
)
SEMANTIC_STOPWORDS = {
    "剧情",
    "角色",
    "人物",
    "主角",
    "关键",
    "表述",
    "事实",
    "规则",
    "模板",
    "节点",
    "内容",
    "方式",
    "时候",
}
STRUCTURE_RULE_LIST_PATHS = (
    "narrative_system.engine",
    "narrative_system.pacing_rules",
    "narrative_system.plot_node_logic",
    "expression_system.description_rules",
    "expression_system.dialogue_rules",
    "expression_system.characterization_rules",
    "expression_system.sensory_rules",
    "aesthetics_system.core_axes",
    "aesthetics_system.pressure_axes",
    "aesthetics_system.humor_recipe",
    "aesthetics_system.satire_targets",
    "aesthetics_system.nonstandard_xianxia_rules",
    "voice_contract.register_mix",
    "voice_contract.negative_pitfalls",
    "character_arc_rules",
    "negative_rules",
)


@dataclass(slots=True)
class AxisKeywordGroup:
    axis_id: str
    label: str
    match_any: list[str]


@dataclass(slots=True)
class JudgeRules:
    rules_path: Path
    pass_score: float
    warn_score: float
    weights: dict[str, float]
    thresholds: dict[str, float]
    rule_prefixes: list[str]
    routing_prefixes: list[str]
    worldbook_prefixes: list[str]
    rag_prefixes: list[str]
    evidence_prefixes: list[str]
    actionable_cues: list[str]
    generic_patterns: list[str]
    axis_groups: dict[str, AxisKeywordGroup]
    anti_pattern_registry: dict[str, dict[str, Any]]


@dataclass(slots=True)
class GoldSetMechanism:
    label: str
    description: str
    must_include_any: list[str]
    should_include_any: list[str]
    forbidden_patterns: list[str]


@dataclass(slots=True)
class GoldSetCase:
    case_version: str
    case_id: str
    node_id: str
    scope_type: str
    source_refs: list[str]
    bucket_targets: list[str]
    batch_targets: list[str]
    must_hit_refs: list[str]
    required_axes: list[str]
    required_mechanisms: list[GoldSetMechanism]
    forbidden_patterns: list[str]
    forbidden_outputs: list[str]
    anti_pattern_watchlist: list[str]
    required_downstream_surfaces: dict[str, list[str]]
    evidence_expectations: dict[str, Any]
    trace_expectations: dict[str, Any]
    human_notes: str
    file_path: Path


@dataclass(slots=True)
class TextNode:
    path: str
    text: str
    source_ref: str = ""


@dataclass(slots=True)
class SupportingEvidenceNode:
    path: str
    claim: str
    evidence_text: str
    source_ref: str


@dataclass(slots=True)
class JudgeResult:
    report_path: Path
    markdown_path: Path
    rows_path: Path
    report: dict[str, Any]


@dataclass(slots=True)
class CaseScopeContext:
    applicable: bool
    reason: str
    case_scope_ref_pool: set[str]
    effective_expected_refs: list[str]
    matched_bucket_ids: list[str]
    matched_batch_ids: list[str]
    failed_bucket_ids: list[str]
    skipped_sparse_bucket_ids: list[str]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        rows.append(cleaned)
    return rows


def _nested_payload_value(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _payload_list(payload: dict[str, Any], path: str) -> list[Any]:
    value = _nested_payload_value(payload, path)
    return value if isinstance(value, list) else []


def _budget_ratio(count: int, *, soft_max: int, hard_max: int) -> float:
    if count <= 0:
        return 0.0
    if count <= soft_max:
        return 1.0
    if count <= hard_max:
        span = max(hard_max - soft_max, 1)
        overflow = count - soft_max
        return round(max(0.8, 1.0 - (0.2 * overflow / span)), 4)
    overflow = count - hard_max
    return round(max(0.25, 0.8 - (0.1 * overflow)), 4)


def _budget_multiplier(budget_ratio: float, *, floor: float) -> float:
    bounded = max(0.0, min(float(budget_ratio), 1.0))
    return round(floor + ((1.0 - floor) * bounded), 4)


def _average_budget_ratio(
    payload: dict[str, Any],
    paths: list[str] | tuple[str, ...],
    *,
    soft_max: int,
    hard_max: int,
) -> float:
    ratios = [
        _budget_ratio(len(items), soft_max=soft_max, hard_max=hard_max)
        for path in paths
        if (items := _payload_list(payload, path))
    ]
    return _average(ratios) if ratios else 0.0


def _required_prefix_ratio(
    required_prefixes: list[str],
    overlaps: list["SupportingEvidenceNode"],
    evidence_nodes: list["SupportingEvidenceNode"],
) -> float:
    if not required_prefixes:
        return 1.0
    overlap_refs = [node.source_ref for node in overlaps if node.source_ref]
    all_refs = [node.source_ref for node in evidence_nodes if node.source_ref]
    prefix_scores: list[float] = []
    for prefix in required_prefixes:
        if any(ref.startswith(prefix) for ref in overlap_refs):
            prefix_scores.append(1.0)
        elif any(ref.startswith(prefix) for ref in all_refs):
            prefix_scores.append(0.5)
        else:
            prefix_scores.append(0.0)
    return _average(prefix_scores)


def _valid_ref_ratio(evidence_nodes: list["SupportingEvidenceNode"], valid_refs: set[str]) -> float:
    refs = [node.source_ref for node in evidence_nodes if node.source_ref]
    if not refs:
        return 0.0
    return round(sum(1 for ref in refs if ref in valid_refs) / len(refs), 4)


def _load_judge_rules(rules_path: str | Path) -> JudgeRules:
    path = Path(rules_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Judge rules config not found: {path}")
    payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    scoring = payload.get("scoring", {})
    weights = payload.get("weights", {})
    thresholds = payload.get("thresholds", {})
    search_paths = payload.get("search_paths", {})
    actionable_cues = payload.get("actionable_cues", {})
    generic_language = payload.get("generic_language", {})
    resources = payload.get("resources", {})

    axis_groups: dict[str, AxisKeywordGroup] = {}
    for row in payload.get("axis_groups", []):
        if not isinstance(row, dict):
            continue
        axis_id = _clean_text(row.get("id"))
        if not axis_id:
            continue
        axis_groups[axis_id] = AxisKeywordGroup(
            axis_id=axis_id,
            label=_clean_text(row.get("label")),
            match_any=[_clean_text(item) for item in row.get("match_any", []) if _clean_text(item)],
        )

    registry_path_value = _clean_text(resources.get("anti_pattern_registry_file"))
    registry_path = (path.parent / registry_path_value).resolve() if registry_path_value else (path.parent / "style_bible_antipattern_registry.json")
    registry_payload = read_json(registry_path) if registry_path.exists() else {}
    anti_pattern_registry = registry_payload if isinstance(registry_payload, dict) else {}

    return JudgeRules(
        rules_path=path,
        pass_score=float(scoring.get("pass_score", 75.0) or 75.0),
        warn_score=float(scoring.get("warn_score", 60.0) or 60.0),
        weights={str(key): float(value or 0) for key, value in weights.items()},
        thresholds={str(key): float(value or 0) for key, value in thresholds.items()},
        rule_prefixes=[_clean_text(item) for item in search_paths.get("rule_prefixes", []) if _clean_text(item)],
        routing_prefixes=[_clean_text(item) for item in search_paths.get("routing_prefixes", []) if _clean_text(item)],
        worldbook_prefixes=[_clean_text(item) for item in search_paths.get("worldbook_prefixes", []) if _clean_text(item)],
        rag_prefixes=[_clean_text(item) for item in search_paths.get("rag_prefixes", []) if _clean_text(item)],
        evidence_prefixes=[_clean_text(item) for item in search_paths.get("evidence_prefixes", []) if _clean_text(item)],
        actionable_cues=[_clean_text(item) for item in actionable_cues.get("phrases", []) if _clean_text(item)],
        generic_patterns=[_clean_text(item) for item in generic_language.get("patterns", []) if _clean_text(item)],
        axis_groups=axis_groups,
        anti_pattern_registry=anti_pattern_registry,
    )


def _load_gold_set_cases(index_path: str | Path, *, node_id: str) -> tuple[dict[str, Any], list[GoldSetCase], str]:
    resolved_index = Path(index_path).resolve()
    if not resolved_index.exists():
        raise FileNotFoundError(f"Gold set index not found: {resolved_index}")
    index_payload = read_json(resolved_index)
    if not isinstance(index_payload, dict):
        raise ValueError(f"Gold set index must be a JSON object: {resolved_index}")

    root_dir = resolved_index.parent
    selected_cases: list[GoldSetCase] = []
    case_hash_payloads: list[dict[str, Any]] = []
    for row in index_payload.get("cases", []):
        if not isinstance(row, dict):
            continue
        if _clean_text(row.get("node_id")) != node_id:
            continue
        relative_file = _clean_text(row.get("file"))
        if not relative_file:
            continue
        case_path = (root_dir / relative_file).resolve()
        case_payload = read_json(case_path)
        if not isinstance(case_payload, dict):
            raise ValueError(f"Gold set case must be a JSON object: {case_path}")
        merged_payload = dict(case_payload)
        for key in (
            "bucket_targets",
            "batch_targets",
            "must_hit_refs",
            "forbidden_outputs",
            "anti_pattern_watchlist",
            "trace_expectations",
        ):
            if key not in merged_payload and key in row:
                merged_payload[key] = row.get(key)
        mechanisms: list[GoldSetMechanism] = []
        for mechanism_row in merged_payload.get("required_mechanisms", []):
            if not isinstance(mechanism_row, dict):
                continue
            mechanisms.append(
                GoldSetMechanism(
                    label=_clean_text(mechanism_row.get("label")),
                    description=_clean_text(mechanism_row.get("description")),
                    must_include_any=[
                        _clean_text(item) for item in mechanism_row.get("must_include_any", []) if _clean_text(item)
                    ],
                    should_include_any=[
                        _clean_text(item) for item in mechanism_row.get("should_include_any", []) if _clean_text(item)
                    ],
                    forbidden_patterns=[
                        _clean_text(item) for item in mechanism_row.get("forbidden_patterns", []) if _clean_text(item)
                    ],
                )
            )
        case = GoldSetCase(
            case_version=_clean_text(merged_payload.get("case_version")),
            case_id=_clean_text(merged_payload.get("case_id")),
            node_id=_clean_text(merged_payload.get("node_id")),
            scope_type=_clean_text(merged_payload.get("scope_type")),
            source_refs=[_clean_text(item) for item in merged_payload.get("source_refs", []) if _clean_text(item)],
            bucket_targets=[_clean_text(item) for item in merged_payload.get("bucket_targets", []) if _clean_text(item)],
            batch_targets=[_clean_text(item) for item in merged_payload.get("batch_targets", []) if _clean_text(item)],
            must_hit_refs=[_clean_text(item) for item in merged_payload.get("must_hit_refs", []) if _clean_text(item)],
            required_axes=[_clean_text(item) for item in merged_payload.get("required_axes", []) if _clean_text(item)],
            required_mechanisms=mechanisms,
            forbidden_patterns=[_clean_text(item) for item in merged_payload.get("forbidden_patterns", []) if _clean_text(item)],
            forbidden_outputs=[_clean_text(item) for item in merged_payload.get("forbidden_outputs", []) if _clean_text(item)],
            anti_pattern_watchlist=[_clean_text(item) for item in merged_payload.get("anti_pattern_watchlist", []) if _clean_text(item)],
            required_downstream_surfaces={
                "rag_worthy": [
                    _clean_text(item)
                    for item in merged_payload.get("required_downstream_surfaces", {}).get("rag_worthy", [])
                    if _clean_text(item)
                ],
                "worldbook_worthy": [
                    _clean_text(item)
                    for item in merged_payload.get("required_downstream_surfaces", {}).get("worldbook_worthy", [])
                    if _clean_text(item)
                ],
                "routing_hints": [
                    _clean_text(item)
                    for item in merged_payload.get("required_downstream_surfaces", {}).get("routing_hints", [])
                    if _clean_text(item)
                ],
            },
            evidence_expectations=merged_payload.get("evidence_expectations", {})
            if isinstance(merged_payload.get("evidence_expectations", {}), dict)
            else {},
            trace_expectations=merged_payload.get("trace_expectations", {})
            if isinstance(merged_payload.get("trace_expectations", {}), dict)
            else {},
            human_notes=_clean_text(merged_payload.get("human_notes")),
            file_path=case_path,
        )
        selected_cases.append(case)
        case_hash_payloads.append(merged_payload)

    if not selected_cases:
        raise ValueError(f"No gold set cases found for node_id={node_id} in {resolved_index}")

    gold_set_hash_payload = {
        "index": index_payload,
        "case_ids": [case.case_id for case in selected_cases],
        "cases": case_hash_payloads,
    }
    return index_payload, sorted(selected_cases, key=lambda item: item.case_id), sha256_payload(gold_set_hash_payload)


def _try_load_run_manifest(input_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    path = input_dir / RUN_MANIFEST_FILE
    if not path.exists():
        return None, None
    payload = read_json(path)
    return path, payload if isinstance(payload, dict) else None


def _auto_detect_eval_dir(input_dir: Path) -> Path | None:
    sibling = input_dir.parent / "style_bible_eval"
    if sibling.exists():
        return sibling
    return None


def _try_load_eval_report(eval_dir: Path | None) -> tuple[Path | None, dict[str, Any] | None, Path | None, dict[str, Any] | None]:
    if eval_dir is None:
        return None, None, None, None
    report_path = eval_dir / STYLE_EVAL_REPORT_FILE
    manifest_path = eval_dir / EVALUATION_MANIFEST_FILE
    report_payload = read_json(report_path) if report_path.exists() else None
    manifest_payload = read_json(manifest_path) if manifest_path.exists() else None
    return (
        report_path if report_path.exists() else None,
        report_payload if isinstance(report_payload, dict) else None,
        manifest_path if manifest_path.exists() else None,
        manifest_payload if isinstance(manifest_payload, dict) else None,
    )


def _select_judge_projection_payload(
    *,
    style_bible_payload: dict[str, Any],
    judge_flat_payload: dict[str, Any] | None,
    export_flat_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    if judge_flat_payload:
        return judge_flat_payload, JUDGE_FLAT_FILE
    if export_flat_payload:
        return export_flat_payload, EXPORT_FLAT_FILE
    normalized_payload = style_bible_payload_to_flat(style_bible_payload)
    if normalized_payload:
        return normalized_payload, "style_bible_final_flattened"
    return style_bible_payload, STYLE_BIBLE_FILE


def _infer_node_id(
    *,
    explicit_node_id: str,
    run_manifest: dict[str, Any] | None,
    style_bible_payload: dict[str, Any],
    gold_set_index_payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if _clean_text(explicit_node_id):
        return _clean_text(explicit_node_id), "explicit"
    if isinstance(run_manifest, dict):
        node_id = _clean_text(run_manifest.get("node_id"))
        if node_id:
            return node_id, "run_manifest"
    style_id = _clean_text(style_bible_payload.get("style_id"))
    match = re.search(r"(main_\d+_[A-Za-z0-9_]+_ch\d{4}_\d{4})", style_id)
    if match:
        return match.group(1), "style_id"

    target_node_ids: list[str] = []
    if isinstance(gold_set_index_payload, dict):
        for row in gold_set_index_payload.get("target_nodes", []):
            if not isinstance(row, dict):
                continue
            node_id = _clean_text(row.get("node_id"))
            if node_id and node_id not in target_node_ids:
                target_node_ids.append(node_id)
        for row in gold_set_index_payload.get("cases", []):
            if not isinstance(row, dict):
                continue
            node_id = _clean_text(row.get("node_id"))
            if node_id and node_id not in target_node_ids:
                target_node_ids.append(node_id)
    if len(target_node_ids) == 1:
        return target_node_ids[0], "gold_set_single_target"

    scope_type = _clean_text(run_manifest.get("scope_type")) if isinstance(run_manifest, dict) else ""
    if target_node_ids:
        target_preview = ", ".join(target_node_ids[:3])
        if len(target_node_ids) > 3:
            target_preview = f"{target_preview}, ..."
        raise ValueError(
            "Unable to infer node_id from candidate metadata. "
            f"Gold set exposes multiple target nodes ({target_preview}). "
            "Provide --node-id explicitly."
        )
    if scope_type == "corpus":
        raise ValueError(
            "Unable to infer node_id for a corpus-scoped style bible. "
            "Provide --node-id explicitly or supply a gold set index with a single target node."
        )
    raise ValueError("Unable to infer node_id. Provide --node-id explicitly for this candidate.")


def _normalize_text(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _char_grams(value: str) -> set[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _containment_similarity(source_text: str, candidate_text: str) -> float:
    source = _normalize_text(source_text)
    candidate = _normalize_text(candidate_text)
    if not source or not candidate:
        return 0.0
    if source in candidate:
        return 1.0
    source_grams = _char_grams(source)
    candidate_grams = _char_grams(candidate)
    if not source_grams or not candidate_grams:
        return 0.0
    return round(len(source_grams & candidate_grams) / len(source_grams), 4)


def _append_unique_text(value: str, sink: list[str], seen: set[str]) -> None:
    cleaned = _clean_text(value).strip("，。；：、,.;:!?！？")
    normalized = _normalize_text(cleaned)
    if len(normalized) < 2 or normalized in seen or cleaned in SEMANTIC_STOPWORDS:
        return
    seen.add(normalized)
    sink.append(cleaned)


def _strip_semantic_scaffold(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    route_match = re.search(r"路由到(.+?)(?:节点|模块|分支|线)?$", text)
    if route_match:
        return _clean_text(route_match.group(1))
    note_match = re.search(r"关于(.+?)的关键表述$", text)
    if note_match:
        return _clean_text(note_match.group(1))
    template_match = re.search(r"(.+?)背后(?:其实)?是(.+?)的叙事模板$", text)
    if template_match:
        return _clean_text(template_match.group(2))

    stripped = text
    for prefix in SEMANTIC_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
            break
    for suffix in SEMANTIC_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
            break
    return _clean_text(stripped)


def _extract_semantic_fragments(value: str) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []

    candidate_texts = [text]
    route_match = re.search(r"路由到(.+?)(?:节点|模块|分支|线)?$", text)
    if route_match:
        candidate_texts.append(route_match.group(1))
    note_match = re.search(r"关于(.+?)的关键表述$", text)
    if note_match:
        candidate_texts.append(note_match.group(1))
    template_match = re.search(r"(.+?)背后(?:其实)?是(.+?)的叙事模板$", text)
    if template_match:
        candidate_texts.extend([template_match.group(1), template_match.group(2)])

    fragments: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_texts:
        parts = [candidate, *SEMANTIC_FRAGMENT_SPLIT_RE.split(candidate)]
        for part in parts:
            part = _clean_text(part)
            if not part:
                continue
            subparts = [part, *SEMANTIC_CONJUNCTION_SPLIT_RE.split(part)]
            for subpart in subparts:
                subpart = _clean_text(subpart)
                if not subpart:
                    continue
                _append_unique_text(subpart, fragments, seen)
                stripped = _strip_semantic_scaffold(subpart)
                if stripped and stripped != subpart:
                    _append_unique_text(stripped, fragments, seen)
                if "可" in stripped and len(_normalize_text(stripped)) >= 3:
                    _append_unique_text(stripped.replace("可", ""), fragments, seen)
    return fragments[:12]


def _semantic_similarity(source_text: str, candidate_text: str) -> float:
    base_score = _containment_similarity(source_text, candidate_text)
    fragments = _extract_semantic_fragments(source_text)
    if not fragments:
        return base_score
    fragment_scores = sorted(
        (_containment_similarity(fragment, candidate_text) for fragment in fragments),
        reverse=True,
    )
    top_fragment_scores = fragment_scores[: min(4, len(fragment_scores))]
    fragment_ratio = _average(top_fragment_scores)
    return round(max(base_score, (0.35 * base_score) + (0.65 * fragment_ratio)), 4)


def _collect_bundle_reference_ids(payload: Any) -> set[str]:
    refs: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            source_ref = _clean_text(node.get("source_ref"))
            if source_ref:
                refs.add(source_ref)
            source_refs = node.get("source_refs")
            if isinstance(source_refs, list):
                for item in source_refs:
                    cleaned = _clean_text(item)
                    if cleaned:
                        refs.add(cleaned)
            window_id = _clean_text(node.get("window_id"))
            if window_id:
                refs.add(window_id)
            scene_id = _clean_text(node.get("scene_id"))
            if scene_id:
                refs.add(f"scene:{scene_id}")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return refs


def _flatten_text_nodes(payload: Any, *, path: str = "") -> list[TextNode]:
    nodes: list[TextNode] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == "supporting_evidence" and isinstance(value, list):
                for index, item in enumerate(value):
                    if not isinstance(item, dict):
                        continue
                    claim = _clean_text(item.get("claim"))
                    evidence_text = _clean_text(item.get("evidence_text"))
                    source_ref = _clean_text(item.get("source_ref"))
                    if claim:
                        nodes.append(TextNode(path=f"{child_path}[{index}].claim", text=claim, source_ref=source_ref))
                    if evidence_text:
                        nodes.append(
                            TextNode(
                                path=f"{child_path}[{index}].evidence_text",
                                text=evidence_text,
                                source_ref=source_ref,
                            )
                        )
                continue
            nodes.extend(_flatten_text_nodes(value, path=child_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            child_path = f"{path}[{index}]"
            if isinstance(item, str):
                cleaned = _clean_text(item)
                if cleaned:
                    nodes.append(TextNode(path=child_path, text=cleaned))
            else:
                nodes.extend(_flatten_text_nodes(item, path=child_path))
    elif isinstance(payload, str):
        cleaned = _clean_text(payload)
        if cleaned:
            nodes.append(TextNode(path=path, text=cleaned))
    return nodes


def _extract_supporting_evidence_nodes(payload: dict[str, Any]) -> list[SupportingEvidenceNode]:
    rows: list[SupportingEvidenceNode] = []
    evidence_items = payload.get("supporting_evidence", [])
    if not isinstance(evidence_items, list):
        return rows
    for index, item in enumerate(evidence_items):
        if not isinstance(item, dict):
            continue
        rows.append(
            SupportingEvidenceNode(
                path=f"supporting_evidence[{index}]",
                claim=_clean_text(item.get("claim")),
                evidence_text=_clean_text(item.get("evidence_text")),
                source_ref=_clean_text(item.get("source_ref")),
            )
        )
    return rows


def _matches_prefix(path: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    return any(path.startswith(prefix) for prefix in prefixes)


def _candidate_nodes_by_prefix(nodes: list[TextNode], prefixes: list[str]) -> list[TextNode]:
    return [node for node in nodes if _matches_prefix(node.path, prefixes)]


def _best_matches(
    nodes: list[TextNode],
    target_texts: list[str],
    *,
    top_k: int = 5,
    semantic: bool = False,
) -> list[dict[str, Any]]:
    cleaned_targets = [_clean_text(item) for item in target_texts if len(_normalize_text(_clean_text(item))) >= 2]
    matches: list[dict[str, Any]] = []
    if not cleaned_targets:
        return matches
    similarity_fn = _semantic_similarity if semantic else _containment_similarity
    for node in nodes:
        best_target = ""
        best_score = 0.0
        for target in cleaned_targets:
            score = similarity_fn(target, node.text)
            if score > best_score:
                best_score = score
                best_target = target
        if best_score <= 0:
            continue
        matches.append(
            {
                "path": node.path,
                "text": node.text,
                "source_ref": node.source_ref,
                "score": round(best_score, 4),
                "matched_target": best_target,
            }
        )
    matches.sort(key=lambda row: (row["score"], len(row["text"])), reverse=True)
    unique: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in matches:
        if row["path"] in seen_paths:
            continue
        seen_paths.add(row["path"])
        unique.append(row)
        if len(unique) >= top_k:
            break
    return unique


def _any_keyword_hits(nodes: list[TextNode], keywords: list[str]) -> tuple[bool, list[str], list[dict[str, Any]]]:
    cleaned_keywords = [_clean_text(item) for item in keywords if len(_clean_text(item)) >= 1]
    if not cleaned_keywords:
        return False, [], []
    matched_keywords: list[str] = []
    matched_rows: list[dict[str, Any]] = []
    for keyword in cleaned_keywords:
        keyword_matched = False
        for node in nodes:
            if keyword in node.text:
                keyword_matched = True
                matched_rows.append(
                    {
                        "path": node.path,
                        "text": node.text,
                        "source_ref": node.source_ref,
                        "matched_target": keyword,
                        "score": 1.0,
                    }
                )
                break
        if keyword_matched:
            matched_keywords.append(keyword)
    unique_rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in matched_rows:
        if row["path"] in seen_paths:
            continue
        seen_paths.add(row["path"])
        unique_rows.append(row)
    return bool(matched_keywords), matched_keywords, unique_rows[:5]


def _semantic_keyword_hits(
    nodes: list[TextNode],
    keywords: list[str],
    *,
    min_score: float,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    cleaned_keywords = [_clean_text(item) for item in keywords if len(_normalize_text(_clean_text(item))) >= 2]
    if not cleaned_keywords:
        return False, [], []

    matched_keywords: list[str] = []
    matched_rows: list[dict[str, Any]] = []
    for keyword in cleaned_keywords:
        best_node: TextNode | None = None
        best_score = 0.0
        for node in nodes:
            score = _semantic_similarity(keyword, node.text)
            if score > best_score:
                best_score = score
                best_node = node
        if best_node is None or best_score < min_score:
            continue
        matched_keywords.append(keyword)
        matched_rows.append(
            {
                "path": best_node.path,
                "text": best_node.text,
                "source_ref": best_node.source_ref,
                "matched_target": keyword,
                "score": round(best_score, 4),
            }
        )

    unique_rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in sorted(matched_rows, key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True):
        if row["path"] in seen_paths:
            continue
        seen_paths.add(row["path"])
        unique_rows.append(row)
    return bool(matched_keywords), matched_keywords, unique_rows[:5]


def _coverage_ratio(
    exact_hits: list[str],
    semantic_hits: list[str],
    *,
    total_terms: int,
    budget_cap: int,
) -> float:
    if total_terms <= 0:
        return 1.0
    target_budget = max(1, min(total_terms, budget_cap))
    unique_hits = len(set(exact_hits) | set(semantic_hits))
    return round(min(unique_hits / target_budget, 1.0), 4)


def _case_context_targets(case: GoldSetCase, rules: JudgeRules) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    _append_unique_text(case.human_notes, targets, seen)
    for bucket_id in case.bucket_targets:
        _append_unique_text(bucket_id.replace("_", " "), targets, seen)
    for axis_id in case.required_axes:
        axis_group = rules.axis_groups.get(axis_id)
        if axis_group is None:
            continue
        _append_unique_text(axis_group.label, targets, seen)
        for keyword in axis_group.match_any[:4]:
            _append_unique_text(keyword, targets, seen)
    for mechanism in case.required_mechanisms:
        _append_unique_text(mechanism.label, targets, seen)
        _append_unique_text(mechanism.description, targets, seen)
        for keyword in mechanism.must_include_any[:4]:
            _append_unique_text(keyword, targets, seen)
        for keyword in mechanism.should_include_any[:3]:
            _append_unique_text(keyword, targets, seen)
    return targets


def _score(max_score: float, ratio: float) -> float:
    bounded = max(0.0, min(float(ratio), 1.0))
    return round(max_score * bounded, 2)


def _status_from_ratio(ratio: float, *, min_ratio: float, warn_ratio: float, reverse: bool = False) -> str:
    if reverse:
        if ratio <= min_ratio:
            return "pass"
        if ratio <= warn_ratio:
            return "warn"
        return "fail"
    if ratio >= min_ratio:
        return "pass"
    if ratio >= warn_ratio:
        return "warn"
    return "fail"


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _load_eval_summary(
    evaluation_report: dict[str, Any] | None,
    evaluation_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = evaluation_report.get("summary", {}) if isinstance(evaluation_report, dict) else {}
    manifest = evaluation_manifest if isinstance(evaluation_manifest, dict) else {}
    return {
        "status": _clean_text(summary.get("status")),
        "overall_score": float(summary.get("overall_score", 0) or 0),
        "max_score": float(summary.get("max_score", 0) or 0),
        "quality_gate_passed": bool(summary.get("quality_gate_passed", manifest.get("quality_gate_passed", False))),
        "check_counts": summary.get("check_counts", {}),
        "evaluation_id": _clean_text(manifest.get("evaluation_id")),
    }


def _case_expected_refs(case: GoldSetCase) -> list[str]:
    trace_refs = case.trace_expectations.get("required_trace_refs_any", [])
    evidence_refs = case.evidence_expectations.get("required_source_refs_any", [])
    values: list[Any] = [
        *case.must_hit_refs,
        *(trace_refs if isinstance(trace_refs, list) else []),
        *(evidence_refs if isinstance(evidence_refs, list) else []),
        *case.source_refs,
    ]
    return _unique_strings(values)


def _trace_grounding_ref_pool(reduce_trace_payload: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("grounding_ref_pool", "memo_ref_pool"):
        for ref in reduce_trace_payload.get(key, []):
            cleaned = _clean_text(ref)
            if cleaned:
                refs.add(cleaned)
    return refs


def _trace_local_reduce_rows(reduce_trace_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in reduce_trace_payload.get("local_reduces", []):
        if not isinstance(row, dict):
            continue
        grounding_refs = set(
            _unique_strings(
                list(row.get("grounding_ref_pool", []))
                if isinstance(row.get("grounding_ref_pool", []), list)
                else list(row.get("memo_ref_pool", []))
                if isinstance(row.get("memo_ref_pool", []), list)
                else []
            )
        )
        rows.append(
            {
                "bucket_id": _clean_text(row.get("bucket_id")),
                "memo_id": _clean_text(row.get("memo_id")),
                "batch_ids": _unique_strings(row.get("batch_ids", []) if isinstance(row.get("batch_ids", []), list) else []),
                "grounding_ref_pool": grounding_refs,
                "sparse": bool(row.get("sparse")),
            }
        )
    return rows


def _degradation_bucket_sets(
    reduce_trace_payload: dict[str, Any],
    style_bible_payload: dict[str, Any],
) -> tuple[set[str], set[str]]:
    degradation_status = style_bible_payload.get("metadata", {}).get("degradation_status", {})
    failed_bucket_ids = set(
        _unique_strings(
            [
                *(reduce_trace_payload.get("failed_bucket_ids", []) if isinstance(reduce_trace_payload.get("failed_bucket_ids", []), list) else []),
                *(degradation_status.get("failed_bucket_ids", []) if isinstance(degradation_status.get("failed_bucket_ids", []), list) else []),
            ]
        )
    )
    skipped_sparse_bucket_ids = set(
        _unique_strings(
            [
                *(
                    reduce_trace_payload.get("skipped_sparse_bucket_ids", [])
                    if isinstance(reduce_trace_payload.get("skipped_sparse_bucket_ids", []), list)
                    else []
                ),
                *(
                    degradation_status.get("skipped_sparse_buckets", [])
                    if isinstance(degradation_status.get("skipped_sparse_buckets", []), list)
                    else []
                ),
            ]
        )
    )
    return failed_bucket_ids, skipped_sparse_bucket_ids


def _build_case_scope_context(
    case: GoldSetCase,
    *,
    reduce_trace_payload: dict[str, Any],
    style_bible_payload: dict[str, Any],
) -> CaseScopeContext:
    local_reduce_rows = _trace_local_reduce_rows(reduce_trace_payload)
    failed_bucket_ids, skipped_sparse_bucket_ids = _degradation_bucket_sets(
        reduce_trace_payload,
        style_bible_payload,
    )
    target_bucket_ids = set(_unique_strings(case.bucket_targets))
    target_batch_ids = set(_unique_strings(case.batch_targets))
    expected_refs = _case_expected_refs(case)

    if target_bucket_ids or target_batch_ids:
        matched_rows = []
        for row in local_reduce_rows:
            bucket_id = _clean_text(row.get("bucket_id"))
            batch_ids = set(_unique_strings(row.get("batch_ids", [])))
            if target_bucket_ids and bucket_id not in target_bucket_ids:
                continue
            if target_batch_ids and not (batch_ids & target_batch_ids):
                continue
            matched_rows.append(row)
    else:
        matched_rows = local_reduce_rows

    active_rows = [row for row in matched_rows if not bool(row.get("sparse"))]
    case_scope_ref_pool: set[str] = set()
    for row in active_rows:
        case_scope_ref_pool.update(set(row.get("grounding_ref_pool", set())))

    effective_expected_refs = sorted(set(expected_refs) & case_scope_ref_pool)
    matched_bucket_ids = _unique_strings([row.get("bucket_id", "") for row in matched_rows])
    matched_batch_ids = _unique_strings(
        [batch_id for row in matched_rows for batch_id in row.get("batch_ids", [])]
    )

    applicable = True
    reason = "applicable"
    if target_bucket_ids or target_batch_ids:
        if not matched_rows:
            applicable = False
            reason = "target_scope_missing_from_run"
        elif not active_rows:
            applicable = False
            if target_bucket_ids & skipped_sparse_bucket_ids:
                reason = "target_scope_skipped_sparse"
            elif target_bucket_ids & failed_bucket_ids:
                reason = "target_scope_failed"
            else:
                reason = "target_scope_has_no_successful_local_reduce"
    if applicable and expected_refs and not effective_expected_refs:
        applicable = False
        reason = "expected_refs_out_of_scope"

    return CaseScopeContext(
        applicable=applicable,
        reason=reason,
        case_scope_ref_pool=case_scope_ref_pool,
        effective_expected_refs=effective_expected_refs,
        matched_bucket_ids=matched_bucket_ids,
        matched_batch_ids=matched_batch_ids,
        failed_bucket_ids=sorted(failed_bucket_ids),
        skipped_sparse_bucket_ids=sorted(skipped_sparse_bucket_ids),
    )


def _not_applicable_dimension(
    *,
    dimension: str,
    max_score: float,
    reason: str,
    case_scope_context: CaseScopeContext,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "ratio": 0.0,
        "status": "not_applicable",
        "score": 0.0,
        "max_score": 0.0,
        "matched_paths": [],
        "matched_source_refs": [],
        "details": {
            "reason": _clean_text(reason),
            "suppressed_max_score": max_score,
            "case_scope_ref_pool": sorted(case_scope_context.case_scope_ref_pool),
            "effective_expected_refs": list(case_scope_context.effective_expected_refs),
            "matched_bucket_ids": list(case_scope_context.matched_bucket_ids),
            "matched_batch_ids": list(case_scope_context.matched_batch_ids),
            "failed_bucket_ids": list(case_scope_context.failed_bucket_ids),
            "skipped_sparse_bucket_ids": list(case_scope_context.skipped_sparse_bucket_ids),
        },
    }


def _build_reduce_trace_maps(reduce_trace_payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    trace_rows: dict[str, dict[str, Any]] = {}
    trace_ref_pool: set[str] = set()
    for row in reduce_trace_payload.get("evidence_map", []):
        if not isinstance(row, dict):
            continue
        claim_id = _clean_text(row.get("claim_id"))
        if not claim_id:
            continue
        evidence_refs = _unique_strings(row.get("evidence_refs", []) if isinstance(row.get("evidence_refs", []), list) else [])
        trace_rows[claim_id] = {
            "claim_id": claim_id,
            "claim": _clean_text(row.get("claim")),
            "evidence_refs": evidence_refs,
        }
        trace_ref_pool.update(evidence_refs)
    trace_ref_pool.update(_trace_grounding_ref_pool(reduce_trace_payload))
    return trace_rows, trace_ref_pool


def _selected_reasoning_ids(case: GoldSetCase) -> list[str]:
    candidates: list[Any] = []
    for key in ("required_reasoning_ids", "reasoning_ids", "required_claim_ids", "claim_ids"):
        value = case.trace_expectations.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    return _unique_strings(candidates)


def _select_reasoning_entries(
    case: GoldSetCase,
    reasoning_bundle: StyleBibleReasoningBundle,
) -> list[Any]:
    entries = list(reasoning_bundle.entries)
    selected_ids = set(_selected_reasoning_ids(case))
    if selected_ids:
        selected = [entry for entry in entries if _clean_text(entry.reasoning_id) in selected_ids]
        if selected:
            return selected

    expected_refs = set(_case_expected_refs(case))
    candidates = entries
    if case.bucket_targets:
        bucket_filtered = [entry for entry in candidates if _clean_text(entry.bucket_id) in set(case.bucket_targets)]
        if bucket_filtered:
            candidates = bucket_filtered
    if expected_refs:
        ref_filtered = [entry for entry in candidates if expected_refs & set(_unique_strings(list(entry.evidence_refs)))]
        if ref_filtered:
            return ref_filtered
    return candidates


def _evaluate_trace_auditability(
    case: GoldSetCase,
    *,
    case_scope_context: CaseScopeContext,
    reasoning_bundle: StyleBibleReasoningBundle,
    reduce_trace_payload: dict[str, Any],
    evidence_nodes: list[SupportingEvidenceNode],
    rule_nodes: list[TextNode],
    routing_nodes: list[TextNode],
    worldbook_nodes: list[TextNode],
    rag_nodes: list[TextNode],
    rules: JudgeRules,
) -> dict[str, Any]:
    max_score = rules.weights.get("trace_auditability", 0.0)
    if not case_scope_context.applicable:
        return _not_applicable_dimension(
            dimension="trace_auditability",
            max_score=max_score,
            reason=case_scope_context.reason,
            case_scope_context=case_scope_context,
        )
    if not reasoning_bundle.entries:
        return {
            "dimension": "trace_auditability",
            "ratio": 0.0,
            "status": "fail",
            "score": 0.0,
            "max_score": max_score,
            "matched_paths": [],
            "matched_source_refs": [],
            "details": {"reason": "missing_reasoning_bundle"},
        }
    if not reduce_trace_payload:
        return {
            "dimension": "trace_auditability",
            "ratio": 0.0,
            "status": "fail",
            "score": 0.0,
            "max_score": max_score,
            "matched_paths": [],
            "matched_source_refs": [],
            "details": {"reason": "missing_reduce_trace"},
        }

    trace_rows_by_id, trace_ref_pool = _build_reduce_trace_maps(reduce_trace_payload)
    target_entries = _select_reasoning_entries(case, reasoning_bundle)
    if not target_entries:
        return {
            "dimension": "trace_auditability",
            "ratio": 0.0,
            "status": "fail",
            "score": 0.0,
            "max_score": max_score,
            "matched_paths": [],
            "matched_source_refs": [],
            "details": {
                "reason": "no_target_reasoning_entries",
                "bucket_targets": case.bucket_targets,
                "batch_targets": case.batch_targets,
                "expected_refs": list(case_scope_context.effective_expected_refs),
                "case_scope_ref_pool": sorted(case_scope_context.case_scope_ref_pool),
            },
        }

    expected_refs = list(case_scope_context.effective_expected_refs or _case_expected_refs(case))
    expected_ref_set = set(expected_refs)
    evidence_ref_set = {node.source_ref for node in evidence_nodes if _clean_text(node.source_ref)}
    searchable_nodes = [*rule_nodes, *routing_nodes, *worldbook_nodes, *rag_nodes]
    matched_paths: list[str] = []
    matched_source_refs: list[str] = []
    aligned_rows: list[dict[str, Any]] = []
    missing_trace_ids: list[str] = []
    aligned_scores: list[float] = []
    union_trace_refs: set[str] = set()
    trace_present_count = 0

    for entry in target_entries:
        reasoning_id = _clean_text(entry.reasoning_id)
        trace_row = trace_rows_by_id.get(reasoning_id, {})
        trace_refs = set(_unique_strings(trace_row.get("evidence_refs", []) if isinstance(trace_row, dict) else []))
        if trace_row:
            trace_present_count += 1
        else:
            missing_trace_ids.append(reasoning_id)
        entry_refs = set(_unique_strings(list(entry.evidence_refs)))
        union_trace_refs.update(trace_refs or entry_refs)
        target_texts = _unique_strings(
            [
                entry.claim,
                entry.observed_commonality,
                entry.mechanism_inference,
                entry.downstream_constraint,
            ]
        )
        best_rows = _best_matches(searchable_nodes, target_texts, top_k=3, semantic=True)
        alignment_ratio = _average([float(row.get("score", 0.0) or 0.0) for row in best_rows[:2]])
        aligned_scores.append(alignment_ratio)
        for row in best_rows:
            path = _clean_text(row.get("path"))
            if path:
                matched_paths.append(path)
            source_ref = _clean_text(row.get("source_ref"))
            if source_ref:
                matched_source_refs.append(source_ref)
        aligned_rows.append(
            {
                "reasoning_id": reasoning_id,
                "bucket_id": _clean_text(entry.bucket_id),
                "alignment_ratio": alignment_ratio,
                "trace_present": bool(trace_row),
                "trace_evidence_refs": sorted(trace_refs),
                "reasoning_evidence_refs": _unique_strings(list(entry.evidence_refs)),
                "matched_paths": [_clean_text(row.get("path")) for row in best_rows[:3] if _clean_text(row.get("path"))],
            }
        )

    trace_presence_ratio = _ratio(trace_present_count, len(target_entries))
    expected_ref_hit_ratio = _ratio(len(expected_ref_set & union_trace_refs), len(expected_ref_set)) if expected_ref_set else 1.0
    supporting_ref_hit_ratio = _ratio(len(expected_ref_set & evidence_ref_set), len(expected_ref_set)) if expected_ref_set else 1.0
    final_alignment_ratio = _average(aligned_scores)
    trace_pool_hit_ratio = _ratio(len(expected_ref_set & trace_ref_pool), len(expected_ref_set)) if expected_ref_set else 1.0
    ratio = round(
        (0.3 * trace_presence_ratio)
        + (0.25 * expected_ref_hit_ratio)
        + (0.2 * supporting_ref_hit_ratio)
        + (0.15 * trace_pool_hit_ratio)
        + (0.1 * final_alignment_ratio),
        4,
    )

    return {
        "dimension": "trace_auditability",
        "ratio": ratio,
        "status": _status_from_ratio(
            ratio,
            min_ratio=float(rules.thresholds.get("min_trace_audit_ratio", 0.65)),
            warn_ratio=float(rules.thresholds.get("warn_trace_audit_ratio", 0.45)),
        ),
        "score": _score(max_score, ratio),
        "max_score": max_score,
        "matched_paths": _unique_strings(matched_paths)[:5],
        "matched_source_refs": _unique_strings(matched_source_refs)[:5],
        "details": {
            "bucket_targets": case.bucket_targets,
            "batch_targets": case.batch_targets,
            "case_scope_ref_pool": sorted(case_scope_context.case_scope_ref_pool),
            "selected_reasoning_ids": [_clean_text(entry.reasoning_id) for entry in target_entries],
            "missing_trace_ids": missing_trace_ids,
            "expected_refs": expected_refs,
            "trace_presence_ratio": trace_presence_ratio,
            "expected_ref_hit_ratio": expected_ref_hit_ratio,
            "supporting_ref_hit_ratio": supporting_ref_hit_ratio,
            "trace_pool_hit_ratio": trace_pool_hit_ratio,
            "final_alignment_ratio": final_alignment_ratio,
            "aligned_rows": aligned_rows,
        },
    }


def _evaluate_axis_coverage(case: GoldSetCase, nodes: list[TextNode], rules: JudgeRules) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    hit_count = 0
    for axis_id in case.required_axes:
        axis_group = rules.axis_groups.get(axis_id)
        if axis_group is None:
            hits.append(
                {
                    "axis_id": axis_id,
                    "label": "",
                    "matched": False,
                    "matched_keywords": [],
                    "matched_paths": [],
                }
            )
            continue
        matched, matched_keywords, matched_rows = _any_keyword_hits(nodes, axis_group.match_any)
        if matched:
            hit_count += 1
        hits.append(
            {
                "axis_id": axis_group.axis_id,
                "label": axis_group.label,
                "matched": matched,
                "matched_keywords": matched_keywords,
                "matched_paths": [row["path"] for row in matched_rows],
            }
        )

    ratio = 1.0 if not case.required_axes else round(hit_count / len(case.required_axes), 4)
    return {
        "dimension": "axis_coverage",
        "ratio": ratio,
        "status": _status_from_ratio(
            ratio,
            min_ratio=float(rules.thresholds.get("min_axis_hit_ratio", 0.75)),
            warn_ratio=float(rules.thresholds.get("warn_axis_hit_ratio", 0.5)),
        ),
        "score": _score(rules.weights.get("axis_coverage", 0.0), ratio),
        "max_score": rules.weights.get("axis_coverage", 0.0),
        "matched_paths": [path for item in hits for path in item.get("matched_paths", [])][:5],
        "matched_source_refs": [],
        "details": {
            "axis_hits": hits,
            "required_axis_count": len(case.required_axes),
            "hit_count": hit_count,
        },
    }


def _evaluate_mechanism_specificity(
    case: GoldSetCase,
    nodes: list[TextNode],
    rules: JudgeRules,
    *,
    style_bible_payload: dict[str, Any],
) -> dict[str, Any]:
    candidate_nodes = _candidate_nodes_by_prefix(nodes, rules.rule_prefixes)
    rule_budget_ratio = _average_budget_ratio(
        style_bible_payload,
        STRUCTURE_RULE_LIST_PATHS,
        soft_max=int(rules.thresholds.get("rule_item_soft_max", 8) or 8),
        hard_max=int(rules.thresholds.get("rule_item_hard_max", 10) or 10),
    )
    rule_budget_multiplier = _budget_multiplier(rule_budget_ratio, floor=0.65)
    mechanism_rows: list[dict[str, Any]] = []
    mechanism_scores: list[float] = []
    matched_paths: list[str] = []
    matched_source_refs: list[str] = []

    for mechanism in case.required_mechanisms:
        description_targets = [mechanism.label, mechanism.description]
        description_matches = _best_matches(candidate_nodes, description_targets, top_k=3, semantic=True)
        semantic_target_matches = _best_matches(
            candidate_nodes,
            [
                mechanism.label,
                mechanism.description,
                *mechanism.must_include_any,
                *mechanism.should_include_any,
            ],
            top_k=5,
            semantic=True,
        )
        description_score = max(
            max((row["score"] for row in description_matches), default=0.0),
            _average([float(row.get("score", 0.0) or 0.0) for row in semantic_target_matches[:2]]),
        )
        exact_must_hit, matched_must_keywords, must_rows = _any_keyword_hits(candidate_nodes, mechanism.must_include_any)
        semantic_must_hit, semantic_must_keywords, semantic_must_rows = _semantic_keyword_hits(
            candidate_nodes,
            mechanism.must_include_any,
            min_score=float(rules.thresholds.get("similarity_floor", 0.4) or 0.4) * 0.8,
        )
        exact_should_hit, matched_should_keywords, should_rows = _any_keyword_hits(candidate_nodes, mechanism.should_include_any)
        semantic_should_hit, semantic_should_keywords, semantic_should_rows = _semantic_keyword_hits(
            candidate_nodes,
            mechanism.should_include_any,
            min_score=float(rules.thresholds.get("similarity_floor", 0.4) or 0.4) * 0.7,
        )
        must_hit = exact_must_hit or semantic_must_hit
        should_hit = exact_should_hit or semantic_should_hit
        must_coverage = _coverage_ratio(
            matched_must_keywords,
            semantic_must_keywords,
            total_terms=len(mechanism.must_include_any),
            budget_cap=3,
        )
        should_coverage = _coverage_ratio(
            matched_should_keywords,
            semantic_should_keywords,
            total_terms=len(mechanism.should_include_any),
            budget_cap=2,
        )
        must_credit = must_coverage
        should_credit = should_coverage
        if mechanism.must_include_any and not must_hit:
            must_credit = max(must_credit, min(description_score, 0.6))
        if mechanism.should_include_any and not should_hit:
            should_credit = max(should_credit, min(description_score, 0.4))
        base_ratio = round(
            (0.5 * description_score)
            + (0.3 * must_credit)
            + (0.2 * should_credit),
            4,
        )
        mechanism_ratio = round(base_ratio * rule_budget_multiplier, 4)
        mechanism_scores.append(mechanism_ratio)
        for row in description_matches + semantic_target_matches + must_rows + semantic_must_rows + should_rows + semantic_should_rows:
            matched_paths.append(row["path"])
            if row.get("source_ref"):
                matched_source_refs.append(row["source_ref"])
        mechanism_rows.append(
            {
                "label": mechanism.label,
                "description": mechanism.description,
                "ratio": mechanism_ratio,
                "must_hit": must_hit,
                "should_hit": should_hit,
                "description_score": description_score,
                "must_credit": must_credit,
                "should_credit": should_credit,
                "base_ratio": base_ratio,
                "matched_must_keywords": matched_must_keywords,
                "semantic_must_keywords": semantic_must_keywords,
                "matched_should_keywords": matched_should_keywords,
                "semantic_should_keywords": semantic_should_keywords,
                "matched_paths": [
                    row["path"]
                    for row in description_matches
                    + semantic_target_matches
                    + must_rows
                    + semantic_must_rows
                    + should_rows
                    + semantic_should_rows
                ][:5],
            }
        )

    ratio = _average(mechanism_scores)
    return {
        "dimension": "mechanism_specificity",
        "ratio": ratio,
        "status": _status_from_ratio(
            ratio,
            min_ratio=float(rules.thresholds.get("min_mechanism_ratio", 0.65)),
            warn_ratio=float(rules.thresholds.get("warn_mechanism_ratio", 0.45)),
        ),
        "score": _score(rules.weights.get("mechanism_specificity", 0.0), ratio),
        "max_score": rules.weights.get("mechanism_specificity", 0.0),
        "matched_paths": list(dict.fromkeys(matched_paths))[:5],
        "matched_source_refs": list(dict.fromkeys(item for item in matched_source_refs if item))[:5],
        "details": {
            "rule_budget_ratio": rule_budget_ratio,
            "budget_multiplier": rule_budget_multiplier,
            "mechanisms": mechanism_rows,
        },
    }


def _evaluate_evidence_faithfulness(
    case: GoldSetCase,
    evidence_nodes: list[SupportingEvidenceNode],
    valid_refs: set[str],
    rules: JudgeRules,
) -> dict[str, Any]:
    required_prefixes = [
        _clean_text(item)
        for item in case.evidence_expectations.get("required_source_ref_prefixes", [])
        if _clean_text(item)
    ]
    expected_refs = _case_expected_refs(case)
    min_supporting_evidence = int(case.evidence_expectations.get("min_supporting_evidence", 1) or 1)
    overlaps: list[SupportingEvidenceNode] = []
    for node in evidence_nodes:
        if node.source_ref and node.source_ref in expected_refs:
            overlaps.append(node)
    overlap_count = len({node.source_ref for node in overlaps if node.source_ref})
    valid_overlap_count = len({node.source_ref for node in overlaps if node.source_ref in valid_refs})
    overlap_ratio = min(overlap_count / max(min_supporting_evidence, 1), 1.0)
    valid_overlap_ratio = 0.0 if overlap_count == 0 else round(valid_overlap_count / overlap_count, 4)
    all_valid_ref_ratio = _valid_ref_ratio(evidence_nodes, valid_refs)
    prefix_ratio = _required_prefix_ratio(required_prefixes, overlaps, evidence_nodes)
    evidence_budget_ratio = _budget_ratio(
        len(evidence_nodes),
        soft_max=int(rules.thresholds.get("supporting_evidence_soft_max", 18) or 18),
        hard_max=int(rules.thresholds.get("supporting_evidence_hard_max", 20) or 20),
    )
    evidence_budget_multiplier = _budget_multiplier(evidence_budget_ratio, floor=0.4)

    evidence_similarity_targets = [case.human_notes]
    for mechanism in case.required_mechanisms:
        evidence_similarity_targets.extend([mechanism.label, mechanism.description])
    similarity_scores: list[float] = []
    for node in overlaps:
        best = max(
            [_semantic_similarity(target, node.claim) for target in evidence_similarity_targets if len(_clean_text(target)) >= 2]
            + [_semantic_similarity(target, node.evidence_text) for target in evidence_similarity_targets if len(_clean_text(target)) >= 2],
            default=0.0,
        )
        similarity_scores.append(best)
    similarity_ratio = _average(similarity_scores)
    global_similarity_rows: list[dict[str, Any]] = []
    for node in evidence_nodes:
        best_target = ""
        best_score = 0.0
        for target in evidence_similarity_targets:
            if len(_clean_text(target)) < 2:
                continue
            score = max(
                _semantic_similarity(target, node.claim),
                _semantic_similarity(target, node.evidence_text),
            )
            if score > best_score:
                best_score = score
                best_target = target
        if best_score <= 0:
            continue
        global_similarity_rows.append(
            {
                "path": node.path,
                "claim": node.claim,
                "evidence_text": node.evidence_text,
                "source_ref": node.source_ref,
                "score": round(best_score, 4),
                "matched_target": best_target,
            }
        )
    global_similarity_rows.sort(key=lambda row: float(row.get("score", 0.0) or 0.0), reverse=True)
    top_global_rows = global_similarity_rows[: max(1, min(3, max(min_supporting_evidence, 2)))]
    global_similarity_ratio = _average([float(row.get("score", 0.0) or 0.0) for row in top_global_rows])
    if overlap_count > 0:
        base_ratio = round(
            (0.45 * overlap_ratio)
            + (0.15 * prefix_ratio)
            + (0.15 * similarity_ratio)
            + (0.15 * global_similarity_ratio)
            + (0.1 * valid_overlap_ratio),
            4,
        )
        ratio = round(base_ratio * evidence_budget_multiplier, 4)
    else:
        base_ratio = round(
            (0.65 * global_similarity_ratio)
            + (0.2 * prefix_ratio)
            + (0.15 * all_valid_ref_ratio),
            4,
        )
        ratio = round(min(base_ratio * evidence_budget_multiplier, 0.55), 4)

    return {
        "dimension": "evidence_faithfulness",
        "ratio": ratio,
        "status": _status_from_ratio(
            ratio,
            min_ratio=float(rules.thresholds.get("min_evidence_ratio", 0.5)),
            warn_ratio=float(rules.thresholds.get("warn_evidence_ratio", 0.25)),
        ),
        "score": _score(rules.weights.get("evidence_faithfulness", 0.0), ratio),
        "max_score": rules.weights.get("evidence_faithfulness", 0.0),
        "matched_paths": [node.path for node in overlaps][:5],
        "matched_source_refs": [node.source_ref for node in overlaps if node.source_ref][:5],
        "details": {
            "expected_refs": expected_refs,
            "required_prefixes": required_prefixes,
            "overlap_count": overlap_count,
            "valid_overlap_count": valid_overlap_count,
            "valid_overlap_ratio": valid_overlap_ratio,
            "all_valid_ref_ratio": all_valid_ref_ratio,
            "min_supporting_evidence": min_supporting_evidence,
            "prefix_ratio": prefix_ratio,
            "similarity_ratio": similarity_ratio,
            "global_similarity_ratio": global_similarity_ratio,
            "evidence_budget_ratio": evidence_budget_ratio,
            "budget_multiplier": evidence_budget_multiplier,
            "fallback_mode": overlap_count == 0,
            "top_global_supporting_evidence": top_global_rows,
        },
    }


def _evaluate_downstream_dimension(
    *,
    case: GoldSetCase,
    case_scope_context: CaseScopeContext,
    dimension: str,
    required_items: list[str],
    candidate_nodes: list[TextNode],
    fallback_nodes: list[TextNode],
    rules: JudgeRules,
    weight_key: str,
) -> dict[str, Any]:
    max_score = rules.weights.get(weight_key, 0.0)
    if not case_scope_context.applicable:
        return _not_applicable_dimension(
            dimension=dimension,
            max_score=max_score,
            reason=case_scope_context.reason,
            case_scope_context=case_scope_context,
        )
    if not required_items:
        return _not_applicable_dimension(
            dimension=dimension,
            max_score=max_score,
            reason="no_required_items",
            case_scope_context=case_scope_context,
        )
    item_rows: list[dict[str, Any]] = []
    item_scores: list[float] = []
    matched_paths: list[str] = []
    matched_source_refs: list[str] = []
    surface_budget_ratio = _budget_ratio(
        len(candidate_nodes),
        soft_max=int(rules.thresholds.get("surface_item_soft_max", 8) or 8),
        hard_max=int(rules.thresholds.get("surface_item_hard_max", 10) or 10),
    )
    surface_budget_multiplier = _budget_multiplier(surface_budget_ratio, floor=0.4)
    context_targets = _case_context_targets(case, rules)
    surface_context_matches = _best_matches(candidate_nodes, context_targets, top_k=3, semantic=True)
    fallback_context_matches = _best_matches(fallback_nodes, context_targets, top_k=3, semantic=True)
    surface_context_ratio = _average([float(row.get("score", 0.0) or 0.0) for row in surface_context_matches])
    fallback_context_ratio = _average([float(row.get("score", 0.0) or 0.0) for row in fallback_context_matches])
    surface_weight = 0.85 if dimension == "routing_executability" else 0.75 if dimension == "worldbook_exportability" else 0.7

    for required_item in required_items:
        surface_match = _best_matches(candidate_nodes, [required_item], top_k=1, semantic=True)
        surface_row = surface_match[0] if surface_match else {}
        fallback_match = _best_matches(fallback_nodes, [required_item], top_k=1, semantic=True)
        fallback_row = fallback_match[0] if fallback_match else {}
        direct_surface_similarity = float(surface_row.get("score", 0.0) or 0.0)
        direct_fallback_similarity = float(fallback_row.get("score", 0.0) or 0.0)
        surface_alignment = round((0.65 * direct_surface_similarity) + (0.35 * surface_context_ratio), 4)
        fallback_alignment = round(max(direct_fallback_similarity, fallback_context_ratio), 4)
        similarity = round((surface_weight * surface_alignment) + ((1.0 - surface_weight) * fallback_alignment), 4)
        if dimension == "routing_executability" and _clean_text(surface_row.get("text")) and "路由到" in _clean_text(surface_row.get("text")):
            similarity = min(1.0, round(similarity + 0.05, 4))
        if dimension == "rag_atomicity":
            atomic_text = _clean_text(surface_row.get("text")) or _clean_text(fallback_row.get("text"))
            item_length = len(atomic_text)
            min_length = int(rules.thresholds.get("rag_atomic_length_min", 6) or 6)
            max_length = int(rules.thresholds.get("rag_atomic_length_max", 40) or 40)
            atomicity_bonus = 1.0 if min_length <= item_length <= max_length else 0.85 if item_length <= (max_length * 2) else 0.7
            similarity = round(similarity * atomicity_bonus, 4)
        item_scores.append(similarity)
        for row in (surface_row, fallback_row):
            path = _clean_text(row.get("path"))
            if path:
                matched_paths.append(path)
            source_ref = _clean_text(row.get("source_ref"))
            if source_ref:
                matched_source_refs.append(source_ref)
        item_rows.append(
            {
                "required_item": required_item,
                "surface_match_path": _clean_text(surface_row.get("path")),
                "surface_match_text": _clean_text(surface_row.get("text")),
                "surface_similarity": direct_surface_similarity,
                "fallback_match_path": _clean_text(fallback_row.get("path")),
                "fallback_match_text": _clean_text(fallback_row.get("text")),
                "fallback_similarity": direct_fallback_similarity,
                "surface_context_similarity": surface_context_ratio,
                "fallback_context_similarity": fallback_context_ratio,
                "surface_alignment": surface_alignment,
                "fallback_alignment": fallback_alignment,
                "similarity": similarity,
            }
        )

    ratio = round(_average(item_scores) * surface_budget_multiplier, 4)
    return {
        "dimension": dimension,
        "ratio": ratio,
        "status": _status_from_ratio(
            ratio,
            min_ratio=float(rules.thresholds.get("min_downstream_similarity", 0.4)),
            warn_ratio=float(rules.thresholds.get("warn_downstream_similarity", 0.25)),
        ),
        "score": _score(max_score, ratio),
        "max_score": max_score,
        "matched_paths": list(dict.fromkeys(item for item in matched_paths if item))[:5],
        "matched_source_refs": list(dict.fromkeys(item for item in matched_source_refs if item))[:5],
        "details": {
            "required_item_count": len(required_items),
            "case_scope_ref_pool": sorted(case_scope_context.case_scope_ref_pool),
            "items": item_rows,
            "candidate_item_count": len(candidate_nodes),
            "fallback_item_count": len(fallback_nodes),
            "surface_budget_ratio": surface_budget_ratio,
            "budget_multiplier": surface_budget_multiplier,
            "case_context_targets": context_targets,
            "surface_context_matches": surface_context_matches,
            "fallback_context_matches": fallback_context_matches,
        },
    }


def _evaluate_prompt_preset_usability(
    case: GoldSetCase,
    nodes: list[TextNode],
    rules: JudgeRules,
    *,
    style_bible_payload: dict[str, Any],
) -> dict[str, Any]:
    candidate_nodes = _candidate_nodes_by_prefix(nodes, rules.rule_prefixes)
    rule_budget_ratio = _average_budget_ratio(
        style_bible_payload,
        STRUCTURE_RULE_LIST_PATHS,
        soft_max=int(rules.thresholds.get("rule_item_soft_max", 8) or 8),
        hard_max=int(rules.thresholds.get("rule_item_hard_max", 10) or 10),
    )
    rule_budget_multiplier = _budget_multiplier(rule_budget_ratio, floor=0.5)
    target_texts: list[str] = []
    for mechanism in case.required_mechanisms:
        target_texts.extend(
            [
                mechanism.label,
                mechanism.description,
                *mechanism.must_include_any,
                *mechanism.should_include_any,
            ]
        )
    target_texts.extend(case.required_downstream_surfaces.get("routing_hints", []))
    best_rows = _best_matches(candidate_nodes, target_texts, top_k=5)
    match_ratio = _average([float(row.get("score", 0.0) or 0.0) for row in best_rows])
    actionable_hits = 0
    for row in best_rows:
        text = _clean_text(row.get("text"))
        if any(text.startswith(cue) or cue in text[:10] for cue in rules.actionable_cues):
            actionable_hits += 1
    actionable_ratio = 0.0 if not best_rows else round(actionable_hits / len(best_rows), 4)
    base_ratio = round((0.6 * match_ratio) + (0.4 * actionable_ratio), 4)
    ratio = round(base_ratio * rule_budget_multiplier, 4)
    return {
        "dimension": "prompt_preset_usability",
        "ratio": ratio,
        "status": _status_from_ratio(
            ratio,
            min_ratio=float(rules.thresholds.get("min_prompt_ratio", 0.35)),
            warn_ratio=float(rules.thresholds.get("warn_prompt_ratio", 0.2)),
        ),
        "score": _score(rules.weights.get("prompt_preset_usability", 0.0), ratio),
        "max_score": rules.weights.get("prompt_preset_usability", 0.0),
        "matched_paths": [row["path"] for row in best_rows][:5],
        "matched_source_refs": [row["source_ref"] for row in best_rows if _clean_text(row.get("source_ref"))][:5],
        "details": {
            "base_ratio": base_ratio,
            "match_ratio": match_ratio,
            "actionable_ratio": actionable_ratio,
            "rule_budget_ratio": rule_budget_ratio,
            "budget_multiplier": rule_budget_multiplier,
            "top_matches": best_rows,
        },
    }


def _evaluate_anti_genericity(case: GoldSetCase, nodes: list[TextNode], rules: JudgeRules) -> dict[str, Any]:
    candidate_nodes = _candidate_nodes_by_prefix(nodes, rules.rule_prefixes)
    target_texts: list[str] = []
    for mechanism in case.required_mechanisms:
        target_texts.extend([mechanism.label, mechanism.description])
    best_rows = _best_matches(candidate_nodes, target_texts, top_k=5)
    generic_hits = 0
    for row in best_rows:
        text = _clean_text(row.get("text"))
        if any(pattern in text for pattern in rules.generic_patterns):
            generic_hits += 1
    generic_ratio = 1.0 if not best_rows else round(generic_hits / len(best_rows), 4)
    ratio = round(1.0 - generic_ratio, 4) if best_rows else 0.0
    return {
        "dimension": "anti_genericity",
        "ratio": ratio,
        "status": _status_from_ratio(
            generic_ratio,
            min_ratio=float(rules.thresholds.get("max_generic_ratio", 0.2)),
            warn_ratio=float(rules.thresholds.get("warn_generic_ratio", 0.4)),
            reverse=True,
        ),
        "score": _score(rules.weights.get("anti_genericity", 0.0), ratio),
        "max_score": rules.weights.get("anti_genericity", 0.0),
        "matched_paths": [row["path"] for row in best_rows][:5],
        "matched_source_refs": [row["source_ref"] for row in best_rows if _clean_text(row.get("source_ref"))][:5],
        "details": {
            "generic_ratio": generic_ratio,
            "generic_hits": generic_hits,
            "top_matches": best_rows,
        },
    }


def _evaluate_anti_pattern_resistance(
    case: GoldSetCase,
    *,
    all_nodes: list[TextNode],
    rule_nodes: list[TextNode],
    routing_nodes: list[TextNode],
    worldbook_nodes: list[TextNode],
    rag_nodes: list[TextNode],
    rules: JudgeRules,
) -> dict[str, Any]:
    generic_hits = [node for node in rule_nodes if any(pattern in node.text for pattern in rules.generic_patterns)]
    vague_routing_hits = [
        node
        for node in routing_nodes
        if not any(prefix in node.text for prefix in ("路由到", "路由至", "进入", "归到", "节点"))
        or not any(trigger in node.text for trigger in ("当", "如果", "遇到", "出现", "凡是", "涉及"))
    ]
    keyword_stuffing_hits = [
        node
        for node in rule_nodes
        if len([part for part in re.split(r"[、,，/|；;：:\s]+", node.text) if _clean_text(part)]) >= 4
        and not any(cue in node.text for cue in rules.actionable_cues)
    ]
    worldbook_candidates = worldbook_nodes + rag_nodes
    ungrounded_worldbook_hits = [
        node
        for node in worldbook_candidates
        if not any(prefix in node.text for prefix in ("机构", "规则", "门槛", "资格", "资源", "制度", "节点", "世界书"))
    ]

    similarity_floor = float(rules.thresholds.get("forbidden_output_similarity", 0.78) or 0.78)
    forbidden_output_hits: list[dict[str, Any]] = []
    for pattern in [*case.forbidden_patterns, *case.forbidden_outputs]:
        best_match = _best_matches(all_nodes, [pattern], top_k=1, semantic=True)
        if not best_match:
            continue
        row = best_match[0]
        text = _clean_text(row.get("text"))
        if pattern in text or float(row.get("score", 0.0) or 0.0) >= similarity_floor:
            forbidden_output_hits.append(
                {
                    "pattern": pattern,
                    "path": _clean_text(row.get("path")),
                    "source_ref": _clean_text(row.get("source_ref")),
                    "score": float(row.get("score", 0.0) or 0.0),
                }
            )

    pattern_rows = {
        "GENERIC_MECHANISM": 0.0 if not rule_nodes else round(len(generic_hits) / len(rule_nodes), 4),
        "VAGUE_ROUTING": 0.0 if not routing_nodes else round(len(vague_routing_hits) / len(routing_nodes), 4),
        "KEYWORD_STUFFING": 0.0 if not rule_nodes else round(len(keyword_stuffing_hits) / len(rule_nodes), 4),
        "UNGROUNDED_WORLDBOOK": 0.0 if not worldbook_candidates else round(len(ungrounded_worldbook_hits) / len(worldbook_candidates), 4),
    }
    monitored_codes = _unique_strings(case.anti_pattern_watchlist)
    if not monitored_codes:
        monitored_codes = ["GENERIC_MECHANISM", "VAGUE_ROUTING", "KEYWORD_STUFFING", "UNGROUNDED_WORLDBOOK"]
    active_ratios = [pattern_rows[code] for code in monitored_codes if code in pattern_rows and pattern_rows[code] > 0]
    if forbidden_output_hits:
        active_ratios.append(1.0)
    violation_ratio = _average(active_ratios) if active_ratios else 0.0
    ratio = round(1.0 - violation_ratio, 4)
    return {
        "dimension": "anti_pattern_resistance",
        "ratio": ratio,
        "status": _status_from_ratio(
            violation_ratio,
            min_ratio=float(rules.thresholds.get("max_anti_pattern_violation_ratio", 0.15)),
            warn_ratio=float(rules.thresholds.get("warn_anti_pattern_violation_ratio", 0.3)),
            reverse=True,
        ),
        "score": _score(rules.weights.get("anti_pattern_resistance", 0.0), ratio),
        "max_score": rules.weights.get("anti_pattern_resistance", 0.0),
        "matched_paths": [
            *(node.path for node in generic_hits[:2]),
            *(node.path for node in vague_routing_hits[:2]),
            *(node.path for node in keyword_stuffing_hits[:2]),
            *(node.path for node in ungrounded_worldbook_hits[:2]),
            *(row["path"] for row in forbidden_output_hits[:2] if _clean_text(row.get("path"))),
        ][:5],
        "matched_source_refs": _unique_strings(
            [row["source_ref"] for row in forbidden_output_hits if _clean_text(row.get("source_ref"))]
        )[:5],
        "details": {
            "case_id": case.case_id,
            "bucket_targets": case.bucket_targets,
            "violation_ratio": violation_ratio,
            "monitored_codes": monitored_codes,
            "registry_rows": {
                code: rules.anti_pattern_registry.get(code, {})
                for code in monitored_codes
                if rules.anti_pattern_registry.get(code)
            },
            "generic_mechanism_hits": [node.path for node in generic_hits[:4]],
            "vague_routing_hits": [node.path for node in vague_routing_hits[:4]],
            "keyword_stuffing_hits": [node.path for node in keyword_stuffing_hits[:4]],
            "ungrounded_worldbook_hits": [node.path for node in ungrounded_worldbook_hits[:4]],
            "forbidden_output_hits": forbidden_output_hits[:4],
        },
    }


def _build_case_rows(
    *,
    case: GoldSetCase,
    dimensions: list[dict[str, Any]],
    style_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        rows.append(
            {
                "row_id": f"{case.case_id}::{dimension['dimension']}",
                "criterion_id": dimension["dimension"],
                "case_id": case.case_id,
                "node_id": case.node_id,
                "style_id": style_id,
                "run_id": run_id,
                "dimension": dimension["dimension"],
                "score": dimension["score"],
                "max_score": dimension["max_score"],
                "ratio": dimension["ratio"],
                "status": dimension["status"],
                "matched_paths": dimension.get("matched_paths", []),
                "matched_source_refs": dimension.get("matched_source_refs", []),
                "case_source_refs": case.source_refs,
                "details": dimension.get("details", {}),
            }
        )
    return rows


def _summarize_cases(
    *,
    case_results: list[dict[str, Any]],
    judge_rules: JudgeRules,
) -> dict[str, Any]:
    total_case_max_score = round(sum(judge_rules.weights.values()), 2)
    pass_score_ratio = round(judge_rules.pass_score / total_case_max_score, 4) if total_case_max_score > 0 else 0.0
    warn_score_ratio = round(judge_rules.warn_score / total_case_max_score, 4) if total_case_max_score > 0 else 0.0

    applicable_cases = [row for row in case_results if row.get("status") != "not_applicable"]
    overall_score = _average([float(row.get("score", 0) or 0) for row in applicable_cases])
    max_score = _average([float(row.get("max_score", 0) or 0) for row in applicable_cases])
    overall_ratio = round(overall_score / max_score, 4) if max_score > 0 else 0.0

    pass_count = sum(1 for row in applicable_cases if row.get("status") == "pass")
    warn_count = sum(1 for row in applicable_cases if row.get("status") == "warn")
    fail_count = sum(1 for row in applicable_cases if row.get("status") == "fail")
    not_applicable_count = sum(1 for row in case_results if row.get("status") == "not_applicable")
    pass_case_ratio = 0.0 if not applicable_cases else round(pass_count / len(applicable_cases), 4)
    if not applicable_cases:
        status = "not_applicable"
    elif overall_ratio >= pass_score_ratio and pass_case_ratio >= float(
        judge_rules.thresholds.get("min_pass_case_ratio", 0.6)
    ):
        status = "pass"
    elif overall_ratio >= warn_score_ratio or pass_case_ratio >= float(
        judge_rules.thresholds.get("warn_pass_case_ratio", 0.45)
    ):
        status = "warn"
    else:
        status = "fail"

    dimension_names = list(judge_rules.weights.keys())
    dimension_averages: dict[str, float] = {}
    for dimension_name in dimension_names:
        applicable_dimension_scores = [
            float(row.get("dimension_scores", {}).get(dimension_name, {}).get("score", 0) or 0)
            for row in applicable_cases
            if row.get("dimension_scores", {}).get(dimension_name, {}).get("status") != "not_applicable"
        ]
        dimension_averages[dimension_name] = _average(
            [
                *applicable_dimension_scores,
            ]
        )

    return {
        "status": status,
        "overall_score": overall_score,
        "max_score": max_score,
        "overall_ratio": overall_ratio,
        "pass_score": judge_rules.pass_score,
        "warn_score": judge_rules.warn_score,
        "pass_score_ratio": pass_score_ratio,
        "warn_score_ratio": warn_score_ratio,
        "case_count": len(case_results),
        "applicable_case_count": len(applicable_cases),
        "not_applicable_case_count": not_applicable_count,
        "pass_case_count": pass_count,
        "warn_case_count": warn_count,
        "fail_case_count": fail_count,
        "pass_case_ratio": pass_case_ratio,
        "quality_gate_passed": status == "pass",
        "dimension_scores": dimension_averages,
    }


def _build_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    semantic_observability = report.get("semantic_observability", {})
    lines = [
        "# Judge Style Bible Report v2",
        "",
        f"- style_id: `{_clean_text(report.get('style_id'))}`",
        f"- run_id: `{_clean_text(report.get('run_id'))}`",
        f"- node_id: `{_clean_text(report.get('node_id'))}`",
        f"- status: `{_clean_text(summary.get('status'))}`",
        f"- overall_score: `{summary.get('overall_score', 0)}` / `{summary.get('max_score', 0)}`",
        f"- overall_ratio: `{summary.get('overall_ratio', 0)}`",
        f"- pass_case_ratio: `{summary.get('pass_case_ratio', 0)}`",
        f"- applicable_case_count: `{summary.get('applicable_case_count', 0)}`",
        f"- not_applicable_case_count: `{summary.get('not_applicable_case_count', 0)}`",
        "",
        "## Case Summary",
        "",
        "| case_id | status | score | top failing dimensions |",
        "| --- | --- | ---: | --- |",
    ]

    for case_result in report.get("case_results", []):
        failing_dimensions = [
            name
            for name, payload in case_result.get("dimension_scores", {}).items()
            if isinstance(payload, dict) and payload.get("status") in {"fail", "warn"}
        ]
        lines.append(
            f"| `{case_result.get('case_id', '')}` | `{case_result.get('status', '')}` | "
            f"{case_result.get('score', 0)} | {', '.join(failing_dimensions[:4]) or '-'} |"
        )

    lines.extend(
        [
            "",
            "## Dimension Averages",
            "",
            "| dimension | average score |",
            "| --- | ---: |",
        ]
    )
    for dimension, score in summary.get("dimension_scores", {}).items():
        lines.append(f"| `{dimension}` | {score} |")

    if isinstance(semantic_observability, dict) and semantic_observability:
        lines.extend(["", "## Semantic Sidecar", ""])
        for key in ("semantic_score", "lexical_prior_score", "evidence_overlap_score", "final_decision_source"):
            lines.append(f"- {key}: `{semantic_observability.get(key, '')}`")

    return "\n".join(lines) + "\n"


def run_style_bible_judge(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    gold_set_index: str | Path,
    judge_rules_config: str | Path,
    node_id: str = "",
    evaluation_dir: str | Path | None = None,
    resume: bool = False,
) -> JudgeResult | None:
    source_dir = Path(input_dir).resolve()
    output_path = ensure_dir(output_dir).resolve()
    report_path = output_path / JUDGE_REPORT_JSON_FILE
    markdown_path = output_path / JUDGE_REPORT_MD_FILE
    rows_path = output_path / JUDGE_ROWS_JSONL_FILE

    if resume and report_path.exists() and markdown_path.exists() and rows_path.exists():
        return None

    style_bible_path = source_dir / STYLE_BIBLE_FILE
    reasoning_path = source_dir / REASONING_FILE
    reduce_trace_path = source_dir / REDUCE_TRACE_FILE
    judge_flat_path = source_dir / JUDGE_FLAT_FILE
    export_flat_path = source_dir / EXPORT_FLAT_FILE
    source_bundle_path = source_dir / SOURCE_BUNDLE_FILE
    if not style_bible_path.exists():
        raise FileNotFoundError(f"Style bible file not found: {style_bible_path}")
    if not source_bundle_path.exists():
        raise FileNotFoundError(f"Style bible source bundle not found: {source_bundle_path}")

    tracker = RunTracker(
        stage="stable-judge-style-bible",
        output_dir=output_path,
        total_items=1,
        item_label="style_bible",
        source_dir=source_dir,
        metadata={
            "gold_set_index": str(Path(gold_set_index).resolve()),
            "judge_rules_config": str(Path(judge_rules_config).resolve()),
        },
    )

    try:
        style_bible_payload = read_json(style_bible_path)
        reasoning_payload = read_json(reasoning_path) if reasoning_path.exists() else {}
        reduce_trace_payload = read_json(reduce_trace_path) if reduce_trace_path.exists() else {}
        judge_flat_payload = read_json(judge_flat_path) if judge_flat_path.exists() else {}
        export_flat_payload = read_json(export_flat_path) if export_flat_path.exists() else {}
        source_bundle = read_json(source_bundle_path)
        if not isinstance(style_bible_payload, dict):
            raise ValueError(f"Style bible payload must be an object: {style_bible_path}")
        if reasoning_payload and not isinstance(reasoning_payload, dict):
            raise ValueError(f"Reasoning payload must be an object: {reasoning_path}")
        if reduce_trace_payload and not isinstance(reduce_trace_payload, dict):
            raise ValueError(f"Reduce trace payload must be an object: {reduce_trace_path}")
        if judge_flat_payload and not isinstance(judge_flat_payload, dict):
            raise ValueError(f"Judge flat payload must be an object: {judge_flat_path}")
        if export_flat_payload and not isinstance(export_flat_payload, dict):
            raise ValueError(f"Flat export payload must be an object: {export_flat_path}")
        if not isinstance(source_bundle, dict):
            raise ValueError(f"Source bundle payload must be an object: {source_bundle_path}")
        normalized_payload, projection_source = _select_judge_projection_payload(
            style_bible_payload=style_bible_payload,
            judge_flat_payload=judge_flat_payload,
            export_flat_payload=export_flat_payload,
        )
        StyleBibleResult.model_validate(normalized_payload)
        try:
            reasoning_bundle = StyleBibleReasoningBundle.model_validate(reasoning_payload) if reasoning_payload else StyleBibleReasoningBundle()
            reasoning_bundle_error = ""
        except Exception as exc:  # noqa: BLE001
            reasoning_bundle = StyleBibleReasoningBundle()
            reasoning_bundle_error = f"{type(exc).__name__}: {exc}"

        run_manifest_path, run_manifest = _try_load_run_manifest(source_dir)
        resolved_gold_set_index = Path(gold_set_index).resolve()
        gold_set_index_payload = read_json(resolved_gold_set_index) if resolved_gold_set_index.exists() else None
        if not isinstance(gold_set_index_payload, dict):
            gold_set_index_payload = None
        resolved_node_id, node_id_inference_source = _infer_node_id(
            explicit_node_id=node_id,
            run_manifest=run_manifest,
            style_bible_payload=style_bible_payload,
            gold_set_index_payload=gold_set_index_payload,
        )
        index_payload, gold_cases, gold_set_hash = _load_gold_set_cases(gold_set_index, node_id=resolved_node_id)
        judge_rules = _load_judge_rules(judge_rules_config)
        valid_refs = _collect_bundle_reference_ids(source_bundle)
        text_nodes = _flatten_text_nodes(normalized_payload)
        evidence_nodes = _extract_supporting_evidence_nodes(normalized_payload)
        rule_nodes = _candidate_nodes_by_prefix(text_nodes, judge_rules.rule_prefixes)
        routing_nodes = _candidate_nodes_by_prefix(text_nodes, judge_rules.routing_prefixes)
        worldbook_nodes = _candidate_nodes_by_prefix(text_nodes, judge_rules.worldbook_prefixes)
        rag_nodes = _candidate_nodes_by_prefix(text_nodes, judge_rules.rag_prefixes)

        resolved_eval_dir = Path(evaluation_dir).resolve() if evaluation_dir else _auto_detect_eval_dir(source_dir)
        eval_report_path, eval_report, eval_manifest_path, eval_manifest = _try_load_eval_report(resolved_eval_dir)
        eval_summary = _load_eval_summary(eval_report, eval_manifest)
        runtime_flags = load_style_bible_runtime_flags()

        case_results: list[dict[str, Any]] = []
        judge_rows: list[dict[str, Any]] = []
        dimension_order = [
            "axis_coverage",
            "mechanism_specificity",
            "evidence_faithfulness",
            "trace_auditability",
            "routing_executability",
            "worldbook_exportability",
            "rag_atomicity",
            "prompt_preset_usability",
            "anti_genericity",
            "anti_pattern_resistance",
        ]
        total_case_max_score = round(sum(judge_rules.weights.values()), 2)
        pass_score_ratio = round(judge_rules.pass_score / total_case_max_score, 4) if total_case_max_score > 0 else 0.0
        warn_score_ratio = round(judge_rules.warn_score / total_case_max_score, 4) if total_case_max_score > 0 else 0.0
        for case in gold_cases:
            case_scope_context = _build_case_scope_context(
                case,
                reduce_trace_payload=reduce_trace_payload if isinstance(reduce_trace_payload, dict) else {},
                style_bible_payload=style_bible_payload,
            )
            if not case_scope_context.applicable:
                dimensions = [
                    _not_applicable_dimension(
                        dimension=dimension_name,
                        max_score=judge_rules.weights.get(dimension_name, 0.0),
                        reason=case_scope_context.reason,
                        case_scope_context=case_scope_context,
                    )
                    for dimension_name in dimension_order
                ]
            else:
                dimensions = [
                    _evaluate_axis_coverage(case, text_nodes, judge_rules),
                    _evaluate_mechanism_specificity(
                        case,
                        text_nodes,
                        judge_rules,
                        style_bible_payload=style_bible_payload,
                    ),
                    _evaluate_evidence_faithfulness(case, evidence_nodes, valid_refs, judge_rules),
                    _evaluate_trace_auditability(
                        case,
                        case_scope_context=case_scope_context,
                        reasoning_bundle=reasoning_bundle,
                        reduce_trace_payload=reduce_trace_payload if isinstance(reduce_trace_payload, dict) else {},
                        evidence_nodes=evidence_nodes,
                        rule_nodes=rule_nodes,
                        routing_nodes=routing_nodes,
                        worldbook_nodes=worldbook_nodes,
                        rag_nodes=rag_nodes,
                        rules=judge_rules,
                    ),
                    _evaluate_downstream_dimension(
                        case=case,
                        case_scope_context=case_scope_context,
                        dimension="routing_executability",
                        required_items=case.required_downstream_surfaces.get("routing_hints", []),
                        candidate_nodes=routing_nodes,
                        fallback_nodes=rule_nodes,
                        rules=judge_rules,
                        weight_key="routing_executability",
                    ),
                    _evaluate_downstream_dimension(
                        case=case,
                        case_scope_context=case_scope_context,
                        dimension="worldbook_exportability",
                        required_items=case.required_downstream_surfaces.get("worldbook_worthy", []),
                        candidate_nodes=worldbook_nodes,
                        fallback_nodes=rule_nodes,
                        rules=judge_rules,
                        weight_key="worldbook_exportability",
                    ),
                    _evaluate_downstream_dimension(
                        case=case,
                        case_scope_context=case_scope_context,
                        dimension="rag_atomicity",
                        required_items=case.required_downstream_surfaces.get("rag_worthy", []),
                        candidate_nodes=rag_nodes,
                        fallback_nodes=rule_nodes,
                        rules=judge_rules,
                        weight_key="rag_atomicity",
                    ),
                    _evaluate_prompt_preset_usability(
                        case,
                        text_nodes,
                        judge_rules,
                        style_bible_payload=style_bible_payload,
                    ),
                    _evaluate_anti_genericity(case, text_nodes, judge_rules),
                    _evaluate_anti_pattern_resistance(
                        case,
                        all_nodes=text_nodes,
                        rule_nodes=rule_nodes,
                        routing_nodes=routing_nodes,
                        worldbook_nodes=worldbook_nodes,
                        rag_nodes=rag_nodes,
                        rules=judge_rules,
                    ),
                ]

            applicable_dimensions = [
                item
                for item in dimensions
                if item.get("status") != "not_applicable" and float(item.get("max_score", 0) or 0) > 0
            ]
            case_score = round(sum(float(item.get("score", 0) or 0) for item in applicable_dimensions), 2)
            case_max_score = round(sum(float(item.get("max_score", 0) or 0) for item in applicable_dimensions), 2)
            case_ratio = round(case_score / case_max_score, 4) if case_max_score > 0 else 0.0
            if case_max_score <= 0:
                case_status = "not_applicable"
            elif case_ratio >= pass_score_ratio:
                case_status = "pass"
            elif case_ratio >= warn_score_ratio:
                case_status = "warn"
            else:
                case_status = "fail"
            judge_rows.extend(
                _build_case_rows(
                    case=case,
                    dimensions=dimensions,
                    style_id=_clean_text(style_bible_payload.get("style_id")),
                    run_id=_clean_text(run_manifest.get("run_id")) if isinstance(run_manifest, dict) else "",
                )
            )
            case_results.append(
                {
                    "case_id": case.case_id,
                    "scope_type": case.scope_type,
                    "bucket_targets": case.bucket_targets,
                    "batch_targets": case.batch_targets,
                    "source_refs": case.source_refs,
                    "must_hit_refs": case.must_hit_refs,
                    "score": case_score,
                    "max_score": case_max_score,
                    "ratio": case_ratio,
                    "status": case_status,
                    "applicable_dimension_count": len(applicable_dimensions),
                    "not_applicable_dimension_count": sum(
                        1 for item in dimensions if item.get("status") == "not_applicable"
                    ),
                    "scope_context": {
                        "applicable": case_scope_context.applicable,
                        "reason": case_scope_context.reason,
                        "case_scope_ref_pool": sorted(case_scope_context.case_scope_ref_pool),
                        "effective_expected_refs": list(case_scope_context.effective_expected_refs),
                        "matched_bucket_ids": list(case_scope_context.matched_bucket_ids),
                        "matched_batch_ids": list(case_scope_context.matched_batch_ids),
                        "failed_bucket_ids": list(case_scope_context.failed_bucket_ids),
                        "skipped_sparse_bucket_ids": list(case_scope_context.skipped_sparse_bucket_ids),
                    },
                    "dimension_scores": {item["dimension"]: item for item in dimensions},
                    "human_notes": case.human_notes,
                }
            )

        summary = _summarize_cases(case_results=case_results, judge_rules=judge_rules)
        report = {
            "judge_version": "style-bible-judge-v2",
            "generated_at": utc_timestamp(),
            "style_id": _clean_text(style_bible_payload.get("style_id")),
            "run_id": _clean_text(run_manifest.get("run_id")) if isinstance(run_manifest, dict) else "",
            "node_id": resolved_node_id,
            "node_id_inference_source": node_id_inference_source,
            "scope": _clean_text(style_bible_payload.get("scope")),
            "style_bible_schema_version": _clean_text(run_manifest.get("style_bible_schema_version"))
            if isinstance(run_manifest, dict)
            else "",
            "summary": summary,
            "rule_evaluation_summary": eval_summary,
            "reasoning_bundle_error": reasoning_bundle_error,
            "style_bible_projection_source": projection_source,
            "feature_flags": runtime_flags.as_dict(),
            "case_results": case_results,
            "hashes": {
                "style_bible_sha256": sha256_payload(style_bible_payload),
                "judge_flat_sha256": sha256_payload(judge_flat_payload) if judge_flat_payload else "",
                "source_bundle_sha256": sha256_payload(source_bundle),
                "gold_set_sha256": gold_set_hash,
                "judge_rules_sha256": file_sha256(judge_rules.rules_path),
            },
            "source_files": {
                "style_bible_file": str(style_bible_path),
                "reasoning_file": str(reasoning_path) if reasoning_path.exists() else "",
                "reduce_trace_file": str(reduce_trace_path) if reduce_trace_path.exists() else "",
                "judge_flat_file": str(judge_flat_path) if judge_flat_path.exists() else "",
                "export_flat_file": str(export_flat_path) if export_flat_path.exists() else "",
                "source_bundle_file": str(source_bundle_path),
                "run_manifest_file": str(run_manifest_path) if run_manifest_path else "",
                "evaluation_report_file": str(eval_report_path) if eval_report_path else "",
                "evaluation_manifest_file": str(eval_manifest_path) if eval_manifest_path else "",
                "gold_set_index_file": str(Path(gold_set_index).resolve()),
                "judge_rules_config_file": str(Path(judge_rules_config).resolve()),
                "gold_case_files": [str(case.file_path) for case in gold_cases],
            },
            "index_metadata": {
                "gold_set_version": _clean_text(index_payload.get("gold_set_version")),
                "case_schema_version": _clean_text(index_payload.get("case_schema_version")),
                "bucketed_v2": bool(index_payload.get("bucketed_v2", False)),
            },
        }

        write_json(report_path, report)
        write_jsonl(rows_path, judge_rows)
        write_markdown(markdown_path, _build_markdown_report(report))
        tracker.record_success(
            "judge-style-bible",
            "Gold-set judge report written.",
            case_count=len(case_results),
            overall_score=summary.get("overall_score", 0),
            status=summary.get("status", ""),
        )
        tracker.finish(
            "Style bible gold-set judge completed.",
            report_file=str(report_path),
            rows_file=str(rows_path),
        )
        return JudgeResult(
            report_path=report_path,
            markdown_path=markdown_path,
            rows_path=rows_path,
            report=report,
        )
    except Exception as exc:  # noqa: BLE001
        tracker.fail_run(f"Style bible gold-set judge aborted: {exc}", error_type=type(exc).__name__)
        raise
