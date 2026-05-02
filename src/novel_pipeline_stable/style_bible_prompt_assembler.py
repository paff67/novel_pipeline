from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Annotated, Any, Iterable, Literal

from pydantic import BaseModel, Field, create_model

from novel_pipeline_stable.io_utils import read_json
from novel_pipeline_stable.models import (
    StyleBibleBucketBatchMemo,
    StyleBibleLocalPartialFinal,
    StyleBibleLocalReducerOutput,
    local_rule_row_model_for_path,
)
from novel_pipeline_stable.prompting import load_prompt
from novel_pipeline_stable.style_bible_inputs import clean_text
from novel_pipeline_stable.style_bible_surface_specs import (
    SURFACE_PATH_ORDER,
    scalar_enum_spec_for_path,
    scalar_value_aliases_for_path,
    surface_path_prompt_contract,
    surface_path_spec_for_path,
)


DEFAULT_ANTI_PATTERN_TOKEN_BUDGET = 1600
MIN_ANTI_PATTERN_TOKEN_BUDGET = 1200
MAX_ANTI_PATTERN_TOKEN_BUDGET = 2000
DEFAULT_MAX_ANTI_PATTERN_EXAMPLES = 6
MIN_ANTI_PATTERN_EXAMPLES = 4
MAX_ANTI_PATTERN_EXAMPLES = 8

GLOBAL_PROMPT_SETTINGS = """全局设定：
- Prompt 结构必须严格遵循：绝对静态层 -> 按桶静态层 -> 动态输入层。
- 严禁在 Prompt 前半部分插入 batch_id、当前时间戳、Request UUID 等动态变量。
- 动态标识只能出现在 JSON payload 的最后字段，不得污染静态前缀。
- anti-pattern 只是负面约束，不提供新证据来源。
- 证据字段只能使用输入中已经存在的合法 ref。"""


@dataclass(slots=True)
class AntiPatternExample:
    code: str
    severity: int
    tags: list[str]
    applies_to_buckets: list[str]
    applies_to_phases: list[str]
    bad_output: str
    why_bad: str
    good_pattern: str


@dataclass(slots=True)
class PromptAssembly:
    system_instruction: str
    user_payload: dict[str, Any]
    response_model: type[BaseModel]
    selected_antipattern_codes: list[str]
    anti_pattern_token_budget: int
    anti_pattern_token_estimate: int
    assembly_order: list[str]


def _project_root_from_prompt_dir(prompt_dir: str | Path) -> Path:
    return Path(prompt_dir).resolve().parent


def _default_registry_path(prompt_dir: str | Path) -> Path:
    return _project_root_from_prompt_dir(prompt_dir) / "config" / "style_bible_antipattern_registry.json"


def _estimate_tokens(text: str) -> int:
    cleaned = clean_text(text)
    if not cleaned:
        return 0
    return max(int(len(cleaned) * 0.8), 1)


def _cleaned_unique(values: Iterable[Any], *, sort_output: bool = False) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        rows.append(cleaned)
    return sorted(rows) if sort_output else rows


def _normalize_section_targets_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    row = payload if isinstance(payload, dict) else {}
    return {
        "bucket_id": clean_text(row.get("bucket_id")),
        "preferred_paths": _cleaned_unique(row.get("preferred_paths", [])),
        "scalar_paths": _cleaned_unique(row.get("scalar_paths", [])),
        "repair_paths": _cleaned_unique(row.get("repair_paths", [])),
        "prompt_hints": _cleaned_unique(row.get("prompt_hints", [])),
        "repair_priority": int(row.get("repair_priority", 0) or 0),
    }


def _normalize_existing_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_rows.append(
            {
                "path": clean_text(row.get("path")),
                "rule_id": clean_text(row.get("rule_id")),
                "text": clean_text(row.get("text")),
                "trigger": clean_text(row.get("trigger")),
                "constraint": clean_text(row.get("constraint")),
                "query_feature_matcher": clean_text(row.get("query_feature_matcher")),
                "route_target_action": clean_text(row.get("route_target_action")),
                "forbidden_action": clean_text(row.get("forbidden_action")),
                "correction_guideline": clean_text(row.get("correction_guideline")),
            }
        )
    return normalized_rows


