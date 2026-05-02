from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib

from novel_pipeline_stable.io_utils import ensure_dir, read_json, write_json
from novel_pipeline_stable.models import (
    StyleBibleBatch,
    StyleBibleBatchItem,
    StyleBibleBatchPlan,
    StyleBibleBucketBatchSummary,
    StyleBibleCoverageStageCounts,
    StyleBibleRoutedIndex,
    StyleBibleRoutedItem,
)
from novel_pipeline_stable.style_bible_contracts import (
    BATCH_PLAN_FILE,
    BATCHING_MODE_BUCKET_AFFINITY_V3,
    ORPHANAGE_BUCKET_ID,
    PLANNER_DEBUG_REPORT_FILE,
    PRIORITY_BUCKETS,
    STYLE_BIBLE_BATCH_PLAN_VERSION,
)
from novel_pipeline_stable.style_bible_inputs import chapter_sort_key, clean_text


@dataclass(slots=True)
class BatchingRules:
    token_budget: int = 5200
    max_batches_per_bucket: int = 6
    absolute_max_batches_per_bucket: int = 6
    soft_batches_per_bucket: int = 6
    max_items_per_batch: int = 28
    max_scene_items_per_batch: int = 20
    max_style_window_items_per_batch: int = 10
    chapter_support_limit: int = 4
    plot_node_support_limit: int = 4
    min_bucket_confidence: float = 0.18
    min_scene_in_any_batch_ratio: float = 0.70
    min_style_window_in_any_batch_ratio: float = 0.90
    min_batches_per_core_bucket: int = 1
    scene_token_quota: int = 0
    style_window_token_quota: int = 0
    scene_per_style_window_target: int = 4
    axis_novelty: float = 0.30
    evidence_density: float = 0.20
    entity_novelty: float = 0.05
    chapter_continuity_bonus: float = 0.32
    entity_overlap_bonus: float = 0.22
    plot_node_overlap_bonus: float = 0.12
    conflict_intensity: float = 0.15
    institution_density: float = 0.10
    voice_novelty: float = 0.10
    capacity_efficiency_bonus: float = 0.22
    style_window_scene_guard_penalty: float = 0.18
    redundancy_penalty: float = 0.20
    duplicate_item_penalty: float = 0.35
    repeat_chapter_penalty: float = 0.15
    token_pressure_penalty: float = 0.12
    source_path: str = ""


@dataclass(slots=True)
class BatchState:
    bucket_id: str
    label: str
    axis_focus: list[str]
    token_budget: int
    items: list[StyleBibleRoutedItem] = field(default_factory=list)
    estimated_tokens: int = 0
    scene_count: int = 0
    style_window_count: int = 0
    scene_tokens: int = 0
    style_window_tokens: int = 0
    chapter_ids: set[str] = field(default_factory=set)
    axis_ids: set[str] = field(default_factory=set)
    entity_ids: set[str] = field(default_factory=set)
    plot_node_ids: set[str] = field(default_factory=set)
    item_scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class BucketPlanningContext:
    bucket_id: str
    label: str
    axis_focus: list[str]
    catalog_rank: int
    candidates: list[StyleBibleRoutedItem]
    remaining: list[StyleBibleRoutedItem] = field(default_factory=list)
    states: list[BatchState] = field(default_factory=list)


def _default_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_batching_rules.toml"


def _clip_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(float(numerator) / float(denominator), 1.0)), 4)


def _load_batching_rules(rules_config: str | Path | None) -> BatchingRules:
    target = Path(rules_config).resolve() if rules_config else _default_rules_path().resolve()
    if not target.exists():
        return BatchingRules(source_path=str(target))

    payload = tomllib.loads(target.read_text(encoding="utf-8"))
    limits = payload.get("limits", {})
    targets = payload.get("targets", {})
    weights = payload.get("weights", {})
    configured_max_batches = int(limits.get("max_batches_per_bucket", 6))
    soft_batches_raw = limits.get("soft_batches_per_bucket")
    soft_batches = int(soft_batches_raw) if soft_batches_raw is not None else configured_max_batches
    absolute_max_batches_raw = limits.get("absolute_max_batches_per_bucket")
    absolute_max_batches = int(
        absolute_max_batches_raw
        if absolute_max_batches_raw is not None
        else max(configured_max_batches, soft_batches, 0)
    )
    if absolute_max_batches_raw is not None and absolute_max_batches <= 0:
        absolute_max_batches = 0
    elif absolute_max_batches > 0:
        absolute_max_batches = max(absolute_max_batches, soft_batches)
    return BatchingRules(
        token_budget=int(limits.get("token_budget", 5200)),
        max_batches_per_bucket=max(soft_batches, 0),
        absolute_max_batches_per_bucket=max(absolute_max_batches, 0),
        soft_batches_per_bucket=soft_batches,
        max_items_per_batch=int(limits.get("max_items_per_batch", 28)),
        max_scene_items_per_batch=int(limits.get("max_scene_items_per_batch", 20)),
        max_style_window_items_per_batch=int(limits.get("max_style_window_items_per_batch", 10)),
        chapter_support_limit=int(limits.get("chapter_support_limit", 4)),
        plot_node_support_limit=int(limits.get("plot_node_support_limit", 4)),
        min_bucket_confidence=float(limits.get("min_bucket_confidence", 0.18)),
        min_scene_in_any_batch_ratio=float(targets.get("min_scene_in_any_batch_ratio", 0.70)),
        min_style_window_in_any_batch_ratio=float(targets.get("min_style_window_in_any_batch_ratio", 0.90)),
        min_batches_per_core_bucket=int(targets.get("min_batches_per_core_bucket", 1)),
        scene_token_quota=int(targets.get("scene_token_quota", limits.get("scene_token_quota", 0))),
        style_window_token_quota=int(targets.get("style_window_token_quota", limits.get("style_window_token_quota", 0))),
        scene_per_style_window_target=int(targets.get("scene_per_style_window_target", 4)),
        axis_novelty=float(weights.get("axis_novelty", 0.30)),
        evidence_density=float(weights.get("evidence_density", 0.20)),
        entity_novelty=float(weights.get("entity_novelty", 0.05)),
        chapter_continuity_bonus=float(weights.get("chapter_continuity_bonus", 0.32)),
        entity_overlap_bonus=float(weights.get("entity_overlap_bonus", 0.22)),
        plot_node_overlap_bonus=float(weights.get("plot_node_overlap_bonus", 0.12)),
        conflict_intensity=float(weights.get("conflict_intensity", 0.15)),
        institution_density=float(weights.get("institution_density", 0.10)),
        voice_novelty=float(weights.get("voice_novelty", 0.10)),
        capacity_efficiency_bonus=float(weights.get("capacity_efficiency_bonus", 0.22)),
        style_window_scene_guard_penalty=float(weights.get("style_window_scene_guard_penalty", 0.18)),
        redundancy_penalty=float(weights.get("redundancy_penalty", 0.20)),
        duplicate_item_penalty=float(weights.get("duplicate_item_penalty", 0.35)),
        repeat_chapter_penalty=float(weights.get("repeat_chapter_penalty", 0.15)),
        token_pressure_penalty=float(weights.get("token_pressure_penalty", 0.12)),
        source_path=str(target),
    )


