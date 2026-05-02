from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SECTION_TARGETS_FILE = "style_bible_section_targets.toml"
DEFAULT_FULL_EVAL_RULES_FILE = "style_bible_eval_rules.toml"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config"


def default_section_targets_path() -> Path:
    return (_config_dir() / DEFAULT_SECTION_TARGETS_FILE).resolve()


def _resolve_config_path(raw_value: str | Path | None, *, base_dir: Path) -> Path:
    if raw_value is None:
        return (base_dir / DEFAULT_FULL_EVAL_RULES_FILE).resolve()
    text = _clean_text(raw_value)
    if not text:
        return (base_dir / DEFAULT_FULL_EVAL_RULES_FILE).resolve()
    path = Path(text)
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _cleaned_unique(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        rows.append(cleaned)
    return tuple(rows)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean_text(value).casefold()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _load_section_requirements(rules_path: Path) -> tuple[tuple[str, ...], dict[str, int]]:
    if not rules_path.exists():
        raise FileNotFoundError(f"Section completeness rules config not found: {rules_path}")
    payload = tomllib.loads(rules_path.read_text(encoding="utf-8-sig"))
    required_scalars = _cleaned_unique(((payload.get("required_scalars") or {}).get("fields", [])))
    minimums_payload = payload.get("minimums", {})
    minimums = {
        str(key): int(value or 0)
        for key, value in minimums_payload.items()
        if _clean_text(key)
    }
    return required_scalars, minimums


@dataclass(frozen=True, slots=True)
class SectionSlotSpec:
    slot_id: str
    label: str = ""
    cue: str = ""
    canonical_description: str = ""
    downstream_shape: str = ""
    fresh_evidence_required: bool = False

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "cue": self.cue,
            "canonical_description": self.canonical_description,
            "downstream_shape": self.downstream_shape,
            "fresh_evidence_required": bool(self.fresh_evidence_required),
        }


@dataclass(frozen=True, slots=True)
class SectionPathTarget:
    path: str
    target_count: int = 0
    max_new_rows: int = 0
    retrieval_top_k: int = 0
    bucket_allowlist: tuple[str, ...] = ()
    downstream_shape: str = ""
    prompt_hints: tuple[str, ...] = ()
    dedupe_threshold: float = 0.92
    slot_match_threshold: float = 0.8
    soft_slot_match_floor: float = 0.72
    max_gray_keep: int = 1
    enabled: bool = True
    slot_specs: tuple[SectionSlotSpec, ...] = ()

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "target_count": int(self.target_count),
            "max_new_rows": int(self.max_new_rows),
            "retrieval_top_k": int(self.retrieval_top_k),
            "bucket_allowlist": list(self.bucket_allowlist),
            "downstream_shape": self.downstream_shape,
            "prompt_hints": list(self.prompt_hints),
            "dedupe_threshold": float(self.dedupe_threshold),
            "slot_match_threshold": float(self.slot_match_threshold),
            "soft_slot_match_floor": float(self.soft_slot_match_floor),
            "max_gray_keep": int(self.max_gray_keep),
            "enabled": bool(self.enabled),
            "slot_specs": [
                slot.as_prompt_payload()
                for slot in self.slot_specs
            ],
        }


@dataclass(frozen=True, slots=True)
class BucketSectionTargets:
    bucket_id: str
    preferred_paths: tuple[str, ...] = ()
    scalar_paths: tuple[str, ...] = ()
    prompt_hints: tuple[str, ...] = ()
    repair_priority: int = 100

    @property
    def repair_paths(self) -> tuple[str, ...]:
        rows: list[str] = []
        seen: set[str] = set()
        for value in (*self.scalar_paths, *self.preferred_paths):
            cleaned = _clean_text(value)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            rows.append(cleaned)
        return tuple(rows)

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "preferred_paths": list(self.preferred_paths),
            "scalar_paths": list(self.scalar_paths),
            "repair_paths": list(self.repair_paths),
            "prompt_hints": list(self.prompt_hints),
            "repair_priority": int(self.repair_priority),
        }


