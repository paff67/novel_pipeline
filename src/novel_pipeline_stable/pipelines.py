from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient, StructuredGenerationError
from novel_pipeline_stable.config import StableProjectConfig
from novel_pipeline_stable.io_utils import ensure_dir, iter_json_files, iter_text_files, read_json, write_json
from novel_pipeline_stable.models import (
    EntityRecord,
    EventRecord,
    FactExtractionResult,
    FactRecord,
    PowerSystemNote,
    RelationshipChange,
    SceneStyleMarker,
    StyleWindowSignalResult,
)
from novel_pipeline_stable.monitoring import RunTracker
from novel_pipeline_stable.prompting import load_prompt
from novel_pipeline_stable.splitter import load_chapter_document, split_chapter_into_scenes
from novel_pipeline_stable.text_normalization import normalize_for_extraction


METADATA_JSON_NAMES = {"manifest.json", "failures.json", "run_status.json"}


def _style_anchor(text: Any, *, limit: int, tail: bool = False) -> str:
    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) <= limit:
        return collapsed
    if tail:
        return collapsed[-limit:]
    return collapsed[:limit]


@dataclass(slots=True)
class FactExtractionRunResult:
    record: dict[str, Any]
    model_name: str
    usage_metadata: dict[str, Any]
    request_metrics: dict[str, Any]
    extraction_strategy: str


class FactExtractionPrimaryPassResult(BaseModel):
    chapter_id: str = ""
    scene_id: str = ""
    scene_summary: str = ""
    entities: list[EntityRecord] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_non_empty_primary_content(self) -> "FactExtractionPrimaryPassResult":
        has_scene_summary = bool(self.scene_summary.strip())
        if has_scene_summary or self.entities or self.events or self.facts:
            return self
        raise ValueError("Fact extraction primary pass result cannot be empty.")


class FactExtractionSupplementPassResult(BaseModel):
    chapter_id: str = ""
    scene_id: str = ""
    relationship_changes: list[RelationshipChange] = Field(default_factory=list)
    power_system_notes: list[PowerSystemNote] = Field(default_factory=list)
    style_markers: list[SceneStyleMarker] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


def _load_existing_rows(path: Path, *, resume: bool) -> list[dict]:
    if not resume or not path.exists():
        return []
    if path.stat().st_size == 0:
        _backup_invalid_tracking_file(path, reason="empty")
        return []
    try:
        payload = read_json(path)
    except json.JSONDecodeError:
        _backup_invalid_tracking_file(path, reason="invalid_json")
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _backup_invalid_tracking_file(path: Path, *, reason: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.stem}.{reason}.{timestamp}{path.suffix}")
    path.replace(backup_path)
    return backup_path


def _write_tracking_files(
    manifest_path: Path,
    manifest_by_key: dict[str, dict],
    failures_path: Path,
    failures_by_key: dict[str, dict],
    *,
    manifest_sort_key: str,
    failure_sort_key: str,
) -> None:
    manifest_rows = sorted(manifest_by_key.values(), key=lambda row: row.get(manifest_sort_key, ""))
    failure_rows = sorted(failures_by_key.values(), key=lambda row: row.get(failure_sort_key, ""))
    write_json(manifest_path, manifest_rows)
    write_json(failures_path, failure_rows)


def _serialize_payload(config: StableProjectConfig, payload: dict[str, Any]) -> str:
    if config.stability.compact_json:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_fact_payload(config: StableProjectConfig, scene: dict) -> dict:
    normalized_source = normalize_for_extraction(scene["text"])
    payload = {
        "chapter_id": scene["chapter_id"],
        "chapter_title": scene["chapter_title"],
        "scene_id": scene["scene_id"],
        "scene_index": scene["scene_index"],
        "source_text": normalized_source.text,
    }
    if normalized_source.applied or not config.stability.omit_empty_normalization_applied:
        payload["normalization_applied"] = [
            {
                "rule_id": item.rule_id,
                "replacement": item.replacement,
                "count": item.count,
            }
            for item in normalized_source.applied
        ]
    return payload


