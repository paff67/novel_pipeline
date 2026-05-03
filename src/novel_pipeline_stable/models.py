from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter, WrapValidator, model_validator

from novel_pipeline_stable.style_bible_surface_specs import (
    SURFACE_PATH_SPECS,
    SurfacePath,
    canonicalize_scalar_value,
    scalar_enum_spec_for_path,
    surface_path_spec_for_path,
)


class Evidence(BaseModel):
    evidence_text: str = ""


class EntityRecord(BaseModel):
    name: str
    entity_type: Literal["character", "location", "faction", "item", "skill", "concept", "other"]
    aliases: list[str] = Field(default_factory=list)
    role_in_scene: str = ""
    explicitly_named: bool = True
    evidence: Evidence


class EventRecord(BaseModel):
    name: str
    summary: str
    event_type: str = ""
    participants: list[str] = Field(default_factory=list)
    location: str = ""
    outcomes: list[str] = Field(default_factory=list)
    evidence: Evidence


class FactRecord(BaseModel):
    subject: str
    predicate: str
    object: str
    fact_type: Literal["explicit", "inferred"]
    confidence: Literal["high", "medium", "low"] = "high"
    evidence: Evidence


class RelationshipChange(BaseModel):
    source: str
    target: str
    relation: str
    change: str
    evidence: Evidence


class PowerSystemNote(BaseModel):
    topic: str
    note: str
    evidence: Evidence


class SceneStyleMarker(BaseModel):
    marker: str
    explanation: str
    evidence: Evidence


class FactExtractionResult(BaseModel):
    chapter_id: str
    scene_id: str
    scene_summary: str
    entities: list[EntityRecord] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)
    relationship_changes: list[RelationshipChange] = Field(default_factory=list)
    power_system_notes: list[PowerSystemNote] = Field(default_factory=list)
    style_markers: list[SceneStyleMarker] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_non_empty_content(self) -> "FactExtractionResult":
        has_scene_summary = bool(self.scene_summary.strip())
        has_primary_lists = bool(self.entities or self.events or self.facts)
        has_secondary_lists = bool(self.relationship_changes or self.power_system_notes or self.style_markers)
        has_open_questions = any(question.strip() for question in self.open_questions)
        if has_scene_summary or has_primary_lists or has_secondary_lists or has_open_questions:
            return self
        raise ValueError("Fact extraction result cannot be empty.")


STYLE_WINDOW_SIGNAL_SCHEMA_VERSION = "style-window-signal-v2"

StylePerspectiveToken = Literal[
    "first_person",
    "close_third_person",
    "omniscient_third_person",
    "multi_pov",
    "objective_camera",
    "unspecified",
]
StyleDistanceToken = Literal["intimate", "close", "medium", "far", "mixed", "unspecified"]
StyleTemporalityToken = Literal[
    "linear_forward",
    "intercut",
    "flashback_insert",
    "retrospective_frame",
    "mixed",
    "unspecified",
]
StyleInnerMonologueToken = Literal["embedded", "quoted", "summary_report", "none", "mixed", "unspecified"]


def _clean_style_signal_text(value: Any) -> str:
    return str(value or "").strip()


class StyleEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_ref: str
    quote: str


class StyleSignalRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mechanism_label: str
    execution_logic: str
    trigger: str
    constraint: str
    evidence_ids: list[str]


class StyleRoutingHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_feature_matcher: str
    route_target_action: str
    evidence_ids: list[str]
    axis_id: str
    bucket_id: str


class StyleNegativePitfall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbidden_action: str
    correction_guideline: str
    evidence_ids: list[str]


class StyleAxisHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    axis_id: str
    evidence_ids: list[str]


class StyleBucketHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_id: str
    evidence_ids: list[str]


class StyleScalarContracts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perspective: StylePerspectiveToken
    distance: StyleDistanceToken
    temporality: StyleTemporalityToken
    inner_monologue_mode: StyleInnerMonologueToken


class StyleWindowSignalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[STYLE_WINDOW_SIGNAL_SCHEMA_VERSION]
    window_id: str
    chapter_ids: list[str]
    source_chapter_titles: list[str]
    scalar_contracts: StyleScalarContracts
    surface_markers: list[str]
    narrative_engine_rules: list[StyleSignalRule]
    pacing_rules: list[StyleSignalRule]
    plot_node_logic_rules: list[StyleSignalRule]
    description_rules: list[StyleSignalRule]
    dialogue_rules: list[StyleSignalRule]
    characterization_rules: list[StyleSignalRule]
    sensory_rules: list[StyleSignalRule]
    humor_rules: list[StyleSignalRule]
    satire_rules: list[StyleSignalRule]
    nonstandard_xianxia_rules: list[StyleSignalRule]
    narrator_voice_rules: list[StyleSignalRule]
    register_mix_rules: list[StyleSignalRule]
    negative_pitfalls: list[StyleNegativePitfall]
    rag_candidates: list[StyleRoutingHint]
    worldbook_candidates: list[StyleRoutingHint]
    routing_hints: list[StyleRoutingHint]
    axis_hints: list[StyleAxisHint]
    bucket_hints: list[StyleBucketHint]
    evidence_index: list[StyleEvidenceRef]

    @model_validator(mode="before")
    @classmethod
    def _normalize_model_omissions(cls, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        normalized = dict(payload)
        normalized.setdefault("source_chapter_titles", [])
        scalar_contracts = normalized.get("scalar_contracts")
        if isinstance(scalar_contracts, dict):
            normalized_scalars = dict(scalar_contracts)
            for key in ("perspective", "distance", "temporality", "inner_monologue_mode"):
                if not str(normalized_scalars.get(key, "") or "").strip():
                    normalized_scalars[key] = "unspecified"
            if str(normalized_scalars.get("perspective", "") or "").strip().casefold() == "mixed":
                normalized_scalars["perspective"] = "multi_pov"
            normalized["scalar_contracts"] = normalized_scalars
        return normalized

    @model_validator(mode="after")
    def _ensure_non_empty_content(self) -> "StyleWindowSignalResult":
        signal_lists = (
            self.surface_markers,
            self.narrative_engine_rules,
            self.pacing_rules,
            self.plot_node_logic_rules,
            self.description_rules,
            self.dialogue_rules,
            self.characterization_rules,
            self.sensory_rules,
            self.humor_rules,
            self.satire_rules,
            self.nonstandard_xianxia_rules,
            self.narrator_voice_rules,
            self.register_mix_rules,
            self.negative_pitfalls,
            self.rag_candidates,
            self.worldbook_candidates,
            self.routing_hints,
            self.axis_hints,
            self.bucket_hints,
        )
        has_signal_lists = any(bool(items) for items in signal_lists)
        has_scalar_contracts = any(
            getattr(self.scalar_contracts, key) != "unspecified"
            for key in ("perspective", "distance", "temporality", "inner_monologue_mode")
        )
        if not has_signal_lists and not has_scalar_contracts:
            raise ValueError("Style extraction result cannot be empty.")

        evidence_ids = {
            _clean_style_signal_text(item.evidence_id)
            for item in self.evidence_index
            if _clean_style_signal_text(item.evidence_id)
        }
        evidence_fields = (
            "narrative_engine_rules",
            "pacing_rules",
            "plot_node_logic_rules",
            "description_rules",
            "dialogue_rules",
            "characterization_rules",
            "sensory_rules",
            "humor_rules",
            "satire_rules",
            "nonstandard_xianxia_rules",
            "narrator_voice_rules",
            "register_mix_rules",
            "negative_pitfalls",
            "rag_candidates",
            "worldbook_candidates",
            "routing_hints",
            "axis_hints",
            "bucket_hints",
        )
        if has_signal_lists and not evidence_ids:
            raise ValueError("Style extraction result must provide evidence_index rows for populated signal fields.")
        for field_name in evidence_fields:
            for item in getattr(self, field_name):
                missing = [
                    evidence_id
                    for evidence_id in getattr(item, "evidence_ids", [])
                    if _clean_style_signal_text(evidence_id) and _clean_style_signal_text(evidence_id) not in evidence_ids
                ]
                if missing:
                    raise ValueError(f"{field_name} contains unresolved evidence_ids: {missing}")
        return self


class StyleBibleNarrativeSystem(BaseModel):
    engine: list[str] = Field(default_factory=list)
    perspective: str = ""
    distance: str = ""
    temporality: str = ""
    pacing_rules: list[str] = Field(default_factory=list)
    plot_node_logic: list[str] = Field(default_factory=list)


class StyleBibleExpressionSystem(BaseModel):
    description_rules: list[str] = Field(default_factory=list)
    dialogue_rules: list[str] = Field(default_factory=list)
    characterization_rules: list[str] = Field(default_factory=list)
    sensory_rules: list[str] = Field(default_factory=list)


class StyleBibleAestheticsSystem(BaseModel):
    core_axes: list[str] = Field(default_factory=list)
    pressure_axes: list[str] = Field(default_factory=list)
    humor_recipe: list[str] = Field(default_factory=list)
    satire_targets: list[str] = Field(default_factory=list)
    nonstandard_xianxia_rules: list[str] = Field(default_factory=list)


class StyleBibleVoiceContract(BaseModel):
    narrator_voice: str = ""
    inner_monologue_mode: str = ""
    register_mix: list[str] = Field(default_factory=list)
    negative_pitfalls: list[str] = Field(default_factory=list)


class StyleBibleWorldbookBinding(BaseModel):
    rag_worthy: list[str] = Field(default_factory=list)
    worldbook_worthy: list[str] = Field(default_factory=list)
    routing_hints: list[str] = Field(default_factory=list)


class StyleBibleEvidence(BaseModel):
    claim: str = ""
    evidence_text: str = ""
    source_ref: str = ""


class StyleBibleResult(BaseModel):
    style_id: str = ""
    scope: str = ""
    narrative_system: StyleBibleNarrativeSystem = Field(default_factory=StyleBibleNarrativeSystem)
    expression_system: StyleBibleExpressionSystem = Field(default_factory=StyleBibleExpressionSystem)
    aesthetics_system: StyleBibleAestheticsSystem = Field(default_factory=StyleBibleAestheticsSystem)
    voice_contract: StyleBibleVoiceContract = Field(default_factory=StyleBibleVoiceContract)
    character_arc_rules: list[str] = Field(default_factory=list)
    worldbook_binding: StyleBibleWorldbookBinding = Field(default_factory=StyleBibleWorldbookBinding)
    negative_rules: list[str] = Field(default_factory=list)
    supporting_evidence: list[StyleBibleEvidence] = Field(default_factory=list)


def _clean_model_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _contains_meaningful_model_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_meaningful_model_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_meaningful_model_value(item) for item in value)
    return bool(_clean_model_text(value))


def _is_empty_model_shell(value: Any) -> bool:
    if isinstance(value, dict):
        return not any(_contains_meaningful_model_value(item) for item in value.values())
    if isinstance(value, list):
        return not any(_contains_meaningful_model_value(item) for item in value)
    return not _clean_model_text(value)


