from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_markdown
from novel_pipeline_stable.models import StyleBibleResultV2
from novel_pipeline_stable.style_bible_contracts import REASONING_FILE, REDUCE_TRACE_FILE


STYLE_BIBLE_FILE = "style_bible_final.json"
WORLD_GRAPH_MANIFEST_FILE = "world_graph_manifest.json"
HYBRID_RAG_CONTRACT_FILE = "hybrid_rag_contract.json"
HYBRID_RAG_CONTRACT_MD_FILE = "hybrid_rag_contract.md"
HYBRID_RAG_CONTRACT_SCHEMA_VERSION = 1

STYLE_LIST_SECTION_PATHS = (
    "narrative_system.engine",
    "narrative_system.pacing_rules",
    "narrative_system.plot_node_logic",
    "expression_system.description_rules",
    "expression_system.dialogue_rules",
    "expression_system.characterization_rules",
    "expression_system.sensory_rules",
    "aesthetics_system.core_axes",
    "aesthetics_system.pressure_axes",
    "aesthetics_system.humor_recipe",
    "aesthetics_system.satire_targets",
    "aesthetics_system.nonstandard_xianxia_rules",
    "voice_contract.register_mix",
    "voice_contract.negative_pitfalls",
    "character_arc_rules",
    "worldbook_binding.rag_worthy",
    "worldbook_binding.worldbook_worthy",
    "worldbook_binding.routing_hints",
    "negative_rules",
)
STYLE_SCALAR_SECTION_PATHS = (
    "narrative_system.perspective",
    "narrative_system.distance",
    "narrative_system.temporality",
    "voice_contract.narrator_voice",
    "voice_contract.inner_monologue_mode",
)