def _membership_confidence(item: StyleBibleRoutedItem, bucket_id: str) -> float:
    return max(
        (
            membership.confidence
            for membership in item.bucket_memberships
            if membership.bucket_id == bucket_id
        ),
        default=0.0,
    )


def _is_orphanage_bucket(bucket_id: str) -> bool:
    return clean_text(bucket_id) == ORPHANAGE_BUCKET_ID


def _bucket_axis_focus(routed_index: StyleBibleRoutedIndex, bucket_id: str) -> list[str]:
    for bucket in routed_index.bucket_catalog:
        if bucket.id == bucket_id:
            return list(bucket.primary_axes)
    for bucket in PRIORITY_BUCKETS:
        if bucket.bucket_id == bucket_id:
            return list(bucket.primary_axes)
    return []


def _planning_bucket_rows(routed_index: StyleBibleRoutedIndex) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in routed_index.bucket_catalog:
        bucket_id = clean_text(bucket.id)
        if not bucket_id or bucket_id in seen:
            continue
        seen.add(bucket_id)
        rows.append(
            {
                "bucket_id": bucket_id,
                "label": clean_text(bucket.label) or bucket_id,
                "axis_focus": [clean_text(item) for item in bucket.primary_axes if clean_text(item)],
            }
        )
    for bucket in PRIORITY_BUCKETS:
        if bucket.bucket_id in seen:
            continue
        seen.add(bucket.bucket_id)
        rows.append(
            {
                "bucket_id": bucket.bucket_id,
                "label": clean_text(bucket.label) or bucket.bucket_id,
                "axis_focus": [clean_text(item) for item in bucket.primary_axes if clean_text(item)],
            }
        )
    return rows


def _resolved_token_quotas(rules: BatchingRules) -> tuple[int, int]:
    scene_quota = max(int(rules.scene_token_quota or 0), 0)
    style_quota = max(int(rules.style_window_token_quota or 0), 0)
    if scene_quota <= 0 and style_quota <= 0:
        return 0, 0
    if scene_quota > rules.token_budget:
        scene_quota = rules.token_budget
    if style_quota > rules.token_budget:
        style_quota = rules.token_budget
    total_quota = scene_quota + style_quota
    if total_quota <= rules.token_budget:
        return scene_quota, style_quota
    if total_quota <= 0:
        return 0, 0
    scale = float(rules.token_budget) / float(total_quota)
    scene_quota = int(scene_quota * scale)
    style_quota = int(style_quota * scale)
    overflow = max(scene_quota + style_quota - rules.token_budget, 0)
    if overflow:
        if scene_quota >= style_quota:
            scene_quota = max(scene_quota - overflow, 0)
        else:
            style_quota = max(style_quota - overflow, 0)
    return scene_quota, style_quota


def _soft_batch_target(rules: BatchingRules) -> int:
    return max(int(rules.soft_batches_per_bucket or rules.max_batches_per_bucket), 0)


def _absolute_batch_cap(rules: BatchingRules) -> int:
    absolute_cap = max(int(rules.absolute_max_batches_per_bucket or 0), 0)
    if absolute_cap <= 0:
        return 0
    return max(absolute_cap, _soft_batch_target(rules))


def _theoretical_batch_bounds(candidates: list[StyleBibleRoutedItem], rules: BatchingRules) -> dict[str, int]:
    total_tokens = sum(item.token_estimate for item in candidates)
    scene_count = sum(1 for item in candidates if item.item_type == "scene")
    style_window_count = len(candidates) - scene_count
    by_tokens = (total_tokens + rules.token_budget - 1) // max(rules.token_budget, 1) if total_tokens else 0
    by_scenes = (
        (scene_count + rules.max_scene_items_per_batch - 1) // max(rules.max_scene_items_per_batch, 1)
        if scene_count and rules.max_scene_items_per_batch > 0
        else 0
    )
    by_style_windows = (
        (style_window_count + rules.max_style_window_items_per_batch - 1) // max(rules.max_style_window_items_per_batch, 1)
        if style_window_count and rules.max_style_window_items_per_batch > 0
        else 0
    )
    return {
        "candidate_count": len(candidates),
        "candidate_scene_count": scene_count,
        "candidate_style_window_count": style_window_count,
        "candidate_token_sum": total_tokens,
        "theoretical_min_batches_by_tokens": by_tokens,
        "theoretical_min_batches_by_scenes": by_scenes,
        "theoretical_min_batches_by_style_windows": by_style_windows,
        "theoretical_lower_bound": max(by_tokens, by_scenes, by_style_windows),
    }


