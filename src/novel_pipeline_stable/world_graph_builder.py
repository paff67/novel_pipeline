from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novel_pipeline_stable.canon_builder import normalize_name, stable_id
from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_jsonl
from novel_pipeline_stable.style_bible_inputs import chapter_sort_key, clean_text, load_story_node_scope


WORLD_GRAPH_SCHEMA_VERSION = 1


@dataclass(slots=True)
class WorldGraphBuildResult:
    output_dir: Path
    node_path: Path
    edge_path: Path
    community_path: Path
    node_summary_path: Path
    alias_index_path: Path
    manifest_path: Path
    node_count: int
    edge_count: int
    community_count: int


def _utc_now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


def _load_optional_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [row for row in read_jsonl(path) if isinstance(row, dict)]


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _compact_strings(values: list[Any], *, limit: int | None = None) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if limit is not None and len(results) >= limit:
            break
    return results


def _scope_payload(
    *,
    chapter_id: Any = "",
    scene_id: Any = "",
    story_node_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "chapter_id": clean_text(chapter_id),
        "scene_id": clean_text(scene_id),
        "story_node_id": clean_text((story_node_scope or {}).get("node_id")),
        "story_node_label": clean_text((story_node_scope or {}).get("label")),
        "start_chapter": clean_text((story_node_scope or {}).get("start_chapter")),
        "end_chapter": clean_text((story_node_scope or {}).get("end_chapter")),
    }


def _entity_alias_rows(
    entity_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[dict[str, Any]]]:
    entity_by_id: dict[str, dict[str, Any]] = {}
    alias_to_ids: dict[str, list[str]] = defaultdict(list)
    surface_by_alias: dict[str, list[str]] = defaultdict(list)

    for row in entity_rows:
        entity_id = clean_text(row.get("entity_id"))
        if not entity_id:
            continue
        entity_by_id[entity_id] = dict(row)
        candidate_values = [row.get("name", ""), *list(row.get("aliases", []) or [])]
        for value in candidate_values:
            surface = clean_text(value)
            normalized = normalize_name(surface)
            if not normalized:
                continue
            if entity_id not in alias_to_ids[normalized]:
                alias_to_ids[normalized].append(entity_id)
            if surface and surface not in surface_by_alias[normalized]:
                surface_by_alias[normalized].append(surface)

    alias_rows = [
        {
            "alias": alias,
            "entity_ids": sorted(entity_ids),
            "entity_names": [clean_text(entity_by_id.get(entity_id, {}).get("name")) for entity_id in sorted(entity_ids)],
            "surface_forms": sorted(surface_by_alias.get(alias, [])),
        }
        for alias, entity_ids in sorted(alias_to_ids.items(), key=lambda item: item[0])
    ]
    return entity_by_id, {alias: sorted(entity_ids) for alias, entity_ids in alias_to_ids.items()}, alias_rows


def _resolve_entity_id(name: Any, alias_to_ids: dict[str, list[str]]) -> tuple[str, str, list[str]]:
    normalized = normalize_name(clean_text(name))
    if not normalized:
        return "", "missing", []
    candidate_ids = alias_to_ids.get(normalized, [])
    if not candidate_ids:
        return "", "unresolved", []
    if len(candidate_ids) == 1:
        return candidate_ids[0], "exact", list(candidate_ids)
    ordered = sorted(candidate_ids)
    return ordered[0], "ambiguous", ordered


