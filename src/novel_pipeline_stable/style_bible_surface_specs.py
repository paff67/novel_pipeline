from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Literal


MergeStrategy = Literal["scalar_pick_one", "rule_dedupe_union", "rule_dedupe_aggressive", "append_capped"]
Cardinality = Literal["scalar", "list"]
ConflictPolicy = Literal["pick_best", "drop_group"]
RuleFamily = Literal["constraint", "routing_hint", "negative", "scalar"]


class SurfacePath(StrEnum):
    NARRATIVE_ENGINE = "narrative_system.engine"
    NARRATIVE_PERSPECTIVE = "narrative_system.perspective"
    NARRATIVE_DISTANCE = "narrative_system.distance"
    NARRATIVE_TEMPORALITY = "narrative_system.temporality"
    NARRATIVE_PACING = "narrative_system.pacing_rules"
    NARRATIVE_PLOT_NODE_LOGIC = "narrative_system.plot_node_logic"
    EXPRESSION_DESCRIPTION = "expression_system.description_rules"
    EXPRESSION_DIALOGUE = "expression_system.dialogue_rules"
    EXPRESSION_CHARACTERIZATION = "expression_system.characterization_rules"
    EXPRESSION_SENSORY = "expression_system.sensory_rules"
    AESTHETICS_CORE_AXES = "aesthetics_system.core_axes"
    AESTHETICS_PRESSURE_AXES = "aesthetics_system.pressure_axes"
    AESTHETICS_HUMOR = "aesthetics_system.humor_recipe"
    AESTHETICS_SATIRE = "aesthetics_system.satire_targets"
    AESTHETICS_NONSTANDARD_XIANXIA = "aesthetics_system.nonstandard_xianxia_rules"
    VOICE_NARRATOR_VOICE = "voice_contract.narrator_voice"
    VOICE_INNER_MONOLOGUE = "voice_contract.inner_monologue_mode"
    VOICE_REGISTER_MIX = "voice_contract.register_mix"
    VOICE_NEGATIVE_PITFALLS = "voice_contract.negative_pitfalls"
    CHARACTER_ARC_RULES = "character_arc_rules"
    WORLDBOOK_RAG_WORTHY = "worldbook_binding.rag_worthy"
    WORLDBOOK_WORLDBOOK_WORTHY = "worldbook_binding.worldbook_worthy"
    WORLDBOOK_ROUTING_HINTS = "worldbook_binding.routing_hints"
    NEGATIVE_RULES = "negative_rules"


def _clean_surface_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_scalar_lookup(value: Any) -> str:
    cleaned = _clean_surface_text(value).casefold()
    if not cleaned:
        return ""
    return re.sub(r"[\s\-_/.]+", "", cleaned)


@dataclass(frozen=True, slots=True)
class ScalarEnumSpec:
    path: SurfacePath
    allowed_values: tuple[str, ...]
    default_value: str = ""
    constraint_template: str = ""
    default_when_missing: bool = False
    value_aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SurfacePathSpec:
    path: SurfacePath
    cardinality: Cardinality
    merge_strategy: MergeStrategy
    rule_family: RuleFamily
    row_model: str
    required_fields: tuple[str, ...]
    max_items: int
    high_risk: bool = False
    conflict_policy: ConflictPolicy = "pick_best"
    aggressive_group_fields: tuple[str, ...] = ()
    conflict_field: str = ""
    enum_source: str = ""


SURFACE_PATH_ORDER: tuple[SurfacePath, ...] = (
    SurfacePath.NARRATIVE_ENGINE,
    SurfacePath.NARRATIVE_PERSPECTIVE,
    SurfacePath.NARRATIVE_DISTANCE,
    SurfacePath.NARRATIVE_TEMPORALITY,
    SurfacePath.NARRATIVE_PACING,
    SurfacePath.NARRATIVE_PLOT_NODE_LOGIC,
    SurfacePath.EXPRESSION_DESCRIPTION,
    SurfacePath.EXPRESSION_DIALOGUE,
    SurfacePath.EXPRESSION_CHARACTERIZATION,
    SurfacePath.EXPRESSION_SENSORY,
    SurfacePath.AESTHETICS_CORE_AXES,
    SurfacePath.AESTHETICS_PRESSURE_AXES,
    SurfacePath.AESTHETICS_HUMOR,
    SurfacePath.AESTHETICS_SATIRE,
    SurfacePath.AESTHETICS_NONSTANDARD_XIANXIA,
    SurfacePath.VOICE_NARRATOR_VOICE,
    SurfacePath.VOICE_INNER_MONOLOGUE,
    SurfacePath.VOICE_REGISTER_MIX,
    SurfacePath.VOICE_NEGATIVE_PITFALLS,
    SurfacePath.CHARACTER_ARC_RULES,
    SurfacePath.WORLDBOOK_RAG_WORTHY,
    SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY,
    SurfacePath.WORLDBOOK_ROUTING_HINTS,
    SurfacePath.NEGATIVE_RULES,
)