def _initial_bucket_debug_row(
    *,
    bucket_id: str,
    label: str,
    axis_focus: list[str],
    candidates: list[StyleBibleRoutedItem],
    rules: BatchingRules,
) -> dict[str, Any]:
    bounds = _theoretical_batch_bounds(candidates, rules)
    return {
        "bucket_id": bucket_id,
        "label": label,
        "axis_focus": axis_focus,
        "candidate_count": bounds["candidate_count"],
        "candidate_scene_count": bounds["candidate_scene_count"],
        "candidate_style_window_count": bounds["candidate_style_window_count"],
        "candidate_token_sum": bounds["candidate_token_sum"],
        "theoretical_min_batches_by_tokens": bounds["theoretical_min_batches_by_tokens"],
        "theoretical_min_batches_by_scenes": bounds["theoretical_min_batches_by_scenes"],
        "theoretical_min_batches_by_styles": bounds["theoretical_min_batches_by_style_windows"],
        "theoretical_min_batches_by_style_windows": bounds["theoretical_min_batches_by_style_windows"],
        "theoretical_lower_bound": bounds["theoretical_lower_bound"],
        "created_batches": 0,
        "created_batch_count": 0,
        "spillover_batches": 0,
        "spillover_batch_count": 0,
        "build_attempt_count": 0,
        "stalled_build_count": 0,
        "remaining_candidate_count": 0,
        "remaining_scene_count": 0,
        "remaining_style_window_count": 0,
        "remaining_token_sum": 0,
        "remaining_global_unbatched_count": 0,
        "remaining_global_unbatched_scene_count": 0,
        "remaining_global_unbatched_style_window_count": 0,
        "dropped_by_hard_cap": 0,
        "dropped_by_token_cap": 0,
        "dropped_by_scene_cap": 0,
        "dropped_by_style_cap": 0,
        "hard_cap_blocked_count": 0,
        "fit_rejections": {
            "token_budget": 0,
            "max_items": 0,
            "max_scene_items": 0,
            "max_style_window_items": 0,
            "scene_quota_reserved": 0,
            "style_window_quota_reserved": 0,
        },
    }


def _record_fit_rejection(debug_row: dict[str, Any] | None, reason: str) -> None:
    if not debug_row:
        return
    fit_rejections = debug_row.setdefault("fit_rejections", {})
    fit_rejections[reason] = int(fit_rejections.get(reason, 0) or 0) + 1


def _soft_batch_cap_reached(states: list[BatchState], rules: BatchingRules) -> bool:
    soft_target = _soft_batch_target(rules)
    return soft_target > 0 and len(states) >= soft_target


def _absolute_batch_cap_reached(states: list[BatchState], rules: BatchingRules) -> bool:
    absolute_cap = _absolute_batch_cap(rules)
    return absolute_cap > 0 and len(states) >= absolute_cap


def _spillover_batch_count(state_count: int, rules: BatchingRules) -> int:
    soft_target = _soft_batch_target(rules)
    if soft_target <= 0:
        return 0
    return max(state_count - soft_target, 0)


def _bucket_hunger_snapshot(
    context: BucketPlanningContext,
    *,
    used_counts: dict[str, int],
    rules: BatchingRules,
) -> dict[str, Any]:
    remaining = context.remaining
    remaining_count = len(remaining)
    remaining_scene_count = sum(1 for item in remaining if item.item_type == "scene")
    remaining_style_window_count = remaining_count - remaining_scene_count
    remaining_token_sum = sum(item.token_estimate for item in remaining)
    remaining_unique_count = sum(1 for item in remaining if used_counts.get(item.item_id, 0) <= 0)
    remaining_unique_scene_count = sum(
        1 for item in remaining if item.item_type == "scene" and used_counts.get(item.item_id, 0) <= 0
    )
    candidate_score_sum = round(
        sum(
            max(_membership_confidence(item, context.bucket_id) - (used_counts.get(item.item_id, 0) * rules.duplicate_item_penalty), 0.0)
            for item in remaining
        ),
        4,
    )
    theoretical_batches_remaining = (
        (remaining_token_sum + rules.token_budget - 1) // max(rules.token_budget, 1) if remaining_token_sum else 0
    )
    return {
        "bucket_id": context.bucket_id,
        "current_batch_count": len(context.states),
        "soft_batch_target": _soft_batch_target(rules),
        "absolute_batch_cap": _absolute_batch_cap(rules),
        "soft_target_reached": _soft_batch_cap_reached(context.states, rules),
        "hard_cap_reached": _absolute_batch_cap_reached(context.states, rules),
        "remaining_count": remaining_count,
        "remaining_scene_count": remaining_scene_count,
        "remaining_style_window_count": remaining_style_window_count,
        "remaining_token_sum": remaining_token_sum,
        "remaining_unique_count": remaining_unique_count,
        "remaining_unique_scene_count": remaining_unique_scene_count,
        "candidate_score_sum": candidate_score_sum,
        "theoretical_batches_remaining": theoretical_batches_remaining,
    }


def _bucket_round_sort_key(
    context: BucketPlanningContext,
    *,
    used_counts: dict[str, int],
    rules: BatchingRules,
) -> tuple[Any, ...]:
    hunger = _bucket_hunger_snapshot(context, used_counts=used_counts, rules=rules)
    return (
        int(bool(hunger["soft_target_reached"])),
        -int(hunger["remaining_unique_scene_count"]),
        -int(hunger["remaining_unique_count"]),
        -int(hunger["theoretical_batches_remaining"]),
        -float(hunger["candidate_score_sum"]),
        -int(hunger["remaining_token_sum"]),
        context.catalog_rank,
        context.bucket_id,
    )


