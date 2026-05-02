from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import iter_json_files, read_json, read_jsonl
from novel_pipeline_stable.story_nodes import chapter_in_range
from novel_pipeline_stable.style_window_normalization import normalize_style_window_payload


METADATA_JSON_NAMES = {"manifest.json", "failures.json", "run_status.json", "run_manifest.json"}


@dataclass(slots=True)
class StyleBibleInputBundle:
    fact_rows: list[dict[str, Any]]
    style_rows: list[dict[str, Any]]
    chapter_rows: list[dict[str, Any]]
    plot_rows: list[dict[str, Any]]
    entity_rows: list[dict[str, Any]]
    canon_index: dict[str, Any]
    style_index: dict[str, Any]
    story_node_scope: dict[str, Any] | None = None


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def chapter_sort_key(value: Any) -> tuple[int, Any]:
    text = clean_text(value)
    if not text:
        return (2, "")
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def load_story_node_scope(canon_dir: str | Path) -> dict[str, Any] | None:
    scope_path = Path(canon_dir).resolve() / "story_node_scope.json"
    if not scope_path.exists():
        return None
    payload = read_json(scope_path)
    if not isinstance(payload, dict):
        raise ValueError(f"Story node scope file must be a JSON object: {scope_path}")

    start_chapter = clean_text(payload.get("start_chapter"))
    end_chapter = clean_text(payload.get("end_chapter"))
    if not start_chapter or not end_chapter:
        raise ValueError(f"Story node scope file is missing start/end chapter fields: {scope_path}")

    return {
        "scope_type": clean_text(payload.get("scope_type")) or "story_node",
        "node_id": clean_text(payload.get("node_id")),
        "label": clean_text(payload.get("label")),
        "start_chapter": start_chapter,
        "end_chapter": end_chapter,
        "dominant_layer": payload.get("dominant_layer"),
        "dominant_layer_label": clean_text(payload.get("dominant_layer_label")),
        "manifest_path": clean_text(payload.get("manifest_path")),
        "user_notes": clean_text(payload.get("user_notes")),
    }


def build_scope_hint(chapter_ids: list[str], *, story_node_scope: dict[str, Any] | None = None) -> str:
    if story_node_scope:
        start_chapter = clean_text(story_node_scope.get("start_chapter"))
        end_chapter = clean_text(story_node_scope.get("end_chapter"))
        label = clean_text(story_node_scope.get("label"))
        if label and start_chapter and end_chapter:
            return f"{label} ({start_chapter}-{end_chapter}) / facts+style+canon joint distillation"
        if start_chapter and end_chapter:
            return f"chapters {start_chapter}-{end_chapter} / facts+style+canon joint distillation"

    ordered = sorted({clean_text(chapter_id) for chapter_id in chapter_ids if clean_text(chapter_id)}, key=chapter_sort_key)
    if not ordered:
        return "facts+style+canon joint distillation"
    return f"chapters {ordered[0]}-{ordered[-1]} / facts+style+canon joint distillation"


def _compact_strings(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if len(results) >= limit:
            break
    return results


def _should_skip_json_path(path: Path) -> bool:
    return path.name in METADATA_JSON_NAMES or any(
        path.name.startswith(prefix)
        for prefix in ("manifest.", "failures.", "run_status.", "run_manifest.")
    )


def _read_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = read_json(path)
    except JSONDecodeError:
        if path.stat().st_size == 0 or _should_skip_json_path(path):
            return None
        raise
    return payload if isinstance(payload, dict) else None


def _normalize_style_row(payload: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    normalized = normalize_style_window_payload(payload, source_path=source_path)
    source_titles = _compact_strings(payload.get("source_chapter_titles"), limit=8)
    if source_titles:
        normalized["source_chapter_titles"] = source_titles
    return normalized


def _fact_payload_in_scope(payload: dict[str, Any], story_node_scope: dict[str, Any] | None) -> bool:
    if story_node_scope is None:
        return True
    return chapter_in_range(
        clean_text(payload.get("chapter_id")),
        clean_text(story_node_scope.get("start_chapter")),
        clean_text(story_node_scope.get("end_chapter")),
    )


def _style_payload_in_scope(payload: dict[str, Any], story_node_scope: dict[str, Any] | None) -> bool:
    if story_node_scope is None:
        return True
    chapter_ids = payload.get("chapter_ids", [])
    if not isinstance(chapter_ids, list) or not chapter_ids:
        return False
    start_chapter = clean_text(story_node_scope.get("start_chapter"))
    end_chapter = clean_text(story_node_scope.get("end_chapter"))
    return all(chapter_in_range(clean_text(chapter_id), start_chapter, end_chapter) for chapter_id in chapter_ids)


def load_style_bible_inputs(
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
) -> StyleBibleInputBundle:
    canon_path = Path(canon_dir).resolve()
    story_node_scope = load_story_node_scope(canon_path)

    fact_rows: list[dict[str, Any]] = []
    for path in iter_json_files(facts_dir):
        if _should_skip_json_path(path):
            continue
        payload = _read_payload(path)
        if payload is not None and _fact_payload_in_scope(payload, story_node_scope):
            fact_rows.append(payload)

    style_rows: list[dict[str, Any]] = []
    for path in iter_json_files(style_dir):
        if _should_skip_json_path(path):
            continue
        payload = _read_payload(path)
        if payload is None:
            continue
        normalized_style = _normalize_style_row(payload, source_path=path)
        if _style_payload_in_scope(normalized_style, story_node_scope):
            style_rows.append(normalized_style)

    if not fact_rows:
        scope_suffix = ""
        if story_node_scope is not None:
            scope_suffix = (
                " within scope "
                f"{story_node_scope.get('start_chapter', '')}-{story_node_scope.get('end_chapter', '')}"
            )
        raise FileNotFoundError(f"No fact extraction JSON files found in {Path(facts_dir).resolve()}{scope_suffix}")
    if not style_rows:
        scope_suffix = ""
        if story_node_scope is not None:
            scope_suffix = (
                " within scope "
                f"{story_node_scope.get('start_chapter', '')}-{story_node_scope.get('end_chapter', '')}"
            )
        raise FileNotFoundError(f"No style extraction JSON files found in {Path(style_dir).resolve()}{scope_suffix}")

    chapter_summaries_path = canon_path / "chapter_summaries.jsonl"
    chapter_rows = read_jsonl(chapter_summaries_path) if chapter_summaries_path.exists() else []
    if not chapter_rows:
        raise FileNotFoundError(
            "Missing required canon artifact `chapter_summaries.jsonl`. "
            "Run `build-canon` before style bible v2 so the routed pipeline has chapter-level support rows."
        )

    plot_nodes_path = canon_path / "plot_nodes_draft.jsonl"
    entities_path = canon_path / "entities.jsonl"
    canon_index_path = canon_path / "canon_index.json"
    style_index_path = canon_path / "style_index.json"

    plot_rows = read_jsonl(plot_nodes_path) if plot_nodes_path.exists() else []
    entity_rows = read_jsonl(entities_path) if entities_path.exists() else []
    canon_index = read_json(canon_index_path) if canon_index_path.exists() else {}
    style_index = read_json(style_index_path) if style_index_path.exists() else {}
    if not isinstance(canon_index, dict):
        canon_index = {}
    if not isinstance(style_index, dict):
        style_index = {}

    return StyleBibleInputBundle(
        fact_rows=fact_rows,
        style_rows=style_rows,
        chapter_rows=chapter_rows,
        plot_rows=plot_rows,
        entity_rows=entity_rows,
        canon_index=canon_index,
        style_index=style_index,
        story_node_scope=story_node_scope,
    )