@dataclass(frozen=True, slots=True)
class StyleBibleSectionTargets:
    source_path: Path
    section_rules_path: Path
    repair_max_rounds: int
    max_buckets_per_round: int
    max_paths_per_bucket: int
    densify_enabled: bool
    densify_max_rounds: int
    densify_max_paths_per_round: int
    required_scalars: tuple[str, ...]
    minimums: dict[str, int]
    default_bucket_targets: BucketSectionTargets
    bucket_targets: dict[str, BucketSectionTargets]
    path_targets: dict[str, SectionPathTarget]

    def targets_for_bucket(self, bucket_id: str) -> BucketSectionTargets:
        normalized_bucket_id = _clean_text(bucket_id)
        if normalized_bucket_id in self.bucket_targets:
            return self.bucket_targets[normalized_bucket_id]
        return BucketSectionTargets(
            bucket_id=normalized_bucket_id,
            preferred_paths=self.default_bucket_targets.preferred_paths,
            scalar_paths=self.default_bucket_targets.scalar_paths,
            prompt_hints=self.default_bucket_targets.prompt_hints,
            repair_priority=self.default_bucket_targets.repair_priority,
        )

    def densify_target_for_path(self, path: str) -> SectionPathTarget | None:
        return self.path_targets.get(_clean_text(path))


def _load_slot_spec(payload: Any, *, parent_downstream_shape: str = "") -> SectionSlotSpec | None:
    row = payload if isinstance(payload, dict) else {}
    slot_id = _clean_text(row.get("slot_id"))
    if not slot_id:
        return None
    cue = _clean_text(row.get("cue"))
    canonical_description = _clean_text(row.get("canonical_description"))
    downstream_shape = _clean_text(row.get("downstream_shape")) or _clean_text(parent_downstream_shape)
    if not cue or not canonical_description or not downstream_shape:
        raise ValueError(
            "Section slot specs must define slot_id, cue, canonical_description, and downstream_shape."
        )
    return SectionSlotSpec(
        slot_id=slot_id,
        label=_clean_text(row.get("label")),
        cue=cue,
        canonical_description=canonical_description,
        downstream_shape=downstream_shape,
        fresh_evidence_required=_as_bool(row.get("fresh_evidence_required"), default=False),
    )


def _load_bucket_targets(bucket_id: str, payload: Any) -> BucketSectionTargets:
    row = payload if isinstance(payload, dict) else {}
    return BucketSectionTargets(
        bucket_id=_clean_text(bucket_id),
        preferred_paths=_cleaned_unique(row.get("preferred_paths", [])),
        scalar_paths=_cleaned_unique(row.get("scalar_paths", [])),
        prompt_hints=_cleaned_unique(row.get("prompt_hints", [])),
        repair_priority=int(row.get("repair_priority", 100) or 100),
    )