def _candidate_score(
    item: StyleBibleRoutedItem,
    bucket_id: str,
    state: BatchState,
    used_counts: dict[str, int],
    rules: BatchingRules,
    candidates: list[StyleBibleRoutedItem],
) -> float:
    confidence = _membership_confidence(item, bucket_id)
    if confidence < rules.min_bucket_confidence and not _is_orphanage_bucket(bucket_id):
        return 0.0

    item_axes = set(item.axes)
    new_axes = item_axes - state.axis_ids
    axis_novelty = _clip_ratio(len(new_axes), max(len(item_axes), 1))
    item_entities = set(item.support_refs.get("entity_ids", []))
    new_entities = item_entities - state.entity_ids
    entity_novelty = _clip_ratio(len(new_entities), max(len(item_entities), 1)) if item_entities else 0.0
    shared_entities = item_entities & state.entity_ids
    entity_overlap = _clip_ratio(len(shared_entities), max(len(item_entities), 1)) if item_entities else 0.0
    item_plot_nodes = set(item.support_refs.get("plot_node_ids", []))
    shared_plot_nodes = item_plot_nodes & state.plot_node_ids
    plot_node_overlap = _clip_ratio(len(shared_plot_nodes), max(len(item_plot_nodes), 1)) if item_plot_nodes else 0.0
    chapter_overlap = len(set(item.chapter_ids) & state.chapter_ids)
    chapter_overlap_ratio = _clip_ratio(chapter_overlap, max(len(set(item.chapter_ids)), 1)) if item.chapter_ids else 0.0

    score = confidence
    if _is_orphanage_bucket(bucket_id):
        score = max(score, rules.min_bucket_confidence)
    score += rules.axis_novelty * axis_novelty
    score += rules.evidence_density * item.features.evidence_density
    score += rules.entity_novelty * entity_novelty
    score += rules.chapter_continuity_bonus * chapter_overlap_ratio
    score += rules.entity_overlap_bonus * entity_overlap
    score += rules.plot_node_overlap_bonus * plot_node_overlap
    score += rules.conflict_intensity * item.features.conflict_intensity
    score += rules.institution_density * item.features.institution_density
    score += rules.voice_novelty * item.features.voice_novelty
    if rules.capacity_efficiency_bonus > 0:
        capacity_efficiency = min((score * 1000.0) / max(float(item.token_estimate), 1.0), 2.0)
        score += rules.capacity_efficiency_bonus * _clip_ratio(capacity_efficiency, 2.0)

    penalty = 0.0
    if used_counts.get(item.item_id, 0):
        penalty += rules.duplicate_item_penalty * used_counts[item.item_id]
    if chapter_overlap:
        penalty += rules.repeat_chapter_penalty * _clip_ratio(chapter_overlap, max(len(set(item.chapter_ids)), 1))
    if item.item_id in state.item_scores:
        penalty += 1.0
    token_pressure = _clip_ratio(item.token_estimate, max(rules.token_budget, 1))
    if item.item_type == "style_window":
        token_pressure = min(token_pressure * 1.15, 1.0)
    penalty += rules.token_pressure_penalty * token_pressure
    penalty += (rules.token_pressure_penalty * 0.75) * (token_pressure * token_pressure)
    if (
        item.item_type == "style_window"
        and state.style_window_count > 0
        and rules.style_window_scene_guard_penalty > 0
    ):
        scene_density_target = max(
            min(rules.scene_per_style_window_target, max(rules.max_scene_items_per_batch, 1)),
            0,
        )
        scene_shortfall = (scene_density_target * state.style_window_count) - state.scene_count
        if (
            scene_density_target > 0
            and scene_shortfall > 0
            and _has_viable_item_of_type(
                candidates,
                state,
                rules,
                "scene",
                skip_item_id=item.item_id,
            )
        ):
            penalty += rules.style_window_scene_guard_penalty * _clip_ratio(scene_shortfall, scene_density_target)

    return round(max(score - penalty, 0.0), 4)


def _eligible_candidates(
    candidates: list[StyleBibleRoutedItem],
    bucket_id: str,
    state: BatchState,
    used_counts: dict[str, int],
    rules: BatchingRules,
) -> list[tuple[StyleBibleRoutedItem, float]]:
    ranked: list[tuple[StyleBibleRoutedItem, float]] = []
    for item in candidates:
        if item.item_id in state.item_scores:
            continue
        score = _candidate_score(item, bucket_id, state, used_counts, rules, candidates)
        if score <= 0:
            continue
        ranked.append((item, score))
    ranked.sort(
        key=lambda pair: (
            -pair[1],
            chapter_sort_key(pair[0].primary_chapter_id),
            pair[0].item_id,
        )
    )
    return ranked


def _fits_batch_capacity(item: StyleBibleRoutedItem, state: BatchState, rules: BatchingRules) -> str | None:
    if state.estimated_tokens + item.token_estimate > state.token_budget:
        return "token_budget"
    if len(state.items) >= rules.max_items_per_batch:
        return "max_items"
    if item.item_type == "scene" and state.scene_count >= rules.max_scene_items_per_batch:
        return "max_scene_items"
    if item.item_type == "style_window" and state.style_window_count >= rules.max_style_window_items_per_batch:
        return "max_style_window_items"
    return None


def _has_viable_item_of_type(
    candidates: list[StyleBibleRoutedItem],
    state: BatchState,
    rules: BatchingRules,
    item_type: str,
    *,
    skip_item_id: str = "",
) -> bool:
    for candidate in candidates:
        if candidate.item_type != item_type:
            continue
        if candidate.item_id == skip_item_id or candidate.item_id in state.item_scores:
            continue
        if _fits_batch_capacity(candidate, state, rules) is None:
            return True
    return False


def _quota_block_reason(
    item: StyleBibleRoutedItem,
    state: BatchState,
    rules: BatchingRules,
    candidates: list[StyleBibleRoutedItem],
) -> str | None:
    scene_quota, style_window_quota = _resolved_token_quotas(rules)
    if scene_quota <= 0 and style_window_quota <= 0:
        return None
    if item.item_type == "scene":
        if scene_quota > 0 and state.scene_tokens + item.token_estimate > scene_quota:
            if (
                style_window_quota > 0
                and state.style_window_tokens < style_window_quota
                and _has_viable_item_of_type(
                    candidates,
                    state,
                    rules,
                    "style_window",
                    skip_item_id=item.item_id,
                )
            ):
                return "style_window_quota_reserved"
        return None
    if style_window_quota > 0 and state.style_window_tokens + item.token_estimate > style_window_quota:
        if (
            scene_quota > 0
            and state.scene_tokens < scene_quota
            and _has_viable_item_of_type(
                candidates,
                state,
                rules,
                "scene",
                skip_item_id=item.item_id,
            )
        ):
            return "scene_quota_reserved"
    return None