def _build_style_payload(config: StableProjectConfig, window: list) -> tuple[dict, list[dict]]:
    normalized_chapters = []
    scene_locator: list[dict[str, Any]] = []
    applied_rules: list[dict] = []
    for chapter in window:
        normalized_text = normalize_for_extraction(chapter.text)
        chapter_payload = {
            "chapter_id": chapter.chapter_id,
            "title": chapter.title,
            "source_text": normalized_text.text,
        }
        if normalized_text.applied or not config.stability.omit_empty_normalization_applied:
            chapter_payload["normalization_applied"] = [
                {
                    "rule_id": item.rule_id,
                    "replacement": item.replacement,
                    "count": item.count,
                }
                for item in normalized_text.applied
            ]
        normalized_chapters.append(chapter_payload)
        applied_rules.extend(chapter_payload.get("normalization_applied", []))
        normalized_chapter = chapter.model_copy(update={"text": normalized_text.text})
        for scene in split_chapter_into_scenes(normalized_chapter, config.as_project_config()):
            scene_locator.append(
                {
                    "source_ref": f"scene:{scene.scene_id}",
                    "chapter_id": scene.chapter_id,
                    "scene_id": scene.scene_id,
                    "start_anchor": _style_anchor(scene.text, limit=48),
                    "end_anchor": _style_anchor(scene.text, limit=48, tail=True),
                }
            )
    payload = {
        "window_id": f"{window[0].chapter_id}_{window[-1].chapter_id}",
        "chapter_ids": [chapter.chapter_id for chapter in window],
        "chapters": normalized_chapters,
        "scene_locator": scene_locator,
    }
    return payload, applied_rules


def _build_primary_fact_instruction(system_prompt: str) -> str:
    return (
        f"{system_prompt}\n\n"
        "本次是 facts 两段式抽取的第一段。\n"
        "只输出这些字段：`chapter_id`、`scene_id`、`scene_summary`、`entities`、`events`、`facts`。\n"
        "不要输出 `relationship_changes`、`power_system_notes`、`style_markers`、`open_questions`。"
    )


def _build_supplement_fact_instruction(system_prompt: str, *, include_primary_context: bool) -> str:
    instruction = (
        f"{system_prompt}\n\n"
        "本次是 facts 两段式抽取的第二段。\n"
        "只输出这些字段：`chapter_id`、`scene_id`、`relationship_changes`、`power_system_notes`、`style_markers`、`open_questions`。\n"
        "不要重复输出 `scene_summary`、`entities`、`events`、`facts`。"
    )
    if include_primary_context:
        instruction += "\n如果 payload 里带有 `primary_pass_context`，它只是辅助线索；最终判断仍然以 `source_text` 为准。"
    return instruction


def _should_use_facts_two_pass(
    config: StableProjectConfig,
    scene: dict,
    system_prompt: str,
    payload: dict[str, Any],
) -> tuple[bool, list[str]]:
    if not config.stability.facts_two_pass_enabled:
        return False, []

    reasons: list[str] = []
    scene_chars = int(scene.get("char_count") or len(str(scene.get("text") or payload.get("source_text", ""))))
    request_chars = len(system_prompt) + len(_serialize_payload(config, payload))

    scene_threshold = int(config.stability.facts_two_pass_scene_char_threshold)
    if scene_threshold > 0 and scene_chars >= scene_threshold:
        reasons.append(f"scene_chars={scene_chars}>={scene_threshold}")

    request_threshold = int(config.stability.facts_two_pass_request_char_threshold)
    if request_threshold > 0 and request_chars >= request_threshold:
        reasons.append(f"request_chars={request_chars}>={request_threshold}")

    return bool(reasons), reasons


def _extract_request_metrics(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, StructuredGenerationError):
        return exc.request_metrics
    return {}


