from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Iterable
import tomllib

from novel_pipeline_stable.io_utils import ensure_dir, write_json
from novel_pipeline_stable.models import (
    StyleBibleAxisCoverageRow,
    StyleBibleBatchPlan,
    StyleBibleBucketCoverageRow,
    StyleBibleBucketMembership,
    StyleBibleCatalogEntry,
    StyleBibleChapterCoverageRow,
    StyleBibleCoverageStageCounts,
    StyleBibleCoverageStageSummary,
    StyleBibleFeatureMetrics,
    StyleBibleRoutedIndex,
    StyleBibleRoutedItem,
    StyleBibleSamplingReport,
)
from novel_pipeline_stable.project_domain_vocabulary import load_project_domain_vocabulary
from novel_pipeline_stable.source_text_cleanup import strip_source_site_noise
from novel_pipeline_stable.style_bible_contracts import (
    BATCHING_MODE_BUCKET_AFFINITY_V3,
    ORPHANAGE_BUCKET_ID,
    ORPHANAGE_BUCKET_ROUTING_THRESHOLD,
    PRIORITY_AXES,
    PRIORITY_BUCKETS,
    ROUTED_INDEX_FILE,
    ROUTING_MODE_SIGNAL_FUSION_V2,
    STYLE_BIBLE_ROUTED_INDEX_VERSION,
    STYLE_BIBLE_SAMPLING_REPORT_VERSION,
    axis_catalog_payload,
    bucket_catalog_payload,
)
from novel_pipeline_stable.style_bible_inputs import (
    StyleBibleInputBundle,
    build_scope_hint,
    chapter_sort_key,
    clean_text,
    load_style_bible_inputs,
)
from novel_pipeline_stable.style_bible_runtime_flags import (
    DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS,
    StyleBibleRuntimeFlags,
    load_style_bible_runtime_flags,
)


DEFAULT_PROJECT_DOMAIN_VOCABULARY = load_project_domain_vocabulary()

INSTITUTION_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("institution")
RESOURCE_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("resource_pressure")
BODY_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("body")
DARK_HUMOR_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("dark_humor")
SALES_PITCH_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("sales_pitch")
CONTRACT_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("contract")
CONFLICT_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("conflict")
VOICE_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("voice")
RELATIONSHIP_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("relationship")
LABOR_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("labor")
COOPERATION_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("cooperation")
SHAME_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("shame")
FAMILY_SIGNAL_KEYWORDS = DEFAULT_PROJECT_DOMAIN_VOCABULARY.signal_terms("family")


HIGH_PRECISION_AXES = frozenset({"dark_humor", "institutional_absurdity", "asset_repricing"})
AXIS_ID_SET = frozenset(axis.axis_id for axis in PRIORITY_AXES)
BUCKET_DEFINITION_BY_ID = {bucket.bucket_id: bucket for bucket in PRIORITY_BUCKETS}
AXIS_LEXICAL_PRIORS = {
    axis.axis_id: DEFAULT_PROJECT_DOMAIN_VOCABULARY.axis_terms(axis.axis_id) or axis.keywords
    for axis in PRIORITY_AXES
}
BUCKET_LEXICAL_PRIORS = {
    bucket.bucket_id: DEFAULT_PROJECT_DOMAIN_VOCABULARY.bucket_terms(bucket.bucket_id) or bucket.keywords
    for bucket in PRIORITY_BUCKETS
}


@dataclass(slots=True)
class RoutingRules:
    axis_selection_min_score: float = 0.22
    axis_selection_fallback_score: float = 0.12
    axis_selection_secondary_score: float = 0.18
    style_window_high_precision_bonus: float = 0.04
    evidence_density_bonus_threshold: float = 0.35
    evidence_density_bonus: float = 0.04
    dark_humor_voice_feature_multiplier: float = 0.60
    dark_humor_conflict_support_multiplier: float = 0.80
    dark_humor_min_voice_novelty: float = 0.16
    dark_humor_min_signal: float = 0.34
    dark_humor_min_secondary_signal: float = 0.18
    dark_humor_keyword_hit_threshold: int = 2
    dark_humor_keyword_voice_novelty: float = 0.22
    dark_humor_style_window_min_signal: float = 0.26
    dark_humor_style_window_min_evidence_density: float = 0.32
    dark_humor_bucket_min_axis_score: float = 0.30
    dark_humor_bucket_min_voice_novelty: float = 0.30
    dark_humor_bucket_min_secondary_signal: float = 0.20
    dark_humor_bucket_keyword_hit_threshold: int = 2
    dark_humor_bucket_min_confidence: float = 0.30
    institutional_absurdity_min_institution_density: float = 0.18
    institutional_absurdity_secondary_signal: float = 0.16
    institutional_absurdity_style_window_keyword_hit_threshold: int = 2
    institutional_absurdity_style_window_min_evidence_density: float = 0.32
    institutional_pipeline_bucket_min_axis_score: float = 0.24
    institutional_pipeline_bucket_min_institution_density: float = 0.22
    institutional_pipeline_bucket_min_secondary_signal: float = 0.20
    institutional_pipeline_bucket_keyword_hit_threshold: int = 2
    institutional_pipeline_bucket_min_confidence: float = 0.30
    asset_repricing_min_pricing_signal: float = 0.18
    asset_repricing_min_secondary_signal: float = 0.16
    asset_repricing_keyword_hit_threshold: int = 2
    source_path: str = ""


DEFAULT_ROUTING_RULES = RoutingRules()


def _default_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_router_rules.toml"


def _load_routing_rules(rules_config: str | Path | None) -> RoutingRules:
    target = Path(rules_config).resolve() if rules_config else _default_rules_path().resolve()
    if not target.exists():
        return RoutingRules(source_path=str(target))

    payload = tomllib.loads(target.read_text(encoding="utf-8-sig"))
    selection = payload.get("selection", {})
    dark_humor = payload.get("dark_humor", {})
    institutional_absurdity = payload.get("institutional_absurdity", {})
    institutional_pipeline = payload.get("institutional_pipeline", {})
    asset_repricing = payload.get("asset_repricing", {})
    return RoutingRules(
        axis_selection_min_score=float(selection.get("axis_selection_min_score", 0.22)),
        axis_selection_fallback_score=float(selection.get("axis_selection_fallback_score", 0.12)),
        axis_selection_secondary_score=float(selection.get("axis_selection_secondary_score", 0.18)),
        style_window_high_precision_bonus=float(selection.get("style_window_high_precision_bonus", 0.04)),
        evidence_density_bonus_threshold=float(selection.get("evidence_density_bonus_threshold", 0.35)),
        evidence_density_bonus=float(selection.get("evidence_density_bonus", 0.04)),
        dark_humor_voice_feature_multiplier=float(dark_humor.get("voice_feature_multiplier", 0.60)),
        dark_humor_conflict_support_multiplier=float(dark_humor.get("conflict_support_multiplier", 0.80)),
        dark_humor_min_voice_novelty=float(dark_humor.get("min_voice_novelty", 0.16)),
        dark_humor_min_signal=float(dark_humor.get("min_signal", 0.34)),
        dark_humor_min_secondary_signal=float(dark_humor.get("min_secondary_signal", 0.18)),
        dark_humor_keyword_hit_threshold=int(dark_humor.get("keyword_hit_threshold", 2)),
        dark_humor_keyword_voice_novelty=float(dark_humor.get("keyword_voice_novelty", 0.22)),
        dark_humor_style_window_min_signal=float(dark_humor.get("style_window_min_signal", 0.26)),
        dark_humor_style_window_min_evidence_density=float(dark_humor.get("style_window_min_evidence_density", 0.32)),
        dark_humor_bucket_min_axis_score=float(dark_humor.get("bucket_min_axis_score", 0.30)),
        dark_humor_bucket_min_voice_novelty=float(dark_humor.get("bucket_min_voice_novelty", 0.30)),
        dark_humor_bucket_min_secondary_signal=float(dark_humor.get("bucket_min_secondary_signal", 0.20)),
        dark_humor_bucket_keyword_hit_threshold=int(dark_humor.get("bucket_keyword_hit_threshold", 2)),
        dark_humor_bucket_min_confidence=float(dark_humor.get("bucket_min_confidence", 0.30)),
        institutional_absurdity_min_institution_density=float(
            institutional_absurdity.get("min_institution_density", 0.18)
        ),
        institutional_absurdity_secondary_signal=float(
            institutional_absurdity.get("secondary_signal_threshold", 0.16)
        ),
        institutional_absurdity_style_window_keyword_hit_threshold=int(
            institutional_absurdity.get("style_window_keyword_hit_threshold", 2)
        ),
        institutional_absurdity_style_window_min_evidence_density=float(
            institutional_absurdity.get("style_window_min_evidence_density", 0.32)
        ),
        institutional_pipeline_bucket_min_axis_score=float(
            institutional_pipeline.get("bucket_min_axis_score", 0.24)
        ),
        institutional_pipeline_bucket_min_institution_density=float(
            institutional_pipeline.get("bucket_min_institution_density", 0.22)
        ),
        institutional_pipeline_bucket_min_secondary_signal=float(
            institutional_pipeline.get("bucket_min_secondary_signal", 0.20)
        ),
        institutional_pipeline_bucket_keyword_hit_threshold=int(
            institutional_pipeline.get("bucket_keyword_hit_threshold", 2)
        ),
        institutional_pipeline_bucket_min_confidence=float(
            institutional_pipeline.get("bucket_min_confidence", 0.30)
        ),
        asset_repricing_min_pricing_signal=float(asset_repricing.get("min_pricing_signal", 0.18)),
        asset_repricing_min_secondary_signal=float(asset_repricing.get("min_secondary_signal", 0.16)),
        asset_repricing_keyword_hit_threshold=int(asset_repricing.get("keyword_hit_threshold", 2)),
        source_path=str(target),
    )