@dataclass(slots=True)
class HybridRAGContractBuildResult:
    output_dir: Path
    contract_path: Path
    markdown_path: Path
    contract: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _nested_payload_value(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _payload_list(payload: dict[str, Any], path: str) -> list[Any]:
    value = _nested_payload_value(payload, path)
    return value if isinstance(value, list) else []


def _count_style_sections(style_payload: dict[str, Any]) -> dict[str, Any]:
    list_counts = {path: len(_payload_list(style_payload, path)) for path in STYLE_LIST_SECTION_PATHS}
    scalar_presence = {path: bool(_nested_payload_value(style_payload, path)) for path in STYLE_SCALAR_SECTION_PATHS}
    return {
        "list_counts": list_counts,
        "scalar_presence": scalar_presence,
        "total_list_items": sum(list_counts.values()),
        "present_scalar_count": sum(1 for present in scalar_presence.values() if present),
    }


def _fallback_world_graph_counts(world_graph_root: Path) -> dict[str, Any]:
    return {
        "nodes": len(read_jsonl(world_graph_root / "world_graph_nodes.jsonl"))
        if (world_graph_root / "world_graph_nodes.jsonl").exists()
        else 0,
        "edges": len(read_jsonl(world_graph_root / "world_graph_edges.jsonl"))
        if (world_graph_root / "world_graph_edges.jsonl").exists()
        else 0,
        "communities": len(read_jsonl(world_graph_root / "world_graph_communities.jsonl"))
        if (world_graph_root / "world_graph_communities.jsonl").exists()
        else 0,
        "node_summaries": len(read_jsonl(world_graph_root / "world_graph_node_summaries.jsonl"))
        if (world_graph_root / "world_graph_node_summaries.jsonl").exists()
        else 0,
    }


def _build_markdown_report(contract: dict[str, Any]) -> str:
    style_lane = contract.get("style_lane", {})
    world_lane = contract.get("world_lane", {})
    hybrid_policy = contract.get("hybrid_policy", {})
    style_counts = style_lane.get("section_counts", {})
    world_counts = world_lane.get("graph_counts", {})

    lines = [
        "# Hybrid RAG 检索契约",
        "",
        f"- 生成时间: {contract.get('generated_at', '')}",
        f"- schema_version: {contract.get('schema_version', '')}",
        f"- style_id: {style_lane.get('style_id', '')}",
        "",
        "## Style Lane",
        "",
        f"- 总 list 规则数: {style_counts.get('total_list_items', 0)}",
        f"- 有效 scalar 数: {style_counts.get('present_scalar_count', 0)}",
        f"- 支持查询模式: {', '.join(style_lane.get('supported_query_modes', []))}",
        "",
        "## World Lane",
        "",
        f"- nodes: {world_counts.get('nodes', 0)}",
        f"- edges: {world_counts.get('edges', 0)}",
        f"- communities: {world_counts.get('communities', 0)}",
        f"- node_summaries: {world_counts.get('node_summaries', 0)}",
        f"- 支持查询模式: {', '.join(world_lane.get('supported_query_modes', []))}",
        "",
        "## Hybrid Policy",
        "",
        f"- 默认路由: {hybrid_policy.get('default_route', '')}",
        f"- 合并顺序: {', '.join(hybrid_policy.get('merge_order', []))}",
        f"- 运行时输入: {', '.join(hybrid_policy.get('runtime_inputs', []))}",
        f"- 运行时输出: {', '.join(hybrid_policy.get('runtime_outputs', []))}",
        "",
        "## Notes",
        "",
    ]
    for note in hybrid_policy.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def build_hybrid_rag_contract(
    style_bible_dir: str | Path,
    world_graph_dir: str | Path,
    output_dir: str | Path,
) -> HybridRAGContractBuildResult:
    style_root = Path(style_bible_dir).resolve()
    world_graph_root = Path(world_graph_dir).resolve()
    output_root = ensure_dir(output_dir).resolve()

    style_bible_path = style_root / STYLE_BIBLE_FILE
    if not style_bible_path.exists():
        raise FileNotFoundError(f"Style bible file not found: {style_bible_path}")
    world_graph_manifest_path = world_graph_root / WORLD_GRAPH_MANIFEST_FILE

    style_payload = read_json(style_bible_path)
    if not isinstance(style_payload, dict):
        raise ValueError(f"Style bible payload must be an object: {style_bible_path}")
    StyleBibleResultV2.model_validate(style_payload)

    world_graph_manifest = read_json(world_graph_manifest_path) if world_graph_manifest_path.exists() else {}
    if not isinstance(world_graph_manifest, dict):
        world_graph_manifest = {}

    style_counts = _count_style_sections(style_payload)
    world_graph_counts = world_graph_manifest.get("output_counts")
    if not isinstance(world_graph_counts, dict):
        world_graph_counts = _fallback_world_graph_counts(world_graph_root)

    contract = {
        "schema_version": HYBRID_RAG_CONTRACT_SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "style_lane": {
            "style_id": _clean_text(style_payload.get("style_id")),
            "scope": _clean_text(style_payload.get("scope")),
            "entry_files": {
                "style_bible": STYLE_BIBLE_FILE,
                "reasoning": REASONING_FILE,
                "reduce_trace": REDUCE_TRACE_FILE,
            },
            "supported_query_modes": [
                "style_rule_lookup",
                "voice_contract_lookup",
                "negative_rule_lookup",
                "routing_hint_lookup",
            ],
            "section_counts": style_counts,
            "notes": [
                "Style lane is responsible for how to write, how to route, and what to avoid.",
                "Style lane is not the authority layer for world facts or entity relationships.",
            ],
        },
        "world_lane": {
            "story_node_scope": world_graph_manifest.get("story_node_scope", {}),
            "entry_files": {
                "manifest": WORLD_GRAPH_MANIFEST_FILE,
                "nodes": "world_graph_nodes.jsonl",
                "edges": "world_graph_edges.jsonl",
                "communities": "world_graph_communities.jsonl",
                "node_summaries": "world_graph_node_summaries.jsonl",
                "alias_index": "world_graph_alias_index.json",
            },
            "supported_query_modes": [
                "entity_lookup",
                "relationship_lookup",
                "community_lookup",
                "world_rule_lookup",
            ],
            "graph_counts": world_graph_counts,
            "notes": [
                "World lane is responsible for entities, relationships, rule systems, and chapter/story-node communities.",
                "World lane is designed to remain GraphRAG-friendly and can later map onto Qdrant or other vector stores.",
            ],
        },
        "hybrid_policy": {
            "default_route": "hybrid",
            "merge_order": ["style", "world"],
            "runtime_inputs": [
                "query_text",
                "intent_hint",
                "story_node_id",
                "session_state_refs",
                "top_k",
            ],
            "runtime_outputs": [
                "route_decision",
                "style_hits",
                "world_hits",
                "merged_hits",
            ],
            "notes": [
                "Style and World are two retrieval lanes with distinct responsibilities.",
                "Runtime middleware should classify intent first, then retrieve from one lane or both.",
                "Qdrant is optional and belongs at the retrieval layer, not at the canonical asset layer.",
            ],
        },
    }

    contract_path = output_root / HYBRID_RAG_CONTRACT_FILE
    markdown_path = output_root / HYBRID_RAG_CONTRACT_MD_FILE
    write_json(contract_path, contract)
    write_markdown(markdown_path, _build_markdown_report(contract))

    return HybridRAGContractBuildResult(
        output_dir=output_root,
        contract_path=contract_path,
        markdown_path=markdown_path,
        contract=contract,
    )


__all__ = [
    "HYBRID_RAG_CONTRACT_FILE",
    "HYBRID_RAG_CONTRACT_MD_FILE",
    "HYBRID_RAG_CONTRACT_SCHEMA_VERSION",
    "HybridRAGContractBuildResult",
    "build_hybrid_rag_contract",
]