def _fit_block_reason(
    item: StyleBibleRoutedItem,
    state: BatchState,
    rules: BatchingRules,
    candidates: list[StyleBibleRoutedItem],
) -> str | None:
    capacity_reason = _fits_batch_capacity(item, state, rules)
    if capacity_reason is not None:
        return capacity_reason
    return _quota_block_reason(item, state, rules, candidates)


def _fits_batch(
    item: StyleBibleRoutedItem,
    state: BatchState,
    rules: BatchingRules,
    candidates: list[StyleBibleRoutedItem],
) -> bool:
    return _fit_block_reason(item, state, rules, candidates) is None


def _required_item_type(
    candidates: list[StyleBibleRoutedItem],
    state: BatchState,
    rules: BatchingRules,
) -> str:
    scene_quota, style_window_quota = _resolved_token_quotas(rules)
    if (
        scene_quota > 0
        and state.scene_tokens < scene_quota
        and _has_viable_item_of_type(candidates, state, rules, "scene")
    ):
        return "scene"
    if (
        style_window_quota > 0
        and state.style_window_tokens < style_window_quota
        and _has_viable_item_of_type(candidates, state, rules, "style_window")
    ):
        return "style_window"
    return ""


def _append_item(state: BatchState, item: StyleBibleRoutedItem, score: float) -> None:
    state.items.append(item)
    state.estimated_tokens += item.token_estimate
    state.item_scores[item.item_id] = score
    if item.item_type == "scene":
        state.scene_count += 1
        state.scene_tokens += item.token_estimate
    else:
        state.style_window_count += 1
        state.style_window_tokens += item.token_estimate
    state.chapter_ids.update(item.chapter_ids)
    state.axis_ids.update(item.axes)
    state.entity_ids.update(item.support_refs.get("entity_ids", []))
    state.plot_node_ids.update(item.support_refs.get("plot_node_ids", []))


def _try_append_ranked_candidate(
    ranked: list[tuple[StyleBibleRoutedItem, float]],
    *,
    candidates: list[StyleBibleRoutedItem],
    state: BatchState,
    rules: BatchingRules,
    required_item_type: str,
    debug_row: dict[str, Any] | None,
) -> bool:
    for item, score in ranked:
        if required_item_type and item.item_type != required_item_type:
            continue
        block_reason = _fit_block_reason(item, state, rules, candidates)
        if block_reason is not None:
            _record_fit_rejection(debug_row, block_reason)
            continue
        _append_item(state, item, score)
        return True
    return False


def _build_batch(
    candidates: list[StyleBibleRoutedItem],
    bucket_id: str,
    label: str,
    axis_focus: list[str],
    used_counts: dict[str, int],
    rules: BatchingRules,
    debug_row: dict[str, Any] | None = None,
) -> BatchState | None:
    state = BatchState(bucket_id=bucket_id, label=label, axis_focus=axis_focus, token_budget=rules.token_budget)
    while True:
        ranked = _eligible_candidates(candidates, bucket_id, state, used_counts, rules)
        if not ranked:
            break
        required_item_type = _required_item_type(candidates, state, rules)
        appended = _try_append_ranked_candidate(
            ranked,
            candidates=candidates,
            state=state,
            rules=rules,
            required_item_type=required_item_type,
            debug_row=debug_row,
        )
        if not appended and required_item_type:
            appended = _try_append_ranked_candidate(
                ranked,
                candidates=candidates,
                state=state,
                rules=rules,
                required_item_type="",
                debug_row=debug_row,
            )
        if not appended:
            break

    return state if state.items else None


def _support_catalog_maps(routed_index: StyleBibleRoutedIndex) -> dict[str, Any]:
    plot_by_chapter: dict[str, list[str]] = defaultdict(list)
    for row in routed_index.support_catalog.get("plot_nodes", []):
        if not isinstance(row, dict):
            continue
        chapter_id = clean_text(row.get("chapter_id"))
        node_id = clean_text(row.get("node_id"))
        if chapter_id and node_id:
            plot_by_chapter[chapter_id].append(node_id)
    return {"plot_by_chapter": {key: value[:] for key, value in plot_by_chapter.items()}}


def _support_refs(state: BatchState, support_catalog_maps: dict[str, Any], rules: BatchingRules) -> dict[str, list[str]]:
    chapter_ids = sorted(state.chapter_ids, key=chapter_sort_key)[: rules.chapter_support_limit]
    plot_node_ids = list(state.plot_node_ids)
    if len(plot_node_ids) < rules.plot_node_support_limit:
        for chapter_id in chapter_ids:
            for node_id in support_catalog_maps.get("plot_by_chapter", {}).get(chapter_id, []):
                if node_id not in plot_node_ids:
                    plot_node_ids.append(node_id)
                if len(plot_node_ids) >= rules.plot_node_support_limit:
                    break
            if len(plot_node_ids) >= rules.plot_node_support_limit:
                break

    refs = {
        "chapter_ids": chapter_ids,
        "plot_node_ids": plot_node_ids[: rules.plot_node_support_limit],
        "entity_ids": sorted(state.entity_ids)[:6],
    }
    return {key: value for key, value in refs.items() if value}


def _finalize_batch(
    state: BatchState,
    *,
    batch_index: int,
    support_catalog_maps: dict[str, Any],
    rules: BatchingRules,
) -> StyleBibleBatch:
    ordered_items = sorted(
        state.items,
        key=lambda item: (
            -state.item_scores.get(item.item_id, 0.0),
            chapter_sort_key(item.primary_chapter_id),
            item.item_id,
        ),
    )
    redundancy_penalty = _clip_ratio(max(len(ordered_items) - len(state.chapter_ids), 0), max(len(ordered_items), 1))
    novelty_score = _clip_ratio(len(state.axis_ids) + len(state.entity_ids), max(len(ordered_items) + len(state.axis_focus), 1))
    batch_score = round(
        max(
            sum(state.item_scores.values()) / max(len(state.item_scores), 1)
            - (rules.redundancy_penalty * redundancy_penalty),
            0.0,
        ),
        4,
    )
    return StyleBibleBatch(
        batch_id=f"{state.bucket_id}__b{batch_index:02d}",
        bucket_id=state.bucket_id,
        label=state.label,
        cache_affinity_key=state.bucket_id,
        axis_focus=state.axis_focus,
        token_budget=state.token_budget,
        estimated_tokens=state.estimated_tokens,
        scene_count=state.scene_count,
        style_window_count=state.style_window_count,
        chapter_ids=sorted(state.chapter_ids, key=chapter_sort_key),
        item_ids=[item.item_id for item in ordered_items],
        items=[
            StyleBibleBatchItem(
                item_id=item.item_id,
                item_type=item.item_type,
                source_ref=item.source_ref,
                chapter_ids=item.chapter_ids,
                token_estimate=item.token_estimate,
                batch_score=state.item_scores.get(item.item_id, 0.0),
                axis_ids=item.axes,
                bucket_ids=[membership.bucket_id for membership in item.bucket_memberships],
            )
            for item in ordered_items
        ],
        support_refs=_support_refs(state, support_catalog_maps, rules),
        novelty_score=round(novelty_score, 4),
        redundancy_penalty=round(redundancy_penalty, 4),
        batch_score=batch_score,
    )