def _literal_endpoint_node(
    *,
    name: str,
    story_node_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    literal_name = clean_text(name)
    return {
        "node_id": stable_id("literal_endpoint", normalize_name(literal_name) or literal_name),
        "node_type": "literal_endpoint",
        "title": literal_name or "unresolved endpoint",
        "summary": "Unresolved graph endpoint preserved from canon extraction.",
        **_scope_payload(story_node_scope=story_node_scope),
        "source_file": "derived",
    }


def _entity_summary(row: dict[str, Any]) -> str:
    notes = _compact_strings(list(row.get("notes", []) or []), limit=3)
    aliases = _compact_strings(list(row.get("aliases", []) or []), limit=3)
    parts = [clean_text(row.get("name"))]
    if aliases:
        parts.append(f"aliases: {', '.join(aliases)}")
    if notes:
        parts.append(f"notes: {'; '.join(notes)}")
    first_seen = clean_text(row.get("first_seen_chapter"))
    if first_seen:
        parts.append(f"first seen: chapter {first_seen}")
    return " | ".join(part for part in parts if part)


def _add_node(
    node_records: dict[str, dict[str, Any]],
    node: dict[str, Any],
    *,
    chapter_member_ids: dict[str, set[str]],
) -> str:
    node_id = clean_text(node.get("node_id"))
    if not node_id:
        raise ValueError("World graph node is missing node_id.")
    if node_id not in node_records:
        node_records[node_id] = node
    chapter_id = clean_text(node.get("chapter_id"))
    if chapter_id:
        chapter_member_ids[chapter_id].add(node_id)
    return node_id


def _ensure_named_endpoint(
    *,
    name: Any,
    alias_to_ids: dict[str, list[str]],
    entity_by_id: dict[str, dict[str, Any]],
    node_records: dict[str, dict[str, Any]],
    chapter_member_ids: dict[str, set[str]],
    story_node_scope: dict[str, Any] | None,
    resolution_stats: Counter[str],
) -> tuple[str, str]:
    entity_id, resolution, _candidate_ids = _resolve_entity_id(name, alias_to_ids)
    if entity_id:
        resolution_stats[f"endpoint_resolution_{resolution}"] += 1
        if resolution == "ambiguous":
            resolution_stats["ambiguous_alias_endpoint_mentions"] += 1
        if entity_id not in node_records and entity_id in entity_by_id:
            _add_node(
                node_records,
                {
                    "node_id": entity_id,
                    "node_type": "entity",
                    "entity_type": clean_text(entity_by_id[entity_id].get("entity_type")) or "other",
                    "title": clean_text(entity_by_id[entity_id].get("name")) or entity_id,
                    "summary": _entity_summary(entity_by_id[entity_id]),
                    "aliases": _compact_strings(list(entity_by_id[entity_id].get("aliases", []) or [])),
                    "first_seen_chapter": clean_text(entity_by_id[entity_id].get("first_seen_chapter")),
                    **_scope_payload(
                        chapter_id=entity_by_id[entity_id].get("first_seen_chapter"),
                        story_node_scope=story_node_scope,
                    ),
                    "supporting_scene_ids": sorted(
                        _compact_strings(list(entity_by_id[entity_id].get("supporting_scene_ids", []) or []))
                    ),
                    "notes": _compact_strings(list(entity_by_id[entity_id].get("notes", []) or []), limit=8),
                    "source_file": "entities.jsonl",
                },
                chapter_member_ids=chapter_member_ids,
            )
        return entity_id, resolution

    resolution_stats["endpoint_resolution_literal"] += 1
    literal_node = _literal_endpoint_node(name=clean_text(name), story_node_scope=story_node_scope)
    literal_id = _add_node(node_records, literal_node, chapter_member_ids=chapter_member_ids)
    return literal_id, "literal"


def _add_edge(
    edge_records: dict[str, dict[str, Any]],
    *,
    edge_type: str,
    source_id: str,
    target_id: str,
    relation_label: str = "",
    chapter_id: Any = "",
    scene_id: Any = "",
    support_text: str = "",
    story_node_scope: dict[str, Any] | None = None,
    chapter_edge_ids: dict[str, set[str]],
    metadata: dict[str, Any] | None = None,
) -> str:
    normalized_edge_type = clean_text(edge_type) or "related_to"
    normalized_source = clean_text(source_id)
    normalized_target = clean_text(target_id)
    if not normalized_source or not normalized_target:
        raise ValueError("World graph edge requires non-empty source and target ids.")
    edge_id = stable_id(
        "edge",
        (
            f"{normalized_edge_type}:{normalized_source}:{normalized_target}:"
            f"{clean_text(relation_label)}:{clean_text(chapter_id)}:{clean_text(scene_id)}:{clean_text(support_text)[:120]}"
        ),
    )
    if edge_id not in edge_records:
        edge_records[edge_id] = {
            "edge_id": edge_id,
            "edge_type": normalized_edge_type,
            "source_id": normalized_source,
            "target_id": normalized_target,
            "relation_label": clean_text(relation_label),
            "support_text": clean_text(support_text),
            **_scope_payload(chapter_id=chapter_id, scene_id=scene_id, story_node_scope=story_node_scope),
            **(metadata or {}),
        }
    normalized_chapter = clean_text(chapter_id)
    if normalized_chapter:
        chapter_edge_ids[normalized_chapter].add(edge_id)
    return edge_id


def _node_retrieval_text(node: dict[str, Any]) -> str:
    parts = [
        f"type: {clean_text(node.get('node_type'))}",
        clean_text(node.get("title")),
        clean_text(node.get("summary")),
    ]
    if clean_text(node.get("entity_type")):
        parts.append(f"entity_type: {clean_text(node.get('entity_type'))}")
    aliases = _compact_strings(list(node.get("aliases", []) or []), limit=5)
    if aliases:
        parts.append(f"aliases: {', '.join(aliases)}")
    if clean_text(node.get("chapter_id")):
        parts.append(f"chapter: {clean_text(node.get('chapter_id'))}")
    if clean_text(node.get("scene_id")):
        parts.append(f"scene: {clean_text(node.get('scene_id'))}")
    if clean_text(node.get("story_node_id")):
        parts.append(f"story_node: {clean_text(node.get('story_node_id'))}")
    return " | ".join(part for part in parts if part)


def _scope_community_summary(
    *,
    chapter_ids: list[str],
    node_rows: list[dict[str, Any]],
    plot_nodes: list[dict[str, Any]],
    relationship_change_rows: list[dict[str, Any]],
    power_system_note_rows: list[dict[str, Any]],
    chapter_summary_by_id: dict[str, dict[str, Any]],
) -> str:
    chapter_span = ""
    if chapter_ids:
        chapter_span = f"chapter {chapter_ids[0]}" if len(chapter_ids) == 1 else f"chapters {chapter_ids[0]}-{chapter_ids[-1]}"
    key_entities = _compact_strings(
        [row.get("title", "") for row in node_rows if clean_text(row.get("node_type")) == "entity"],
        limit=8,
    )
    plot_spine = _compact_strings([row.get("summary", "") or row.get("title", "") for row in plot_nodes], limit=4)
    relationship_snippets = _compact_strings(
        [
            " / ".join(
                part
                for part in (
                    clean_text(row.get("source")),
                    clean_text(row.get("relation")),
                    clean_text(row.get("target")),
                    clean_text(row.get("change")),
                )
                if part
            )
            for row in relationship_change_rows
        ],
        limit=6,
    )
    power_snippets = _compact_strings(
        [
            " / ".join(part for part in (clean_text(row.get("topic")), clean_text(row.get("note"))) if part)
            for row in power_system_note_rows
        ],
        limit=6,
    )
    open_questions = _compact_strings(
        [
            question
            for chapter_id in chapter_ids
            for question in list(chapter_summary_by_id.get(chapter_id, {}).get("open_questions", []) or [])
        ],
        limit=6,
    )

    summary_parts: list[str] = []
    if chapter_span:
        summary_parts.append(chapter_span)
    if plot_spine:
        summary_parts.append(f"plot spine: {' ; '.join(plot_spine)}")
    if key_entities:
        summary_parts.append(f"key entities: {', '.join(key_entities)}")
    if relationship_snippets:
        summary_parts.append(f"relationship pressure: {' ; '.join(relationship_snippets)}")
    if power_snippets:
        summary_parts.append(f"system rules: {' ; '.join(power_snippets)}")
    if open_questions:
        summary_parts.append(f"open questions: {' ; '.join(open_questions)}")
    return " | ".join(part for part in summary_parts if part)


def _scope_community_row(
    *,
    chapter_ids: list[str],
    node_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    plot_nodes: list[dict[str, Any]],
    relationship_change_rows: list[dict[str, Any]],
    power_system_note_rows: list[dict[str, Any]],
    chapter_summary_by_id: dict[str, dict[str, Any]],
    story_node_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    story_node_id = clean_text((story_node_scope or {}).get("node_id"))
    community_type = "story_node_scope" if story_node_id else "global_scope"
    title = (
        clean_text((story_node_scope or {}).get("label"))
        or story_node_id
        or "Global world graph scope"
    )
    return {
        "community_id": stable_id("community", f"{community_type}:{story_node_id or 'global'}:{'|'.join(chapter_ids[:20])}"),
        "community_type": community_type,
        "title": title,
        "summary": _scope_community_summary(
            chapter_ids=chapter_ids,
            node_rows=node_rows,
            plot_nodes=plot_nodes,
            relationship_change_rows=relationship_change_rows,
            power_system_note_rows=power_system_note_rows,
            chapter_summary_by_id=chapter_summary_by_id,
        ),
        "chapter_id": chapter_ids[0] if chapter_ids else "",
        "chapter_ids": list(chapter_ids),
        "story_node_id": story_node_id,
        "member_node_ids": sorted(clean_text(row.get("node_id")) for row in node_rows if clean_text(row.get("node_id"))),
        "edge_ids": sorted(clean_text(row.get("edge_id")) for row in edge_rows if clean_text(row.get("edge_id"))),
        "plot_node_ids": sorted(clean_text(row.get("node_id")) for row in plot_nodes if clean_text(row.get("node_id"))),
        "relationship_change_ids": sorted(
            clean_text(row.get("relationship_change_id"))
            for row in relationship_change_rows
            if clean_text(row.get("relationship_change_id"))
        ),
        "power_system_note_ids": sorted(
            clean_text(row.get("power_system_note_id"))
            for row in power_system_note_rows
            if clean_text(row.get("power_system_note_id"))
        ),
    }


def build_world_graph(canon_dir: str | Path, output_dir: str | Path) -> WorldGraphBuildResult:
    canon_root = Path(canon_dir).resolve()
    output_root = ensure_dir(output_dir).resolve()

    entity_rows = _load_optional_jsonl(canon_root / "entities.jsonl")
    fact_rows = _load_optional_jsonl(canon_root / "facts.jsonl")
    event_rows = _load_optional_jsonl(canon_root / "events.jsonl")
    relationship_change_rows = _load_optional_jsonl(canon_root / "relationship_changes.jsonl")
    power_system_note_rows = _load_optional_jsonl(canon_root / "power_system_notes.jsonl")
    chapter_summaries = _load_optional_jsonl(canon_root / "chapter_summaries.jsonl")
    plot_nodes = _load_optional_jsonl(canon_root / "plot_nodes_draft.jsonl")
    canon_index = _load_optional_json(canon_root / "canon_index.json")
    story_node_scope = load_story_node_scope(canon_root)

    entity_by_id, alias_to_ids, alias_rows = _entity_alias_rows(entity_rows)

    node_records: dict[str, dict[str, Any]] = {}
    edge_records: dict[str, dict[str, Any]] = {}
    chapter_member_ids: dict[str, set[str]] = defaultdict(set)
    chapter_edge_ids: dict[str, set[str]] = defaultdict(set)
    resolution_stats: Counter[str] = Counter()
    relationship_rows_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    power_rows_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in entity_rows:
        entity_id = clean_text(row.get("entity_id"))
        if not entity_id:
            continue
        _add_node(
            node_records,
            {
                "node_id": entity_id,
                "node_type": "entity",
                "entity_type": clean_text(row.get("entity_type")) or "other",
                "title": clean_text(row.get("name")) or entity_id,
                "summary": _entity_summary(row),
                "aliases": _compact_strings(list(row.get("aliases", []) or [])),
                "first_seen_chapter": clean_text(row.get("first_seen_chapter")),
                **_scope_payload(chapter_id=row.get("first_seen_chapter"), story_node_scope=story_node_scope),
                "supporting_scene_ids": sorted(_compact_strings(list(row.get("supporting_scene_ids", []) or []))),
                "notes": _compact_strings(list(row.get("notes", []) or []), limit=8),
                "source_file": "entities.jsonl",
            },
            chapter_member_ids=chapter_member_ids,
        )

    for row in fact_rows:
        fact_id = clean_text(row.get("fact_id"))
        if not fact_id:
            continue
        chapter_id = clean_text(row.get("chapter_id"))
        scene_id = clean_text(row.get("scene_id"))
        subject = clean_text(row.get("subject"))
        predicate = clean_text(row.get("predicate"))
        obj = clean_text(row.get("object"))
        fact_text = " ".join(part for part in (subject, predicate, obj) if part).strip() or fact_id
        _add_node(
            node_records,
            {
                "node_id": fact_id,
                "node_type": "fact",
                "title": fact_text,
                "summary": fact_text,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "fact_type": clean_text(row.get("fact_type")) or "explicit",
                "confidence": clean_text(row.get("confidence")) or "high",
                **_scope_payload(chapter_id=chapter_id, scene_id=scene_id, story_node_scope=story_node_scope),
                "source_file": "facts.jsonl",
            },
            chapter_member_ids=chapter_member_ids,
        )
        subject_id, subject_resolution = _ensure_named_endpoint(
            name=subject,
            alias_to_ids=alias_to_ids,
            entity_by_id=entity_by_id,
            node_records=node_records,
            chapter_member_ids=chapter_member_ids,
            story_node_scope=story_node_scope,
            resolution_stats=resolution_stats,
        )
        object_id, object_resolution = _ensure_named_endpoint(
            name=obj,
            alias_to_ids=alias_to_ids,
            entity_by_id=entity_by_id,
            node_records=node_records,
            chapter_member_ids=chapter_member_ids,
            story_node_scope=story_node_scope,
            resolution_stats=resolution_stats,
        )
        common_metadata = {
            "fact_id": fact_id,
            "source_resolution": subject_resolution,
            "target_resolution": object_resolution,
        }
        _add_edge(
            edge_records,
            edge_type="states_fact",
            source_id=subject_id,
            target_id=fact_id,
            relation_label=predicate,
            chapter_id=chapter_id,
            scene_id=scene_id,
            support_text=fact_text,
            story_node_scope=story_node_scope,
            chapter_edge_ids=chapter_edge_ids,
            metadata=common_metadata,
        )
        _add_edge(
            edge_records,
            edge_type="fact_targets",
            source_id=fact_id,
            target_id=object_id,
            relation_label=predicate,
            chapter_id=chapter_id,
            scene_id=scene_id,
            support_text=fact_text,
            story_node_scope=story_node_scope,
            chapter_edge_ids=chapter_edge_ids,
            metadata=common_metadata,
        )
        _add_edge(
            edge_records,
            edge_type="fact_relation",
            source_id=subject_id,
            target_id=object_id,
            relation_label=predicate,
            chapter_id=chapter_id,
            scene_id=scene_id,
            support_text=fact_text,
            story_node_scope=story_node_scope,
            chapter_edge_ids=chapter_edge_ids,
            metadata=common_metadata,
        )

    for row in event_rows:
        event_id = clean_text(row.get("event_id"))
        if not event_id:
            continue
        chapter_id = clean_text(row.get("chapter_id"))
        scene_id = clean_text(row.get("scene_id"))
        outcomes = _compact_strings(list(row.get("outcomes", []) or []), limit=4)
        _add_node(
            node_records,
            {
                "node_id": event_id,
                "node_type": "event",
                "title": clean_text(row.get("name")) or event_id,
                "summary": clean_text(row.get("summary")) or "; ".join(outcomes),
                "event_type": clean_text(row.get("event_type")),
                "participants": _compact_strings(list(row.get("participants", []) or [])),
                "location": clean_text(row.get("location")),
                "outcomes": outcomes,
                **_scope_payload(chapter_id=chapter_id, scene_id=scene_id, story_node_scope=story_node_scope),
                "source_file": "events.jsonl",
            },
            chapter_member_ids=chapter_member_ids,
        )
        for participant in list(row.get("participants", []) or []):
            participant_id, participant_resolution = _ensure_named_endpoint(
                name=participant,
                alias_to_ids=alias_to_ids,
                entity_by_id=entity_by_id,
                node_records=node_records,
                chapter_member_ids=chapter_member_ids,
                story_node_scope=story_node_scope,
                resolution_stats=resolution_stats,
            )
            _add_edge(
                edge_records,
                edge_type="participates_in",
                source_id=participant_id,
                target_id=event_id,
                relation_label="participant",
                chapter_id=chapter_id,
                scene_id=scene_id,
                support_text=clean_text(row.get("summary")) or clean_text(participant),
                story_node_scope=story_node_scope,
                chapter_edge_ids=chapter_edge_ids,
                metadata={"event_id": event_id, "source_resolution": participant_resolution},
            )
        location = clean_text(row.get("location"))
        if location:
            location_id, location_resolution = _ensure_named_endpoint(
                name=location,
                alias_to_ids=alias_to_ids,
                entity_by_id=entity_by_id,
                node_records=node_records,
                chapter_member_ids=chapter_member_ids,
                story_node_scope=story_node_scope,
                resolution_stats=resolution_stats,
            )
            _add_edge(
                edge_records,
                edge_type="occurs_at",
                source_id=event_id,
                target_id=location_id,
                relation_label="location",
                chapter_id=chapter_id,
                scene_id=scene_id,
                support_text=clean_text(row.get("summary")) or location,
                story_node_scope=story_node_scope,
                chapter_edge_ids=chapter_edge_ids,
                metadata={"event_id": event_id, "target_resolution": location_resolution},
            )

    for row in plot_nodes:
        plot_node_id = clean_text(row.get("node_id"))
        if not plot_node_id:
            continue
        chapter_id = clean_text(row.get("chapter_id"))
        _add_node(
            node_records,
            {
                "node_id": plot_node_id,
                "node_type": "plot_node",
                "title": clean_text(row.get("title")) or plot_node_id,
                "summary": clean_text(row.get("summary")),
                "chapter_title": clean_text(row.get("chapter_title")),
                "event_names": _compact_strings(list(row.get("event_names", []) or []), limit=6),
                "event_ids": _compact_strings(list(row.get("event_ids", []) or [])),
                "participants": _compact_strings(list(row.get("participants", []) or []), limit=8),
                "locations": _compact_strings(list(row.get("locations", []) or []), limit=6),
                "open_questions": _compact_strings(list(row.get("open_questions", []) or []), limit=8),
                "plot_relevance_hint": clean_text(row.get("plot_relevance_hint")),
                **_scope_payload(chapter_id=chapter_id, story_node_scope=story_node_scope),
                "source_file": "plot_nodes_draft.jsonl",
            },
            chapter_member_ids=chapter_member_ids,
        )
        for event_id in list(row.get("event_ids", []) or []):
            if clean_text(event_id):
                _add_edge(
                    edge_records,
                    edge_type="contains_event",
                    source_id=plot_node_id,
                    target_id=clean_text(event_id),
                    relation_label="contains_event",
                    chapter_id=chapter_id,
                    support_text=clean_text(row.get("summary")),
                    story_node_scope=story_node_scope,
                    chapter_edge_ids=chapter_edge_ids,
                    metadata={"plot_node_id": plot_node_id},
                )
        for participant in list(row.get("participants", []) or []):
            participant_id, participant_resolution = _ensure_named_endpoint(
                name=participant,
                alias_to_ids=alias_to_ids,
                entity_by_id=entity_by_id,
                node_records=node_records,
                chapter_member_ids=chapter_member_ids,
                story_node_scope=story_node_scope,
                resolution_stats=resolution_stats,
            )
            _add_edge(
                edge_records,
                edge_type="focuses_on",
                source_id=plot_node_id,
                target_id=participant_id,
                relation_label="participant_focus",
                chapter_id=chapter_id,
                support_text=clean_text(row.get("summary")) or clean_text(participant),
                story_node_scope=story_node_scope,
                chapter_edge_ids=chapter_edge_ids,
                metadata={"plot_node_id": plot_node_id, "target_resolution": participant_resolution},
            )
        for location in list(row.get("locations", []) or []):
            location_id, location_resolution = _ensure_named_endpoint(
                name=location,
                alias_to_ids=alias_to_ids,
                entity_by_id=entity_by_id,
                node_records=node_records,
                chapter_member_ids=chapter_member_ids,
                story_node_scope=story_node_scope,
                resolution_stats=resolution_stats,
            )
            _add_edge(
                edge_records,
                edge_type="located_in",
                source_id=plot_node_id,
                target_id=location_id,
                relation_label="location_focus",
                chapter_id=chapter_id,
                support_text=clean_text(row.get("summary")) or clean_text(location),
                story_node_scope=story_node_scope,
                chapter_edge_ids=chapter_edge_ids,
                metadata={"plot_node_id": plot_node_id, "target_resolution": location_resolution},
            )

    for row in relationship_change_rows:
        chapter_id = clean_text(row.get("chapter_id"))
        relationship_rows_by_chapter[chapter_id].append(row)
        source_id, source_resolution = _ensure_named_endpoint(
            name=row.get("source"),
            alias_to_ids=alias_to_ids,
            entity_by_id=entity_by_id,
            node_records=node_records,
            chapter_member_ids=chapter_member_ids,
            story_node_scope=story_node_scope,
            resolution_stats=resolution_stats,
        )
        target_id, target_resolution = _ensure_named_endpoint(
            name=row.get("target"),
            alias_to_ids=alias_to_ids,
            entity_by_id=entity_by_id,
            node_records=node_records,
            chapter_member_ids=chapter_member_ids,
            story_node_scope=story_node_scope,
            resolution_stats=resolution_stats,
        )
        _add_edge(
            edge_records,
            edge_type="relationship_change",
            source_id=source_id,
            target_id=target_id,
            relation_label=clean_text(row.get("relation")),
            chapter_id=chapter_id,
            scene_id=row.get("scene_id"),
            support_text=clean_text(row.get("change")),
            story_node_scope=story_node_scope,
            chapter_edge_ids=chapter_edge_ids,
            metadata={
                "relationship_change_id": clean_text(row.get("relationship_change_id")),
                "change": clean_text(row.get("change")),
                "source_resolution": source_resolution,
                "target_resolution": target_resolution,
            },
        )

    for row in power_system_note_rows:
        power_note_id = clean_text(row.get("power_system_note_id"))
        if not power_note_id:
            continue
        chapter_id = clean_text(row.get("chapter_id"))
        power_rows_by_chapter[chapter_id].append(row)
        _add_node(
            node_records,
            {
                "node_id": power_note_id,
                "node_type": "power_rule",
                "title": clean_text(row.get("topic")) or power_note_id,
                "summary": clean_text(row.get("note")),
                "topic": clean_text(row.get("topic")),
                **_scope_payload(chapter_id=chapter_id, scene_id=row.get("scene_id"), story_node_scope=story_node_scope),
                "source_file": "power_system_notes.jsonl",
            },
            chapter_member_ids=chapter_member_ids,
        )
        topic = clean_text(row.get("topic"))
        if topic:
            topic_id, topic_resolution = _ensure_named_endpoint(
                name=topic,
                alias_to_ids=alias_to_ids,
                entity_by_id=entity_by_id,
                node_records=node_records,
                chapter_member_ids=chapter_member_ids,
                story_node_scope=story_node_scope,
                resolution_stats=resolution_stats,
            )
            _add_edge(
                edge_records,
                edge_type="about_topic",
                source_id=power_note_id,
                target_id=topic_id,
                relation_label="topic",
                chapter_id=chapter_id,
                scene_id=row.get("scene_id"),
                support_text=clean_text(row.get("note")),
                story_node_scope=story_node_scope,
                chapter_edge_ids=chapter_edge_ids,
                metadata={"power_system_note_id": power_note_id, "target_resolution": topic_resolution},
            )

    chapter_summary_by_id = {
        clean_text(row.get("chapter_id")): row
        for row in chapter_summaries
        if clean_text(row.get("chapter_id"))
    }
    plot_nodes_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plot_nodes:
        chapter_id = clean_text(row.get("chapter_id"))
        if chapter_id:
            plot_nodes_by_chapter[chapter_id].append(row)

    community_rows: list[dict[str, Any]] = []
    chapter_ids = sorted(
        {
            *chapter_summary_by_id.keys(),
            *plot_nodes_by_chapter.keys(),
            *chapter_member_ids.keys(),
        },
        key=chapter_sort_key,
    )
    for chapter_id in chapter_ids:
        chapter_summary = chapter_summary_by_id.get(chapter_id, {})
        chapter_plot_nodes = plot_nodes_by_chapter.get(chapter_id, [])
        chapter_relationships = relationship_rows_by_chapter.get(chapter_id, [])
        chapter_powers = power_rows_by_chapter.get(chapter_id, [])
        member_ids = sorted(chapter_member_ids.get(chapter_id, set()))
        edge_ids = sorted(chapter_edge_ids.get(chapter_id, set()))
        relationship_snippets = _compact_strings(
            [
                " / ".join(
                    part
                    for part in (
                        clean_text(row.get("source")),
                        clean_text(row.get("relation")),
                        clean_text(row.get("target")),
                        clean_text(row.get("change")),
                    )
                    if part
                )
                for row in chapter_relationships
            ],
            limit=3,
        )
        power_snippets = _compact_strings(
            [
                " / ".join(part for part in (clean_text(row.get("topic")), clean_text(row.get("note"))) if part)
                for row in chapter_powers
            ],
            limit=3,
        )
        plot_summaries = _compact_strings([row.get("summary", "") for row in chapter_plot_nodes], limit=2)
        summary_parts = []
        if plot_summaries:
            summary_parts.append(" ; ".join(plot_summaries))
        if relationship_snippets:
            summary_parts.append(f"关系变化: {' ; '.join(relationship_snippets)}")
        if power_snippets:
            summary_parts.append(f"规则与体系: {' ; '.join(power_snippets)}")
        open_questions = _compact_strings(list(chapter_summary.get("open_questions", []) or []), limit=4)
        if open_questions:
            summary_parts.append(f"开放问题: {' ; '.join(open_questions)}")
        community_rows.append(
            {
                "community_id": stable_id("community", f"chapter:{chapter_id}:{'|'.join(member_ids[:12])}"),
                "community_type": "chapter_scope",
                "title": clean_text(chapter_summary.get("chapter_title")) or f"Chapter {chapter_id} community",
                "summary": " | ".join(part for part in summary_parts if part),
                "chapter_id": chapter_id,
                "story_node_id": clean_text((story_node_scope or {}).get("node_id")),
                "member_node_ids": member_ids,
                "edge_ids": edge_ids,
                "plot_node_ids": sorted(
                    clean_text(row.get("node_id")) for row in chapter_plot_nodes if clean_text(row.get("node_id"))
                ),
                "relationship_change_ids": sorted(
                    clean_text(row.get("relationship_change_id"))
                    for row in chapter_relationships
                    if clean_text(row.get("relationship_change_id"))
                ),
                "power_system_note_ids": sorted(
                    clean_text(row.get("power_system_note_id"))
                    for row in chapter_powers
                    if clean_text(row.get("power_system_note_id"))
                ),
            }
        )

    node_rows = sorted(
        node_records.values(),
        key=lambda row: (
            clean_text(row.get("chapter_id")),
            clean_text(row.get("node_type")),
            clean_text(row.get("title")),
            clean_text(row.get("node_id")),
        ),
    )
    edge_rows = sorted(
        edge_records.values(),
        key=lambda row: (
            clean_text(row.get("chapter_id")),
            clean_text(row.get("edge_type")),
            clean_text(row.get("relation_label")),
            clean_text(row.get("edge_id")),
        ),
    )
    if node_rows or edge_rows:
        community_rows.append(
            _scope_community_row(
                chapter_ids=chapter_ids,
                node_rows=node_rows,
                edge_rows=edge_rows,
                plot_nodes=plot_nodes,
                relationship_change_rows=relationship_change_rows,
                power_system_note_rows=power_system_note_rows,
                chapter_summary_by_id=chapter_summary_by_id,
                story_node_scope=story_node_scope,
            )
        )
    community_rows = sorted(
        community_rows,
        key=lambda row: (
            clean_text(row.get("chapter_id")),
            clean_text(row.get("community_type")),
            clean_text(row.get("title")),
            clean_text(row.get("community_id")),
        ),
    )
    node_summary_rows = [
        {
            "summary_id": stable_id("node_summary", clean_text(row.get("node_id"))),
            "node_id": clean_text(row.get("node_id")),
            "node_type": clean_text(row.get("node_type")),
            "title": clean_text(row.get("title")),
            "chapter_id": clean_text(row.get("chapter_id")),
            "story_node_id": clean_text(row.get("story_node_id")),
            "retrieval_text": _node_retrieval_text(row),
        }
        for row in node_rows
    ]

    node_type_counts = Counter(clean_text(row.get("node_type")) for row in node_rows if clean_text(row.get("node_type")))
    edge_type_counts = Counter(clean_text(row.get("edge_type")) for row in edge_rows if clean_text(row.get("edge_type")))
    community_type_counts = Counter(
        clean_text(row.get("community_type"))
        for row in community_rows
        if clean_text(row.get("community_type"))
    )

    node_path = output_root / "world_graph_nodes.jsonl"
    edge_path = output_root / "world_graph_edges.jsonl"
    community_path = output_root / "world_graph_communities.jsonl"
    node_summary_path = output_root / "world_graph_node_summaries.jsonl"
    alias_index_path = output_root / "world_graph_alias_index.json"
    manifest_path = output_root / "world_graph_manifest.json"

    write_jsonl(node_path, node_rows)
    write_jsonl(edge_path, edge_rows)
    write_jsonl(community_path, community_rows)
    write_jsonl(node_summary_path, node_summary_rows)
    write_json(
        alias_index_path,
        {
            "schema_version": WORLD_GRAPH_SCHEMA_VERSION,
            "generated_at": _utc_now_iso(),
            "aliases": alias_rows,
        },
    )
    write_json(
        manifest_path,
        {
            "schema_version": WORLD_GRAPH_SCHEMA_VERSION,
            "generated_at": _utc_now_iso(),
            "canon_dir": str(canon_root),
            "story_node_scope": story_node_scope or {},
            "canon_index": canon_index,
            "input_counts": {
                "entities": len(entity_rows),
                "facts": len(fact_rows),
                "events": len(event_rows),
                "relationship_changes": len(relationship_change_rows),
                "power_system_notes": len(power_system_note_rows),
                "chapter_summaries": len(chapter_summaries),
                "plot_nodes": len(plot_nodes),
            },
            "output_counts": {
                "nodes": len(node_rows),
                "edges": len(edge_rows),
                "communities": len(community_rows),
                "node_summaries": len(node_summary_rows),
                "node_type_counts": dict(sorted(node_type_counts.items(), key=lambda item: item[0])),
                "edge_type_counts": dict(sorted(edge_type_counts.items(), key=lambda item: item[0])),
                "community_type_counts": dict(sorted(community_type_counts.items(), key=lambda item: item[0])),
            },
            "resolution_stats": dict(sorted(resolution_stats.items(), key=lambda item: item[0])),
            "chapter_ids": chapter_ids,
            "files": {
                "nodes": node_path.name,
                "edges": edge_path.name,
                "communities": community_path.name,
                "node_summaries": node_summary_path.name,
                "alias_index": alias_index_path.name,
            },
        },
    )

    return WorldGraphBuildResult(
        output_dir=output_root,
        node_path=node_path,
        edge_path=edge_path,
        community_path=community_path,
        node_summary_path=node_summary_path,
        alias_index_path=alias_index_path,
        manifest_path=manifest_path,
        node_count=len(node_rows),
        edge_count=len(edge_rows),
        community_count=len(community_rows),
    )


__all__ = [
    "WORLD_GRAPH_SCHEMA_VERSION",
    "WorldGraphBuildResult",
    "build_world_graph",
]