def _unique_strings(values: Iterable[Any], *, limit: int | None = None) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_router_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if limit is not None and len(results) >= limit:
            break
    return results


def _clean_router_text(value: Any) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    stripped, _ = strip_source_site_noise(cleaned)
    return clean_text(stripped)


def _clip_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(float(numerator) / float(denominator), 1.0)), 4)


def _keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    body = _clean_router_text(text)
    if not body:
        return []
    hits: list[str] = []
    for keyword in keywords:
        token = clean_text(keyword)
        if token and token in body and token not in hits:
            hits.append(token)
    return hits


def _keyword_score(text: str, keywords: Iterable[str], *, normalizer: int = 4) -> float:
    hits = _keyword_hits(text, keywords)
    return _clip_ratio(len(hits), max(normalizer, 1))


def _estimate_tokens(text: str) -> int:
    body = clean_text(text)
    if not body:
        return 0
    return max(64, ceil(len(body) / 1.6))


def _build_chapter_rank_map(chapter_rows: list[dict[str, Any]]) -> tuple[dict[str, int], list[str]]:
    ordered = sorted(
        {clean_text(row.get("chapter_id")) for row in chapter_rows if clean_text(row.get("chapter_id"))},
        key=chapter_sort_key,
    )
    return {chapter_id: index for index, chapter_id in enumerate(ordered)}, ordered


def _chapter_position(chapter_ids: list[str], chapter_rank_map: dict[str, int]) -> float:
    valid_ranks = [chapter_rank_map[chapter_id] for chapter_id in chapter_ids if chapter_id in chapter_rank_map]
    if not valid_ranks:
        return 0.0
    if len(chapter_rank_map) <= 1:
        return 0.5
    return round(sum(valid_ranks) / len(valid_ranks) / (len(chapter_rank_map) - 1), 4)


def _scene_text_fragments(row: dict[str, Any]) -> list[str]:
    fragments: list[str] = [row.get("scene_summary"), row.get("chapter_title")]
    for event in row.get("events", []):
        if not isinstance(event, dict):
            continue
        fragments.extend((event.get("name"), event.get("summary"), event.get("location")))
        fragments.extend(event.get("outcomes", []))
    for fact in row.get("facts", []):
        if not isinstance(fact, dict):
            continue
        fragments.extend((fact.get("subject"), fact.get("predicate"), fact.get("object")))
    for change in row.get("relationship_changes", []):
        if not isinstance(change, dict):
            continue
        fragments.extend((change.get("source"), change.get("target"), change.get("relation"), change.get("change")))
    for note in row.get("power_system_notes", []):
        if not isinstance(note, dict):
            continue
        fragments.extend((note.get("topic"), note.get("note")))
    for marker in row.get("style_markers", []):
        if not isinstance(marker, dict):
            continue
        fragments.extend((marker.get("marker"), marker.get("explanation")))
    fragments.extend(row.get("open_questions", []))
    return _unique_strings(fragments, limit=80)