SURFACE_PATH_SPECS: dict[SurfacePath, SurfacePathSpec] = {
    SurfacePath.NARRATIVE_ENGINE: SurfacePathSpec(
        path=SurfacePath.NARRATIVE_ENGINE,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.NARRATIVE_PERSPECTIVE: SurfacePathSpec(
        path=SurfacePath.NARRATIVE_PERSPECTIVE,
        cardinality="scalar",
        merge_strategy="scalar_pick_one",
        rule_family="scalar",
        row_model="ScalarRuleItem",
        required_fields=("text",),
        max_items=1,
        enum_source=SurfacePath.NARRATIVE_PERSPECTIVE.value,
    ),
    SurfacePath.NARRATIVE_DISTANCE: SurfacePathSpec(
        path=SurfacePath.NARRATIVE_DISTANCE,
        cardinality="scalar",
        merge_strategy="scalar_pick_one",
        rule_family="scalar",
        row_model="ScalarRuleItem",
        required_fields=("text",),
        max_items=1,
        enum_source=SurfacePath.NARRATIVE_DISTANCE.value,
    ),
    SurfacePath.NARRATIVE_TEMPORALITY: SurfacePathSpec(
        path=SurfacePath.NARRATIVE_TEMPORALITY,
        cardinality="scalar",
        merge_strategy="scalar_pick_one",
        rule_family="scalar",
        row_model="ScalarRuleItem",
        required_fields=("text",),
        max_items=1,
        enum_source=SurfacePath.NARRATIVE_TEMPORALITY.value,
    ),
    SurfacePath.NARRATIVE_PACING: SurfacePathSpec(
        path=SurfacePath.NARRATIVE_PACING,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.NARRATIVE_PLOT_NODE_LOGIC: SurfacePathSpec(
        path=SurfacePath.NARRATIVE_PLOT_NODE_LOGIC,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.EXPRESSION_DESCRIPTION: SurfacePathSpec(
        path=SurfacePath.EXPRESSION_DESCRIPTION,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.EXPRESSION_DIALOGUE: SurfacePathSpec(
        path=SurfacePath.EXPRESSION_DIALOGUE,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.EXPRESSION_CHARACTERIZATION: SurfacePathSpec(
        path=SurfacePath.EXPRESSION_CHARACTERIZATION,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
        high_risk=True,
    ),
    SurfacePath.EXPRESSION_SENSORY: SurfacePathSpec(
        path=SurfacePath.EXPRESSION_SENSORY,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.AESTHETICS_CORE_AXES: SurfacePathSpec(
        path=SurfacePath.AESTHETICS_CORE_AXES,
        cardinality="list",
        merge_strategy="append_capped",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.AESTHETICS_PRESSURE_AXES: SurfacePathSpec(
        path=SurfacePath.AESTHETICS_PRESSURE_AXES,
        cardinality="list",
        merge_strategy="append_capped",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.AESTHETICS_HUMOR: SurfacePathSpec(
        path=SurfacePath.AESTHETICS_HUMOR,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.AESTHETICS_SATIRE: SurfacePathSpec(
        path=SurfacePath.AESTHETICS_SATIRE,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.AESTHETICS_NONSTANDARD_XIANXIA: SurfacePathSpec(
        path=SurfacePath.AESTHETICS_NONSTANDARD_XIANXIA,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.VOICE_NARRATOR_VOICE: SurfacePathSpec(
        path=SurfacePath.VOICE_NARRATOR_VOICE,
        cardinality="scalar",
        merge_strategy="scalar_pick_one",
        rule_family="scalar",
        row_model="ScalarRuleItem",
        required_fields=("text",),
        max_items=1,
        enum_source=SurfacePath.VOICE_NARRATOR_VOICE.value,
    ),
    SurfacePath.VOICE_INNER_MONOLOGUE: SurfacePathSpec(
        path=SurfacePath.VOICE_INNER_MONOLOGUE,
        cardinality="scalar",
        merge_strategy="scalar_pick_one",
        rule_family="scalar",
        row_model="ScalarRuleItem",
        required_fields=("text",),
        max_items=1,
        enum_source=SurfacePath.VOICE_INNER_MONOLOGUE.value,
    ),
    SurfacePath.VOICE_REGISTER_MIX: SurfacePathSpec(
        path=SurfacePath.VOICE_REGISTER_MIX,
        cardinality="list",
        merge_strategy="append_capped",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=6,
    ),
    SurfacePath.VOICE_NEGATIVE_PITFALLS: SurfacePathSpec(
        path=SurfacePath.VOICE_NEGATIVE_PITFALLS,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="negative",
        row_model="NegativeRuleItem",
        required_fields=("forbidden_action", "correction_guideline"),
        max_items=8,
    ),
    SurfacePath.CHARACTER_ARC_RULES: SurfacePathSpec(
        path=SurfacePath.CHARACTER_ARC_RULES,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="constraint",
        row_model="NarrativeRuleItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
    ),
    SurfacePath.WORLDBOOK_RAG_WORTHY: SurfacePathSpec(
        path=SurfacePath.WORLDBOOK_RAG_WORTHY,
        cardinality="list",
        merge_strategy="rule_dedupe_aggressive",
        rule_family="constraint",
        row_model="WorldbookFactItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
        high_risk=True,
        conflict_policy="drop_group",
        aggressive_group_fields=("trigger", "text"),
        conflict_field="constraint",
    ),
    SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY: SurfacePathSpec(
        path=SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY,
        cardinality="list",
        merge_strategy="rule_dedupe_aggressive",
        rule_family="constraint",
        row_model="WorldbookFactItem",
        required_fields=("trigger", "constraint"),
        max_items=8,
        high_risk=True,
        conflict_policy="drop_group",
        aggressive_group_fields=("trigger", "text"),
        conflict_field="constraint",
    ),
    SurfacePath.WORLDBOOK_ROUTING_HINTS: SurfacePathSpec(
        path=SurfacePath.WORLDBOOK_ROUTING_HINTS,
        cardinality="list",
        merge_strategy="rule_dedupe_aggressive",
        rule_family="routing_hint",
        row_model="RoutingHintItem",
        required_fields=("query_feature_matcher", "route_target_action"),
        max_items=8,
        high_risk=True,
        conflict_policy="drop_group",
        aggressive_group_fields=("query_feature_matcher", "trigger", "text"),
        conflict_field="route_target_action",
    ),
    SurfacePath.NEGATIVE_RULES: SurfacePathSpec(
        path=SurfacePath.NEGATIVE_RULES,
        cardinality="list",
        merge_strategy="rule_dedupe_union",
        rule_family="negative",
        row_model="NegativeRuleItem",
        required_fields=("forbidden_action", "correction_guideline"),
        max_items=10,
        high_risk=True,
    ),
}


LIST_SURFACE_PATHS: tuple[str, ...] = tuple(
    path.value for path in SURFACE_PATH_ORDER if SURFACE_PATH_SPECS[path].cardinality == "list"
)
SCALAR_SURFACE_PATHS: tuple[str, ...] = tuple(
    path.value for path in SURFACE_PATH_ORDER if SURFACE_PATH_SPECS[path].cardinality == "scalar"
)
HIGH_RISK_SURFACE_PATHS: tuple[str, ...] = tuple(
    path.value for path in SURFACE_PATH_ORDER if SURFACE_PATH_SPECS[path].high_risk
)

SCALAR_SURFACE_PATH_ALIASES: dict[str, str] = {
    "narrative_system_perspective": SurfacePath.NARRATIVE_PERSPECTIVE.value,
    "narrative_system_distance": SurfacePath.NARRATIVE_DISTANCE.value,
    "narrative_system_temporality": SurfacePath.NARRATIVE_TEMPORALITY.value,
    "voice_contract_narrator_voice": SurfacePath.VOICE_NARRATOR_VOICE.value,
    "voice_contract_inner_monologue_mode": SurfacePath.VOICE_INNER_MONOLOGUE.value,
}

SCALAR_ENUM_SPECS: dict[str, ScalarEnumSpec] = {
    SurfacePath.NARRATIVE_PERSPECTIVE.value: ScalarEnumSpec(
        path=SurfacePath.NARRATIVE_PERSPECTIVE,
        allowed_values=(
            "first_person",
            "close_third_person",
            "omniscient_third_person",
            "multi_pov",
            "objective_camera",
        ),
        default_value="close_third_person",
        constraint_template="视角枚举必须选择 `{value}`。",
        default_when_missing=True,
        value_aliases=(
            ("limited_first_person", "first_person"),
            ("first person", "first_person"),
            ("first-person", "first_person"),
            ("第一人称", "first_person"),
            ("close third person", "close_third_person"),
            ("close-third-person", "close_third_person"),
            ("third_person_limited", "close_third_person"),
            ("third person limited", "close_third_person"),
            ("第三人称限制视角", "close_third_person"),
            ("第三人称近距", "close_third_person"),
            ("restricted_omniscient", "omniscient_third_person"),
            ("omniscient third person", "omniscient_third_person"),
            ("omniscient", "omniscient_third_person"),
            ("全知第三人称", "omniscient_third_person"),
            ("全知视角", "omniscient_third_person"),
            ("multi pov", "multi_pov"),
            ("multiple pov", "multi_pov"),
            ("multi-perspective", "multi_pov"),
            ("多视角", "multi_pov"),
            ("多pov", "multi_pov"),
            ("objective pov", "objective_camera"),
            ("objective camera", "objective_camera"),
            ("camera objective", "objective_camera"),
            ("客观镜头", "objective_camera"),
            ("镜头视角", "objective_camera"),
        ),
    ),
    SurfacePath.NARRATIVE_DISTANCE.value: ScalarEnumSpec(
        path=SurfacePath.NARRATIVE_DISTANCE,
        allowed_values=("intimate", "close", "medium", "far", "mixed"),
        default_value="close",
        constraint_template="叙事距离枚举必须选择 `{value}`。",
        default_when_missing=True,
        value_aliases=(
            ("very_close", "intimate"),
            ("intimate close", "intimate"),
            ("贴身", "intimate"),
            ("贴身近距", "intimate"),
            ("near", "close"),
            ("near close", "close"),
            ("近距离", "close"),
            ("近景", "close"),
            ("中距离", "medium"),
            ("远距离", "far"),
            ("远景", "far"),
            ("mixed_distance", "mixed"),
            ("远近切换", "mixed"),
            ("距离混合", "mixed"),
        ),
    ),
    SurfacePath.NARRATIVE_TEMPORALITY.value: ScalarEnumSpec(
        path=SurfacePath.NARRATIVE_TEMPORALITY,
        allowed_values=(
            "linear_forward",
            "intercut",
            "flashback_insert",
            "retrospective_frame",
            "mixed",
        ),
        default_value="linear_forward",
        constraint_template="时间组织枚举必须选择 `{value}`。",
        default_when_missing=True,
        value_aliases=(
            ("linear", "linear_forward"),
            ("linear chronology", "linear_forward"),
            ("顺叙", "linear_forward"),
            ("线性推进", "linear_forward"),
            ("crosscut", "intercut"),
            ("交叉剪辑", "intercut"),
            ("flashback", "flashback_insert"),
            ("插叙", "flashback_insert"),
            ("retrospective", "retrospective_frame"),
            ("回顾框架", "retrospective_frame"),
            ("mixed temporality", "mixed"),
            ("时间混合", "mixed"),
        ),
    ),
    SurfacePath.VOICE_NARRATOR_VOICE.value: ScalarEnumSpec(
        path=SurfacePath.VOICE_NARRATOR_VOICE,
        allowed_values=("deadpan_procedural",),
        default_value="deadpan_procedural",
        constraint_template="旁白声线默认选择 `{value}`。",
        value_aliases=(
            ("deadpan", "deadpan_procedural"),
            ("procedural", "deadpan_procedural"),
            ("deadpan procedural", "deadpan_procedural"),
            ("procedural deadpan", "deadpan_procedural"),
            ("bureaucratic deadpan", "deadpan_procedural"),
            ("emotionally flat report", "deadpan_procedural"),
            ("冷面", "deadpan_procedural"),
            ("冷面流程腔", "deadpan_procedural"),
            ("一本正经", "deadpan_procedural"),
            ("公文腔", "deadpan_procedural"),
            ("程序化冷面", "deadpan_procedural"),
        ),
    ),
    SurfacePath.VOICE_INNER_MONOLOGUE.value: ScalarEnumSpec(
        path=SurfacePath.VOICE_INNER_MONOLOGUE,
        allowed_values=("sparse_inline", "quoted_fragments", "free_indirect", "none", "mixed"),
        default_value="sparse_inline",
        constraint_template="内心独白模式枚举必须选择 `{value}`。",
        default_when_missing=True,
        value_aliases=(
            ("embedded", "sparse_inline"),
            ("inline", "sparse_inline"),
            ("summary_report", "sparse_inline"),
            ("sparse inline", "sparse_inline"),
            ("嵌入式", "sparse_inline"),
            ("压缩旁批", "sparse_inline"),
            ("quoted", "quoted_fragments"),
            ("quoted fragments", "quoted_fragments"),
            ("引号独白", "quoted_fragments"),
            ("free indirect", "free_indirect"),
            ("free_indirect_discourse", "free_indirect"),
            ("自由间接引语", "free_indirect"),
            ("no_inner_monologue", "none"),
            ("无内心独白", "none"),
            ("mixed_mode", "mixed"),
            ("混合", "mixed"),
        ),
    ),
}


def canonical_scalar_surface_path(path: Any) -> str:
    normalized = _clean_surface_text(path)
    return SCALAR_SURFACE_PATH_ALIASES.get(normalized, normalized)


def surface_path_spec_for_path(path: Any) -> SurfacePathSpec | None:
    normalized = _clean_surface_text(path)
    if not normalized:
        return None
    try:
        surface_path = path if isinstance(path, SurfacePath) else SurfacePath(normalized)
    except ValueError:
        return None
    return SURFACE_PATH_SPECS.get(surface_path)


def scalar_enum_spec_for_path(path: Any) -> ScalarEnumSpec | None:
    return SCALAR_ENUM_SPECS.get(canonical_scalar_surface_path(path))


def scalar_value_aliases_for_path(path: Any) -> dict[str, str]:
    spec = scalar_enum_spec_for_path(path)
    if spec is None:
        return {}
    return {alias: canonical for alias, canonical in spec.value_aliases}


def canonicalize_scalar_value(path: Any, value: Any) -> str:
    spec = scalar_enum_spec_for_path(path)
    cleaned = _clean_surface_text(value)
    if spec is None or not cleaned:
        return cleaned
    normalized = _normalize_scalar_lookup(cleaned)
    if not normalized:
        return cleaned
    for allowed in spec.allowed_values:
        if _normalize_scalar_lookup(allowed) == normalized:
            return allowed
    for alias, canonical in spec.value_aliases:
        if _normalize_scalar_lookup(alias) == normalized:
            return canonical
    return cleaned


def scalar_value_lookup_rows(path: Any) -> list[tuple[str, str]]:
    spec = scalar_enum_spec_for_path(path)
    if spec is None:
        return []
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for allowed in spec.allowed_values:
        normalized = _normalize_scalar_lookup(allowed)
        if normalized and normalized not in seen:
            rows.append((allowed, allowed))
            seen.add(normalized)
    for alias, canonical in spec.value_aliases:
        normalized = _normalize_scalar_lookup(alias)
        if normalized and normalized not in seen:
            rows.append((alias, canonical))
            seen.add(normalized)
    return rows

def surface_path_prompt_contract(*, paths: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    allowed_paths: set[str] | None = None
    if paths is not None:
        allowed_paths = {
            spec.path.value
            for raw_path in paths
            if (spec := surface_path_spec_for_path(raw_path)) is not None
        }
    rows: list[dict[str, Any]] = []
    for path in SURFACE_PATH_ORDER:
        spec = SURFACE_PATH_SPECS[path]
        if allowed_paths is not None and path.value not in allowed_paths:
            continue
        payload = {
            "path": path.value,
            "cardinality": spec.cardinality,
            "rule_family": spec.rule_family,
            "row_model": spec.row_model,
            "required_fields": list(spec.required_fields),
            "merge_strategy": spec.merge_strategy,
            "max_items": spec.max_items,
            "high_risk": spec.high_risk,
        }
        if spec.enum_source:
            payload["enum_source"] = spec.enum_source
        scalar_spec = SCALAR_ENUM_SPECS.get(path.value)
        if scalar_spec is not None:
            payload["enum_candidates"] = list(scalar_spec.allowed_values)
            payload["value_aliases"] = scalar_value_aliases_for_path(path.value)
            payload["default_value"] = scalar_spec.default_value
        rows.append(payload)
    return rows