def _hydrate_fact_record(record: dict[str, Any], *, payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(record)
    hydrated["chapter_id"] = payload["chapter_id"]
    hydrated["scene_id"] = payload["scene_id"]
    return hydrated


def _fact_content_stats(record: dict[str, Any]) -> dict[str, int]:
    def _list_count(key: str) -> int:
        value = record.get(key)
        return len(value) if isinstance(value, list) else 0

    summary = record.get("scene_summary", "")
    return {
        "scene_summary_chars": len(summary.strip()) if isinstance(summary, str) else 0,
        "entities": _list_count("entities"),
        "events": _list_count("events"),
        "facts": _list_count("facts"),
        "relationship_changes": _list_count("relationship_changes"),
        "power_system_notes": _list_count("power_system_notes"),
        "style_markers": _list_count("style_markers"),
        "open_questions": _list_count("open_questions"),
    }


def _has_fact_content(stats: dict[str, int]) -> bool:
    return any(value > 0 for value in stats.values())


def _ensure_fact_record_has_content(
    *,
    request_key: str,
    strategy: str,
    stage: str,
    request_metrics: dict[str, Any],
    record: dict[str, Any],
) -> None:
    stats = _fact_content_stats(record)
    if _has_fact_content(stats):
        return

    failure_metrics = dict(request_metrics)
    failure_metrics.setdefault("request_key", request_key)
    failure_metrics["strategy"] = strategy
    failure_metrics["completed"] = False
    failure_metrics["content_validation_failed"] = True
    failure_metrics["content_validation_stage"] = stage
    failure_metrics["content_stats"] = stats
    raise StructuredGenerationError(
        "Fact extraction produced an empty shell after structured parsing/repair.",
        request_metrics=failure_metrics,
    )


def _hydrate_style_record(record: dict[str, Any], *, payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(record)
    hydrated["window_id"] = payload["window_id"]
    hydrated["chapter_ids"] = list(payload["chapter_ids"])
    return hydrated


def _style_content_stats(record: dict[str, Any]) -> dict[str, int]:
    def _list_count(key: str) -> int:
        value = record.get(key)
        return len(value) if isinstance(value, list) else 0

    scalar_contracts = record.get("scalar_contracts", {})
    scalar_contract_count = 0
    if isinstance(scalar_contracts, dict):
        scalar_contract_count = sum(
            1
            for key in ("perspective", "distance", "temporality", "inner_monologue_mode")
            if str(scalar_contracts.get(key, "")).strip() not in {"", "unspecified"}
        )

    signal_fields = {
        "surface_markers": _list_count("surface_markers"),
        "narrative_engine_rules": _list_count("narrative_engine_rules"),
        "pacing_rules": _list_count("pacing_rules"),
        "plot_node_logic_rules": _list_count("plot_node_logic_rules"),
        "description_rules": _list_count("description_rules"),
        "dialogue_rules": _list_count("dialogue_rules"),
        "characterization_rules": _list_count("characterization_rules"),
        "sensory_rules": _list_count("sensory_rules"),
        "humor_rules": _list_count("humor_rules"),
        "satire_rules": _list_count("satire_rules"),
        "nonstandard_xianxia_rules": _list_count("nonstandard_xianxia_rules"),
        "narrator_voice_rules": _list_count("narrator_voice_rules"),
        "register_mix_rules": _list_count("register_mix_rules"),
        "negative_pitfalls": _list_count("negative_pitfalls"),
        "rag_candidates": _list_count("rag_candidates"),
        "worldbook_candidates": _list_count("worldbook_candidates"),
        "routing_hints": _list_count("routing_hints"),
        "axis_hints": _list_count("axis_hints"),
        "bucket_hints": _list_count("bucket_hints"),
        "evidence_index": _list_count("evidence_index"),
    }
    signal_fields["scalar_contracts"] = scalar_contract_count
    signal_fields["signal_total"] = sum(value for key, value in signal_fields.items() if key != "evidence_index")
    return signal_fields


def _has_style_content(stats: dict[str, int]) -> bool:
    return int(stats.get("signal_total", 0) or 0) > 0


def _ensure_style_record_has_content(
    *,
    request_key: str,
    request_metrics: dict[str, Any],
    record: dict[str, Any],
) -> None:
    stats = _style_content_stats(record)
    if _has_style_content(stats):
        return

    failure_metrics = dict(request_metrics)
    failure_metrics.setdefault("request_key", request_key)
    failure_metrics["completed"] = False
    failure_metrics["content_validation_failed"] = True
    failure_metrics["content_validation_stage"] = "style"
    failure_metrics["content_stats"] = stats
    raise StructuredGenerationError(
        "Style extraction produced an empty shell after structured parsing/repair.",
        request_metrics=failure_metrics,
    )


def _metric_float(metrics: dict[str, Any], key: str) -> float:
    try:
        return float(metrics.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _metric_int(metrics: dict[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _build_primary_pass_context(primary: FactExtractionPrimaryPassResult) -> dict[str, Any]:
    return {
        "scene_summary": primary.scene_summary,
        "entities": [
            {
                "name": entity.name,
                "entity_type": entity.entity_type,
                "aliases": entity.aliases,
                "role_in_scene": entity.role_in_scene,
            }
            for entity in primary.entities
        ],
        "events": [
            {
                "name": event.name,
                "summary": event.summary,
                "event_type": event.event_type,
                "participants": event.participants,
                "location": event.location,
                "outcomes": event.outcomes,
            }
            for event in primary.events
        ],
        "facts": [
            {
                "subject": fact.subject,
                "predicate": fact.predicate,
                "object": fact.object,
                "fact_type": fact.fact_type,
                "confidence": fact.confidence,
            }
            for fact in primary.facts
        ],
    }


def _build_supplement_fact_payload(
    config: StableProjectConfig,
    payload: dict[str, Any],
    primary: FactExtractionPrimaryPassResult,
) -> dict[str, Any]:
    supplement_payload = dict(payload)
    if config.stability.facts_two_pass_include_primary_context:
        supplement_payload["primary_pass_context"] = _build_primary_pass_context(primary)
    return supplement_payload


def _combine_two_pass_success_metrics(
    *,
    model_name: str,
    trigger_reasons: list[str],
    primary_metrics: dict[str, Any],
    supplement_metrics: dict[str, Any],
    initial_full_pass_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_pass_metrics = initial_full_pass_metrics or {}
    total_elapsed = (
        _metric_float(full_pass_metrics, "total_elapsed_seconds")
        + _metric_float(primary_metrics, "total_elapsed_seconds")
        + _metric_float(supplement_metrics, "total_elapsed_seconds")
    )
    response_chars = (
        _metric_int(full_pass_metrics, "response_chars")
        + _metric_int(primary_metrics, "response_chars")
        + _metric_int(supplement_metrics, "response_chars")
    )
    request_chars = (
        _metric_int(full_pass_metrics, "request_chars")
        + _metric_int(primary_metrics, "request_chars")
        + _metric_int(supplement_metrics, "request_chars")
    )

    metrics = {
        "strategy": "two_pass",
        "model": model_name,
        "completed": True,
        "trigger_reasons": trigger_reasons,
        "total_elapsed_seconds": round(total_elapsed, 3),
        "response_chars": response_chars,
        "request_chars": request_chars,
        "repair_used": bool(full_pass_metrics.get("repair_used") or primary_metrics.get("repair_used") or supplement_metrics.get("repair_used")),
        "usage_metadata": {
            "primary": primary_metrics.get("usage_metadata", {}),
            "supplement": supplement_metrics.get("usage_metadata", {}),
        },
        "requests": {
            "primary": primary_metrics,
            "supplement": supplement_metrics,
        },
    }
    if full_pass_metrics:
        metrics["initial_full_pass"] = full_pass_metrics
    return metrics


def _raise_two_pass_failure(
    *,
    request_key: str,
    model_name: str,
    trigger_reasons: list[str],
    phase: str,
    exc: Exception,
    primary_metrics: dict[str, Any] | None = None,
    supplement_metrics: dict[str, Any] | None = None,
    initial_full_pass_metrics: dict[str, Any] | None = None,
) -> None:
    current_metrics = _extract_request_metrics(exc)
    full_pass_metrics = initial_full_pass_metrics or {}
    primary_request_metrics = primary_metrics or {}
    supplement_request_metrics = supplement_metrics or {}

    requests: dict[str, Any] = {}
    if primary_request_metrics:
        requests["primary"] = primary_request_metrics
    if supplement_request_metrics:
        requests["supplement"] = supplement_request_metrics
    if current_metrics:
        requests[phase] = current_metrics

    total_elapsed = (
        _metric_float(full_pass_metrics, "total_elapsed_seconds")
        + _metric_float(primary_request_metrics, "total_elapsed_seconds")
        + _metric_float(supplement_request_metrics, "total_elapsed_seconds")
        + _metric_float(current_metrics, "total_elapsed_seconds")
    )
    response_chars = (
        _metric_int(full_pass_metrics, "response_chars")
        + _metric_int(primary_request_metrics, "response_chars")
        + _metric_int(supplement_request_metrics, "response_chars")
        + _metric_int(current_metrics, "response_chars")
    )
    request_chars = (
        _metric_int(full_pass_metrics, "request_chars")
        + _metric_int(primary_request_metrics, "request_chars")
        + _metric_int(supplement_request_metrics, "request_chars")
        + _metric_int(current_metrics, "request_chars")
    )

    metrics = {
        "request_key": request_key,
        "strategy": "two_pass",
        "model": model_name,
        "completed": False,
        "failed_phase": phase,
        "trigger_reasons": trigger_reasons,
        "total_elapsed_seconds": round(total_elapsed, 3),
        "response_chars": response_chars,
        "request_chars": request_chars,
        "requests": requests,
    }
    if full_pass_metrics:
        metrics["initial_full_pass"] = full_pass_metrics

    raise StructuredGenerationError(
        f"Two-pass fact extraction failed during {phase}: {exc}",
        request_metrics=metrics,
    ) from exc


def _run_single_pass_fact_extraction(
    client: StableOpenAICompatibleStructuredClient,
    config: StableProjectConfig,
    system_prompt: str,
    payload: dict[str, Any],
    *,
    request_key: str,
) -> FactExtractionRunResult:
    response = client.generate_structured(
        request_key=request_key,
        model_name=config.model.fact_model,
        response_model=FactExtractionResult,
        system_instruction=system_prompt,
        user_payload=payload,
        temperature=config.model.fact_temperature,
        max_output_tokens=config.model.fact_max_output_tokens,
    )
    request_metrics = dict(response.request_metrics)
    request_metrics["strategy"] = "single_pass"
    record = _hydrate_fact_record(response.parsed.model_dump(mode="json"), payload=payload)
    _ensure_fact_record_has_content(
        request_key=request_key,
        strategy="single_pass",
        stage="single_pass",
        request_metrics=request_metrics,
        record=record,
    )
    return FactExtractionRunResult(
        record=record,
        model_name=response.model_name,
        usage_metadata=response.usage_metadata,
        request_metrics=request_metrics,
        extraction_strategy="single_pass",
    )


def _run_two_pass_fact_extraction(
    client: StableOpenAICompatibleStructuredClient,
    config: StableProjectConfig,
    system_prompt: str,
    payload: dict[str, Any],
    *,
    request_key: str,
    trigger_reasons: list[str],
    initial_full_pass_metrics: dict[str, Any] | None = None,
) -> FactExtractionRunResult:
    primary_instruction = _build_primary_fact_instruction(system_prompt)
    supplement_instruction = _build_supplement_fact_instruction(
        system_prompt,
        include_primary_context=config.stability.facts_two_pass_include_primary_context,
    )

    try:
        primary_response = client.generate_structured(
            request_key=f"{request_key}_facts_primary",
            model_name=config.model.fact_model,
            response_model=FactExtractionPrimaryPassResult,
            system_instruction=primary_instruction,
            user_payload=payload,
            temperature=config.model.fact_temperature,
            max_output_tokens=config.model.fact_max_output_tokens,
        )
        primary_record = _hydrate_fact_record(primary_response.parsed.model_dump(mode="json"), payload=payload)
        _ensure_fact_record_has_content(
            request_key=f"{request_key}_facts_primary",
            strategy="two_pass",
            stage="two_pass_primary",
            request_metrics=primary_response.request_metrics,
            record=primary_record,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_two_pass_failure(
            request_key=request_key,
            model_name=config.model.fact_model,
            trigger_reasons=trigger_reasons,
            phase="primary",
            exc=exc,
            initial_full_pass_metrics=initial_full_pass_metrics,
        )

    primary = FactExtractionPrimaryPassResult.model_validate(primary_record)
    supplement_payload = _build_supplement_fact_payload(config, payload, primary)

    try:
        supplement_response = client.generate_structured(
            request_key=f"{request_key}_facts_supplement",
            model_name=config.model.fact_model,
            response_model=FactExtractionSupplementPassResult,
            system_instruction=supplement_instruction,
            user_payload=supplement_payload,
            temperature=config.model.fact_temperature,
            max_output_tokens=config.model.fact_max_output_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_two_pass_failure(
            request_key=request_key,
            model_name=config.model.fact_model,
            trigger_reasons=trigger_reasons,
            phase="supplement",
            exc=exc,
            primary_metrics=primary_response.request_metrics,
            initial_full_pass_metrics=initial_full_pass_metrics,
        )

    supplement = supplement_response.parsed
    merged = FactExtractionResult.model_validate(
        {
            "chapter_id": payload["chapter_id"],
            "scene_id": payload["scene_id"],
            "scene_summary": primary.scene_summary,
            "entities": primary.entities,
            "events": primary.events,
            "facts": primary.facts,
            "relationship_changes": supplement.relationship_changes,
            "power_system_notes": supplement.power_system_notes,
            "style_markers": supplement.style_markers,
            "open_questions": supplement.open_questions,
        }
    )
    request_metrics = _combine_two_pass_success_metrics(
        model_name=config.model.fact_model,
        trigger_reasons=trigger_reasons,
        primary_metrics=primary_response.request_metrics,
        supplement_metrics=supplement_response.request_metrics,
        initial_full_pass_metrics=initial_full_pass_metrics,
    )
    merged_record = _hydrate_fact_record(merged.model_dump(mode="json"), payload=payload)
    _ensure_fact_record_has_content(
        request_key=request_key,
        strategy="two_pass",
        stage="two_pass_merged",
        request_metrics=request_metrics,
        record=merged_record,
    )
    return FactExtractionRunResult(
        record=merged_record,
        model_name=config.model.fact_model,
        usage_metadata={
            "primary": primary_response.usage_metadata,
            "supplement": supplement_response.usage_metadata,
        },
        request_metrics=request_metrics,
        extraction_strategy="two_pass",
    )


def _run_fact_extraction(
    client: StableOpenAICompatibleStructuredClient,
    config: StableProjectConfig,
    scene: dict,
    system_prompt: str,
    payload: dict[str, Any],
    *,
    request_key: str,
) -> FactExtractionRunResult:
    use_two_pass, reasons = _should_use_facts_two_pass(config, scene, system_prompt, payload)
    if use_two_pass:
        return _run_two_pass_fact_extraction(
            client,
            config,
            system_prompt,
            payload,
            request_key=request_key,
            trigger_reasons=reasons,
        )

    try:
        return _run_single_pass_fact_extraction(
            client,
            config,
            system_prompt,
            payload,
            request_key=request_key,
        )
    except Exception as exc:  # noqa: BLE001
        if not (config.stability.facts_two_pass_enabled and config.stability.facts_two_pass_on_failure):
            raise

        fallback_reasons = [f"single_pass_failure={type(exc).__name__}"]
        return _run_two_pass_fact_extraction(
            client,
            config,
            system_prompt,
            payload,
            request_key=request_key,
            trigger_reasons=fallback_reasons,
            initial_full_pass_metrics=_extract_request_metrics(exc),
        )


def extract_facts(
    config: StableProjectConfig,
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    limit: int | None = None,
    start_at: int = 0,
    resume: bool = False,
) -> None:
    client = StableOpenAICompatibleStructuredClient(config, artifacts_dir=output_dir)
    system_prompt = load_prompt(config.prompt_dir, "fact_extraction.md")
    output_path = ensure_dir(output_dir)
    manifest_path = output_path / "manifest.json"
    failures_path = output_path / "failures.json"

    files = [path for path in iter_json_files(input_dir) if path.name not in METADATA_JSON_NAMES]
    if start_at:
        files = files[start_at:]
    if limit is not None:
        files = files[:limit]

    tracker = RunTracker(
        stage="stable-extract-facts",
        output_dir=output_path,
        total_items=len(files),
        item_label="scene",
        source_dir=input_dir,
        metadata={
            "model": config.model.fact_model,
            "resume": resume,
            "start_at": start_at,
            "limit": limit,
            "stream": config.stability.stream,
            "user_agent": config.stability.user_agent,
            "facts_two_pass_enabled": config.stability.facts_two_pass_enabled,
        },
    )

    manifest_by_key = {
        row["source_file"]: row
        for row in _load_existing_rows(manifest_path, resume=resume)
        if row.get("source_file")
    }
    failures_by_key = {
        row["source_file"]: row
        for row in _load_existing_rows(failures_path, resume=resume)
        if row.get("source_file")
    }
    success_count = 0
    failure_count = 0

    try:
        for index, scene_file in enumerate(files, start=1):
            output_file = output_path / scene_file.name
            scene = read_json(scene_file)
            scene_id = scene.get("scene_id", scene_file.stem)
            chapter_id = scene.get("chapter_id", "")
            if resume and output_file.exists():
                failures_by_key.pop(scene_file.name, None)
                _write_tracking_files(
                    manifest_path,
                    manifest_by_key,
                    failures_path,
                    failures_by_key,
                    manifest_sort_key="source_file",
                    failure_sort_key="source_file",
                )
                tracker.record_skip(
                    scene_file.name,
                    f"Skipped existing output for {scene_file.name}.",
                    scene_id=scene_id,
                    chapter_id=chapter_id,
                )
                continue

            payload = _build_fact_payload(config, scene)
            try:
                result = _run_fact_extraction(
                    client,
                    config,
                    scene,
                    system_prompt,
                    payload,
                    request_key=scene_file.stem,
                )
                record = _hydrate_fact_record(result.record, payload=payload)
                record["chapter_title"] = payload["chapter_title"]
                record["scene_index"] = payload["scene_index"]
                record["source_file"] = scene_file.name
                write_json(output_file, record)
                manifest_by_key[scene_file.name] = {
                    "source_file": scene_file.name,
                    "output_file": output_file.name,
                    "chapter_id": scene["chapter_id"],
                    "scene_id": scene["scene_id"],
                    "chapter_title": scene["chapter_title"],
                    "model": result.model_name,
                    "usage_metadata": result.usage_metadata,
                    "request_metrics": result.request_metrics,
                    "extraction_strategy": result.extraction_strategy,
                    "trigger_reasons": result.request_metrics.get("trigger_reasons", []),
                    "normalization_applied": payload.get("normalization_applied", []),
                }
                failures_by_key.pop(scene_file.name, None)
                success_count += 1
                _write_tracking_files(
                    manifest_path,
                    manifest_by_key,
                    failures_path,
                    failures_by_key,
                    manifest_sort_key="source_file",
                    failure_sort_key="source_file",
                )
                tracker.record_success(
                    scene_file.name,
                    f"Wrote {output_file.name} ({index}/{len(files)}).",
                    scene_id=scene["scene_id"],
                    chapter_id=scene["chapter_id"],
                    extraction_strategy=result.extraction_strategy,
                    elapsed_seconds=result.request_metrics.get("total_elapsed_seconds"),
                    response_chars=result.request_metrics.get("response_chars"),
                )
            except Exception as exc:  # noqa: BLE001
                metrics = _extract_request_metrics(exc)
                failures_by_key[scene_file.name] = {
                    "source_file": scene_file.name,
                    "output_file": output_file.name,
                    "chapter_id": scene["chapter_id"],
                    "scene_id": scene["scene_id"],
                    "chapter_title": scene["chapter_title"],
                    "model": config.model.fact_model,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "request_metrics": metrics,
                    "extraction_strategy": metrics.get("strategy", "single_pass"),
                    "trigger_reasons": metrics.get("trigger_reasons", []),
                    "normalization_applied": payload.get("normalization_applied", []),
                }
                failure_count += 1
                _write_tracking_files(
                    manifest_path,
                    manifest_by_key,
                    failures_path,
                    failures_by_key,
                    manifest_sort_key="source_file",
                    failure_sort_key="source_file",
                )
                tracker.record_failure(
                    scene_file.name,
                    f"Request failed for {scene_file.name}: {exc}",
                    scene_id=scene["scene_id"],
                    chapter_id=scene["chapter_id"],
                    extraction_strategy=metrics.get("strategy", "single_pass"),
                    error_type=type(exc).__name__,
                )

        _write_tracking_files(
            manifest_path,
            manifest_by_key,
            failures_path,
            failures_by_key,
            manifest_sort_key="source_file",
            failure_sort_key="source_file",
        )
        tracker.finish(
            "Stable fact extraction completed.",
            manifest_count=len(manifest_by_key),
            outstanding_failures=len(failures_by_key),
            success_count=success_count,
            failure_count=failure_count,
        )
    except Exception as exc:  # noqa: BLE001
        tracker.fail_run(f"Stable fact extraction aborted: {exc}", error_type=type(exc).__name__)
        raise


def extract_style(
    config: StableProjectConfig,
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    limit: int | None = None,
    start_at: int = 0,
    resume: bool = False,
) -> None:
    client = StableOpenAICompatibleStructuredClient(config, artifacts_dir=output_dir)
    system_prompt = load_prompt(config.prompt_dir, "style_extraction.md")
    output_path = ensure_dir(output_dir)
    manifest_path = output_path / "manifest.json"
    failures_path = output_path / "failures.json"

    input_path = Path(input_dir)
    chapters = [load_chapter_document(path) for path in iter_text_files(input_path)]
    window_size = config.style_windows.window_size
    stride = config.style_windows.stride
    windows: list[list] = []

    for start in range(0, len(chapters), stride):
        window = chapters[start : start + window_size]
        if len(window) < window_size:
            break
        windows.append(window)

    if start_at:
        windows = windows[start_at:]
    if limit is not None:
        windows = windows[:limit]

    tracker = RunTracker(
        stage="stable-extract-style",
        output_dir=output_path,
        total_items=len(windows),
        item_label="window",
        source_dir=input_dir,
        metadata={
            "model": config.model.style_model,
            "resume": resume,
            "start_at": start_at,
            "limit": limit,
            "window_size": window_size,
            "stride": stride,
            "stream": config.stability.stream,
        },
    )

    manifest_by_key = {
        row["window_id"]: row
        for row in _load_existing_rows(manifest_path, resume=resume)
        if row.get("window_id")
    }
    failures_by_key = {
        row["window_id"]: row
        for row in _load_existing_rows(failures_path, resume=resume)
        if row.get("window_id")
    }
    success_count = 0
    failure_count = 0

    try:
        for index, window in enumerate(windows, start=1):
            start_id = window[0].chapter_id
            end_id = window[-1].chapter_id
            file_name = f"style_window_{start_id}_{end_id}.json"
            output_file = output_path / file_name
            window_id = f"{start_id}_{end_id}"
            if resume and output_file.exists():
                failures_by_key.pop(window_id, None)
                _write_tracking_files(
                    manifest_path,
                    manifest_by_key,
                    failures_path,
                    failures_by_key,
                    manifest_sort_key="window_id",
                    failure_sort_key="window_id",
                )
                tracker.record_skip(window_id, f"Skipped existing output for {file_name}.", file_name=file_name)
                continue

            payload, applied_rules = _build_style_payload(config, window)
            try:
                response = client.generate_structured(
                    request_key=f"style_{window_id}",
                    model_name=config.model.style_model,
                    response_model=StyleWindowSignalResult,
                    system_instruction=system_prompt,
                    user_payload=payload,
                    temperature=config.model.style_temperature,
                    max_output_tokens=config.model.style_max_output_tokens,
                    response_format_mode="json_schema",
                )
                record = _hydrate_style_record(response.parsed.model_dump(mode="json"), payload=payload)
                _ensure_style_record_has_content(
                    request_key=f"style_{window_id}",
                    request_metrics=response.request_metrics,
                    record=record,
                )
                record["source_chapter_titles"] = [chapter.title for chapter in window]
                write_json(output_file, record)
                manifest_by_key[window_id] = {
                    "window_id": window_id,
                    "schema_version": record.get("schema_version", ""),
                    "output_file": output_file.name,
                    "chapter_ids": payload["chapter_ids"],
                    "model": response.model_name,
                    "usage_metadata": response.usage_metadata,
                    "request_metrics": response.request_metrics,
                    "normalization_applied": applied_rules,
                }
                failures_by_key.pop(window_id, None)
                success_count += 1
                _write_tracking_files(
                    manifest_path,
                    manifest_by_key,
                    failures_path,
                    failures_by_key,
                    manifest_sort_key="window_id",
                    failure_sort_key="window_id",
                )
                tracker.record_success(
                    window_id,
                    f"Wrote {file_name} ({index}/{len(windows)}).",
                    file_name=file_name,
                    elapsed_seconds=response.request_metrics.get("total_elapsed_seconds"),
                    response_chars=response.request_metrics.get("response_chars"),
                )
            except Exception as exc:  # noqa: BLE001
                metrics = _extract_request_metrics(exc)
                failures_by_key[window_id] = {
                    "window_id": window_id,
                    "output_file": output_file.name,
                    "chapter_ids": payload["chapter_ids"],
                    "model": config.model.style_model,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "request_metrics": metrics,
                    "normalization_applied": applied_rules,
                }
                failure_count += 1
                _write_tracking_files(
                    manifest_path,
                    manifest_by_key,
                    failures_path,
                    failures_by_key,
                    manifest_sort_key="window_id",
                    failure_sort_key="window_id",
                )
                tracker.record_failure(
                    window_id,
                    f"Request failed for {file_name}: {exc}",
                    file_name=file_name,
                    error_type=type(exc).__name__,
                )

        _write_tracking_files(
            manifest_path,
            manifest_by_key,
            failures_path,
            failures_by_key,
            manifest_sort_key="window_id",
            failure_sort_key="window_id",
        )
        tracker.finish(
            "Stable style extraction completed.",
            manifest_count=len(manifest_by_key),
            outstanding_failures=len(failures_by_key),
            success_count=success_count,
            failure_count=failure_count,
        )
    except Exception as exc:  # noqa: BLE001
        tracker.fail_run(f"Stable style extraction aborted: {exc}", error_type=type(exc).__name__)
        raise

