from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_jsonl
from novel_pipeline_stable.style_bible_inputs import clean_text


WORLD_GRAPH_GRAPHRAG_SCHEMA_VERSION = 1


@dataclass(slots=True)
class WorldGraphGraphRAGExportResult:
    output_dir: Path
    entity_path: Path
    relationship_path: Path
    text_unit_path: Path
    community_report_path: Path
    manifest_path: Path
    entity_count: int
    relationship_count: int
    text_unit_count: int
    community_report_count: int


def _utc_now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


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


def _preferred_source_ref(payload: dict[str, Any], *, fallback_key: str) -> str:
    scene_id = clean_text(payload.get("scene_id"))
    chapter_id = clean_text(payload.get("chapter_id"))
    story_node_id = clean_text(payload.get("story_node_id"))
    fallback_value = clean_text(payload.get(fallback_key))
    if scene_id:
        return f"scene:{scene_id}"
    if chapter_id:
        return f"chapter:{chapter_id}"
    if story_node_id:
        return f"story_node:{story_node_id}"
    return fallback_value


def _entity_row(node: dict[str, Any]) -> dict[str, Any]:
    node_id = clean_text(node.get("node_id"))
    title = clean_text(node.get("title")) or node_id
    summary = clean_text(node.get("summary")) or title
    node_type = clean_text(node.get("node_type")) or "unknown"
    entity_type = clean_text(node.get("entity_type")) or node_type
    return {
        "entity_id": node_id,
        "human_readable_id": title,
        "title": title,
        "description": summary,
        "entity_type": entity_type,
        "graph_node_type": node_type,
        "chapter_id": clean_text(node.get("chapter_id")),
        "scene_id": clean_text(node.get("scene_id")),
        "story_node_id": clean_text(node.get("story_node_id")),
        "aliases": _compact_strings(list(node.get("aliases", []) or []), limit=12),
        "source_file": clean_text(node.get("source_file")),
    }


def _relationship_row(edge: dict[str, Any], *, node_titles: dict[str, str]) -> dict[str, Any]:
    source_id = clean_text(edge.get("source_id"))
    target_id = clean_text(edge.get("target_id"))
    relation_label = clean_text(edge.get("relation_label"))
    support_text = clean_text(edge.get("support_text"))
    edge_type = clean_text(edge.get("edge_type")) or "related_to"
    description_parts = [part for part in (relation_label, support_text) if part]
    return {
        "relationship_id": clean_text(edge.get("edge_id")),
        "source_entity_id": source_id,
        "target_entity_id": target_id,
        "source_title": node_titles.get(source_id, source_id),
        "target_title": node_titles.get(target_id, target_id),
        "relationship_type": edge_type,
        "description": " | ".join(description_parts),
        "weight": 1.0,
        "chapter_id": clean_text(edge.get("chapter_id")),
        "scene_id": clean_text(edge.get("scene_id")),
        "story_node_id": clean_text(edge.get("story_node_id")),
        "source_ref": _preferred_source_ref(edge, fallback_key="edge_id"),
    }