def _try_add_to_existing_batch(
    states: list[BatchState],
    item: StyleBibleRoutedItem,
    bucket_id: str,
    used_counts: dict[str, int],
    rules: BatchingRules,
    remaining_candidates: list[StyleBibleRoutedItem],
    debug_row: dict[str, Any] | None = None,
) -> bool:
    best_state: BatchState | None = None
    best_score = 0.0
    for state in states:
        block_reason = _fit_block_reason(item, state, rules, remaining_candidates)
        if block_reason is not None:
            _record_fit_rejection(debug_row, block_reason)
            continue
        score = _candidate_score(item, bucket_id, state, used_counts, rules, remaining_candidates)
        if score > best_score:
            best_state = state
            best_score = score
    if best_state is None or best_score <= 0:
        return False
    _append_item(best_state, item, best_score)
    return True


def _planner_rules_payload(rules: BatchingRules) -> dict[str, Any]:
    scene_token_quota, style_window_token_quota = _resolved_token_quotas(rules)
    return {
        "token_budget": rules.token_budget,
        "max_batches_per_bucket": _soft_batch_target(rules),
        "soft_batches_per_bucket": _soft_batch_target(rules),
        "absolute_max_batches_per_bucket": _absolute_batch_cap(rules),
        "max_items_per_batch": rules.max_items_per_batch,
        "max_scene_items_per_batch": rules.max_scene_items_per_batch,
        "max_style_window_items_per_batch": rules.max_style_window_items_per_batch,
        "min_bucket_confidence": rules.min_bucket_confidence,
        "scene_token_quota": scene_token_quota,
        "style_window_token_quota": style_window_token_quota,
        "scene_per_style_window_target": rules.scene_per_style_window_target,
        "chapter_continuity_bonus": rules.chapter_continuity_bonus,
        "entity_overlap_bonus": rules.entity_overlap_bonus,
        "plot_node_overlap_bonus": rules.plot_node_overlap_bonus,
        "capacity_efficiency_bonus": rules.capacity_efficiency_bonus,
        "style_window_scene_guard_penalty": rules.style_window_scene_guard_penalty,
        "source_path": rules.source_path,
    }