def _style_rule_rows(row: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    values = row.get(field_name, [])
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _style_hint_rows(row: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    values = row.get(field_name, [])
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _style_evidence_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("evidence_index", [])
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _style_hint_score(evidence_ids: Any, *, base: float) -> float:
    count = len(_unique_strings(evidence_ids if isinstance(evidence_ids, list) else [], limit=6))
    return round(min(1.0, base + (0.1 * min(count, 3))), 4)


def _style_window_explicit_axis_scores(row: dict[str, Any]) -> dict[str, float]:
    explicit_scores: dict[str, float] = {}
    for hint in _style_hint_rows(row, "axis_hints"):
        axis_id = clean_text(hint.get("axis_id"))
        if axis_id not in AXIS_ID_SET:
            continue
        explicit_scores[axis_id] = max(
            explicit_scores.get(axis_id, 0.0),
            _style_hint_score(hint.get("evidence_ids"), base=0.68),
        )
    for hint in _style_hint_rows(row, "routing_hints"):
        axis_id = clean_text(hint.get("axis_id"))
        if axis_id in AXIS_ID_SET:
            explicit_scores[axis_id] = max(
                explicit_scores.get(axis_id, 0.0),
                _style_hint_score(hint.get("evidence_ids"), base=0.62),
            )
    for hint in _style_hint_rows(row, "bucket_hints"):
        bucket_id = clean_text(hint.get("bucket_id"))
        bucket = BUCKET_DEFINITION_BY_ID.get(bucket_id)
        if bucket is None:
            continue
        for axis_id in bucket.primary_axes:
            explicit_scores[axis_id] = max(
                explicit_scores.get(axis_id, 0.0),
                _style_hint_score(hint.get("evidence_ids"), base=0.56),
            )
    return explicit_scores


def _style_window_explicit_bucket_scores(row: dict[str, Any]) -> dict[str, float]:
    explicit_scores: dict[str, float] = {}
    for hint in _style_hint_rows(row, "bucket_hints"):
        bucket_id = clean_text(hint.get("bucket_id"))
        if bucket_id not in BUCKET_DEFINITION_BY_ID:
            continue
        explicit_scores[bucket_id] = max(
            explicit_scores.get(bucket_id, 0.0),
            _style_hint_score(hint.get("evidence_ids"), base=0.72),
        )
    for hint in _style_hint_rows(row, "routing_hints"):
        bucket_id = clean_text(hint.get("bucket_id"))
        if bucket_id not in BUCKET_DEFINITION_BY_ID:
            continue
        explicit_scores[bucket_id] = max(
            explicit_scores.get(bucket_id, 0.0),
            _style_hint_score(hint.get("evidence_ids"), base=0.66),
        )
    return explicit_scores


def _style_window_summary_parts(row: dict[str, Any]) -> list[str]:
    summary_parts: list[str] = []
    summary_parts.extend(row.get("surface_markers", []))
    for field_name in ("narrative_engine_rules", "humor_rules", "dialogue_rules", "nonstandard_xianxia_rules"):
        summary_parts.extend(item.get("mechanism_label") for item in _style_rule_rows(row, field_name))
    summary_parts.extend(item.get("bucket_id") for item in _style_hint_rows(row, "bucket_hints"))
    summary_parts.extend(item.get("axis_id") for item in _style_hint_rows(row, "axis_hints"))
    return _unique_strings(summary_parts, limit=6)


def _style_window_text_fragments(row: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    fragments.extend(row.get("surface_markers", []))
    fragments.extend(row.get("source_chapter_titles", []))
    scalar_contracts = row.get("scalar_contracts", {})
    if isinstance(scalar_contracts, dict):
        fragments.extend(
            scalar_contracts.get(key)
            for key in ("perspective", "distance", "temporality", "inner_monologue_mode")
        )
    for field_name in (
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
    ):
        for item in _style_rule_rows(row, field_name):
            fragments.extend(
                (
                    item.get("mechanism_label"),
                    item.get("execution_logic"),
                    item.get("trigger"),
                    item.get("constraint"),
                )
            )
    for field_name in ("rag_candidates", "worldbook_candidates", "routing_hints"):
        for item in _style_hint_rows(row, field_name):
            fragments.extend(
                (
                    item.get("axis_id"),
                    item.get("bucket_id"),
                    item.get("query_feature_matcher"),
                    item.get("route_target_action"),
                )
            )
    for item in _style_hint_rows(row, "axis_hints"):
        fragments.append(item.get("axis_id"))
    for item in _style_hint_rows(row, "bucket_hints"):
        fragments.append(item.get("bucket_id"))
    for item in _style_hint_rows(row, "negative_pitfalls"):
        fragments.extend((item.get("forbidden_action"), item.get("correction_guideline")))
    for evidence in _style_evidence_rows(row):
        fragments.extend((evidence.get("source_ref"), evidence.get("quote")))
    return _unique_strings(fragments, limit=80)


def _dark_humor_secondary_signal(features: StyleBibleFeatureMetrics, rules: RoutingRules) -> float:
    return max(
        features.institution_density,
        features.conflict_intensity * rules.dark_humor_conflict_support_multiplier,
    )


def _institutional_pipeline_secondary_signal(
    features: StyleBibleFeatureMetrics,
    _axis_scores: dict[str, float],
) -> float:
    return max(
        features.voice_novelty,
        features.contract_signal,
        features.dark_humor_signal * 0.8,
        features.conflict_intensity * 0.75,
    )


def _axis_feature_score(
    axis_id: str,
    features: StyleBibleFeatureMetrics,
    text: str,
    *,
    rules: RoutingRules | None = None,
) -> float:
    resolved_rules = rules or DEFAULT_ROUTING_RULES
    if axis_id == "resource_pressure":
        return max(features.resource_pressure_density, features.contract_signal * 0.65, features.sales_pitch_signal * 0.45)
    if axis_id == "education_filter":
        return max(features.institution_density, _keyword_score(text, ("考试", "学校", "录取", "排名"), normalizer=3))
    if axis_id == "body_modification":
        return max(features.body_modification_density, features.resource_pressure_density * 0.35)
    if axis_id == "institutional_absurdity":
        return max(features.institution_density, features.dark_humor_signal * 0.4)
    if axis_id == "dark_humor":
        dark_humor_support = _dark_humor_secondary_signal(features, resolved_rules)
        return max(
            features.dark_humor_signal,
            min(features.voice_novelty, dark_humor_support) * resolved_rules.dark_humor_voice_feature_multiplier,
        )
    if axis_id == "family_labor":
        return max(_keyword_score(text, FAMILY_SIGNAL_KEYWORDS, normalizer=2), features.resource_pressure_density * 0.6)
    if axis_id == "labor_logic":
        return max(_keyword_score(text, LABOR_SIGNAL_KEYWORDS, normalizer=3), features.evidence_density * 0.5)
    if axis_id == "identity_shame":
        return max(_keyword_score(text, SHAME_SIGNAL_KEYWORDS, normalizer=3), features.conflict_intensity * 0.55)
    if axis_id == "production_commonwealth":
        return max(_keyword_score(text, COOPERATION_SIGNAL_KEYWORDS, normalizer=3), features.entity_density * 0.5)
    if axis_id == "asset_repricing":
        return max(features.contract_signal, features.sales_pitch_signal, features.resource_pressure_density * 0.45)
    return 0.0


def _passes_high_precision_axis_gate(
    axis_id: str,
    *,
    features: StyleBibleFeatureMetrics,
    keyword_hit_count: int,
    item_type: str,
    rules: RoutingRules | None = None,
) -> bool:
    resolved_rules = rules or DEFAULT_ROUTING_RULES
    if axis_id == "dark_humor":
        secondary_signal = _dark_humor_secondary_signal(features, resolved_rules)
        if features.voice_novelty < resolved_rules.dark_humor_min_voice_novelty:
            return False
        if (
            features.dark_humor_signal >= resolved_rules.dark_humor_min_signal
            and secondary_signal >= resolved_rules.dark_humor_min_secondary_signal
        ):
            return True
        if (
            keyword_hit_count >= resolved_rules.dark_humor_keyword_hit_threshold
            and features.voice_novelty >= resolved_rules.dark_humor_keyword_voice_novelty
            and secondary_signal >= resolved_rules.dark_humor_min_secondary_signal
        ):
            return True
        if (
            item_type == "style_window"
            and features.dark_humor_signal >= resolved_rules.dark_humor_style_window_min_signal
            and features.evidence_density >= resolved_rules.dark_humor_style_window_min_evidence_density
            and secondary_signal >= resolved_rules.dark_humor_min_secondary_signal
        ):
            return True
        return False

    if axis_id == "institutional_absurdity":
        if features.institution_density < resolved_rules.institutional_absurdity_min_institution_density:
            return False
        secondary_signal = max(
            features.dark_humor_signal,
            features.voice_novelty,
            features.contract_signal,
            features.conflict_intensity * 0.8,
        )
        if secondary_signal >= resolved_rules.institutional_absurdity_secondary_signal:
            return True
        if (
            item_type == "style_window"
            and keyword_hit_count >= resolved_rules.institutional_absurdity_style_window_keyword_hit_threshold
            and features.evidence_density >= resolved_rules.institutional_absurdity_style_window_min_evidence_density
        ):
            return True
        return False

    if axis_id == "asset_repricing":
        pricing_signal = max(features.contract_signal, features.sales_pitch_signal)
        if pricing_signal < resolved_rules.asset_repricing_min_pricing_signal:
            return False
        if (
            max(features.resource_pressure_density, features.conflict_intensity, features.institution_density)
            >= resolved_rules.asset_repricing_min_secondary_signal
        ):
            return True
        if keyword_hit_count >= resolved_rules.asset_repricing_keyword_hit_threshold:
            return True
        return False

    return True


def _axis_score_weights(axis_id: str) -> tuple[float, float]:
    if axis_id in HIGH_PRECISION_AXES:
        return 0.25, 0.55
    return 0.65, 0.27


def _router_cutover_enabled(runtime_flags: StyleBibleRuntimeFlags | None) -> bool:
    resolved_flags = runtime_flags or DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS
    return bool(
        resolved_flags.router_semantic_cutover_enabled
        and clean_text(resolved_flags.selective_cutover_target) == "router"
    )


def _shadow_axis_score(
    *,
    feature_score: float,
    lexical_prior_score: float,
    explicit_score: float,
    evidence_bonus: float,
    item_type_bonus: float,
    runtime_flags: StyleBibleRuntimeFlags | None,
) -> tuple[float, str]:
    semantic_axis_score = round(max(float(feature_score), float(explicit_score)), 4)
    shadow_score = round(
        min(
            1.0,
            (semantic_axis_score * 0.72)
            + (float(lexical_prior_score) * 0.18)
            + float(evidence_bonus)
            + float(item_type_bonus),
        ),
        4,
    )
    if not _router_cutover_enabled(runtime_flags):
        return shadow_score, "legacy_signal_fusion"
    if (runtime_flags or DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS).router_lexical_fallback_enabled and shadow_score < lexical_prior_score:
        return round(max(shadow_score, lexical_prior_score), 4), "lexical_fallback"
    return shadow_score, "semantic_cutover"


def _shadow_bucket_confidence(
    *,
    axis_alignment: float,
    bonus: float,
    lexical_prior_score: float,
    runtime_flags: StyleBibleRuntimeFlags | None,
) -> tuple[float, str]:
    shadow_confidence = round(
        min(
            1.0,
            (float(axis_alignment) * 0.72)
            + (float(bonus) * 0.18)
            + (float(lexical_prior_score) * 0.10),
        ),
        4,
    )
    if not _router_cutover_enabled(runtime_flags):
        return shadow_confidence, "legacy_signal_fusion"
    if (runtime_flags or DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS).router_lexical_fallback_enabled and shadow_confidence < lexical_prior_score:
        return round(max(shadow_confidence, lexical_prior_score), 4), "lexical_fallback"
    return shadow_confidence, "semantic_cutover"


def _bucket_admission_gate(
    bucket_id: str,
    *,
    axis_scores: dict[str, float],
    features: StyleBibleFeatureMetrics,
    matched_keyword_count: int,
    confidence: float,
    rules: RoutingRules | None = None,
) -> bool:
    resolved_rules = rules or DEFAULT_ROUTING_RULES
    if bucket_id == "dark_humor":
        return (
            axis_scores.get("dark_humor", 0.0) >= resolved_rules.dark_humor_bucket_min_axis_score
            and features.voice_novelty >= resolved_rules.dark_humor_bucket_min_voice_novelty
            and _dark_humor_secondary_signal(features, resolved_rules) >= resolved_rules.dark_humor_bucket_min_secondary_signal
            and matched_keyword_count >= resolved_rules.dark_humor_bucket_keyword_hit_threshold
            and confidence >= resolved_rules.dark_humor_bucket_min_confidence
        )
    if bucket_id == "institutional_pipeline":
        return (
            axis_scores.get("institutional_absurdity", 0.0) >= resolved_rules.institutional_pipeline_bucket_min_axis_score
            and features.institution_density >= resolved_rules.institutional_pipeline_bucket_min_institution_density
            and _institutional_pipeline_secondary_signal(features, axis_scores)
            >= resolved_rules.institutional_pipeline_bucket_min_secondary_signal
            and matched_keyword_count >= resolved_rules.institutional_pipeline_bucket_keyword_hit_threshold
            and confidence >= resolved_rules.institutional_pipeline_bucket_min_confidence
        )
    return True


def _select_axes(axis_scores: dict[str, float], *, rules: RoutingRules | None = None) -> list[str]:
    resolved_rules = rules or DEFAULT_ROUTING_RULES
    ordered = sorted(axis_scores.items(), key=lambda item: (-item[1], item[0]))
    selected = [axis_id for axis_id, score in ordered if score >= resolved_rules.axis_selection_min_score]
    if not selected and ordered and ordered[0][1] >= resolved_rules.axis_selection_fallback_score:
        selected = [ordered[0][0]]
    if len(selected) == 1 and len(ordered) > 1 and ordered[1][1] >= resolved_rules.axis_selection_secondary_score:
        selected.append(ordered[1][0])
    return selected[:4]


def _bucket_feature_bonus(bucket_id: str, features: StyleBibleFeatureMetrics, text: str) -> float:
    if bucket_id in {"resource_pressure", "family_survival", "gray_labor"}:
        return features.resource_pressure_density
    if bucket_id in {"exam_screening", "institutional_pipeline"}:
        return features.institution_density
    if bucket_id == "body_assetization":
        return max(features.body_modification_density, features.resource_pressure_density * 0.4)
    if bucket_id == "dark_humor":
        return features.dark_humor_signal
    if bucket_id == "identity_shame":
        return _keyword_score(text, SHAME_SIGNAL_KEYWORDS, normalizer=3)
    if bucket_id == "collective_production":
        return _keyword_score(text, COOPERATION_SIGNAL_KEYWORDS, normalizer=3)
    if bucket_id in {"asset_repricing", "contract_sales"}:
        return max(features.contract_signal, features.sales_pitch_signal)
    if bucket_id == "commercialized_conflict":
        return max(features.conflict_intensity, features.sales_pitch_signal)
    return 0.0


def _round_metric(value: float) -> float:
    return round(max(0.0, min(value, 1.0)), 4)


def _scene_features(row: dict[str, Any], chapter_position: float) -> StyleBibleFeatureMetrics:
    text = "\n".join(_scene_text_fragments(row))
    entity_count = len(row.get("entities", []))
    relationship_count = len(row.get("relationship_changes", []))
    power_note_count = len(row.get("power_system_notes", []))
    style_marker_count = len(row.get("style_markers", []))
    fact_count = len(row.get("facts", []))
    event_count = len(row.get("events", []))
    open_question_count = len(row.get("open_questions", []))
    return StyleBibleFeatureMetrics(
        entity_density=_clip_ratio(entity_count, 8),
        relationship_change_density=_clip_ratio(relationship_count, 4),
        institution_density=max(_keyword_score(text, INSTITUTION_SIGNAL_KEYWORDS), _clip_ratio(power_note_count + event_count, 10)),
        resource_pressure_density=max(_keyword_score(text, RESOURCE_SIGNAL_KEYWORDS), _clip_ratio(fact_count + open_question_count, 18)),
        body_modification_density=max(_keyword_score(text, BODY_SIGNAL_KEYWORDS), _clip_ratio(power_note_count, 4)),
        dark_humor_signal=max(_keyword_score(text, DARK_HUMOR_SIGNAL_KEYWORDS), _clip_ratio(style_marker_count, 5)),
        sales_pitch_signal=_keyword_score(text, SALES_PITCH_KEYWORDS),
        contract_signal=_keyword_score(text, CONTRACT_SIGNAL_KEYWORDS),
        conflict_intensity=max(_keyword_score(text, CONFLICT_SIGNAL_KEYWORDS), _clip_ratio(relationship_count + open_question_count, 8)),
        chapter_position=_round_metric(chapter_position),
        evidence_density=_clip_ratio(fact_count + event_count + relationship_count + power_note_count + style_marker_count, 20),
        voice_novelty=max(_keyword_score(text, VOICE_SIGNAL_KEYWORDS), _clip_ratio(style_marker_count + open_question_count, 10)),
    )


def _chapter_summary_catalog(chapter_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for row in sorted(chapter_rows, key=lambda item: chapter_sort_key(item.get("chapter_id"))):
        catalog.append(
            {
                "chapter_id": clean_text(row.get("chapter_id")),
                "chapter_title": clean_text(row.get("chapter_title")),
                "scene_count": int(row.get("scene_count", 0) or 0),
                "scene_summaries": _unique_strings(row.get("scene_summaries", []), limit=3),
                "open_questions": _unique_strings(row.get("open_questions", []), limit=3),
            }
        )
    return catalog


def _plot_node_catalog(plot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for row in sorted(plot_rows, key=lambda item: (chapter_sort_key(item.get("chapter_id")), clean_text(item.get("node_id")))):
        catalog.append(
            {
                "node_id": clean_text(row.get("node_id")),
                "chapter_id": clean_text(row.get("chapter_id")),
                "title": clean_text(row.get("title")),
                "summary": clean_text(row.get("summary")),
                "participants": _unique_strings(row.get("participants", []), limit=6),
                "locations": _unique_strings(row.get("locations", []), limit=4),
                "plot_relevance_hint": clean_text(row.get("plot_relevance_hint")),
            }
        )
    return catalog


def _entity_catalog(entity_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for row in entity_rows:
        catalog.append(
            {
                "entity_id": clean_text(row.get("entity_id")),
                "name": clean_text(row.get("name")),
                "entity_type": clean_text(row.get("entity_type")),
                "aliases": _unique_strings(row.get("aliases", []), limit=4),
                "first_seen_chapter": clean_text(row.get("first_seen_chapter")),
            }
        )
    return catalog


def _support_ref_maps(
    chapter_rows: list[dict[str, Any]],
    plot_rows: list[dict[str, Any]],
    entity_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    plot_by_chapter: dict[str, list[str]] = defaultdict(list)
    for row in plot_rows:
        chapter_id = clean_text(row.get("chapter_id"))
        node_id = clean_text(row.get("node_id"))
        if chapter_id and node_id:
            plot_by_chapter[chapter_id].append(node_id)

    entities_by_chapter: dict[str, list[str]] = defaultdict(list)
    entity_name_map: dict[str, str] = {}
    for row in entity_rows:
        entity_id = clean_text(row.get("entity_id"))
        if not entity_id:
            continue
        first_seen = clean_text(row.get("first_seen_chapter"))
        if first_seen:
            entities_by_chapter[first_seen].append(entity_id)
        for token in [row.get("name"), *row.get("aliases", [])]:
            cleaned = clean_text(token)
            if cleaned and cleaned not in entity_name_map:
                entity_name_map[cleaned] = entity_id

    chapter_ids = {
        clean_text(row.get("chapter_id"))
        for row in chapter_rows
        if clean_text(row.get("chapter_id"))
    }
    return {
        "chapter_ids": sorted(chapter_ids, key=chapter_sort_key),
        "plot_by_chapter": {key: _unique_strings(value, limit=4) for key, value in plot_by_chapter.items()},
        "entities_by_chapter": {key: _unique_strings(value, limit=6) for key, value in entities_by_chapter.items()},
        "entity_name_map": entity_name_map,
    }


def _support_refs(text: str, chapter_ids: list[str], support_maps: dict[str, Any]) -> dict[str, list[str]]:
    chapter_refs = _unique_strings(chapter_ids, limit=6)
    plot_refs: list[str] = []
    for chapter_id in chapter_refs:
        plot_refs.extend(support_maps.get("plot_by_chapter", {}).get(chapter_id, []))
    plot_refs = _unique_strings(plot_refs, limit=4)

    entity_refs: list[str] = []
    for token, entity_id in support_maps.get("entity_name_map", {}).items():
        if token and token in text:
            entity_refs.append(entity_id)
    if not entity_refs:
        for chapter_id in chapter_refs:
            entity_refs.extend(support_maps.get("entities_by_chapter", {}).get(chapter_id, []))

    refs = {
        "chapter_ids": chapter_refs,
        "plot_node_ids": _unique_strings(plot_refs, limit=4),
        "entity_ids": _unique_strings(entity_refs, limit=6),
    }
    return {key: value for key, value in refs.items() if value}


def _style_window_features(row: dict[str, Any], chapter_position: float) -> StyleBibleFeatureMetrics:
    text = "\n".join(_style_window_text_fragments(row))
    explicit_axis_scores = _style_window_explicit_axis_scores(row)
    narrative_engine_count = len(_style_rule_rows(row, "narrative_engine_rules"))
    pacing_count = len(_style_rule_rows(row, "pacing_rules"))
    plot_logic_count = len(_style_rule_rows(row, "plot_node_logic_rules"))
    description_count = len(_style_rule_rows(row, "description_rules"))
    dialogue_count = len(_style_rule_rows(row, "dialogue_rules"))
    characterization_count = len(_style_rule_rows(row, "characterization_rules"))
    sensory_count = len(_style_rule_rows(row, "sensory_rules"))
    humor_count = len(_style_rule_rows(row, "humor_rules"))
    satire_count = len(_style_rule_rows(row, "satire_rules"))
    nonstandard_count = len(_style_rule_rows(row, "nonstandard_xianxia_rules"))
    narrator_voice_count = len(_style_rule_rows(row, "narrator_voice_rules"))
    register_mix_count = len(_style_rule_rows(row, "register_mix_rules"))
    routing_hint_count = sum(
        len(_style_hint_rows(row, field_name))
        for field_name in ("rag_candidates", "worldbook_candidates", "routing_hints")
    )
    axis_hint_count = len(_style_hint_rows(row, "axis_hints"))
    bucket_hint_count = len(_style_hint_rows(row, "bucket_hints"))
    negative_pitfall_count = len(_style_hint_rows(row, "negative_pitfalls"))
    evidence_count = len(_style_evidence_rows(row))
    surface_marker_count = len(row.get("surface_markers", [])) if isinstance(row.get("surface_markers"), list) else 0
    density_base = (
        surface_marker_count
        + narrative_engine_count
        + pacing_count
        + plot_logic_count
        + description_count
        + dialogue_count
        + characterization_count
        + sensory_count
        + humor_count
        + satire_count
        + nonstandard_count
        + narrator_voice_count
        + register_mix_count
        + routing_hint_count
        + axis_hint_count
        + bucket_hint_count
        + negative_pitfall_count
    )
    return StyleBibleFeatureMetrics(
        entity_density=max(
            _keyword_score(text, RELATIONSHIP_SIGNAL_KEYWORDS, normalizer=3),
            _clip_ratio(characterization_count + dialogue_count, 8),
        ),
        relationship_change_density=max(
            _keyword_score(text, ("关系", "同盟", "压迫", "羞耻"), normalizer=3),
            _clip_ratio(plot_logic_count + satire_count, 6),
        ),
        institution_density=max(
            _keyword_score(text, INSTITUTION_SIGNAL_KEYWORDS),
            explicit_axis_scores.get("institutional_absurdity", 0.0),
            explicit_axis_scores.get("education_filter", 0.0) * 0.85,
            _clip_ratio(routing_hint_count + plot_logic_count, 8),
        ),
        resource_pressure_density=max(
            _keyword_score(text, RESOURCE_SIGNAL_KEYWORDS),
            explicit_axis_scores.get("resource_pressure", 0.0),
            explicit_axis_scores.get("family_labor", 0.0) * 0.65,
            _clip_ratio(narrative_engine_count + plot_logic_count + nonstandard_count, 10),
        ),
        body_modification_density=max(
            _keyword_score(text, BODY_SIGNAL_KEYWORDS),
            explicit_axis_scores.get("body_modification", 0.0),
            _clip_ratio(description_count + sensory_count, 8) * 0.5,
        ),
        dark_humor_signal=max(
            _keyword_score(text, DARK_HUMOR_SIGNAL_KEYWORDS),
            explicit_axis_scores.get("dark_humor", 0.0),
            _clip_ratio(humor_count + satire_count + negative_pitfall_count, 6),
        ),
        sales_pitch_signal=max(
            _keyword_score(text, SALES_PITCH_KEYWORDS),
            _clip_ratio(routing_hint_count, 6) * 0.45,
        ),
        contract_signal=max(
            _keyword_score(text, CONTRACT_SIGNAL_KEYWORDS),
            explicit_axis_scores.get("asset_repricing", 0.0) * 0.9,
            _clip_ratio(routing_hint_count + plot_logic_count, 8),
        ),
        conflict_intensity=max(
            _keyword_score(text, CONFLICT_SIGNAL_KEYWORDS),
            _clip_ratio(plot_logic_count + satire_count + negative_pitfall_count, 8),
        ),
        chapter_position=_round_metric(chapter_position),
        evidence_density=_clip_ratio(density_base + evidence_count, 36),
        voice_novelty=max(
            _keyword_score(text, VOICE_SIGNAL_KEYWORDS),
            explicit_axis_scores.get("dark_humor", 0.0) * 0.8,
            _clip_ratio(narrator_voice_count + register_mix_count + humor_count + dialogue_count, 10),
        ),
    )


def _build_axis_scores_with_shadow(
    text: str,
    features: StyleBibleFeatureMetrics,
    *,
    item_type: str,
    rules: RoutingRules | None = None,
    explicit_scores: dict[str, float] | None = None,
    runtime_flags: StyleBibleRuntimeFlags | None = None,
) -> tuple[dict[str, float], dict[str, int], dict[str, dict[str, Any]]]:
    resolved_rules = rules or DEFAULT_ROUTING_RULES
    resolved_explicit_scores = explicit_scores or {}
    axis_scores: dict[str, float] = {}
    keyword_hit_counts: dict[str, int] = {}
    axis_shadow_trace: dict[str, dict[str, Any]] = {}
    for axis in PRIORITY_AXES:
        lexical_prior_terms = AXIS_LEXICAL_PRIORS.get(axis.axis_id, axis.keywords)
        hits = _keyword_hits(text, lexical_prior_terms)
        keyword_hit_counts[axis.axis_id] = len(hits)
        lexical_prior_score = _clip_ratio(len(hits), max(len(lexical_prior_terms) // 3, 2))
        feature_score = _axis_feature_score(axis.axis_id, features, text, rules=resolved_rules)
        explicit_score = float(resolved_explicit_scores.get(axis.axis_id, 0.0) or 0.0)
        if explicit_score <= 0 and axis.axis_id in HIGH_PRECISION_AXES and not _passes_high_precision_axis_gate(
            axis.axis_id,
            features=features,
            keyword_hit_count=len(hits),
            item_type=item_type,
            rules=resolved_rules,
        ):
            axis_scores[axis.axis_id] = 0.0
            axis_shadow_trace[axis.axis_id] = {
                "semantic_axis_score": round(max(feature_score, explicit_score), 4),
                "feature_score": round(feature_score, 4),
                "lexical_prior_score": round(lexical_prior_score, 4),
                "legacy_score": 0.0,
                "shadow_score": 0.0,
                "selected_score": 0.0,
                "final_decision_source": "high_precision_gate_reject",
                "matched_vocab_ids": [f"axis:{axis.axis_id}:{hit}" for hit in hits[:6]],
            }
            continue
        keyword_weight, feature_weight = _axis_score_weights(axis.axis_id)
        item_type_bonus = 0.0
        if item_type == "style_window" and axis.axis_id in {"institutional_absurdity", "dark_humor", "asset_repricing"}:
            item_type_bonus = resolved_rules.style_window_high_precision_bonus
        evidence_bonus = (
            resolved_rules.evidence_density_bonus
            if features.evidence_density >= resolved_rules.evidence_density_bonus_threshold
            else 0.0
        )
        legacy_score = round(
            max(
                min(
                    1.0,
                    (lexical_prior_score * keyword_weight) + (feature_score * feature_weight) + evidence_bonus + item_type_bonus,
                ),
                explicit_score,
            ),
            4,
        )
        shadow_score, selected_source = _shadow_axis_score(
            feature_score=feature_score,
            lexical_prior_score=lexical_prior_score,
            explicit_score=explicit_score,
            evidence_bonus=evidence_bonus,
            item_type_bonus=item_type_bonus,
            runtime_flags=runtime_flags,
        )
        if _router_cutover_enabled(runtime_flags):
            selected_score = round(max(shadow_score, explicit_score), 4)
            if selected_source == "lexical_fallback" and legacy_score > selected_score:
                selected_score = legacy_score
        else:
            selected_score = legacy_score
        axis_scores[axis.axis_id] = selected_score
        axis_shadow_trace[axis.axis_id] = {
            "semantic_axis_score": round(max(feature_score, explicit_score), 4),
            "feature_score": round(feature_score, 4),
            "lexical_prior_score": round(lexical_prior_score, 4),
            "legacy_score": legacy_score,
            "shadow_score": shadow_score,
            "selected_score": selected_score,
            "final_decision_source": selected_source if _router_cutover_enabled(runtime_flags) else "legacy_signal_fusion",
            "matched_vocab_ids": [f"axis:{axis.axis_id}:{hit}" for hit in hits[:6]],
        }
    return axis_scores, keyword_hit_counts, axis_shadow_trace


def _build_axis_scores(
    text: str,
    features: StyleBibleFeatureMetrics,
    *,
    item_type: str,
    rules: RoutingRules | None = None,
    explicit_scores: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, int]]:
    axis_scores, keyword_hit_counts, _ = _build_axis_scores_with_shadow(
        text,
        features,
        item_type=item_type,
        rules=rules,
        explicit_scores=explicit_scores,
    )
    return axis_scores, keyword_hit_counts


def _bucket_memberships_with_shadow(
    axis_scores: dict[str, float],
    features: StyleBibleFeatureMetrics,
    text: str,
    *,
    rules: RoutingRules | None = None,
    explicit_bucket_scores: dict[str, float] | None = None,
    runtime_flags: StyleBibleRuntimeFlags | None = None,
) -> tuple[list[StyleBibleBucketMembership], dict[str, dict[str, Any]]]:
    resolved_rules = rules or DEFAULT_ROUTING_RULES
    resolved_explicit_scores = explicit_bucket_scores or {}
    memberships: list[StyleBibleBucketMembership] = []
    bucket_shadow_trace: dict[str, dict[str, Any]] = {}
    for bucket in PRIORITY_BUCKETS:
        axis_focus = [axis_scores.get(axis_id, 0.0) for axis_id in bucket.primary_axes]
        axis_alignment = round(sum(axis_focus) / len(axis_focus), 4) if axis_focus else 0.0
        lexical_prior_terms = BUCKET_LEXICAL_PRIORS.get(bucket.bucket_id, bucket.keywords)
        matched_keywords = _keyword_hits(text, lexical_prior_terms)
        lexical_prior_score = _clip_ratio(len(matched_keywords), max(len(lexical_prior_terms) // 3, 2))
        bonus = _bucket_feature_bonus(bucket.bucket_id, features, text)
        legacy_confidence = round(min(1.0, (axis_alignment * 0.6) + (lexical_prior_score * 0.3) + (bonus * 0.1)), 4)
        confidence = legacy_confidence
        if not _bucket_admission_gate(
            bucket.bucket_id,
            axis_scores=axis_scores,
            features=features,
            matched_keyword_count=len(matched_keywords),
            confidence=confidence,
            rules=resolved_rules,
        ):
            confidence = 0.0
        explicit_confidence = float(resolved_explicit_scores.get(bucket.bucket_id, 0.0) or 0.0)
        if explicit_confidence > 0:
            confidence = round(max(confidence, explicit_confidence), 4)
            if "explicit_hint" not in matched_keywords:
                matched_keywords = ["explicit_hint", *matched_keywords]
        shadow_confidence, selected_source = _shadow_bucket_confidence(
            axis_alignment=axis_alignment,
            bonus=bonus,
            lexical_prior_score=lexical_prior_score,
            runtime_flags=runtime_flags,
        )
        if _router_cutover_enabled(runtime_flags):
            confidence = round(max(shadow_confidence, explicit_confidence), 4)
            if selected_source == "lexical_fallback" and legacy_confidence > confidence:
                confidence = legacy_confidence
        matched_vocab_ids = [f"bucket:{bucket.bucket_id}"]
        if explicit_confidence > 0:
            matched_vocab_ids.append(f"bucket:{bucket.bucket_id}:explicit_hint")
        memberships.append(
            StyleBibleBucketMembership(
                bucket_id=bucket.bucket_id,
                confidence=confidence,
                lexical_prior_score=lexical_prior_score,
                matched_axes=[
                    axis_id
                    for axis_id in bucket.primary_axes
                    if axis_scores.get(axis_id, 0.0) >= 0.18 or explicit_confidence > 0
                ],
                matched_keywords=matched_keywords[:6],
                matched_vocab_ids=matched_vocab_ids,
            )
        )
        bucket_shadow_trace[bucket.bucket_id] = {
            "semantic_axis_score": axis_alignment,
            "feature_score": round(bonus, 4),
            "lexical_prior_score": round(lexical_prior_score, 4),
            "legacy_score": legacy_confidence,
            "shadow_score": shadow_confidence,
            "selected_score": confidence,
            "final_decision_source": selected_source if _router_cutover_enabled(runtime_flags) else "legacy_signal_fusion",
            "matched_vocab_ids": matched_vocab_ids,
        }
    memberships.sort(key=lambda item: (-item.confidence, item.bucket_id))
    if not memberships:
        return [StyleBibleBucketMembership(bucket_id=ORPHANAGE_BUCKET_ID, confidence=0.0)], bucket_shadow_trace

    strongest_membership = memberships[0]
    if strongest_membership.confidence < ORPHANAGE_BUCKET_ROUTING_THRESHOLD:
        return [
            StyleBibleBucketMembership(
                bucket_id=ORPHANAGE_BUCKET_ID,
                confidence=strongest_membership.confidence,
            )
        ], bucket_shadow_trace

    return [membership for membership in memberships if membership.confidence >= ORPHANAGE_BUCKET_ROUTING_THRESHOLD][:4], bucket_shadow_trace


def _bucket_memberships(
    axis_scores: dict[str, float],
    features: StyleBibleFeatureMetrics,
    text: str,
    *,
    rules: RoutingRules | None = None,
    explicit_bucket_scores: dict[str, float] | None = None,
) -> list[StyleBibleBucketMembership]:
    memberships, _ = _bucket_memberships_with_shadow(
        axis_scores,
        features,
        text,
        rules=rules,
        explicit_bucket_scores=explicit_bucket_scores,
    )
    return memberships


def _build_routed_items(
    inputs: StyleBibleInputBundle,
    scope_hint: str,
    *,
    rules: RoutingRules,
    runtime_flags: StyleBibleRuntimeFlags | None = None,
) -> list[StyleBibleRoutedItem]:
    chapter_rank_map, _ = _build_chapter_rank_map(inputs.chapter_rows)
    support_maps = _support_ref_maps(inputs.chapter_rows, inputs.plot_rows, inputs.entity_rows)
    items: list[StyleBibleRoutedItem] = []

    ordered_fact_rows = sorted(
        inputs.fact_rows,
        key=lambda row: (chapter_sort_key(row.get("chapter_id")), clean_text(row.get("scene_id"))),
    )
    for row in ordered_fact_rows:
        scene_id = clean_text(row.get("scene_id"))
        if not scene_id:
            continue
        chapter_id = clean_text(row.get("chapter_id"))
        chapter_ids = [chapter_id] if chapter_id else []
        text = "\n".join(_scene_text_fragments(row))
        features = _scene_features(row, _chapter_position(chapter_ids, chapter_rank_map))
        axis_scores, keyword_hit_counts, axis_shadow_trace = _build_axis_scores_with_shadow(
            text,
            features,
            item_type="scene",
            rules=rules,
            runtime_flags=runtime_flags,
        )
        bucket_memberships, bucket_shadow_trace = _bucket_memberships_with_shadow(
            axis_scores,
            features,
            text,
            rules=rules,
            runtime_flags=runtime_flags,
        )
        item = StyleBibleRoutedItem(
            item_id=f"scene:{scene_id}",
            item_type="scene",
            source_ref=f"scene:{scene_id}",
            primary_chapter_id=chapter_id,
            chapter_ids=chapter_ids,
            token_estimate=_estimate_tokens(text),
            text_length=len(text),
            summary=clean_text(row.get("scene_summary")) or clean_text(scope_hint),
            features=features,
            axis_scores=axis_scores,
            axes=_select_axes(axis_scores, rules=rules),
            bucket_memberships=bucket_memberships,
            support_refs=_support_refs(text, chapter_ids, support_maps),
            keyword_hits=keyword_hit_counts,
            routing_debug={
                "axis_scores": axis_shadow_trace,
                "bucket_scores": bucket_shadow_trace,
                "feature_flags": (runtime_flags or DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS).as_dict(),
                "final_decision_source": "semantic_router_cutover"
                if _router_cutover_enabled(runtime_flags)
                else "legacy_signal_fusion",
            },
        )
        items.append(item)

    ordered_style_rows = sorted(
        inputs.style_rows,
        key=lambda row: (
            chapter_sort_key(row.get("chapter_ids", [""])[0] if isinstance(row.get("chapter_ids"), list) and row.get("chapter_ids") else ""),
            clean_text(row.get("window_id")),
        ),
    )
    for row in ordered_style_rows:
        window_id = clean_text(row.get("window_id"))
        if not window_id:
            continue
        chapter_ids = _unique_strings(row.get("chapter_ids", []), limit=12)
        text = "\n".join(_style_window_text_fragments(row))
        features = _style_window_features(row, _chapter_position(chapter_ids, chapter_rank_map))
        explicit_axis_scores = _style_window_explicit_axis_scores(row)
        explicit_bucket_scores = _style_window_explicit_bucket_scores(row)
        axis_scores, keyword_hit_counts, axis_shadow_trace = _build_axis_scores_with_shadow(
            text,
            features,
            item_type="style_window",
            rules=rules,
            explicit_scores=explicit_axis_scores,
            runtime_flags=runtime_flags,
        )
        summary_parts = _style_window_summary_parts(row)
        bucket_memberships, bucket_shadow_trace = _bucket_memberships_with_shadow(
            axis_scores,
            features,
            text,
            rules=rules,
            explicit_bucket_scores=explicit_bucket_scores,
            runtime_flags=runtime_flags,
        )
        item = StyleBibleRoutedItem(
            item_id=window_id,
            item_type="style_window",
            source_ref=window_id,
            primary_chapter_id=chapter_ids[0] if chapter_ids else "",
            chapter_ids=chapter_ids,
            token_estimate=_estimate_tokens(text),
            text_length=len(text),
            summary=" / ".join(summary_parts) or clean_text(scope_hint),
            features=features,
            axis_scores=axis_scores,
            axes=_select_axes(axis_scores, rules=rules),
            bucket_memberships=bucket_memberships,
            support_refs=_support_refs(text, chapter_ids, support_maps),
            keyword_hits=keyword_hit_counts,
            routing_debug={
                "axis_scores": axis_shadow_trace,
                "bucket_scores": bucket_shadow_trace,
                "feature_flags": (runtime_flags or DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS).as_dict(),
                "final_decision_source": "semantic_router_cutover"
                if _router_cutover_enabled(runtime_flags)
                else "legacy_signal_fusion",
            },
        )
        items.append(item)

    return items


def _coverage_stage_counts(
    total_refs: set[str],
    sampled_refs: set[str],
    routed_refs: set[str],
    batched_refs: set[str],
    memoed_refs: set[str] | None = None,
    reduced_refs: set[str] | None = None,
) -> StyleBibleCoverageStageCounts:
    memoed_ref_set = memoed_refs or set()
    reduced_ref_set = reduced_refs or set()
    return StyleBibleCoverageStageCounts(
        total=len(total_refs),
        sampled=len(total_refs & sampled_refs),
        routed=len(total_refs & routed_refs),
        batched=len(total_refs & batched_refs),
        memoed=len(total_refs & memoed_ref_set),
        reduced=len(total_refs & reduced_ref_set),
    )


def _stage_value(counts: StyleBibleCoverageStageCounts, stage_id: str) -> int:
    return int(getattr(counts, stage_id, 0) or 0)


def _stage_summary(
    stage_id: str,
    *,
    total_scene_count: int,
    total_style_window_count: int,
    total_chapter_count: int,
    scene_refs: set[str],
    style_window_refs: set[str],
    chapter_refs: set[str],
    axis_rows: list[StyleBibleAxisCoverageRow],
    bucket_rows: list[StyleBibleBucketCoverageRow],
) -> StyleBibleCoverageStageSummary:
    axis_hit_count = sum(
        1
        for row in axis_rows
        if _stage_value(row.scene_counts, stage_id) > 0 or _stage_value(row.style_window_counts, stage_id) > 0
    )
    bucket_hit_count = sum(
        1
        for row in bucket_rows
        if row.bucket_id != ORPHANAGE_BUCKET_ID
        if _stage_value(row.scene_counts, stage_id) > 0 or _stage_value(row.style_window_counts, stage_id) > 0
    )
    notes = {
        "total": ["Full in-scope corpus baseline before v2 routed selection."],
        "sampled": ["Scope-aware source bundle selection before signal-fusion routing."],
        "routed": ["Signal-fusion router coverage across the scoped corpus."],
        "batched": ["Batch planner coverage after token-budget packing."],
        "memoed": ["Bucket memo coverage after per-bucket synthesis."],
        "reduced": ["Final assembled style bible coverage after hierarchical reduce."],
    }
    return StyleBibleCoverageStageSummary(
        stage_id=stage_id,
        label=stage_id.replace("_", " "),
        scene_ratio=_clip_ratio(len(scene_refs), total_scene_count),
        style_window_ratio=_clip_ratio(len(style_window_refs), total_style_window_count),
        chapter_ratio=_clip_ratio(len(chapter_refs), total_chapter_count),
        axis_coverage_ratio=_clip_ratio(axis_hit_count, len(PRIORITY_AXES)),
        bucket_coverage_ratio=_clip_ratio(bucket_hit_count, len(PRIORITY_BUCKETS)),
        notes=notes.get(stage_id, []),
    )


def _coverage_rows(
    items: list[StyleBibleRoutedItem],
    chapter_ids: list[str],
    *,
    selected_item_ids: set[str],
    selected_chapter_ids: set[str],
    batched_item_ids: set[str],
    batched_chapter_ids: set[str],
    memoed_item_ids: set[str] | None = None,
    memoed_chapter_ids: set[str] | None = None,
    reduced_item_ids: set[str] | None = None,
    reduced_chapter_ids: set[str] | None = None,
) -> tuple[list[StyleBibleAxisCoverageRow], list[StyleBibleBucketCoverageRow], list[StyleBibleChapterCoverageRow]]:
    routed_item_ids = {item.item_id for item in items}
    routed_chapter_ids = {
        chapter_id
        for item in items
        for chapter_id in item.chapter_ids
        if chapter_id
    }
    memoed_item_ref_set = memoed_item_ids or set()
    memoed_chapter_ref_set = memoed_chapter_ids or set()
    reduced_item_ref_set = reduced_item_ids or set()
    reduced_chapter_ref_set = reduced_chapter_ids or set()
    scene_items = [item for item in items if item.item_type == "scene"]
    style_window_items = [item for item in items if item.item_type == "style_window"]

    axis_rows: list[StyleBibleAxisCoverageRow] = []
    bucket_ids_by_axis: dict[str, list[str]] = defaultdict(list)
    bucket_catalog_entries = [StyleBibleCatalogEntry(**payload) for payload in bucket_catalog_payload(include_orphan_bucket=True)]
    for bucket in bucket_catalog_entries:
        for axis_id in bucket.primary_axes:
            bucket_ids_by_axis[axis_id].append(bucket.id)

    for axis in PRIORITY_AXES:
        scene_refs = {item.item_id for item in scene_items if axis.axis_id in item.axes}
        style_refs = {item.item_id for item in style_window_items if axis.axis_id in item.axes}
        chapter_refs = {
            chapter_id
            for item in items
            if axis.axis_id in item.axes
            for chapter_id in item.chapter_ids
            if chapter_id
        }
        top_scene_refs = [
            item.item_id
            for item in sorted(scene_items, key=lambda current: (-current.axis_scores.get(axis.axis_id, 0.0), current.item_id))
            if axis.axis_id in item.axes
        ][:6]
        top_style_refs = [
            item.item_id
            for item in sorted(style_window_items, key=lambda current: (-current.axis_scores.get(axis.axis_id, 0.0), current.item_id))
            if axis.axis_id in item.axes
        ][:6]
        axis_rows.append(
            StyleBibleAxisCoverageRow(
                axis_id=axis.axis_id,
                label=axis.label,
                scene_counts=_coverage_stage_counts(
                    scene_refs,
                    selected_item_ids,
                    routed_item_ids,
                    batched_item_ids,
                    memoed_item_ref_set,
                    reduced_item_ref_set,
                ),
                style_window_counts=_coverage_stage_counts(
                    style_refs,
                    selected_item_ids,
                    routed_item_ids,
                    batched_item_ids,
                    memoed_item_ref_set,
                    reduced_item_ref_set,
                ),
                chapter_counts=_coverage_stage_counts(
                    chapter_refs,
                    selected_chapter_ids,
                    routed_chapter_ids,
                    batched_chapter_ids,
                    memoed_chapter_ref_set,
                    reduced_chapter_ref_set,
                ),
                bucket_ids=sorted(bucket_ids_by_axis.get(axis.axis_id, [])),
                top_scene_refs=top_scene_refs,
                top_style_window_refs=top_style_refs,
            )
        )

    bucket_rows: list[StyleBibleBucketCoverageRow] = []
    for bucket in bucket_catalog_entries:
        scene_refs = {
            item.item_id
            for item in scene_items
            if any(membership.bucket_id == bucket.id for membership in item.bucket_memberships)
        }
        style_refs = {
            item.item_id
            for item in style_window_items
            if any(membership.bucket_id == bucket.id for membership in item.bucket_memberships)
        }
        chapter_refs = {
            chapter_id
            for item in items
            if any(membership.bucket_id == bucket.id for membership in item.bucket_memberships)
            for chapter_id in item.chapter_ids
            if chapter_id
        }
        top_item_refs = [
            item.item_id
            for item in sorted(
                items,
                key=lambda current: (
                    -max(
                        (
                            membership.confidence
                            for membership in current.bucket_memberships
                            if membership.bucket_id == bucket.id
                        ),
                        default=0.0,
                    ),
                    current.item_id,
                ),
            )
            if any(membership.bucket_id == bucket.id for membership in item.bucket_memberships)
        ][:8]
        bucket_rows.append(
            StyleBibleBucketCoverageRow(
                bucket_id=bucket.id,
                label=bucket.label,
                primary_axes=list(bucket.primary_axes),
                scene_counts=_coverage_stage_counts(
                    scene_refs,
                    selected_item_ids,
                    routed_item_ids,
                    batched_item_ids,
                    memoed_item_ref_set,
                    reduced_item_ref_set,
                ),
                style_window_counts=_coverage_stage_counts(
                    style_refs,
                    selected_item_ids,
                    routed_item_ids,
                    batched_item_ids,
                    memoed_item_ref_set,
                    reduced_item_ref_set,
                ),
                chapter_counts=_coverage_stage_counts(
                    chapter_refs,
                    selected_chapter_ids,
                    routed_chapter_ids,
                    batched_chapter_ids,
                    memoed_chapter_ref_set,
                    reduced_chapter_ref_set,
                ),
                top_item_refs=top_item_refs,
            )
        )

    chapter_rows: list[StyleBibleChapterCoverageRow] = []
    ordered_chapters = sorted(
        {chapter_id for chapter_id in chapter_ids if chapter_id} | routed_chapter_ids,
        key=chapter_sort_key,
    )
    for chapter_id in ordered_chapters:
        scene_refs = {item.item_id for item in scene_items if chapter_id in item.chapter_ids}
        style_refs = {item.item_id for item in style_window_items if chapter_id in item.chapter_ids}
        chapter_items = [item for item in items if chapter_id in item.chapter_ids]
        chapter_rows.append(
            StyleBibleChapterCoverageRow(
                chapter_id=chapter_id,
                scene_counts=_coverage_stage_counts(
                    scene_refs,
                    selected_item_ids,
                    routed_item_ids,
                    batched_item_ids,
                    memoed_item_ref_set,
                    reduced_item_ref_set,
                ),
                style_window_counts=_coverage_stage_counts(
                    style_refs,
                    selected_item_ids,
                    routed_item_ids,
                    batched_item_ids,
                    memoed_item_ref_set,
                    reduced_item_ref_set,
                ),
                axis_ids=sorted({axis_id for item in chapter_items for axis_id in item.axes}),
                bucket_ids=sorted(
                    {
                        membership.bucket_id
                        for item in chapter_items
                        for membership in item.bucket_memberships
                    }
                ),
            )
        )

    return axis_rows, bucket_rows, chapter_rows


def route_style_bible_inputs(
    inputs: StyleBibleInputBundle,
    *,
    scope_hint: str | None = None,
    rules_config: str | Path | None = None,
    routing_rules: RoutingRules | None = None,
    runtime_flags: StyleBibleRuntimeFlags | None = None,
) -> StyleBibleRoutedIndex:
    resolved_rules = routing_rules or _load_routing_rules(rules_config)
    chapter_ids = [clean_text(row.get("chapter_id")) for row in inputs.chapter_rows if clean_text(row.get("chapter_id"))]
    resolved_scope_hint = scope_hint or build_scope_hint(chapter_ids, story_node_scope=inputs.story_node_scope)
    items = _build_routed_items(
        inputs,
        resolved_scope_hint,
        rules=resolved_rules,
        runtime_flags=runtime_flags,
    )
    axis_rows, bucket_rows, chapter_rows = _coverage_rows(
        items,
        chapter_ids,
        selected_item_ids=set(),
        selected_chapter_ids=set(),
        batched_item_ids=set(),
        batched_chapter_ids=set(),
    )

    active_axis_count = sum(1 for row in axis_rows if row.scene_counts.total or row.style_window_counts.total)
    active_bucket_count = sum(1 for row in bucket_rows if row.scene_counts.total or row.style_window_counts.total)
    unassigned_items = [item for item in items if not item.axes and not item.bucket_memberships]
    orphan_items = [
        item
        for item in items
        if any(membership.bucket_id == ORPHANAGE_BUCKET_ID for membership in item.bucket_memberships)
    ]
    support_catalog = {
        "chapters": _chapter_summary_catalog(inputs.chapter_rows),
        "plot_nodes": _plot_node_catalog(inputs.plot_rows),
        "entities": _entity_catalog(inputs.entity_rows),
    }
    return StyleBibleRoutedIndex(
        index_version=STYLE_BIBLE_ROUTED_INDEX_VERSION,
        scope_hint=resolved_scope_hint,
        story_node_scope=inputs.story_node_scope or {},
        routing_mode=ROUTING_MODE_SIGNAL_FUSION_V2,
        rules_config=resolved_rules.source_path,
        corpus_stats={
            "chapter_count": len(inputs.chapter_rows),
            "scene_count": len(inputs.fact_rows),
            "style_window_count": len(inputs.style_rows),
            "plot_node_count": len(inputs.plot_rows),
            "entity_count": len(inputs.entity_rows),
        },
        axis_catalog=[
            StyleBibleCatalogEntry(
                **{
                    **payload,
                    "keywords": list(AXIS_LEXICAL_PRIORS.get(payload["id"], payload.get("keywords", []))),
                }
            )
            for payload in axis_catalog_payload()
        ],
        bucket_catalog=[
            StyleBibleCatalogEntry(
                **{
                    **payload,
                    "keywords": list(BUCKET_LEXICAL_PRIORS.get(payload["id"], payload.get("keywords", []))),
                }
            )
            for payload in bucket_catalog_payload(include_orphan_bucket=True)
        ],
        coverage_summary={
            "scene_item_count": len([item for item in items if item.item_type == "scene"]),
            "style_window_item_count": len([item for item in items if item.item_type == "style_window"]),
            "chapter_count": len({chapter_id for item in items for chapter_id in item.chapter_ids if chapter_id}),
            "active_axis_count": active_axis_count,
            "active_bucket_count": active_bucket_count,
            "rules_config": resolved_rules.source_path,
            "lexical_prior_config": str(DEFAULT_PROJECT_DOMAIN_VOCABULARY.source_path),
            "feature_flags": (runtime_flags or DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS).as_dict(),
            "final_decision_source": "semantic_router_cutover"
            if _router_cutover_enabled(runtime_flags)
            else "legacy_signal_fusion",
            "multi_axis_item_count": len([item for item in items if len(item.axes) > 1]),
            "multi_bucket_item_count": len([item for item in items if len(item.bucket_memberships) > 1]),
            "orphan_item_count": len(orphan_items),
            "orphan_scene_count": len([item for item in orphan_items if item.item_type == "scene"]),
            "orphan_style_window_count": len([item for item in orphan_items if item.item_type == "style_window"]),
            "unassigned_item_count": len(unassigned_items),
            "unassigned_scene_count": len([item for item in unassigned_items if item.item_type == "scene"]),
            "unassigned_style_window_count": len([item for item in unassigned_items if item.item_type == "style_window"]),
        },
        axis_coverage=axis_rows,
        bucket_coverage=bucket_rows,
        chapter_coverage=chapter_rows,
        items=items,
        support_catalog=support_catalog,
    )


def build_style_bible_sampling_report(
    routed_index: StyleBibleRoutedIndex,
    *,
    selected_item_ids: Iterable[str] = (),
    selected_chapter_ids: Iterable[str] = (),
    batched_item_ids: Iterable[str] = (),
    batched_chapter_ids: Iterable[str] = (),
    memoed_item_ids: Iterable[str] = (),
    memoed_chapter_ids: Iterable[str] = (),
    reduced_item_ids: Iterable[str] = (),
    reduced_chapter_ids: Iterable[str] = (),
    selection_limits: dict[str, int] | None = None,
    sampling_mode: str = "",
    routing_mode: str = ROUTING_MODE_SIGNAL_FUSION_V2,
    batching_mode: str = BATCHING_MODE_BUCKET_AFFINITY_V3,
    batch_plan: StyleBibleBatchPlan | None = None,
    cache_metrics: dict[str, Any] | None = None,
    ttft_summary: dict[str, Any] | None = None,
) -> StyleBibleSamplingReport:
    selected_item_ref_set = {clean_text(item_id) for item_id in selected_item_ids if clean_text(item_id)}
    selected_chapter_ref_set = {clean_text(chapter_id) for chapter_id in selected_chapter_ids if clean_text(chapter_id)}
    batched_item_ref_set = {clean_text(item_id) for item_id in batched_item_ids if clean_text(item_id)}
    batched_chapter_ref_set = {clean_text(chapter_id) for chapter_id in batched_chapter_ids if clean_text(chapter_id)}
    memoed_item_ref_set = {clean_text(item_id) for item_id in memoed_item_ids if clean_text(item_id)}
    memoed_chapter_ref_set = {clean_text(chapter_id) for chapter_id in memoed_chapter_ids if clean_text(chapter_id)}
    reduced_item_ref_set = {clean_text(item_id) for item_id in reduced_item_ids if clean_text(item_id)}
    reduced_chapter_ref_set = {clean_text(chapter_id) for chapter_id in reduced_chapter_ids if clean_text(chapter_id)}
    chapter_ids = [row.chapter_id for row in routed_index.chapter_coverage]
    axis_rows, bucket_rows, chapter_rows = _coverage_rows(
        routed_index.items,
        chapter_ids,
        selected_item_ids=selected_item_ref_set,
        selected_chapter_ids=selected_chapter_ref_set,
        batched_item_ids=batched_item_ref_set,
        batched_chapter_ids=batched_chapter_ref_set,
        memoed_item_ids=memoed_item_ref_set,
        memoed_chapter_ids=memoed_chapter_ref_set,
        reduced_item_ids=reduced_item_ref_set,
        reduced_chapter_ids=reduced_chapter_ref_set,
    )

    all_scene_refs = {item.item_id for item in routed_index.items if item.item_type == "scene"}
    all_style_window_refs = {item.item_id for item in routed_index.items if item.item_type == "style_window"}
    all_chapter_refs = {chapter_id for chapter_id in chapter_ids if chapter_id}
    stage_coverage = [
        _stage_summary(
            stage_id,
            total_scene_count=len(all_scene_refs),
            total_style_window_count=len(all_style_window_refs),
            total_chapter_count=len(all_chapter_refs),
            scene_refs=scene_refs,
            style_window_refs=style_refs,
            chapter_refs=chapter_refs,
            axis_rows=axis_rows,
            bucket_rows=bucket_rows,
        )
        for stage_id, scene_refs, style_refs, chapter_refs in (
            ("total", all_scene_refs, all_style_window_refs, all_chapter_refs),
            (
                "sampled",
                all_scene_refs & selected_item_ref_set,
                all_style_window_refs & selected_item_ref_set,
                all_chapter_refs & selected_chapter_ref_set,
            ),
            ("routed", all_scene_refs, all_style_window_refs, all_chapter_refs),
            (
                "batched",
                all_scene_refs & batched_item_ref_set,
                all_style_window_refs & batched_item_ref_set,
                all_chapter_refs & batched_chapter_ref_set,
            ),
            (
                "memoed",
                all_scene_refs & memoed_item_ref_set,
                all_style_window_refs & memoed_item_ref_set,
                all_chapter_refs & memoed_chapter_ref_set,
            ),
            (
                "reduced",
                all_scene_refs & reduced_item_ref_set,
                all_style_window_refs & reduced_item_ref_set,
                all_chapter_refs & reduced_chapter_ref_set,
            ),
        )
    ]

    notes: list[str] = []
    orphan_item_count = int(routed_index.coverage_summary.get("orphan_item_count", 0) or 0)
    if orphan_item_count:
        notes.append(
            f"Router recovered {orphan_item_count} low-confidence items into the orphanage bucket instead of forcing them into core buckets."
        )
    unassigned_item_count = int(routed_index.coverage_summary.get("unassigned_item_count", 0) or 0)
    if unassigned_item_count:
        notes.append(f"Router left {unassigned_item_count} items without explicit axis/bucket assignments.")
    if batch_plan is not None and batch_plan.unbatched_item_ids:
        notes.append(f"Batch planner left {len(batch_plan.unbatched_item_ids)} routed items unbatched under current limits.")

    cache_payload = cache_metrics if isinstance(cache_metrics, dict) else {}
    ttft_payload = ttft_summary if isinstance(ttft_summary, dict) else {}

    return StyleBibleSamplingReport(
        report_version=STYLE_BIBLE_SAMPLING_REPORT_VERSION,
        scope_hint=routed_index.scope_hint,
        story_node_scope=routed_index.story_node_scope,
        sampling_mode=clean_text(sampling_mode),
        routing_mode=clean_text(routing_mode) or routed_index.routing_mode,
        batching_mode=clean_text(batching_mode),
        corpus_stats=routed_index.corpus_stats,
        selection_limits=selection_limits or {},
        stage_coverage=stage_coverage,
        axis_coverage=axis_rows,
        bucket_coverage=bucket_rows,
        chapter_coverage=chapter_rows,
        selected_refs={
            "scene": sorted(all_scene_refs & selected_item_ref_set),
            "style_window": sorted(all_style_window_refs & selected_item_ref_set),
            "chapter": sorted(all_chapter_refs & selected_chapter_ref_set, key=chapter_sort_key),
        },
        routed_refs={
            "scene": sorted(all_scene_refs),
            "style_window": sorted(all_style_window_refs),
            "chapter": sorted(all_chapter_refs, key=chapter_sort_key),
        },
        batched_refs={
            "scene": sorted(all_scene_refs & batched_item_ref_set),
            "style_window": sorted(all_style_window_refs & batched_item_ref_set),
            "chapter": sorted(all_chapter_refs & batched_chapter_ref_set, key=chapter_sort_key),
        },
        memoed_refs={
            "scene": sorted(all_scene_refs & memoed_item_ref_set),
            "style_window": sorted(all_style_window_refs & memoed_item_ref_set),
            "chapter": sorted(all_chapter_refs & memoed_chapter_ref_set, key=chapter_sort_key),
        },
        reduced_refs={
            "scene": sorted(all_scene_refs & reduced_item_ref_set),
            "style_window": sorted(all_style_window_refs & reduced_item_ref_set),
            "chapter": sorted(all_chapter_refs & reduced_chapter_ref_set, key=chapter_sort_key),
        },
        overall_cache_hit_ratio=float(cache_payload.get("overall_cache_hit_ratio", 0.0) or 0.0),
        cache_metrics=cache_payload,
        ttft_summary=ttft_payload,
        notes=notes,
    )


def build_style_bible_routed_index(
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: str | Path,
    *,
    scope_label: str | None = None,
    rules_config: str | Path | None = None,
) -> StyleBibleRoutedIndex:
    inputs = load_style_bible_inputs(facts_dir, style_dir, canon_dir)
    chapter_ids = [clean_text(row.get("chapter_id")) for row in inputs.chapter_rows if clean_text(row.get("chapter_id"))]
    scope_hint = scope_label or build_scope_hint(chapter_ids, story_node_scope=inputs.story_node_scope)
    routed_index = route_style_bible_inputs(
        inputs,
        scope_hint=scope_hint,
        rules_config=rules_config,
        runtime_flags=load_style_bible_runtime_flags(),
    )
    output_path = ensure_dir(output_dir) / ROUTED_INDEX_FILE
    write_json(output_path, routed_index.model_dump(mode="json"))
    return routed_index