def _load_path_target(
    payload: Any,
    *,
    minimums: dict[str, int],
    densify_defaults: dict[str, Any],
) -> SectionPathTarget | None:
    row = payload if isinstance(payload, dict) else {}
    path = _clean_text(row.get("path"))
    if not path:
        return None
    downstream_shape = _clean_text(row.get("downstream_shape"))
    if not downstream_shape:
        raise ValueError(f"Section path target must define downstream_shape: {path}")

    raw_slot_specs = row.get("slot_specs", [])
    slot_payloads = raw_slot_specs if isinstance(raw_slot_specs, list) else []
    slot_specs = tuple(
        slot_spec
        for slot_spec in (
            _load_slot_spec(slot_payload, parent_downstream_shape=downstream_shape)
            for slot_payload in slot_payloads
        )
        if slot_spec is not None
    )
    fallback_target_count = minimums.get(path, 0) or len(slot_specs) or 1
    target_count = _as_int(row.get("target_count"), fallback_target_count)
    if target_count <= 0:
        target_count = fallback_target_count
    max_new_rows = _as_int(row.get("max_new_rows"), target_count)
    if max_new_rows <= 0:
        max_new_rows = target_count
    retrieval_top_k = _as_int(row.get("retrieval_top_k"), _as_int(densify_defaults.get("retrieval_top_k"), 12))
    if retrieval_top_k <= 0:
        retrieval_top_k = max(len(slot_specs), 1)
    slot_match_threshold = _as_float(
        row.get("slot_match_threshold"),
        _as_float(densify_defaults.get("slot_match_threshold"), 0.8),
    )
    soft_slot_match_floor = _as_float(
        row.get("soft_slot_match_floor"),
        _as_float(densify_defaults.get("soft_slot_match_floor"), max(slot_match_threshold - 0.08, 0.55)),
    )
    if soft_slot_match_floor <= 0:
        soft_slot_match_floor = max(slot_match_threshold - 0.08, 0.55)
    soft_slot_match_floor = min(float(slot_match_threshold), float(soft_slot_match_floor))

    return SectionPathTarget(
        path=path,
        target_count=target_count,
        max_new_rows=max_new_rows,
        retrieval_top_k=retrieval_top_k,
        bucket_allowlist=_cleaned_unique(row.get("bucket_allowlist", [])),
        downstream_shape=downstream_shape,
        prompt_hints=_cleaned_unique(row.get("prompt_hints", [])),
        dedupe_threshold=_as_float(row.get("dedupe_threshold"), _as_float(densify_defaults.get("dedupe_threshold"), 0.92)),
        slot_match_threshold=slot_match_threshold,
        soft_slot_match_floor=soft_slot_match_floor,
        max_gray_keep=max(_as_int(row.get("max_gray_keep"), _as_int(densify_defaults.get("max_gray_keep"), 1)), 0),
        enabled=_as_bool(row.get("enabled", True), default=True),
        slot_specs=slot_specs,
    )


def load_style_bible_section_targets(path: str | Path | None = None) -> StyleBibleSectionTargets:
    target = Path(path).resolve() if path else default_section_targets_path()
    if not target.exists():
        raise FileNotFoundError(f"Section targets config not found: {target}")

    payload = tomllib.loads(target.read_text(encoding="utf-8-sig"))
    repair = payload.get("repair", {})
    densify = payload.get("densify", {})
    rules_path = _resolve_config_path(
        repair.get("section_rules_config"),
        base_dir=target.parent,
    )
    required_scalars, minimums = _load_section_requirements(rules_path)

    buckets_payload = payload.get("buckets", {})
    default_bucket_targets = _load_bucket_targets("default", buckets_payload.get("default", {}))
    bucket_targets = {
        _clean_text(bucket_id): _load_bucket_targets(bucket_id, row)
        for bucket_id, row in buckets_payload.items()
        if _clean_text(bucket_id) and _clean_text(bucket_id) != "default"
    }

    raw_path_targets = payload.get("path_targets", [])
    path_targets: dict[str, SectionPathTarget] = {}
    if isinstance(raw_path_targets, list):
        for row in raw_path_targets:
            path_target = _load_path_target(
                row,
                minimums=minimums,
                densify_defaults=densify if isinstance(densify, dict) else {},
            )
            if path_target is None:
                continue
            path_targets[path_target.path] = path_target

    return StyleBibleSectionTargets(
        source_path=target,
        section_rules_path=rules_path,
        repair_max_rounds=max(int(repair.get("max_rounds", 1) or 1), 0),
        max_buckets_per_round=max(int(repair.get("max_buckets_per_round", 6) or 6), 1),
        max_paths_per_bucket=max(int(repair.get("max_paths_per_bucket", 8) or 8), 1),
        densify_enabled=_as_bool(densify.get("enabled", True), default=True),
        densify_max_rounds=max(_as_int(densify.get("max_rounds"), 1), 0),
        densify_max_paths_per_round=max(_as_int(densify.get("max_paths_per_round"), 3), 1),
        required_scalars=required_scalars,
        minimums=minimums,
        default_bucket_targets=default_bucket_targets,
        bucket_targets=bucket_targets,
        path_targets=path_targets,
    )


__all__ = [
    "BucketSectionTargets",
    "SectionPathTarget",
    "SectionSlotSpec",
    "StyleBibleSectionTargets",
    "default_section_targets_path",
    "load_style_bible_section_targets",
]