def _normalize_scalar_candidates(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for path, rows in payload.items():
        normalized_path = clean_text(path)
        if not normalized_path or not isinstance(rows, list):
            continue
        cleaned_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = clean_text(row.get("value"))
            if not value:
                continue
            cleaned_rows.append(
                {
                    "value": value,
                    "count": int(row.get("count", 0) or 0),
                    "source_refs": _cleaned_unique(row.get("source_refs", [])),
                }
            )
        if cleaned_rows:
            normalized[normalized_path] = cleaned_rows
    return normalized


def _normalize_scalar_aliases(payload: Any) -> dict[str, dict[str, str]]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for path, aliases in payload.items():
        normalized_path = clean_text(path)
        if not normalized_path or not isinstance(aliases, dict):
            continue
        cleaned_aliases = {
            clean_text(alias): clean_text(canonical)
            for alias, canonical in aliases.items()
            if clean_text(alias) and clean_text(canonical)
        }
        if cleaned_aliases:
            normalized[normalized_path] = cleaned_aliases
    return normalized


def _normalize_repair_request_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    row = payload if isinstance(payload, dict) else {}
    underfilled_rows: list[dict[str, Any]] = []
    for item in row.get("underfilled_paths", []):
        if not isinstance(item, dict):
            continue
        path = clean_text(item.get("path"))
        if not path:
            continue
        underfilled_rows.append(
            {
                "path": path,
                "actual_count": int(item.get("actual_count", 0) or 0),
                "target_count": int(item.get("target_count", 0) or 0),
                "deficit": int(item.get("deficit", 0) or 0),
            }
        )
    bucket_path_counts = {
        clean_text(path): int(value or 0)
        for path, value in (row.get("bucket_path_counts", {}) or {}).items()
        if clean_text(path)
    }
    enum_hints = {
        clean_text(path): _cleaned_unique(values)
        for path, values in (row.get("enum_hints", {}) or {}).items()
        if clean_text(path)
    }
    return {
        "mode": clean_text(row.get("mode")) or "default",
        "requested_paths": _cleaned_unique(row.get("requested_paths", [])),
        "missing_scalar_paths": _cleaned_unique(row.get("missing_scalar_paths", [])),
        "underfilled_paths": underfilled_rows,
        "existing_rows": _normalize_existing_rows(row.get("existing_rows", [])),
        "bucket_path_counts": bucket_path_counts,
        "target_scalar_candidates": _normalize_scalar_candidates(row.get("target_scalar_candidates", {})),
        "enum_hints": enum_hints,
        "enum_aliases": _normalize_scalar_aliases(row.get("enum_aliases", {})),
        "notes": _cleaned_unique(row.get("notes", [])),
    }


def _normalize_slot_specs_payload(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slot_id = clean_text(row.get("slot_id"))
        if not slot_id:
            continue
        cue = clean_text(row.get("cue"))
        canonical_description = clean_text(row.get("canonical_description"))
        downstream_shape = clean_text(row.get("downstream_shape"))
        if not cue or not canonical_description:
            continue
        normalized_rows.append(
            {
                "slot_id": slot_id,
                "cue": cue,
                "canonical_description": canonical_description,
                "downstream_shape": downstream_shape,
                "fresh_evidence_required": bool(row.get("fresh_evidence_required", False)),
            }
        )
    return normalized_rows


def _normalize_path_target_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    row = payload if isinstance(payload, dict) else {}
    return {
        "path": clean_text(row.get("path")),
        "target_count": int(row.get("target_count", 0) or 0),
        "max_new_rows": int(row.get("max_new_rows", 0) or 0),
        "retrieval_top_k": int(row.get("retrieval_top_k", 0) or 0),
        "bucket_allowlist": _cleaned_unique(row.get("bucket_allowlist", [])),
        "downstream_shape": clean_text(row.get("downstream_shape")),
        "prompt_hints": _cleaned_unique(row.get("prompt_hints", [])),
        "dedupe_threshold": float(row.get("dedupe_threshold", 0.0) or 0.0),
        "slot_match_threshold": float(row.get("slot_match_threshold", 0.0) or 0.0),
        "soft_slot_match_floor": float(row.get("soft_slot_match_floor", 0.0) or 0.0),
        "max_gray_keep": int(row.get("max_gray_keep", 0) or 0),
        "enabled": bool(row.get("enabled", True)),
        "slot_specs": _normalize_slot_specs_payload(row.get("slot_specs", [])),
    }


def _normalize_retrieved_reasoning_entries(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reasoning_id = clean_text(row.get("reasoning_id"))
        if not reasoning_id:
            continue
        normalized_rows.append(
            {
                "reasoning_id": reasoning_id,
                "bucket_id": clean_text(row.get("bucket_id")),
                "axis_ids": _cleaned_unique(row.get("axis_ids", [])),
                "claim": clean_text(row.get("claim")),
                "observed_commonality": clean_text(row.get("observed_commonality")),
                "mechanism_inference": clean_text(row.get("mechanism_inference")),
                "downstream_constraint": clean_text(row.get("downstream_constraint")),
                "evidence_refs": _cleaned_unique(row.get("evidence_refs", [])),
                "retrieval_score": float(row.get("retrieval_score", 0.0) or 0.0),
                "matched_slot_ids": _cleaned_unique(row.get("matched_slot_ids", [])),
            }
        )
    return normalized_rows


def _normalize_worldbook_atom_candidates(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        atom_id = clean_text(row.get("atom_id"))
        text = clean_text(row.get("text"))
        if not atom_id and not text:
            continue
        normalized_rows.append(
            {
                "atom_id": atom_id,
                "atom_type": clean_text(row.get("atom_type")),
                "source_family": clean_text(row.get("source_family")),
                "stability": clean_text(row.get("stability")),
                "chapter_id": clean_text(row.get("chapter_id")),
                "scene_id": clean_text(row.get("scene_id")),
                "source_ref": clean_text(row.get("source_ref")),
                "grounding_refs": _cleaned_unique(row.get("grounding_refs", [])),
                "tags": _cleaned_unique(row.get("tags", [])),
                "text": text,
            }
        )
    return normalized_rows


def _normalize_densify_bundle_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    row = payload if isinstance(payload, dict) else {}
    target_gap = row.get("target_gap", {})
    target_gap_payload = {
        "actual_count": int((target_gap or {}).get("actual_count", 0) or 0),
        "target_count": int((target_gap or {}).get("target_count", 0) or 0),
        "deficit": int((target_gap or {}).get("deficit", 0) or 0),
    }
    return {
        "style_bible_id_hint": clean_text(row.get("style_bible_id_hint")),
        "scope_hint": clean_text(row.get("scope_hint")),
        "target_path": clean_text(row.get("target_path")),
        "target_gap": target_gap_payload,
        "existing_rows": _normalize_existing_rows(row.get("existing_rows", [])),
        "missing_slots": _normalize_slot_specs_payload(row.get("missing_slots", [])),
        "retrieved_reasoning_entries": _normalize_retrieved_reasoning_entries(
            row.get("retrieved_reasoning_entries", [])
        ),
        "grounding_ref_pool": _cleaned_unique(row.get("grounding_ref_pool", [])),
        "source_bucket_ids": _cleaned_unique(row.get("source_bucket_ids", [])),
        "burned_reasoning_ids": _cleaned_unique(row.get("burned_reasoning_ids", [])),
        "burned_evidence_refs": _cleaned_unique(row.get("burned_evidence_refs", [])),
        "notes": _cleaned_unique(row.get("notes", [])),
    }


def _normalize_path_targets_payload(rows: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = _normalize_path_target_payload(row if isinstance(row, dict) else {})
        path = clean_text(target.get("path"))
        if not path:
            continue
        normalized[path] = target
    return normalized


def _model_name_slug(value: str) -> str:
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", clean_text(value)) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Path"


def _stable_model_suffix(*parts: str) -> str:
    payload = "||".join(clean_text(part) for part in parts if clean_text(part))
    if not payload:
        return "Default"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _literal_annotation(values: Iterable[str]) -> Any:
    normalized = tuple(clean_text(value) for value in values if clean_text(value))
    if not normalized:
        return str
    return Literal.__getitem__(normalized)


def _select_surface_paths(surface_paths: Iterable[str] | None = None) -> list[str]:
    if surface_paths is None:
        return [path.value for path in SURFACE_PATH_ORDER]
    allowed_paths = {
        row["path"]
        for row in surface_path_prompt_contract(paths=surface_paths)
        if clean_text(row.get("path"))
    }
    ordered_paths = [path.value for path in SURFACE_PATH_ORDER if path.value in allowed_paths]
    return ordered_paths or [path.value for path in SURFACE_PATH_ORDER]


def _surface_path_specs_payload(surface_paths: Iterable[str] | None = None) -> list[dict[str, Any]]:
    selected_paths = _select_surface_paths(surface_paths)
    return surface_path_prompt_contract(paths=selected_paths)


def _local_reduce_selected_paths(
    *,
    section_targets: dict[str, Any],
    repair_request: dict[str, Any],
) -> list[str]:
    if clean_text(repair_request.get("mode")) == "repair" and repair_request.get("requested_paths"):
        return _select_surface_paths(repair_request.get("requested_paths"))
    section_paths = _cleaned_unique(
        [
            *section_targets.get("scalar_paths", []),
            *section_targets.get("preferred_paths", []),
            *section_targets.get("repair_paths", []),
        ]
    )
    return _select_surface_paths(section_paths or None)


def _slot_anchor_lines(slot_specs: Iterable[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for slot in slot_specs:
        cue = clean_text(slot.get("cue"))
        canonical_description = clean_text(slot.get("canonical_description"))
        downstream_shape = clean_text(slot.get("downstream_shape"))
        if not cue and not canonical_description:
            continue
        line = cue or canonical_description
        if cue and canonical_description:
            line = f"{cue}：{canonical_description}"
        if downstream_shape:
            line = f"{line}（下游形态：{downstream_shape}）"
        rows.append(line)
    return rows


def _field_semantic_description(
    field_name: str,
    *,
    path: str,
    path_target: dict[str, Any] | None = None,
) -> str:
    target = path_target or {}
    prefixes = {
        "surface_path": "固定 canonical surface_path。",
        "text": "写成 grounded、可执行、可审计的规范句。",
        "trigger": "写清单一、具体、可审计的触发条件。",
        "constraint": "写清触发后必须执行的约束或稳定规则。",
        "query_feature_matcher": "写成单一、具体、可检索的触发情境。",
        "route_target_action": "写成明确的路由动作，并说明优先返回什么信息。",
        "forbidden_action": "明确禁止的写法、动作或失真方式。",
        "correction_guideline": "写清替代的纠偏动作或修正机制。",
    }
    parts = [prefixes.get(field_name, "遵循当前路径的结构要求。"), f"路径：{clean_text(path)}。"]
    downstream_shape = clean_text(target.get("downstream_shape"))
    if downstream_shape:
        parts.append(f"目标下游形态：{downstream_shape}。")
    slot_lines = _slot_anchor_lines(target.get("slot_specs", []))
    if slot_lines:
        parts.append("优先覆盖这些槽位语义：" + "；".join(slot_lines) + "。")
    prompt_hints = _cleaned_unique(target.get("prompt_hints", []))
    if prompt_hints:
        parts.append("路径提示：" + "；".join(prompt_hints) + "。")
    scalar_spec = scalar_enum_spec_for_path(path)
    if field_name == "text" and scalar_spec is not None:
        parts.append("仅允许输出这些 canonical token：" + "、".join(scalar_spec.allowed_values) + "。")
        aliases = scalar_value_aliases_for_path(path)
        if aliases:
            alias_rows = [f"{alias}->{canonical}" for alias, canonical in aliases.items()]
            parts.append("别名归一提示：" + "；".join(alias_rows) + "。")
    return " ".join(part for part in parts if clean_text(part)).strip()


def _row_field_overrides_for_path(
    path: str,
    *,
    path_target: dict[str, Any] | None = None,
) -> dict[str, tuple[Any, Field]]:
    spec = surface_path_spec_for_path(path)
    if spec is None:
        raise ValueError(f"Unknown surface path for dynamic response model: {path}")
    overrides: dict[str, tuple[Any, Field]] = {
        "surface_path": (
            _literal_annotation([spec.path.value]),
            Field(..., description=_field_semantic_description("surface_path", path=spec.path.value, path_target=path_target)),
        ),
        "text": (
            (
                _literal_annotation(scalar_enum_spec_for_path(spec.path.value).allowed_values)
                if scalar_enum_spec_for_path(spec.path.value) is not None
                else str
            ),
            Field(..., description=_field_semantic_description("text", path=spec.path.value, path_target=path_target)),
        ),
    }
    if spec.rule_family == "routing_hint":
        overrides["query_feature_matcher"] = (
            str,
            Field(
                ...,
                description=_field_semantic_description(
                    "query_feature_matcher",
                    path=spec.path.value,
                    path_target=path_target,
                ),
            ),
        )
        overrides["route_target_action"] = (
            str,
            Field(
                ...,
                description=_field_semantic_description(
                    "route_target_action",
                    path=spec.path.value,
                    path_target=path_target,
                ),
            ),
        )
    elif spec.rule_family == "negative":
        overrides["forbidden_action"] = (
            str,
            Field(
                ...,
                description=_field_semantic_description(
                    "forbidden_action",
                    path=spec.path.value,
                    path_target=path_target,
                ),
            ),
        )
        overrides["correction_guideline"] = (
            str,
            Field(
                ...,
                description=_field_semantic_description(
                    "correction_guideline",
                    path=spec.path.value,
                    path_target=path_target,
                ),
            ),
        )
    elif spec.rule_family != "scalar":
        overrides["trigger"] = (
            str,
            Field(..., description=_field_semantic_description("trigger", path=spec.path.value, path_target=path_target)),
        )
        overrides["constraint"] = (
            str,
            Field(
                ...,
                description=_field_semantic_description("constraint", path=spec.path.value, path_target=path_target),
            ),
        )
    return overrides


def _build_surface_path_response_row_model(
    path: str,
    *,
    path_target: dict[str, Any] | None = None,
) -> type[BaseModel]:
    spec = surface_path_spec_for_path(path)
    if spec is None:
        raise ValueError(f"Unknown surface path for response row model: {path}")
    base_model = local_rule_row_model_for_path(spec.path.value)
    model_name = f"Prompt{_model_name_slug(spec.path.value)}Row{_stable_model_suffix(spec.path.value)}"
    return create_model(
        model_name,
        __base__=base_model,
        **_row_field_overrides_for_path(spec.path.value, path_target=path_target),
    )


def _discriminated_rule_row_annotation(row_models: list[type[BaseModel]]) -> Any:
    if not row_models:
        raise ValueError("At least one response row model is required.")
    if len(row_models) == 1:
        return row_models[0]
    union_annotation: Any = row_models[0]
    for row_model in row_models[1:]:
        union_annotation = union_annotation | row_model
    return Annotated[union_annotation, Field(discriminator="surface_path")]


def _build_prompt_response_model(
    *,
    model_name_prefix: str,
    selected_paths: list[str],
    path_targets_by_path: dict[str, dict[str, Any]],
) -> type[BaseModel]:
    row_models = [
        _build_surface_path_response_row_model(path, path_target=path_targets_by_path.get(path))
        for path in selected_paths
    ]
    row_annotation = _discriminated_rule_row_annotation(row_models)
    final_model_name = f"{model_name_prefix}Final{_stable_model_suffix(*selected_paths)}"
    final_model = create_model(
        final_model_name,
        __base__=StyleBibleLocalPartialFinal,
        rule_rows=(
            list[row_annotation],
            Field(default_factory=list, description="只返回当前 schema 允许、且被证据支撑的 grounded rule rows。"),
        ),
    )
    output_model_name = f"{model_name_prefix}Output{_stable_model_suffix(*selected_paths)}"
    return create_model(
        output_model_name,
        __base__=StyleBibleLocalReducerOutput,
        final=(final_model, Field(default_factory=final_model)),
    )


def _load_antipattern_registry(prompt_dir: str | Path, registry_path: str | Path | None = None) -> list[AntiPatternExample]:
    path = Path(registry_path).resolve() if registry_path else _default_registry_path(prompt_dir).resolve()
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Anti-pattern registry must be a JSON object: {path}")

    entries: list[AntiPatternExample] = []
    for code, row in payload.items():
        if not isinstance(row, dict):
            continue
        entries.append(
            AntiPatternExample(
                code=clean_text(code),
                severity=int(row.get("severity", 1) or 1),
                tags=[clean_text(item) for item in row.get("tags", []) if clean_text(item)],
                applies_to_buckets=[
                    clean_text(item) for item in row.get("applies_to_buckets", []) if clean_text(item)
                ],
                applies_to_phases=[
                    clean_text(item) for item in row.get("applies_to_phases", []) if clean_text(item)
                ],
                bad_output=clean_text(row.get("bad_output")),
                why_bad=clean_text(row.get("why_bad")),
                good_pattern=clean_text(row.get("good_pattern")),
            )
        )
    return entries


def _match_bucket(entry: AntiPatternExample, bucket_id: str, bucket_ids: set[str]) -> float:
    applies = set(entry.applies_to_buckets)
    if "*" in applies:
        return 1.5
    if bucket_id and bucket_id in applies:
        return 4.0
    if bucket_ids and bucket_ids & applies:
        return 2.5
    return 0.0


def _match_phase(entry: AntiPatternExample, phase: str) -> float:
    applies = set(entry.applies_to_phases)
    if not applies:
        return 1.0
    if phase in applies:
        return 2.5
    return 0.0


def _entry_score(
    entry: AntiPatternExample,
    *,
    phase: str,
    bucket_id: str,
    bucket_ids: set[str],
    context_tags: set[str],
) -> float:
    score = float(entry.severity) * 0.4
    score += _match_phase(entry, phase)
    score += _match_bucket(entry, bucket_id, bucket_ids)
    score += len(context_tags & set(entry.tags)) * 0.8
    if "global" in entry.tags:
        score += 0.2
    return round(score, 4)


def _select_antipatterns(
    entries: list[AntiPatternExample],
    *,
    phase: str,
    bucket_id: str,
    bucket_ids: Iterable[str],
    context_tags: Iterable[str],
    token_budget: int,
    max_examples: int,
    global_only: bool = False,
) -> list[AntiPatternExample]:
    normalized_budget = max(int(token_budget), 0)
    normalized_max_examples = max(MIN_ANTI_PATTERN_EXAMPLES, min(MAX_ANTI_PATTERN_EXAMPLES, int(max_examples)))
    normalized_bucket_id = clean_text(bucket_id)
    bucket_id_set = {clean_text(item) for item in bucket_ids if clean_text(item)}
    context_tag_set = {clean_text(item) for item in context_tags if clean_text(item)}
    if normalized_bucket_id:
        context_tag_set.add(normalized_bucket_id)
    if phase:
        context_tag_set.add(phase)

    ranked: list[tuple[float, AntiPatternExample]] = []
    for entry in entries:
        applies = {clean_text(item) for item in entry.applies_to_buckets if clean_text(item)}
        if global_only and "*" not in applies:
            continue
        score = _entry_score(
            entry,
            phase=phase,
            bucket_id=normalized_bucket_id,
            bucket_ids=bucket_id_set,
            context_tags=context_tag_set,
        )
        if score <= 0:
            continue
        ranked.append((score, entry))

    ranked.sort(
        key=lambda item: (
            -item[0],
            -item[1].severity,
            item[1].code,
        )
    )

    selected: list[AntiPatternExample] = []
    used_tokens = 0
    for _score, entry in ranked:
        rendered = _render_antipattern_entry(entry)
        estimated_tokens = _estimate_tokens(rendered)
        if len(selected) >= normalized_max_examples:
            continue
        if used_tokens + estimated_tokens > normalized_budget:
            continue
        selected.append(entry)
        used_tokens += estimated_tokens
        if len(selected) >= normalized_max_examples:
            break
    return selected


def _render_antipattern_entry(entry: AntiPatternExample) -> str:
    tags = ",".join(entry.tags)
    return (
        f'  <example code="{entry.code}" severity="{entry.severity}">\n'
        f"    <tags>{tags}</tags>\n"
        f"    <bad_output>{entry.bad_output}</bad_output>\n"
        f"    <why_bad>{entry.why_bad}</why_bad>\n"
        f"    <good_pattern>{entry.good_pattern}</good_pattern>\n"
        f"  </example>"
    )


def _render_antipattern_context(
    *,
    prompt_dir: str | Path,
    phase: str,
    bucket_id: str,
    selected_entries: list[AntiPatternExample],
    token_budget: int,
    max_examples: int,
    include_empty_placeholder: bool = True,
) -> str:
    guide = load_prompt(prompt_dir, "style_bible_antipatterns_cn.md").strip()
    lines = [guide, "", f'<anti_patterns phase="{clean_text(phase)}" bucket_id="{clean_text(bucket_id)}">']
    lines.append(
        f'  <budget token_budget="{int(token_budget)}" max_examples="{int(max_examples)}" selected_count="{len(selected_entries)}" />'
    )
    if not selected_entries and include_empty_placeholder:
        lines.append("  <example code=\"NONE\" severity=\"0\">")
        lines.append("    <why_bad>当前未注入额外负例，但 grounding 与 anti-pattern 红线依然有效。</why_bad>")
        lines.append("  </example>")
    elif selected_entries:
        for entry in selected_entries:
            lines.append(_render_antipattern_entry(entry))
    lines.append("</anti_patterns>")
    return "\n".join(lines).strip() + "\n"


def _assemble_antipattern_context(
    entries: list[AntiPatternExample],
    *,
    prompt_dir: str | Path,
    phase: str,
    bucket_id: str,
    bucket_ids: Iterable[str],
    context_tags: Iterable[str],
    token_budget: int,
    max_examples: int,
    global_only: bool = False,
) -> tuple[str, list[AntiPatternExample]]:
    normalized_budget = max(MIN_ANTI_PATTERN_TOKEN_BUDGET, min(MAX_ANTI_PATTERN_TOKEN_BUDGET, int(token_budget)))
    static_context = _render_antipattern_context(
        prompt_dir=prompt_dir,
        phase=phase,
        bucket_id=bucket_id,
        selected_entries=[],
        token_budget=normalized_budget,
        max_examples=max_examples,
        include_empty_placeholder=False,
    )
    static_tokens = _estimate_tokens(static_context)
    example_budget = max(normalized_budget - static_tokens, 0)
    selected_entries = _select_antipatterns(
        entries,
        phase=phase,
        bucket_id=bucket_id,
        bucket_ids=bucket_ids,
        context_tags=context_tags,
        token_budget=example_budget,
        max_examples=max_examples,
        global_only=global_only,
    )
    anti_pattern_context = _render_antipattern_context(
        prompt_dir=prompt_dir,
        phase=phase,
        bucket_id=bucket_id,
        selected_entries=selected_entries,
        token_budget=normalized_budget,
        max_examples=max_examples,
    )
    while selected_entries and _estimate_tokens(anti_pattern_context) > normalized_budget:
        selected_entries = selected_entries[:-1]
        anti_pattern_context = _render_antipattern_context(
            prompt_dir=prompt_dir,
            phase=phase,
            bucket_id=bucket_id,
            selected_entries=selected_entries,
            token_budget=normalized_budget,
            max_examples=max_examples,
        )
    if _estimate_tokens(anti_pattern_context) > normalized_budget:
        anti_pattern_context = _render_antipattern_context(
            prompt_dir=prompt_dir,
            phase=phase,
            bucket_id=bucket_id,
            selected_entries=[],
            token_budget=normalized_budget,
            max_examples=max_examples,
        )
    if _estimate_tokens(anti_pattern_context) > normalized_budget:
        raise ValueError(
            f"Anti-pattern context exceeds the hard budget after trimming: phase={phase}, bucket_id={bucket_id}"
        )
    return anti_pattern_context, selected_entries


def assemble_bucket_synthesis_prompt(
    *,
    prompt_dir: str | Path,
    bucket_id: str,
    axis_focus: Iterable[str],
    static_axis_focus: Iterable[str] | None = None,
    prompt_bundle_xml: str,
    memo_id: str,
    batch_id: str,
    label: str,
    chapter_ids: Iterable[str],
    item_ids: Iterable[str],
    allowed_refs: Iterable[str],
    registry_path: str | Path | None = None,
    anti_pattern_token_budget: int = DEFAULT_ANTI_PATTERN_TOKEN_BUDGET,
    max_anti_pattern_examples: int = DEFAULT_MAX_ANTI_PATTERN_EXAMPLES,
) -> PromptAssembly:
    normalized_budget = max(MIN_ANTI_PATTERN_TOKEN_BUDGET, min(MAX_ANTI_PATTERN_TOKEN_BUDGET, int(anti_pattern_token_budget)))
    normalized_max_examples = max(
        MIN_ANTI_PATTERN_EXAMPLES,
        min(MAX_ANTI_PATTERN_EXAMPLES, int(max_anti_pattern_examples)),
    )
    registry = _load_antipattern_registry(prompt_dir, registry_path=registry_path)
    static_axis_ids = _cleaned_unique(static_axis_focus if static_axis_focus is not None else [], sort_output=True)
    runtime_axis_ids = _cleaned_unique(axis_focus)
    # Anti-pattern selection must stay aligned with the static prompt prefix.
    # Never let batch-level runtime axes influence static-context selection.
    context_tags = {clean_text(bucket_id), *static_axis_ids}
    anti_pattern_context, selected_entries = _assemble_antipattern_context(
        registry,
        prompt_dir=prompt_dir,
        phase="bucket_synthesis",
        bucket_id=bucket_id,
        bucket_ids=[bucket_id],
        context_tags=context_tags,
        token_budget=normalized_budget,
        max_examples=normalized_max_examples,
    )
    system_instruction = load_prompt(prompt_dir, "style_bible_bucket_synthesis.md").strip() + "\n\n" + GLOBAL_PROMPT_SETTINGS
    # Keep the cacheable prefix byte-stable for the same bucket across batches:
    # static_context first, then the large per-batch payload, and runtime ids last.
    user_payload = {
        "static_context": {
            "bucket_id": clean_text(bucket_id),
            "axis_focus": static_axis_ids,
            "anti_pattern_context": anti_pattern_context,
        },
        "dynamic_context": {
            "prompt_bundle_xml": prompt_bundle_xml,
        },
        "runtime_identifiers": {
            "memo_id": clean_text(memo_id),
            "bucket_id": clean_text(bucket_id),
            "batch_id": clean_text(batch_id),
            "label": clean_text(label),
            "axis_focus": runtime_axis_ids,
            "chapter_ids": [clean_text(item) for item in chapter_ids if clean_text(item)],
            "item_ids": [clean_text(item) for item in item_ids if clean_text(item)],
            "allowed_refs": [clean_text(item) for item in allowed_refs if clean_text(item)],
        },
    }
    return PromptAssembly(
        system_instruction=system_instruction,
        user_payload=user_payload,
        response_model=StyleBibleBucketBatchMemo,
        selected_antipattern_codes=[entry.code for entry in selected_entries],
        anti_pattern_token_budget=normalized_budget,
        anti_pattern_token_estimate=_estimate_tokens(anti_pattern_context),
        assembly_order=["system_prompt", "global_settings", "anti_pattern_context", "prompt_bundle_xml", "runtime_identifiers"],
    )


def assemble_local_reducer_prompt(
    *,
    prompt_dir: str | Path,
    bucket_id: str,
    axis_focus: Iterable[str],
    local_reduce_bundle: dict[str, Any],
    section_targets: dict[str, Any] | None = None,
    path_targets: list[dict[str, Any]] | None = None,
    repair_request: dict[str, Any] | None = None,
    registry_path: str | Path | None = None,
    anti_pattern_token_budget: int = DEFAULT_ANTI_PATTERN_TOKEN_BUDGET,
    max_anti_pattern_examples: int = DEFAULT_MAX_ANTI_PATTERN_EXAMPLES,
) -> PromptAssembly:
    normalized_budget = max(MIN_ANTI_PATTERN_TOKEN_BUDGET, min(MAX_ANTI_PATTERN_TOKEN_BUDGET, int(anti_pattern_token_budget)))
    normalized_max_examples = max(
        MIN_ANTI_PATTERN_EXAMPLES,
        min(MAX_ANTI_PATTERN_EXAMPLES, int(max_anti_pattern_examples)),
    )
    normalized_bucket_id = clean_text(bucket_id)
    static_axis_ids = _cleaned_unique(axis_focus, sort_output=True)
    registry = _load_antipattern_registry(prompt_dir, registry_path=registry_path)
    normalized_section_targets = _normalize_section_targets_payload(section_targets)
    normalized_path_targets = _normalize_path_targets_payload(path_targets)
    normalized_repair_request = _normalize_repair_request_payload(repair_request)
    selected_paths = _local_reduce_selected_paths(
        section_targets=normalized_section_targets,
        repair_request=normalized_repair_request,
    )
    surface_path_specs = _surface_path_specs_payload(selected_paths)
    filtered_path_targets = [
        normalized_path_targets[path]
        for path in selected_paths
        if path in normalized_path_targets
    ]
    response_model = _build_prompt_response_model(
        model_name_prefix="LocalReduce",
        selected_paths=selected_paths,
        path_targets_by_path={path: normalized_path_targets.get(path, {}) for path in selected_paths},
    )
    anti_pattern_context, selected_entries = _assemble_antipattern_context(
        registry,
        prompt_dir=prompt_dir,
        phase="local_reduce",
        bucket_id=normalized_bucket_id,
        bucket_ids=[normalized_bucket_id],
        context_tags={normalized_bucket_id, *static_axis_ids, "local_reduce", "grounding", "routing"},
        token_budget=normalized_budget,
        max_examples=normalized_max_examples,
    )
    system_instruction = load_prompt(prompt_dir, "style_bible_local_reduce.md").strip() + "\n\n" + GLOBAL_PROMPT_SETTINGS
    user_payload = {
        "static_context": {
            "bucket_id": normalized_bucket_id,
            "axis_focus": static_axis_ids,
            "surface_path_specs": surface_path_specs,
            "section_targets": normalized_section_targets,
            "path_targets": filtered_path_targets,
            "anti_pattern_context": anti_pattern_context,
        },
        "dynamic_context": {
            "local_reduce_bundle": local_reduce_bundle,
            "repair_request": normalized_repair_request,
        },
        "runtime_identifiers": {
            "bucket_id": normalized_bucket_id,
            "axis_focus": static_axis_ids,
        },
    }
    return PromptAssembly(
        system_instruction=system_instruction,
        user_payload=user_payload,
        response_model=response_model,
        selected_antipattern_codes=[entry.code for entry in selected_entries],
        anti_pattern_token_budget=normalized_budget,
        anti_pattern_token_estimate=_estimate_tokens(anti_pattern_context),
        assembly_order=["system_prompt", "global_settings", "anti_pattern_context", "local_reduce_bundle", "runtime_identifiers"],
    )


def assemble_section_densify_prompt(
    *,
    prompt_dir: str | Path,
    target_path: str,
    path_target: dict[str, Any] | None,
    densify_bundle: dict[str, Any],
    registry_path: str | Path | None = None,
    anti_pattern_token_budget: int = DEFAULT_ANTI_PATTERN_TOKEN_BUDGET,
    max_anti_pattern_examples: int = DEFAULT_MAX_ANTI_PATTERN_EXAMPLES,
) -> PromptAssembly:
    normalized_budget = max(MIN_ANTI_PATTERN_TOKEN_BUDGET, min(MAX_ANTI_PATTERN_TOKEN_BUDGET, int(anti_pattern_token_budget)))
    normalized_max_examples = max(
        MIN_ANTI_PATTERN_EXAMPLES,
        min(MAX_ANTI_PATTERN_EXAMPLES, int(max_anti_pattern_examples)),
    )
    normalized_target_path = clean_text(target_path)
    normalized_path_target = _normalize_path_target_payload(path_target)
    normalized_bundle = _normalize_densify_bundle_payload(densify_bundle)
    response_path_target = dict(normalized_path_target)
    response_path_target["slot_specs"] = (
        normalized_bundle.get("missing_slots", [])
        or normalized_path_target.get("slot_specs", [])
    )
    response_model = _build_prompt_response_model(
        model_name_prefix="SectionDensify",
        selected_paths=_select_surface_paths([normalized_target_path]),
        path_targets_by_path={normalized_target_path: response_path_target},
    )
    registry = _load_antipattern_registry(prompt_dir, registry_path=registry_path)
    context_tags = {
        normalized_target_path,
        "section_densify",
        "routing",
        "worldbook",
        *normalized_bundle.get("source_bucket_ids", []),
    }
    anti_pattern_context, selected_entries = _assemble_antipattern_context(
        registry,
        prompt_dir=prompt_dir,
        phase="local_reduce",
        bucket_id=normalized_target_path,
        bucket_ids=normalized_bundle.get("source_bucket_ids", []),
        context_tags=context_tags,
        token_budget=normalized_budget,
        max_examples=normalized_max_examples,
    )
    system_instruction = load_prompt(prompt_dir, "style_bible_section_densify.md").strip() + "\n\n" + GLOBAL_PROMPT_SETTINGS
    user_payload = {
        "static_context": {
            "target_path": normalized_target_path,
            "surface_path_specs": _surface_path_specs_payload([normalized_target_path]),
            "path_target": normalized_path_target,
            "anti_pattern_context": anti_pattern_context,
        },
        "dynamic_context": {
            "densify_bundle": normalized_bundle,
        },
        "runtime_identifiers": {
            "target_path": normalized_target_path,
            "missing_slot_ids": [row["slot_id"] for row in normalized_bundle.get("missing_slots", [])],
            "source_bucket_ids": normalized_bundle.get("source_bucket_ids", []),
        },
    }
    return PromptAssembly(
        system_instruction=system_instruction,
        user_payload=user_payload,
        response_model=response_model,
        selected_antipattern_codes=[entry.code for entry in selected_entries],
        anti_pattern_token_budget=normalized_budget,
        anti_pattern_token_estimate=_estimate_tokens(anti_pattern_context),
        assembly_order=["system_prompt", "global_settings", "anti_pattern_context", "densify_bundle", "runtime_identifiers"],
    )