def plan_style_bible_batches_with_debug(
    routed_index: StyleBibleRoutedIndex | dict[str, Any],
    rules_config: str | Path | None = None,
) -> tuple[StyleBibleBatchPlan, dict[str, Any]]:
    if not isinstance(routed_index, StyleBibleRoutedIndex):
        routed_index = StyleBibleRoutedIndex.model_validate(routed_index)

    rules = _load_batching_rules(rules_config)
    support_catalog_maps = _support_catalog_maps(routed_index)
    used_counts: dict[str, int] = defaultdict(int)
    bucket_rows = _planning_bucket_rows(routed_index)
    contexts: dict[str, BucketPlanningContext] = {}
    bucket_debug_by_id: dict[str, dict[str, Any]] = {}
    planner_debug_report: dict[str, Any] = {
        "planner_version": "style-bible-batch-planner-debug-v2",
        "planning_strategy": "dynamic_fair_round_robin_v2",
        "routing_mode": routed_index.routing_mode,
        "rules": _planner_rules_payload(rules),
        "bucket_catalog_order": [row["bucket_id"] for row in bucket_rows],
        "planning_bucket_order": [],
        "planning_rounds": [],
        "bucket_debug": [],
        "bucket_execution_order": [],
        "notes": [],
    }

    for catalog_rank, bucket in enumerate(bucket_rows):
        bucket_id = bucket["bucket_id"]
        candidates = [
            item
            for item in routed_index.items
            if _membership_confidence(item, bucket_id) >= rules.min_bucket_confidence
            or (
                _is_orphanage_bucket(bucket_id)
                and any(membership.bucket_id == bucket_id for membership in item.bucket_memberships)
            )
        ]
        candidates.sort(
            key=lambda item: (
                -_membership_confidence(item, bucket_id),
                chapter_sort_key(item.primary_chapter_id),
                item.item_id,
            )
        )
        contexts[bucket_id] = BucketPlanningContext(
            bucket_id=bucket_id,
            label=bucket["label"],
            axis_focus=bucket["axis_focus"] or _bucket_axis_focus(routed_index, bucket_id),
            catalog_rank=catalog_rank,
            candidates=candidates,
            remaining=candidates[:],
        )
        bucket_debug_by_id[bucket_id] = _initial_bucket_debug_row(
            bucket_id=bucket_id,
            label=bucket["label"],
            axis_focus=bucket["axis_focus"] or _bucket_axis_focus(routed_index, bucket_id),
            candidates=candidates,
            rules=rules,
        )

    execution_order: list[str] = []
    execution_seen: set[str] = set()
    round_index = 0
    while True:
        eligible_contexts = [
            context
            for context in contexts.values()
            if context.remaining and not _absolute_batch_cap_reached(context.states, rules)
        ]
        if not eligible_contexts:
            break
        ordered_contexts = sorted(
            eligible_contexts,
            key=lambda context: _bucket_round_sort_key(
                context,
                used_counts=used_counts,
                rules=rules,
            ),
        )
        planner_debug_report["planning_rounds"].append(
            {
                "round_index": round_index + 1,
                "eligible_bucket_count": len(ordered_contexts),
                "bucket_order": [context.bucket_id for context in ordered_contexts],
                "hunger": [
                    _bucket_hunger_snapshot(
                        context,
                        used_counts=used_counts,
                        rules=rules,
                    )
                    for context in ordered_contexts
                ],
            }
        )
        planner_debug_report["planning_bucket_order"].extend(context.bucket_id for context in ordered_contexts)
        round_progress = False
        for context in ordered_contexts:
            debug_row = bucket_debug_by_id[context.bucket_id]
            debug_row["build_attempt_count"] = int(debug_row.get("build_attempt_count", 0) or 0) + 1
            state = _build_batch(
                context.remaining,
                context.bucket_id,
                context.label,
                context.axis_focus,
                used_counts,
                rules,
                debug_row=debug_row,
            )
            if state is None or not state.items:
                debug_row["stalled_build_count"] = int(debug_row.get("stalled_build_count", 0) or 0) + 1
                continue
            context.states.append(state)
            if context.bucket_id not in execution_seen:
                execution_seen.add(context.bucket_id)
                execution_order.append(context.bucket_id)
            round_progress = True
            batch_item_ids = {item.item_id for item in state.items}
            for item_id in batch_item_ids:
                used_counts[item_id] += 1
            context.remaining = [item for item in context.remaining if item.item_id not in batch_item_ids]
        if not round_progress:
            planner_debug_report["notes"].append("Planner stopped after a no-progress round.")
            break
        round_index += 1

    for context in contexts.values():
        debug_row = bucket_debug_by_id[context.bucket_id]
        remaining_snapshot = context.remaining[:]
        residual: list[StyleBibleRoutedItem] = []
        for item in remaining_snapshot:
            if _try_add_to_existing_batch(
                context.states,
                item,
                context.bucket_id,
                used_counts,
                rules,
                remaining_snapshot,
                debug_row=debug_row,
            ):
                used_counts[item.item_id] += 1
            else:
                residual.append(item)
        context.remaining = residual
        while context.remaining and not _absolute_batch_cap_reached(context.states, rules):
            debug_row["build_attempt_count"] = int(debug_row.get("build_attempt_count", 0) or 0) + 1
            spillover_state = _build_batch(
                context.remaining,
                context.bucket_id,
                context.label,
                context.axis_focus,
                used_counts,
                rules,
                debug_row=debug_row,
            )
            if spillover_state is None or not spillover_state.items:
                debug_row["stalled_build_count"] = int(debug_row.get("stalled_build_count", 0) or 0) + 1
                break
            context.states.append(spillover_state)
            if context.bucket_id not in execution_seen:
                execution_seen.add(context.bucket_id)
                execution_order.append(context.bucket_id)
            spillover_item_ids = {item.item_id for item in spillover_state.items}
            for item_id in spillover_item_ids:
                used_counts[item_id] += 1
            context.remaining = [item for item in context.remaining if item.item_id not in spillover_item_ids]

    batches: list[StyleBibleBatch] = []
    bucket_summaries: list[StyleBibleBucketBatchSummary] = []
    for bucket in bucket_rows:
        bucket_id = bucket["bucket_id"]
        context = contexts[bucket_id]
        states = context.states
        finalized_batches = [
            _finalize_batch(
                state,
                batch_index=index + 1,
                support_catalog_maps=support_catalog_maps,
                rules=rules,
            )
            for index, state in enumerate(states)
        ]
        for batch in finalized_batches:
            batch.cache_affinity_key = batch.bucket_id
        batches.extend(finalized_batches)

        candidate_items = context.candidates
        candidate_scene_refs = {item.item_id for item in candidate_items if item.item_type == "scene"}
        candidate_style_refs = {item.item_id for item in candidate_items if item.item_type == "style_window"}
        candidate_chapter_refs = {
            chapter_id
            for item in candidate_items
            for chapter_id in item.chapter_ids
            if chapter_id
        }
        batched_scene_refs = {
            item.item_id
            for batch in finalized_batches
            for item in batch.items
            if item.item_type == "scene"
        }
        batched_style_refs = {
            item.item_id
            for batch in finalized_batches
            for item in batch.items
            if item.item_type == "style_window"
        }
        batched_chapter_refs = {
            chapter_id
            for batch in finalized_batches
            for chapter_id in batch.chapter_ids
            if chapter_id
        }
        bucket_summaries.append(
            StyleBibleBucketBatchSummary(
                bucket_id=bucket_id,
                label=context.label,
                batch_ids=[batch.batch_id for batch in finalized_batches],
                axis_ids=context.axis_focus,
                scene_counts=StyleBibleCoverageStageCounts(
                    total=len(candidate_scene_refs),
                    routed=len(candidate_scene_refs),
                    batched=len(batched_scene_refs),
                ),
                style_window_counts=StyleBibleCoverageStageCounts(
                    total=len(candidate_style_refs),
                    routed=len(candidate_style_refs),
                    batched=len(batched_style_refs),
                ),
                chapter_counts=StyleBibleCoverageStageCounts(
                    total=len(candidate_chapter_refs),
                    routed=len(candidate_chapter_refs),
                    batched=len(batched_chapter_refs),
                ),
                selected_item_count=len({item.item_id for item in candidate_items}),
            )
        )

    execution_rank = {bucket_id: index for index, bucket_id in enumerate(execution_order)}
    batches = sorted(
        batches,
        key=lambda batch: (
            execution_rank.get(batch.bucket_id, len(execution_rank)),
            batch.batch_id,
        ),
    )
    for planner_rank, batch in enumerate(batches, start=1):
        batch.planner_rank = planner_rank

    all_item_ids = {item.item_id for item in routed_index.items}
    batched_item_ids = {item.item_id for batch in batches for item in batch.items}
    total_scene_count = len([item for item in routed_index.items if item.item_type == "scene"])
    total_style_window_count = len([item for item in routed_index.items if item.item_type == "style_window"])
    batched_scene_count = len([item_id for item_id in batched_item_ids if item_id.startswith("scene:")])
    batched_style_window_count = len(batched_item_ids) - batched_scene_count
    unbatched_item_id_set = all_item_ids - batched_item_ids
    unbatched_item_ids = sorted(unbatched_item_id_set)
    scene_token_quota, style_window_token_quota = _resolved_token_quotas(rules)

    for bucket in bucket_rows:
        context = contexts[bucket["bucket_id"]]
        debug_row = bucket_debug_by_id[context.bucket_id]
        debug_row["created_batch_count"] = len(context.states)
        debug_row["created_batches"] = len(context.states)
        debug_row["spillover_batch_count"] = _spillover_batch_count(len(context.states), rules)
        debug_row["spillover_batches"] = debug_row["spillover_batch_count"]
        debug_row["remaining_candidate_count"] = len(context.remaining)
        debug_row["remaining_scene_count"] = sum(1 for item in context.remaining if item.item_type == "scene")
        debug_row["remaining_style_window_count"] = sum(1 for item in context.remaining if item.item_type == "style_window")
        debug_row["remaining_token_sum"] = sum(item.token_estimate for item in context.remaining)
        debug_row["remaining_global_unbatched_count"] = sum(
            1 for item in context.remaining if item.item_id in unbatched_item_id_set
        )
        debug_row["remaining_global_unbatched_scene_count"] = sum(
            1 for item in context.remaining if item.item_id in unbatched_item_id_set and item.item_type == "scene"
        )
        debug_row["remaining_global_unbatched_style_window_count"] = sum(
            1
            for item in context.remaining
            if item.item_id in unbatched_item_id_set and item.item_type == "style_window"
        )
        debug_row["hard_cap_blocked_count"] = len(context.remaining) if _absolute_batch_cap_reached(context.states, rules) else 0
        debug_row["dropped_by_hard_cap"] = debug_row["hard_cap_blocked_count"]
        fit_rejections = debug_row.get("fit_rejections", {})
        debug_row["dropped_by_token_cap"] = int(fit_rejections.get("token_budget", 0) or 0)
        debug_row["dropped_by_scene_cap"] = int(fit_rejections.get("max_scene_items", 0) or 0) + int(
            fit_rejections.get("scene_quota_reserved", 0) or 0
        )
        debug_row["dropped_by_style_cap"] = int(fit_rejections.get("max_style_window_items", 0) or 0) + int(
            fit_rejections.get("style_window_quota_reserved", 0) or 0
        )

    planner_debug_report["bucket_execution_order"] = execution_order[:]
    planner_debug_report["bucket_debug"] = [bucket_debug_by_id[row["bucket_id"]] for row in bucket_rows]
    planner_debug_report["summary"] = {
        "batch_count": len(batches),
        "bucket_count_with_batches": len([summary for summary in bucket_summaries if summary.batch_ids]),
        "total_item_count": len(all_item_ids),
        "batched_item_count": len(batched_item_ids),
        "unbatched_item_count": len(unbatched_item_ids),
        "scene_item_count": total_scene_count,
        "style_window_item_count": total_style_window_count,
        "batched_scene_ratio": _clip_ratio(batched_scene_count, total_scene_count),
        "batched_style_window_ratio": _clip_ratio(batched_style_window_count, total_style_window_count),
        "scene_token_quota": scene_token_quota,
        "style_window_token_quota": style_window_token_quota,
    }

    plan = StyleBibleBatchPlan(
        plan_version=STYLE_BIBLE_BATCH_PLAN_VERSION,
        scope_hint=routed_index.scope_hint,
        story_node_scope=routed_index.story_node_scope,
        routing_mode=routed_index.routing_mode,
        batching_mode=BATCHING_MODE_BUCKET_AFFINITY_V3,
        bucket_execution_order=execution_order[:],
        source_routed_index_file="",
        rules_config=rules.source_path,
        coverage_summary={
            "total_item_count": len(all_item_ids),
            "batched_item_count": len(batched_item_ids),
            "unbatched_item_count": len(unbatched_item_ids),
            "batch_count": len(batches),
            "bucket_count_with_batches": len([summary for summary in bucket_summaries if summary.batch_ids]),
            "scene_item_count": total_scene_count,
            "style_window_item_count": total_style_window_count,
            "batched_scene_ratio": _clip_ratio(batched_scene_count, total_scene_count),
            "batched_style_window_ratio": _clip_ratio(batched_style_window_count, total_style_window_count),
            "token_budget": rules.token_budget,
            "scene_token_quota": scene_token_quota,
            "style_window_token_quota": style_window_token_quota,
            "soft_batches_per_bucket": _soft_batch_target(rules),
            "absolute_max_batches_per_bucket": _absolute_batch_cap(rules),
            "planning_strategy": planner_debug_report["planning_strategy"],
        },
        bucket_summaries=bucket_summaries,
        batches=batches,
        unbatched_item_ids=unbatched_item_ids,
    )
    return plan, planner_debug_report


def plan_style_bible_batches(
    routed_index: StyleBibleRoutedIndex | dict[str, Any],
    rules_config: str | Path | None = None,
) -> StyleBibleBatchPlan:
    plan, _ = plan_style_bible_batches_with_debug(routed_index, rules_config=rules_config)
    return plan


def build_style_bible_batch_plan(
    routed_index_path: str | Path,
    output_dir: str | Path,
    *,
    rules_config: str | Path | None = None,
) -> StyleBibleBatchPlan:
    payload = read_json(routed_index_path)
    routed_index = StyleBibleRoutedIndex.model_validate(payload)
    output_dir_path = ensure_dir(output_dir)
    plan, planner_debug_report = plan_style_bible_batches_with_debug(routed_index, rules_config=rules_config)
    plan.source_routed_index_file = str(Path(routed_index_path).resolve())
    output_path = output_dir_path / BATCH_PLAN_FILE
    write_json(output_path, plan.model_dump(mode="json"))
    planner_debug_report["source_routed_index_file"] = plan.source_routed_index_file
    planner_debug_report["batch_plan_file"] = str(output_path.resolve())
    write_json(output_dir_path / PLANNER_DEBUG_REPORT_FILE, planner_debug_report)
    return plan
