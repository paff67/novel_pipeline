from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, iter_json_files, read_json, write_json, write_text
from novel_pipeline_stable.models import CanonEntity, CanonIndex
from novel_pipeline_stable.story_nodes import chapter_in_range
from novel_pipeline_stable.style_window_normalization import normalize_style_window_payload


METADATA_JSON_NAMES = {"manifest.json", "failures.json", "run_status.json"}
STYLE_RULE_FIELDS = (
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
)
STYLE_HINT_FIELDS = ("rag_candidates", "worldbook_candidates", "routing_hints")
SCALAR_CONTRACT_KEYS = ("perspective", "distance", "temporality", "inner_monologue_mode")


def normalize_name(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()


def stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    ensure_dir(path.parent)
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if content:
        content += "\n"
    write_text(path, content)


def _unique_compact(values: list[str], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if limit is not None and len(results) >= limit:
            break
    return results


def _compose_plot_node_title(chapter_id: str, chapter_title: str, event_names: list[str]) -> str:
    if chapter_title:
        return chapter_title
    if event_names:
        head = " / ".join(_unique_compact(event_names, limit=2))
        return f"Chapter {chapter_id} node: {head}"
    return f"Chapter {chapter_id} node"


def _compose_plot_node_summary(scene_summaries: list[str], event_rows: list[dict]) -> str:
    event_summaries = _unique_compact([row.get("summary", "") for row in event_rows], limit=3)
    if event_summaries:
        return "; ".join(event_summaries)
    return "; ".join(_unique_compact(scene_summaries, limit=3))


def _score_plot_relevance(event_count: int, participant_count: int, question_count: int) -> str:
    score = 0
    if event_count >= 2:
        score += 2
    elif event_count == 1:
        score += 1
    if participant_count >= 2:
        score += 1
    if question_count >= 2:
        score += 2
    elif question_count == 1:
        score += 1
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _fact_payload_in_scope(payload: dict[str, Any], story_node: dict[str, Any] | None) -> bool:
    if story_node is None:
        return True
    return chapter_in_range(
        str(payload.get("chapter_id", "")),
        str(story_node.get("start_chapter", "")),
        str(story_node.get("end_chapter", "")),
    )


def _style_payload_in_scope(payload: dict[str, Any], story_node: dict[str, Any] | None) -> bool:
    if story_node is None:
        return True
    chapter_ids = payload.get("chapter_ids", [])
    if not isinstance(chapter_ids, list) or not chapter_ids:
        return False
    start_chapter = str(story_node.get("start_chapter", ""))
    end_chapter = str(story_node.get("end_chapter", ""))
    return all(chapter_in_range(str(chapter_id), start_chapter, end_chapter) for chapter_id in chapter_ids)


def _bump_counter(counter: defaultdict[str, int], value: Any) -> None:
    cleaned = str(value or "").strip()
    if cleaned:
        counter[cleaned] += 1


def _sorted_counter(counter: defaultdict[str, int]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def build_canon(
    facts_dir: str | Path,
    style_dir: str | Path,
    output_dir: str | Path,
    *,
    story_node: dict[str, Any] | None = None,
) -> CanonIndex:
    output_path = ensure_dir(output_dir)

    entity_map: dict[tuple[str, str], CanonEntity] = {}
    fact_rows: list[dict] = []
    event_rows: list[dict] = []
    relationship_change_rows: list[dict] = []
    power_system_note_rows: list[dict] = []
    chapter_scene_summaries: dict[str, list[dict]] = defaultdict(list)
    chapter_event_rows: dict[str, list[dict]] = defaultdict(list)
    chapter_scene_ids: dict[str, set[str]] = defaultdict(set)
    chapter_titles: dict[str, str] = {}
    style_rows: list[dict] = []

    for path in iter_json_files(facts_dir):
        if path.name in METADATA_JSON_NAMES or not path.name.startswith("scene_"):
            continue
        payload = read_json(path)
        if not isinstance(payload, dict) or not _fact_payload_in_scope(payload, story_node):
            continue
        chapter_id = payload["chapter_id"]
        scene_id = payload["scene_id"]
        chapter_title = str(payload.get("chapter_title", "")).strip()
        if chapter_title and chapter_id not in chapter_titles:
            chapter_titles[chapter_id] = chapter_title
        chapter_scene_ids[chapter_id].add(scene_id)
        chapter_scene_summaries[chapter_id].append(
            {
                "scene_id": scene_id,
                "scene_summary": payload["scene_summary"],
                "open_questions": payload.get("open_questions", []),
            }
        )

        for entity in payload.get("entities", []):
            key = (entity["entity_type"], normalize_name(entity["name"]))
            if key not in entity_map:
                entity_map[key] = CanonEntity(
                    entity_id=stable_id(entity["entity_type"], entity["name"]),
                    name=entity["name"],
                    entity_type=entity["entity_type"],
                    aliases=sorted(set(entity.get("aliases", []))),
                    first_seen_chapter=chapter_id,
                    supporting_scene_ids=[scene_id],
                    notes=[entity.get("role_in_scene", "")] if entity.get("role_in_scene") else [],
                )
            else:
                existing = entity_map[key]
                existing.aliases = sorted(set(existing.aliases + entity.get("aliases", [])))
                if scene_id not in existing.supporting_scene_ids:
                    existing.supporting_scene_ids.append(scene_id)
                role = entity.get("role_in_scene", "")
                if role and role not in existing.notes:
                    existing.notes.append(role)

        for fact in payload.get("facts", []):
            fact_rows.append(
                {
                    "fact_id": stable_id("fact", f"{chapter_id}:{scene_id}:{fact['subject']}:{fact['predicate']}:{fact['object']}"),
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    **fact,
                }
            )

        for event in payload.get("events", []):
            event_row = {
                "event_id": stable_id("event", f"{chapter_id}:{scene_id}:{event['name']}"),
                "chapter_id": chapter_id,
                "scene_id": scene_id,
                **event,
            }
            event_rows.append(event_row)
            chapter_event_rows[chapter_id].append(event_row)

        for change in payload.get("relationship_changes", []):
            if not isinstance(change, dict):
                continue
            relationship_change_rows.append(
                {
                    "relationship_change_id": stable_id(
                        "relchg",
                        (
                            f"{chapter_id}:{scene_id}:"
                            f"{change.get('source', '')}:{change.get('target', '')}:"
                            f"{change.get('relation', '')}:{change.get('change', '')}"
                        ),
                    ),
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    "source": str(change.get("source", "")).strip(),
                    "target": str(change.get("target", "")).strip(),
                    "relation": str(change.get("relation", "")).strip(),
                    "change": str(change.get("change", "")).strip(),
                    "evidence": change.get("evidence", {}),
                }
            )

        for note in payload.get("power_system_notes", []):
            if not isinstance(note, dict):
                continue
            power_system_note_rows.append(
                {
                    "power_system_note_id": stable_id(
                        "power_note",
                        f"{chapter_id}:{scene_id}:{note.get('topic', '')}:{note.get('note', '')}",
                    ),
                    "chapter_id": chapter_id,
                    "scene_id": scene_id,
                    "topic": str(note.get("topic", "")).strip(),
                    "note": str(note.get("note", "")).strip(),
                    "evidence": note.get("evidence", {}),
                }
            )

    for path in iter_json_files(style_dir):
        if path.name in METADATA_JSON_NAMES or not path.name.startswith("style_window_"):
            continue
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        normalized_style = normalize_style_window_payload(payload, source_path=path)
        if not _style_payload_in_scope(normalized_style, story_node):
            continue
        style_rows.append(normalized_style)

    if story_node is not None and not chapter_scene_summaries:
        raise ValueError(
            "No fact extraction rows matched the confirmed story node range "
            f"{story_node.get('start_chapter', '')}-{story_node.get('end_chapter', '')}."
        )

    entity_rows = [entity.model_dump(mode="json") for entity in entity_map.values()]
    entity_rows.sort(key=lambda row: (row["entity_type"], row["name"]))
    fact_rows.sort(key=lambda row: (row["chapter_id"], row["scene_id"], row["fact_id"]))
    event_rows.sort(key=lambda row: (row["chapter_id"], row["scene_id"], row["event_id"]))
    relationship_change_rows.sort(
        key=lambda row: (row["chapter_id"], row["scene_id"], row["relationship_change_id"])
    )
    power_system_note_rows.sort(
        key=lambda row: (row["chapter_id"], row["scene_id"], row["power_system_note_id"])
    )
    chapter_summaries = []
    for chapter_id in sorted(chapter_scene_summaries):
        scene_rows = sorted(chapter_scene_summaries[chapter_id], key=lambda row: row["scene_id"])
        chapter_summaries.append(
            {
                "chapter_id": chapter_id,
                "chapter_title": chapter_titles.get(chapter_id, ""),
                "scene_count": len(scene_rows),
                "scene_summaries": [row["scene_summary"] for row in scene_rows],
                "open_questions": [item for row in scene_rows for item in row.get("open_questions", [])],
            }
        )

    plot_nodes = []
    for chapter_summary in chapter_summaries:
        chapter_id = chapter_summary["chapter_id"]
        event_group = chapter_event_rows.get(chapter_id, [])
        event_names = _unique_compact([row.get("name", "") for row in event_group], limit=6)
        participants = sorted(
            {
                participant.strip()
                for row in event_group
                for participant in row.get("participants", [])
                if str(participant).strip()
            }
        )
        locations = sorted(
            {
                str(row.get("location", "")).strip()
                for row in event_group
                if str(row.get("location", "")).strip()
            }
        )
        open_questions = _unique_compact(chapter_summary.get("open_questions", []), limit=8)
        plot_nodes.append(
            {
                "node_id": stable_id(
                    "plot_node",
                    f"{chapter_id}:{chapter_summary.get('chapter_title', '')}:{'|'.join(event_names)}",
                ),
                "chapter_id": chapter_id,
                "chapter_title": chapter_summary.get("chapter_title", ""),
                "node_type": "chapter_draft",
                "title": _compose_plot_node_title(
                    chapter_id,
                    chapter_summary.get("chapter_title", ""),
                    event_names,
                ),
                "summary": _compose_plot_node_summary(chapter_summary["scene_summaries"], event_group),
                "event_names": event_names,
                "event_ids": [row["event_id"] for row in event_group],
                "scene_ids": sorted(chapter_scene_ids.get(chapter_id, set())),
                "participants": participants,
                "locations": locations,
                "open_questions": open_questions,
                "plot_relevance_hint": _score_plot_relevance(
                    len(event_group),
                    len(participants),
                    len(open_questions),
                ),
                "source": "derived_from_fact_extraction",
            }
        )

    _write_jsonl(output_path / "entities.jsonl", entity_rows)
    _write_jsonl(output_path / "facts.jsonl", fact_rows)
    _write_jsonl(output_path / "events.jsonl", event_rows)
    _write_jsonl(output_path / "relationship_changes.jsonl", relationship_change_rows)
    _write_jsonl(output_path / "power_system_notes.jsonl", power_system_note_rows)
    _write_jsonl(output_path / "chapter_summaries.jsonl", chapter_summaries)
    _write_jsonl(output_path / "plot_nodes_draft.jsonl", plot_nodes)
    write_json(output_path / "style_bible.json", style_rows)

    axis_hint_counter: defaultdict[str, int] = defaultdict(int)
    bucket_hint_counter: defaultdict[str, int] = defaultdict(int)
    routing_target_counter: defaultdict[str, int] = defaultdict(int)
    mechanism_label_counter: defaultdict[str, int] = defaultdict(int)
    negative_pitfall_counter: defaultdict[str, int] = defaultdict(int)
    scalar_contract_counters: dict[str, defaultdict[str, int]] = {
        key: defaultdict(int) for key in SCALAR_CONTRACT_KEYS
    }
    for row in style_rows:
        scalar_contracts = row.get("scalar_contracts", {})
        if isinstance(scalar_contracts, dict):
            for key in SCALAR_CONTRACT_KEYS:
                value = str(scalar_contracts.get(key, "")).strip()
                if value and value != "unspecified":
                    scalar_contract_counters[key][value] += 1

        for field_name in STYLE_RULE_FIELDS:
            for item in row.get(field_name, []):
                if not isinstance(item, dict):
                    continue
                _bump_counter(mechanism_label_counter, item.get("mechanism_label"))

        for field_name in STYLE_HINT_FIELDS:
            for item in row.get(field_name, []):
                if not isinstance(item, dict):
                    continue
                _bump_counter(routing_target_counter, item.get("route_target_action"))
                _bump_counter(axis_hint_counter, item.get("axis_id"))
                _bump_counter(bucket_hint_counter, item.get("bucket_id"))

        for item in row.get("axis_hints", []):
            if isinstance(item, dict):
                _bump_counter(axis_hint_counter, item.get("axis_id"))
        for item in row.get("bucket_hints", []):
            if isinstance(item, dict):
                _bump_counter(bucket_hint_counter, item.get("bucket_id"))
        for item in row.get("negative_pitfalls", []):
            if isinstance(item, dict):
                _bump_counter(negative_pitfall_counter, item.get("forbidden_action"))

    write_json(
        output_path / "style_index.json",
        {
            "axis_hint_counts": _sorted_counter(axis_hint_counter),
            "bucket_hint_counts": _sorted_counter(bucket_hint_counter),
            "routing_target_counts": _sorted_counter(routing_target_counter),
            "mechanism_label_counts": _sorted_counter(mechanism_label_counter),
            "negative_pitfall_counts": _sorted_counter(negative_pitfall_counter),
            "scalar_contract_counts": {
                key: _sorted_counter(counter) for key, counter in scalar_contract_counters.items()
            },
            "window_count": len(style_rows),
        },
    )
    write_json(
        output_path / "plot_node_index.json",
        {
            "plot_node_count": len(plot_nodes),
            "high_relevance_count": sum(1 for row in plot_nodes if row["plot_relevance_hint"] == "high"),
            "medium_relevance_count": sum(1 for row in plot_nodes if row["plot_relevance_hint"] == "medium"),
            "low_relevance_count": sum(1 for row in plot_nodes if row["plot_relevance_hint"] == "low"),
        },
    )

    index = CanonIndex(
        entity_count=len(entity_rows),
        fact_count=len(fact_rows),
        event_count=len(event_rows),
        chapter_summary_count=len(chapter_summaries),
        style_window_count=len(style_rows),
        plot_node_count=len(plot_nodes),
        relationship_change_count=len(relationship_change_rows),
        power_system_note_count=len(power_system_note_rows),
    )
    write_json(output_path / "canon_index.json", index.model_dump(mode="json"))
    if story_node is not None:
        write_json(
            output_path / "story_node_scope.json",
            {
                "scope_type": "story_node",
                "node_id": story_node.get("node_id", ""),
                "label": story_node.get("label", ""),
                "start_chapter": story_node.get("start_chapter", ""),
                "end_chapter": story_node.get("end_chapter", ""),
                "dominant_layer": story_node.get("dominant_layer"),
                "dominant_layer_label": story_node.get("dominant_layer_label", ""),
                "manifest_path": story_node.get("manifest_path", ""),
                "user_notes": story_node.get("user_notes", ""),
            },
        )
    return index