def _text_unit_row(summary_row: dict[str, Any], *, node_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    node_id = clean_text(summary_row.get("node_id"))
    node = node_lookup.get(node_id, {})
    node_type = clean_text(summary_row.get("node_type")) or clean_text(node.get("node_type"))
    title = clean_text(summary_row.get("title")) or clean_text(node.get("title")) or node_id
    retrieval_text = clean_text(summary_row.get("retrieval_text"))
    return {
        "text_unit_id": clean_text(summary_row.get("summary_id")),
        "node_id": node_id,
        "human_readable_id": title,
        "node_type": node_type,
        "text": retrieval_text,
        "source_ref": _preferred_source_ref(node or summary_row, fallback_key="node_id"),
        "chapter_id": clean_text(summary_row.get("chapter_id")) or clean_text(node.get("chapter_id")),
        "scene_id": clean_text(node.get("scene_id")),
        "story_node_id": clean_text(summary_row.get("story_node_id")) or clean_text(node.get("story_node_id")),
        "entity_ids": [node_id] if node_id else [],
    }


def _community_level(community_type: str) -> int:
    normalized = clean_text(community_type)
    if normalized == "chapter_scope":
        return 1
    if normalized == "story_node_scope":
        return 2
    if normalized == "global_scope":
        return 3
    return 1


def _community_report_row(community: dict[str, Any], *, node_titles: dict[str, str]) -> dict[str, Any]:
    member_ids = _compact_strings(list(community.get("member_node_ids", []) or []))
    member_titles = _compact_strings([node_titles.get(node_id, node_id) for node_id in member_ids], limit=12)
    summary = clean_text(community.get("summary"))
    member_text = ", ".join(member_titles)
    full_content_parts = [summary]
    if member_text:
        full_content_parts.append(f"members: {member_text}")
    community_type = clean_text(community.get("community_type")) or "chapter_scope"
    return {
        "community_id": clean_text(community.get("community_id")),
        "title": clean_text(community.get("title")) or clean_text(community.get("community_id")),
        "summary": summary,
        "full_content": " | ".join(part for part in full_content_parts if part),
        "community_type": community_type,
        "level": _community_level(community_type),
        "rank": len(member_ids),
        "chapter_id": clean_text(community.get("chapter_id")),
        "story_node_id": clean_text(community.get("story_node_id")),
        "chapter_ids": _compact_strings(list(community.get("chapter_ids", []) or [])),
        "member_node_ids": member_ids,
        "edge_ids": _compact_strings(list(community.get("edge_ids", []) or [])),
        "source_ref": _preferred_source_ref(community, fallback_key="community_id"),
    }


def export_world_graph_graphrag(world_graph_dir: str | Path, output_dir: str | Path) -> WorldGraphGraphRAGExportResult:
    world_graph_root = Path(world_graph_dir).resolve()
    output_root = ensure_dir(output_dir).resolve()

    node_path = world_graph_root / "world_graph_nodes.jsonl"
    edge_path = world_graph_root / "world_graph_edges.jsonl"
    community_path = world_graph_root / "world_graph_communities.jsonl"
    node_summary_path = world_graph_root / "world_graph_node_summaries.jsonl"
    manifest_path = world_graph_root / "world_graph_manifest.json"

    if not node_path.exists():
        raise FileNotFoundError(f"World graph nodes file not found: {node_path}")
    if not edge_path.exists():
        raise FileNotFoundError(f"World graph edges file not found: {edge_path}")
    if not community_path.exists():
        raise FileNotFoundError(f"World graph communities file not found: {community_path}")
    if not node_summary_path.exists():
        raise FileNotFoundError(f"World graph node summaries file not found: {node_summary_path}")

    node_rows = [row for row in read_jsonl(node_path) if isinstance(row, dict)]
    edge_rows = [row for row in read_jsonl(edge_path) if isinstance(row, dict)]
    community_rows = [row for row in read_jsonl(community_path) if isinstance(row, dict)]
    node_summary_rows = [row for row in read_jsonl(node_summary_path) if isinstance(row, dict)]
    world_graph_manifest = read_json(manifest_path) if manifest_path.exists() else {}
    if not isinstance(world_graph_manifest, dict):
        world_graph_manifest = {}

    node_lookup = {clean_text(row.get("node_id")): row for row in node_rows if clean_text(row.get("node_id"))}
    node_titles = {
        node_id: clean_text(row.get("title")) or node_id
        for node_id, row in node_lookup.items()
    }

    entity_rows = [_entity_row(row) for row in node_rows if clean_text(row.get("node_id"))]
    relationship_rows = [_relationship_row(row, node_titles=node_titles) for row in edge_rows if clean_text(row.get("edge_id"))]
    text_unit_rows = [
        _text_unit_row(row, node_lookup=node_lookup)
        for row in node_summary_rows
        if clean_text(row.get("summary_id"))
    ]
    community_report_rows = [
        _community_report_row(row, node_titles=node_titles)
        for row in community_rows
        if clean_text(row.get("community_id"))
    ]

    graphrag_entity_path = output_root / "graphrag_entities.jsonl"
    graphrag_relationship_path = output_root / "graphrag_relationships.jsonl"
    graphrag_text_unit_path = output_root / "graphrag_text_units.jsonl"
    graphrag_community_path = output_root / "graphrag_community_reports.jsonl"
    graphrag_manifest_path = output_root / "graphrag_manifest.json"

    write_jsonl(graphrag_entity_path, entity_rows)
    write_jsonl(graphrag_relationship_path, relationship_rows)
    write_jsonl(graphrag_text_unit_path, text_unit_rows)
    write_jsonl(graphrag_community_path, community_report_rows)
    write_json(
        graphrag_manifest_path,
        {
            "schema_version": WORLD_GRAPH_GRAPHRAG_SCHEMA_VERSION,
            "generated_at": _utc_now_iso(),
            "profile": "graphrag_byog_jsonl_v1",
            "source_world_graph_dir": str(world_graph_root),
            "source_world_graph_manifest": world_graph_manifest,
            "table_contract": {
                "entities": graphrag_entity_path.name,
                "relationships": graphrag_relationship_path.name,
                "text_units": graphrag_text_unit_path.name,
                "community_reports": graphrag_community_path.name,
            },
            "counts": {
                "entities": len(entity_rows),
                "relationships": len(relationship_rows),
                "text_units": len(text_unit_rows),
                "community_reports": len(community_report_rows),
            },
            "notes": [
                "This export preserves the current world graph as GraphRAG-ready JSONL tables.",
                "The first landing focuses on BYOG-friendly stable files and does not introduce new storage dependencies.",
            ],
        },
    )

    return WorldGraphGraphRAGExportResult(
        output_dir=output_root,
        entity_path=graphrag_entity_path,
        relationship_path=graphrag_relationship_path,
        text_unit_path=graphrag_text_unit_path,
        community_report_path=graphrag_community_path,
        manifest_path=graphrag_manifest_path,
        entity_count=len(entity_rows),
        relationship_count=len(relationship_rows),
        text_unit_count=len(text_unit_rows),
        community_report_count=len(community_report_rows),
    )


__all__ = [
    "WORLD_GRAPH_GRAPHRAG_SCHEMA_VERSION",
    "WorldGraphGraphRAGExportResult",
    "export_world_graph_graphrag",
]