def _drop_empty_model_shell_items(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    cleaned_items: list[Any] = []
    for item in value:
        if isinstance(item, (dict, list)) and _is_empty_model_shell(item):
            continue
        cleaned_items.append(item)
    return cleaned_items


def _compose_labeled_pair(left_label: str, left_value: Any, right_label: str, right_value: Any) -> str:
    left_text = _clean_model_text(left_value)
    right_text = _clean_model_text(right_value)
    if left_text and right_text:
        return f"{left_label}：{left_text}；{right_label}：{right_text}"
    return left_text or right_text


def _derive_bucket_candidate_text(payload: dict[str, Any]) -> str:
    return _compose_labeled_pair(
        "触发条件",
        payload.get("trigger_condition"),
        "执行动作",
        payload.get("execution_action"),
    )


def _derive_rule_item_text(payload: dict[str, Any]) -> str:
    for left_label, left_key, right_label, right_key in (
        ("触发条件", "trigger", "执行约束", "constraint"),
        ("触发条件", "trigger_condition", "目标动作", "target_action"),
        ("匹配特征", "query_feature_matcher", "路由动作", "route_target_action"),
        ("禁止动作", "forbidden_action", "纠偏指引", "correction_guideline"),
    ):
        text = _compose_labeled_pair(
            left_label,
            payload.get(left_key),
            right_label,
            payload.get(right_key),
        )
        if text:
            return text
    return ""


def _derive_trigger_constraint_from_text(text: Any) -> tuple[str, str]:
    cleaned = _clean_model_text(text)
    if not cleaned:
        return "", ""
    for marker, prefix in (
        ("时，必须", "必须"),
        ("时,必须", "必须"),
        ("时，需", "需"),
        ("时,需", "需"),
        ("时，需要", "需要"),
        ("时,需要", "需要"),
        ("时，不能", "不能"),
        ("时,不能", "不能"),
        ("时，禁止", "禁止"),
        ("时,禁止", "禁止"),
    ):
        if marker in cleaned:
            left, right = cleaned.split(marker, 1)
            trigger = f"{left}{marker[0]}".strip(" ，,；;")
            constraint = f"{prefix}{right}".strip(" ，,；;")
            if trigger and constraint:
                return trigger, constraint
    for separator in ("；", ";", "，", ","):
        if separator in cleaned:
            left, right = cleaned.split(separator, 1)
            trigger = left.strip(" ，,；;")
            constraint = right.strip(" ，,；;")
            if trigger and constraint:
                return trigger, constraint
    return cleaned, cleaned


def _repair_trigger_constraint_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    trigger = _clean_model_text(payload.get("trigger"))
    constraint = _clean_model_text(payload.get("constraint"))
    if trigger and constraint:
        return payload
    text = _clean_model_text(payload.get("text")) or _derive_rule_item_text(payload)
    fallback_trigger, fallback_constraint = _derive_trigger_constraint_from_text(text)
    if not trigger and fallback_trigger:
        payload["trigger"] = fallback_trigger
    if not constraint and fallback_constraint:
        payload["constraint"] = fallback_constraint
    return payload


def _surface_path_enum(value: str | SurfacePath) -> SurfacePath:
    if isinstance(value, SurfacePath):
        return value
    return SurfacePath(_clean_model_text(value))


class StyleBibleRuleBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    rule_id: str
    text: str
    reasoning_ref: str = Field(alias="_reasoning_ref", serialization_alias="_reasoning_ref")
    evidence_refs: list[str]
    anti_pattern_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _hydrate_text_from_structured_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        trigger_condition = payload.pop("trigger_condition", None)
        if not _clean_model_text(payload.get("query_feature_matcher")) and _clean_model_text(trigger_condition):
            payload["query_feature_matcher"] = trigger_condition
        target_action = payload.pop("target_action", None)
        if not _clean_model_text(payload.get("route_target_action")) and _clean_model_text(target_action):
            payload["route_target_action"] = target_action
        text = _clean_model_text(payload.get("text"))
        if not text:
            text = _derive_rule_item_text(payload)
        if text:
            payload["text"] = text
        return payload

    @model_validator(mode="after")
    def _ensure_core_fields(self) -> "StyleBibleRuleBase":
        if not _clean_model_text(self.text):
            self.text = _derive_rule_item_text(self.model_dump(mode="json", by_alias=True))
        if not _clean_model_text(self.text):
            raise ValueError("Rule text cannot be empty.")
        if not _clean_model_text(self.rule_id):
            raise ValueError("rule_id must not be empty.")
        if not _clean_model_text(self.reasoning_ref):
            raise ValueError("_reasoning_ref must not be empty.")
        if not any(_clean_model_text(ref) for ref in self.evidence_refs):
            raise ValueError("evidence_refs must include at least one non-empty ref.")
        return self


class NarrativeRuleItem(StyleBibleRuleBase):
    trigger: str
    constraint: str

    @model_validator(mode="before")
    @classmethod
    def _repair_missing_trigger_constraint(cls, value: Any) -> Any:
        return _repair_trigger_constraint_payload(value)

    @model_validator(mode="after")
    def _validate_constraint_shape(self) -> "NarrativeRuleItem":
        if not _clean_model_text(self.trigger) or not _clean_model_text(self.constraint):
            raise ValueError("NarrativeRuleItem requires non-empty trigger and constraint.")
        return self


class WorldbookFactItem(StyleBibleRuleBase):
    trigger: str
    constraint: str

    @model_validator(mode="before")
    @classmethod
    def _repair_missing_trigger_constraint(cls, value: Any) -> Any:
        return _repair_trigger_constraint_payload(value)

    @model_validator(mode="after")
    def _validate_constraint_shape(self) -> "WorldbookFactItem":
        if not _clean_model_text(self.trigger) or not _clean_model_text(self.constraint):
            raise ValueError("WorldbookFactItem requires non-empty trigger and constraint.")
        return self


class RoutingHintItem(StyleBibleRuleBase):
    query_feature_matcher: str = Field(
        validation_alias=AliasChoices("query_feature_matcher", "trigger_condition"),
    )
    route_target_action: str = Field(
        validation_alias=AliasChoices("route_target_action", "target_action"),
    )

    @model_validator(mode="after")
    def _validate_routing_shape(self) -> "RoutingHintItem":
        if not _clean_model_text(self.query_feature_matcher) or not _clean_model_text(self.route_target_action):
            raise ValueError("RoutingHintItem requires non-empty query_feature_matcher and route_target_action.")
        return self


class NegativeRuleItem(StyleBibleRuleBase):
    forbidden_action: str
    correction_guideline: str

    @model_validator(mode="after")
    def _validate_negative_shape(self) -> "NegativeRuleItem":
        if not _clean_model_text(self.forbidden_action) or not _clean_model_text(self.correction_guideline):
            raise ValueError("NegativeRuleItem requires non-empty forbidden_action and correction_guideline.")
        return self


class ScalarRuleItem(StyleBibleRuleBase):
    @model_validator(mode="after")
    def _validate_scalar_shape(self) -> "ScalarRuleItem":
        if not _clean_model_text(self.text):
            raise ValueError("ScalarRuleItem requires a non-empty text token.")
        return self


class _LocalNarrativeRuleRow(NarrativeRuleItem):
    surface_path: SurfacePath

    @model_validator(mode="after")
    def _validate_surface_path_contract(self) -> "_LocalNarrativeRuleRow":
        surface_path = _surface_path_enum(self.surface_path)
        spec = SURFACE_PATH_SPECS[surface_path]
        if spec.row_model != "NarrativeRuleItem":
            raise ValueError(f"{surface_path.value} must use {spec.row_model}.")
        return self


class _LocalWorldbookFactRow(WorldbookFactItem):
    surface_path: SurfacePath

    @model_validator(mode="after")
    def _validate_surface_path_contract(self) -> "_LocalWorldbookFactRow":
        surface_path = _surface_path_enum(self.surface_path)
        spec = SURFACE_PATH_SPECS[surface_path]
        if spec.row_model != "WorldbookFactItem":
            raise ValueError(f"{surface_path.value} must use {spec.row_model}.")
        return self


class _LocalRoutingHintRow(RoutingHintItem):
    surface_path: SurfacePath

    @model_validator(mode="after")
    def _validate_surface_path_contract(self) -> "_LocalRoutingHintRow":
        surface_path = _surface_path_enum(self.surface_path)
        spec = SURFACE_PATH_SPECS[surface_path]
        if spec.row_model != "RoutingHintItem":
            raise ValueError(f"{surface_path.value} must use {spec.row_model}.")
        return self


class _LocalNegativeRuleRow(NegativeRuleItem):
    surface_path: SurfacePath

    @model_validator(mode="after")
    def _validate_surface_path_contract(self) -> "_LocalNegativeRuleRow":
        surface_path = _surface_path_enum(self.surface_path)
        spec = SURFACE_PATH_SPECS[surface_path]
        if spec.row_model != "NegativeRuleItem":
            raise ValueError(f"{surface_path.value} must use {spec.row_model}.")
        return self


class _LocalScalarRuleRow(ScalarRuleItem):
    surface_path: SurfacePath

    @model_validator(mode="after")
    def _validate_surface_path_contract(self) -> "_LocalScalarRuleRow":
        surface_path = _surface_path_enum(self.surface_path)
        surface_path_value = surface_path.value
        spec = SURFACE_PATH_SPECS[surface_path]
        if spec.row_model != "ScalarRuleItem":
            raise ValueError(f"{surface_path_value} must use {spec.row_model}.")
        scalar_spec = scalar_enum_spec_for_path(surface_path_value)
        if scalar_spec is None:
            return self
        canonical_value = _clean_model_text(canonicalize_scalar_value(surface_path_value, self.text))
        if canonical_value not in scalar_spec.allowed_values:
            raise ValueError(
                f"ScalarRuleItem for {surface_path_value} must use one of {list(scalar_spec.allowed_values)}."
            )
        self.text = canonical_value
        return self


TYPED_RULE_ROW_MODELS: dict[str, type[StyleBibleRuleBase]] = {
    "NarrativeRuleItem": _LocalNarrativeRuleRow,
    "WorldbookFactItem": _LocalWorldbookFactRow,
    "RoutingHintItem": _LocalRoutingHintRow,
    "NegativeRuleItem": _LocalNegativeRuleRow,
    "ScalarRuleItem": _LocalScalarRuleRow,
}


def _resolve_typed_rule_row_model(row_model_name: str) -> type[StyleBibleRuleBase]:
    resolved = TYPED_RULE_ROW_MODELS.get(_clean_model_text(row_model_name))
    if resolved is None:
        raise ValueError(f"Unknown typed rule row model: {row_model_name}")
    return resolved


def local_rule_row_model_for_path(path: str | SurfacePath) -> type[StyleBibleRuleBase]:
    spec = surface_path_spec_for_path(path)
    if spec is None:
        raise ValueError(f"Unknown surface path for local rule row model: {path}")
    return _resolve_typed_rule_row_model(spec.row_model)


def _validate_local_rule_row_against_surface_spec(payload: dict[str, Any]) -> StyleBibleRuleBase:
    try:
        surface_path = SurfacePath(_clean_model_text(payload.get("surface_path")))
    except ValueError as exc:
        raise ValueError(f"Unknown surface_path: {payload.get('surface_path')}") from exc
    spec = SURFACE_PATH_SPECS[surface_path]
    row_model = _resolve_typed_rule_row_model(spec.row_model)
    return row_model.model_validate(payload)


def _parse_local_rule_row(value: Any, handler: Any) -> Any:
    if isinstance(value, StyleBibleRuleBase):
        return value
    if isinstance(value, dict) and _clean_model_text(value.get("surface_path")):
        return _validate_local_rule_row_against_surface_spec(value)
    return handler(value)


LocalRuleRow = Annotated[
    _LocalNarrativeRuleRow | _LocalWorldbookFactRow | _LocalRoutingHintRow | _LocalNegativeRuleRow | _LocalScalarRuleRow,
    WrapValidator(_parse_local_rule_row),
]
LOCAL_RULE_ROW_ADAPTER = TypeAdapter(LocalRuleRow)


def validate_local_rule_row(payload: Any) -> StyleBibleRuleBase:
    return LOCAL_RULE_ROW_ADAPTER.validate_python(payload)


def coerce_style_bible_rule_item(
    value: Any,
    *,
    path: str | SurfacePath | None = None,
) -> StyleBibleRuleBase | None:
    if value is None:
        return None
    if isinstance(value, StyleBibleRuleBase):
        return value
    if not isinstance(value, dict):
        return None
    payload = dict(value)
    spec = surface_path_spec_for_path(path) if path is not None else None
    if spec is not None:
        payload.setdefault("surface_path", spec.path.value)
        return _validate_local_rule_row_against_surface_spec(payload)
    if _clean_model_text(payload.get("surface_path")):
        return _validate_local_rule_row_against_surface_spec(payload)
    if _clean_model_text(payload.get("query_feature_matcher")) or _clean_model_text(payload.get("trigger_condition")):
        return RoutingHintItem.model_validate(payload)
    if _clean_model_text(payload.get("forbidden_action")) or _clean_model_text(payload.get("correction_guideline")):
        return NegativeRuleItem.model_validate(payload)
    if _clean_model_text(payload.get("trigger")) and _clean_model_text(payload.get("constraint")):
        return NarrativeRuleItem.model_validate(payload)
    if _clean_model_text(payload.get("text")):
        return ScalarRuleItem.model_validate(payload)
    return None


class StyleBibleBucketScratchpadStep(BaseModel):
    step: str = ""
    target_ref: str = ""
    exact_quote: str = ""
    structural_analysis: str = ""


class StyleBibleReduceCrossValidationStep(BaseModel):
    synthesis_step: str = ""
    source_memo_ids: list[str] = Field(default_factory=list)
    extracted_common_mechanism: str = ""
    matched_evidence_refs: list[str] = Field(default_factory=list)


class StyleBibleReasoningEntry(BaseModel):
    reasoning_id: str = ""
    bucket_id: str = ""
    axis_ids: list[str] = Field(default_factory=list)
    claim: str = ""
    observed_commonality: str = ""
    mechanism_inference: str = ""
    downstream_constraint: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    anti_pattern_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _hydrate_compact_reasoning_entry(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        reasoning_id = _clean_model_text(
            payload.get("reasoning_id") or payload.get("_reasoning_ref") or payload.get("reasoning_ref")
        )
        text = _clean_model_text(payload.get("claim") or payload.get("text"))
        if reasoning_id:
            payload["reasoning_id"] = reasoning_id
        if text:
            payload["claim"] = text
        return payload

    @model_validator(mode="after")
    def _ensure_meaningful_content(self) -> "StyleBibleReasoningEntry":
        has_text = any(
            _clean_model_text(val)
            for val in (self.claim, self.observed_commonality, self.mechanism_inference)
        )
        has_refs = any(_clean_model_text(ref) for ref in self.evidence_refs)
        if not has_text or not has_refs:
            raise ValueError(
                f"ReasoningEntry {self.reasoning_id} must have substantive content and evidence refs. "
                f"Content found: {has_text}, Refs found: {has_refs}"
            )
        return self


class StyleBibleReasoningBundle(BaseModel):
    reasoning_version: str = ""
    style_id: str = ""
    scope: str = ""
    entries: list[StyleBibleReasoningEntry] = Field(default_factory=list)


def _infer_local_rule_reasoning_ref(
    *,
    reasoning_entries: list[StyleBibleReasoningEntry],
    rule: StyleBibleRuleBase,
) -> str:
    candidate_refs = {
        _clean_model_text(rule.reasoning_ref),
        *[_clean_model_text(ref) for ref in rule.evidence_refs],
    }
    candidate_refs.discard("")
    if not candidate_refs:
        return ""

    best_reasoning_ref = ""
    best_overlap = 0
    best_reasoning_size = 0
    for entry in reasoning_entries:
        reasoning_ref = _clean_model_text(entry.reasoning_id)
        if not reasoning_ref:
            continue
        evidence_refs = {_clean_model_text(ref) for ref in entry.evidence_refs if _clean_model_text(ref)}
        overlap = len(candidate_refs.intersection(evidence_refs))
        if overlap <= 0:
            continue
        reasoning_size = len(evidence_refs)
        if overlap > best_overlap or (overlap == best_overlap and reasoning_size > best_reasoning_size):
            best_reasoning_ref = reasoning_ref
            best_overlap = overlap
            best_reasoning_size = reasoning_size
    return best_reasoning_ref


class StyleBibleNarrativeSystemV2(BaseModel):
    engine: list[NarrativeRuleItem] = Field(default_factory=list)
    perspective: ScalarRuleItem | None = None
    distance: ScalarRuleItem | None = None
    temporality: ScalarRuleItem | None = None
    pacing_rules: list[NarrativeRuleItem] = Field(default_factory=list)
    plot_node_logic: list[NarrativeRuleItem] = Field(default_factory=list)


class StyleBibleExpressionSystemV2(BaseModel):
    description_rules: list[NarrativeRuleItem] = Field(default_factory=list)
    dialogue_rules: list[NarrativeRuleItem] = Field(default_factory=list)
    characterization_rules: list[NarrativeRuleItem] = Field(default_factory=list)
    sensory_rules: list[NarrativeRuleItem] = Field(default_factory=list)


class StyleBibleAestheticsSystemV2(BaseModel):
    core_axes: list[NarrativeRuleItem] = Field(default_factory=list)
    pressure_axes: list[NarrativeRuleItem] = Field(default_factory=list)
    humor_recipe: list[NarrativeRuleItem] = Field(default_factory=list)
    satire_targets: list[NarrativeRuleItem] = Field(default_factory=list)
    nonstandard_xianxia_rules: list[NarrativeRuleItem] = Field(default_factory=list)


class StyleBibleVoiceContractV2(BaseModel):
    narrator_voice: ScalarRuleItem | None = None
    inner_monologue_mode: ScalarRuleItem | None = None
    register_mix: list[NarrativeRuleItem] = Field(default_factory=list)
    negative_pitfalls: list[NegativeRuleItem] = Field(default_factory=list)


class StyleBibleWorldbookBindingV2(BaseModel):
    rag_worthy: list[WorldbookFactItem] = Field(default_factory=list)
    worldbook_worthy: list[WorldbookFactItem] = Field(default_factory=list)
    routing_hints: list[RoutingHintItem] = Field(default_factory=list)


class StyleBibleAssemblerConflict(BaseModel):
    surface_path: str = ""
    conflict_key: str = ""
    resolution: str = ""
    bucket_ids: list[str] = Field(default_factory=list)
    kept_rule_id: str = ""
    dropped_rule_ids: list[str] = Field(default_factory=list)
    note: str = ""


class StyleBibleDegradationStatus(BaseModel):
    mode: Literal["complete", "degraded"] = "complete"
    skipped_sparse_buckets: list[str] = Field(default_factory=list)
    failed_bucket_ids: list[str] = Field(default_factory=list)
    assembler_conflicts: list[StyleBibleAssemblerConflict] = Field(default_factory=list)


class StyleBibleResultMetadata(BaseModel):
    degradation_status: StyleBibleDegradationStatus = Field(default_factory=StyleBibleDegradationStatus)


class StyleBibleResultV2(BaseModel):
    style_id: str = ""
    scope: str = ""
    narrative_system: StyleBibleNarrativeSystemV2 = Field(default_factory=StyleBibleNarrativeSystemV2)
    expression_system: StyleBibleExpressionSystemV2 = Field(default_factory=StyleBibleExpressionSystemV2)
    aesthetics_system: StyleBibleAestheticsSystemV2 = Field(default_factory=StyleBibleAestheticsSystemV2)
    voice_contract: StyleBibleVoiceContractV2 = Field(default_factory=StyleBibleVoiceContractV2)
    character_arc_rules: list[NarrativeRuleItem] = Field(default_factory=list)
    worldbook_binding: StyleBibleWorldbookBindingV2 = Field(default_factory=StyleBibleWorldbookBindingV2)
    negative_rules: list[NegativeRuleItem] = Field(default_factory=list)
    supporting_evidence: list[StyleBibleEvidence] = Field(default_factory=list)
    metadata: StyleBibleResultMetadata = Field(default_factory=StyleBibleResultMetadata)


class StyleBibleLocalPartialFinal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style_id: str = ""
    scope: str = ""
    rule_rows: list[LocalRuleRow] = Field(default_factory=list)


class StyleBibleLocalReducerOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    scratchpad_cross_validation: list[StyleBibleReduceCrossValidationStep] = Field(
        default_factory=list,
        alias="_scratchpad_cross_validation",
        serialization_alias="_scratchpad_cross_validation",
    )
    reasoning: StyleBibleReasoningBundle = Field(default_factory=StyleBibleReasoningBundle)
    final: StyleBibleLocalPartialFinal = Field(default_factory=StyleBibleLocalPartialFinal)

    @model_validator(mode="before")
    @classmethod
    def _collapse_empty_placeholder_shells(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)

        scratchpad_key = (
            "_scratchpad_cross_validation"
            if "_scratchpad_cross_validation" in payload
            else "scratchpad_cross_validation"
        )
        if scratchpad_key in payload:
            payload[scratchpad_key] = _drop_empty_model_shell_items(payload.get(scratchpad_key))

        reasoning_payload = payload.get("reasoning")
        if isinstance(reasoning_payload, dict):
            normalized_reasoning = dict(reasoning_payload)
            normalized_reasoning["entries"] = _drop_empty_model_shell_items(normalized_reasoning.get("entries"))
            payload["reasoning"] = normalized_reasoning

        final_payload = payload.get("final")
        if isinstance(final_payload, dict):
            normalized_final = dict(final_payload)
            normalized_final["rule_rows"] = _drop_empty_model_shell_items(normalized_final.get("rule_rows"))
            payload["final"] = normalized_final
        return payload

    @model_validator(mode="after")
    def _validate_reasoning_references(self) -> "StyleBibleLocalReducerOutput":
        reasoning_ids = {
            _clean_model_text(entry.reasoning_id)
            for entry in self.reasoning.entries
            if _clean_model_text(entry.reasoning_id)
        }
        seen_rule_ids: set[str] = set()
        for row in self.final.rule_rows:
            rule_id = _clean_model_text(row.rule_id)
            if rule_id in seen_rule_ids:
                raise ValueError(f"Duplicate local reducer rule_id: {rule_id}")
            seen_rule_ids.add(rule_id)

            reasoning_ref = _clean_model_text(row.reasoning_ref)
            if reasoning_ref and reasoning_ref not in reasoning_ids:
                inferred_reasoning_ref = _infer_local_rule_reasoning_ref(
                    reasoning_entries=self.reasoning.entries,
                    rule=row,
                )
                if inferred_reasoning_ref in reasoning_ids:
                    row.reasoning_ref = inferred_reasoning_ref
                    reasoning_ref = inferred_reasoning_ref
            if reasoning_ref and reasoning_ref not in reasoning_ids:
                raise ValueError(f"Local reducer row contains unresolved _reasoning_ref: {reasoning_ref}")
        return self


def _flatten_rule_node(value: Any) -> Any:
    if isinstance(value, list):
        flattened_items: list[Any] = []
        for item in value:
            flattened = _flatten_rule_node(item)
            if isinstance(flattened, str):
                if flattened:
                    flattened_items.append(flattened)
                continue
            if flattened not in (None, {}, []):
                flattened_items.append(flattened)
        return flattened_items
    if isinstance(value, dict):
        if "text" in value and any(
            key in value
            for key in (
                "rule_id",
                "_reasoning_ref",
                "reasoning_ref",
                "evidence_refs",
                "anti_pattern_codes",
                "trigger",
                "constraint",
                "query_feature_matcher",
                "route_target_action",
                "forbidden_action",
                "correction_guideline",
                "trigger_condition",
                "execution_action",
            )
        ):
            return _clean_model_text(value.get("text"))
        if any(
            key in value
            for key in (
                "trigger",
                "constraint",
                "query_feature_matcher",
                "route_target_action",
                "forbidden_action",
                "correction_guideline",
                "trigger_condition",
                "execution_action",
            )
        ):
            text = _derive_rule_item_text(value) or _derive_bucket_candidate_text(value)
            if text:
                return text
        flattened_dict: dict[str, Any] = {}
        for key, item in value.items():
            flattened = _flatten_rule_node(item)
            if flattened in (None, {}, []):
                continue
            flattened_dict[str(key)] = flattened
        return flattened_dict
    return value


def style_bible_payload_to_flat(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flattened = _flatten_rule_node(payload)
    return flattened if isinstance(flattened, dict) else {}


class SceneDocument(BaseModel):
    chapter_id: str
    chapter_title: str
    scene_id: str
    scene_index: int
    text: str
    char_count: int
    source_file: str


class ChapterDocument(BaseModel):
    chapter_id: str
    title: str
    text: str
    source_file: str


class CanonEntity(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    first_seen_chapter: str = ""
    supporting_scene_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CanonIndex(BaseModel):
    entity_count: int
    fact_count: int
    event_count: int
    chapter_summary_count: int
    style_window_count: int
    plot_node_count: int = 0
    relationship_change_count: int = 0
    power_system_note_count: int = 0


class StyleBibleCoverageStageCounts(BaseModel):
    total: int = 0
    sampled: int = 0
    routed: int = 0
    batched: int = 0
    memoed: int = 0
    reduced: int = 0


class StyleBibleCoverageStageSummary(BaseModel):
    stage_id: str
    label: str = ""
    scene_ratio: float = 0.0
    style_window_ratio: float = 0.0
    chapter_ratio: float = 0.0
    axis_coverage_ratio: float = 0.0
    bucket_coverage_ratio: float = 0.0
    notes: list[str] = Field(default_factory=list)


class StyleBibleAxisCoverageRow(BaseModel):
    axis_id: str
    label: str = ""
    scene_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    style_window_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    chapter_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    bucket_ids: list[str] = Field(default_factory=list)
    top_scene_refs: list[str] = Field(default_factory=list)
    top_style_window_refs: list[str] = Field(default_factory=list)


class StyleBibleBucketCoverageRow(BaseModel):
    bucket_id: str
    label: str = ""
    primary_axes: list[str] = Field(default_factory=list)
    scene_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    style_window_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    chapter_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    top_item_refs: list[str] = Field(default_factory=list)


class StyleBibleChapterCoverageRow(BaseModel):
    chapter_id: str
    scene_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    style_window_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    axis_ids: list[str] = Field(default_factory=list)
    bucket_ids: list[str] = Field(default_factory=list)


class StyleBibleSamplingReport(BaseModel):
    report_version: str
    scope_hint: str = ""
    story_node_scope: dict[str, Any] = Field(default_factory=dict)
    sampling_mode: str = ""
    routing_mode: str = ""
    batching_mode: str = ""
    corpus_stats: dict[str, Any] = Field(default_factory=dict)
    selection_limits: dict[str, int] = Field(default_factory=dict)
    stage_coverage: list[StyleBibleCoverageStageSummary] = Field(default_factory=list)
    axis_coverage: list[StyleBibleAxisCoverageRow] = Field(default_factory=list)
    bucket_coverage: list[StyleBibleBucketCoverageRow] = Field(default_factory=list)
    chapter_coverage: list[StyleBibleChapterCoverageRow] = Field(default_factory=list)
    selected_refs: dict[str, list[str]] = Field(default_factory=dict)
    routed_refs: dict[str, list[str]] = Field(default_factory=dict)
    batched_refs: dict[str, list[str]] = Field(default_factory=dict)
    memoed_refs: dict[str, list[str]] = Field(default_factory=dict)
    reduced_refs: dict[str, list[str]] = Field(default_factory=dict)
    overall_cache_hit_ratio: float = 0.0
    cache_metrics: dict[str, Any] = Field(default_factory=dict)
    ttft_summary: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class StyleBibleCatalogEntry(BaseModel):
    id: str
    label: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    primary_axes: list[str] = Field(default_factory=list)


class StyleBibleFeatureMetrics(BaseModel):
    entity_density: float = 0.0
    relationship_change_density: float = 0.0
    institution_density: float = 0.0
    resource_pressure_density: float = 0.0
    body_modification_density: float = 0.0
    dark_humor_signal: float = 0.0
    sales_pitch_signal: float = 0.0
    contract_signal: float = 0.0
    conflict_intensity: float = 0.0
    chapter_position: float = 0.0
    evidence_density: float = 0.0
    voice_novelty: float = 0.0


class StyleBibleBucketMembership(BaseModel):
    bucket_id: str
    confidence: float = 0.0
    lexical_prior_score: float = 0.0
    matched_axes: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    matched_vocab_ids: list[str] = Field(default_factory=list)


class StyleBibleRoutedItem(BaseModel):
    item_id: str
    item_type: Literal["scene", "style_window"]
    source_ref: str = ""
    primary_chapter_id: str = ""
    chapter_ids: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    text_length: int = 0
    summary: str = ""
    features: StyleBibleFeatureMetrics = Field(default_factory=StyleBibleFeatureMetrics)
    axis_scores: dict[str, float] = Field(default_factory=dict)
    axes: list[str] = Field(default_factory=list)
    bucket_memberships: list[StyleBibleBucketMembership] = Field(default_factory=list)
    support_refs: dict[str, list[str]] = Field(default_factory=dict)
    keyword_hits: dict[str, int] = Field(default_factory=dict)
    routing_debug: dict[str, Any] = Field(default_factory=dict)


class StyleBibleRoutedIndex(BaseModel):
    index_version: str
    scope_hint: str = ""
    story_node_scope: dict[str, Any] = Field(default_factory=dict)
    routing_mode: str = ""
    rules_config: str = ""
    corpus_stats: dict[str, Any] = Field(default_factory=dict)
    axis_catalog: list[StyleBibleCatalogEntry] = Field(default_factory=list)
    bucket_catalog: list[StyleBibleCatalogEntry] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    axis_coverage: list[StyleBibleAxisCoverageRow] = Field(default_factory=list)
    bucket_coverage: list[StyleBibleBucketCoverageRow] = Field(default_factory=list)
    chapter_coverage: list[StyleBibleChapterCoverageRow] = Field(default_factory=list)
    items: list[StyleBibleRoutedItem] = Field(default_factory=list)
    support_catalog: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class StyleBibleBatchItem(BaseModel):
    item_id: str
    item_type: Literal["scene", "style_window"]
    source_ref: str = ""
    chapter_ids: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    batch_score: float = 0.0
    axis_ids: list[str] = Field(default_factory=list)
    bucket_ids: list[str] = Field(default_factory=list)


class StyleBibleBatch(BaseModel):
    batch_id: str
    bucket_id: str
    label: str = ""
    cache_affinity_key: str = ""
    planner_rank: int = 0
    axis_focus: list[str] = Field(default_factory=list)
    token_budget: int = 0
    estimated_tokens: int = 0
    scene_count: int = 0
    style_window_count: int = 0
    chapter_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    items: list[StyleBibleBatchItem] = Field(default_factory=list)
    support_refs: dict[str, list[str]] = Field(default_factory=dict)
    novelty_score: float = 0.0
    redundancy_penalty: float = 0.0
    batch_score: float = 0.0


class StyleBibleBucketBatchSummary(BaseModel):
    bucket_id: str
    label: str = ""
    batch_ids: list[str] = Field(default_factory=list)
    axis_ids: list[str] = Field(default_factory=list)
    scene_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    style_window_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    chapter_counts: StyleBibleCoverageStageCounts = Field(default_factory=StyleBibleCoverageStageCounts)
    selected_item_count: int = 0


class StyleBibleBatchPlan(BaseModel):
    plan_version: str
    scope_hint: str = ""
    story_node_scope: dict[str, Any] = Field(default_factory=dict)
    routing_mode: str = ""
    batching_mode: str = ""
    bucket_execution_order: list[str] = Field(default_factory=list)
    source_routed_index_file: str = ""
    rules_config: str = ""
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    bucket_summaries: list[StyleBibleBucketBatchSummary] = Field(default_factory=list)
    batches: list[StyleBibleBatch] = Field(default_factory=list)
    unbatched_item_ids: list[str] = Field(default_factory=list)


class StyleBibleBucketRuleCandidate(BaseModel):
    candidate_id: str = ""
    text: str = ""
    trigger_condition: str = ""
    execution_action: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    anti_pattern_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _hydrate_text_from_structured_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        text = _clean_model_text(payload.get("text"))
        if not text:
            text = _derive_bucket_candidate_text(payload)
        if text:
            payload["text"] = text
        return payload

    @model_validator(mode="after")
    def _ensure_text(self) -> "StyleBibleBucketRuleCandidate":
        if not _clean_model_text(self.text):
            self.text = _derive_bucket_candidate_text(self.model_dump(mode="json"))
        return self


class StyleBibleBucketBatchMemo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scratchpad: list[StyleBibleBucketScratchpadStep] = Field(
        default_factory=list,
        alias="_scratchpad",
        serialization_alias="_scratchpad",
    )
    memo_id: str = ""
    bucket_id: str = ""
    batch_id: str = ""
    label: str = ""
    axis_focus: list[str] = Field(default_factory=list)
    chapter_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    allowed_refs: list[str] = Field(default_factory=list)
    rule_candidates: list[StyleBibleBucketRuleCandidate] = Field(default_factory=list)


class StyleBibleBucketMemo(BaseModel):
    memo_version: str = ""
    memo_id: str = ""
    bucket_id: str = ""
    label: str = ""
    scope_hint: str = ""
    story_node_scope: dict[str, Any] = Field(default_factory=dict)
    axis_focus: list[str] = Field(default_factory=list)
    chapter_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    allowed_refs: list[str] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    rule_candidates: list[StyleBibleBucketRuleCandidate] = Field(default_factory=list)
    batch_memos: list[StyleBibleBucketBatchMemo] = Field(default_factory=list)


class StyleBibleReduceTraceEntry(BaseModel):
    claim_id: str = ""
    claim: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class StyleBibleRuleLineageEntry(BaseModel):
    final_rule_id: str = ""
    surface_path: str = ""
    kept_bucket_id: str = ""
    source_bucket_ids: list[str] = Field(default_factory=list)
    source_kind: str = ""
    reasoning_ref: str = ""
    merged_evidence_refs: list[str] = Field(default_factory=list)
    origin_rule_ids: list[str] = Field(default_factory=list)
    conflict_history: list[str] = Field(default_factory=list)


class StyleBibleMergeEvent(BaseModel):
    surface_path: str = ""
    merge_strategy: str = ""
    group_key: str = ""
    kept_rule_id: str = ""
    kept_bucket_id: str = ""
    source_bucket_ids: list[str] = Field(default_factory=list)
    origin_rule_ids: list[str] = Field(default_factory=list)
    dropped_rule_ids: list[str] = Field(default_factory=list)
    resolution: str = ""
    note: str = ""
