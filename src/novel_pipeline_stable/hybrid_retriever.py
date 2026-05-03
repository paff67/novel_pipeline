from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novel_pipeline_stable.config import StableProjectConfig
from novel_pipeline_stable.embedding_client import StableOpenAICompatibleEmbeddingClient
from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_markdown
from novel_pipeline_stable.models import StyleBibleResultV2
from novel_pipeline_stable.project_domain_vocabulary import load_project_domain_vocabulary
from novel_pipeline_stable.style_bible_judge import _semantic_similarity


STYLE_BIBLE_FILE = "style_bible_final.json"
HYBRID_RETRIEVAL_PROBE_JSON_FILE = "hybrid_retrieval_probe.json"
HYBRID_RETRIEVAL_PROBE_MD_FILE = "hybrid_retrieval_probe.md"

DEFAULT_PROJECT_DOMAIN_VOCABULARY = load_project_domain_vocabulary()
STYLE_ROUTE_CUES = DEFAULT_PROJECT_DOMAIN_VOCABULARY.route_terms("style")
WORLD_ROUTE_CUES = DEFAULT_PROJECT_DOMAIN_VOCABULARY.route_terms("world")


@dataclass(slots=True)
class RetrievalHit:
    lane: str
    hit_id: str
    category: str
    title: str
    text: str
    score: float
    lexical_score: float
    embedding_score: float
    source_ref: str
    evidence_refs: list[str]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "hit_id": self.hit_id,
            "category": self.category,
            "title": self.title,
            "text": self.text,
            "score": round(float(self.score), 4),
            "lexical_score": round(float(self.lexical_score), 4),
            "embedding_score": round(float(self.embedding_score), 4),
            "source_ref": self.source_ref,
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class HybridRetrievalResult:
    query: str
    route_decision: str
    route_reason: str
    style_hits: list[RetrievalHit]
    world_hits: list[RetrievalHit]
    merged_hits: list[RetrievalHit]
    route_debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "route_decision": self.route_decision,
            "route_reason": self.route_reason,
            "route_debug": dict(self.route_debug),
            "style_hits": [hit.to_dict() for hit in self.style_hits],
            "world_hits": [hit.to_dict() for hit in self.world_hits],
            "merged_hits": [hit.to_dict() for hit in self.merged_hits],
        }


@dataclass(slots=True)
class HybridRetrievalProbeResult:
    output_dir: Path
    report_path: Path
    markdown_path: Path
    report: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _first_nonempty(*values: Any) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ""


def _score_with_embedding(query_vector: list[float], candidate_vector: list[float]) -> float:
    if not query_vector or not candidate_vector:
        return 0.0
    if len(query_vector) != len(candidate_vector):
        return 0.0
    numerator = sum(left * right for left, right in zip(query_vector, candidate_vector, strict=True))
    left_norm = math.sqrt(sum(value * value for value in query_vector))
    right_norm = math.sqrt(sum(value * value for value in candidate_vector))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


def _route_query(query: str) -> tuple[str, str, dict[str, Any]]:
    lowered = _clean_text(query).casefold()
    matched_style_cues = [cue for cue in STYLE_ROUTE_CUES if cue.casefold() in lowered]
    matched_world_cues = [cue for cue in WORLD_ROUTE_CUES if cue.casefold() in lowered]
    style_score = len(matched_style_cues)
    world_score = len(matched_world_cues)
    route_debug = {
        "lexical_prior_config": str(DEFAULT_PROJECT_DOMAIN_VOCABULARY.source_path),
        "lexical_prior_score": {
            "style": style_score,
            "world": world_score,
        },
        "matched_vocab_ids": [
            *(f"route:style:{cue}" for cue in matched_style_cues[:8]),
            *(f"route:world:{cue}" for cue in matched_world_cues[:8]),
        ],
        "matched_cues": {
            "style": matched_style_cues[:8],
            "world": matched_world_cues[:8],
        },
        "final_decision_source": "lexical_prior",
    }
    if style_score > 0 and world_score == 0:
        return "style", "Matched style-oriented query cues.", route_debug
    if world_score > 0 and style_score == 0:
        return "world", "Matched world-oriented query cues.", route_debug
    if style_score > 0 or world_score > 0:
        return "hybrid", "Query mixes style and world cues.", route_debug
    return "hybrid", "No strong single-lane cue detected; using hybrid fallback.", route_debug


class _OfflineRetrieverBase:
    def __init__(
        self,
        lane: str,
        candidates: list[dict[str, Any]],
        *,
        config: StableProjectConfig | None = None,
        artifacts_dir: str | Path | None = None,
    ) -> None:
        self.lane = lane
        self.candidates = candidates
        self.config = config
        self.artifacts_dir = Path(artifacts_dir).resolve() if artifacts_dir else None
        self.retrieval_top_k = 12
        self.embedding_client: StableOpenAICompatibleEmbeddingClient | None = None
        if config is not None:
            self.retrieval_top_k = max(int(config.embedding.retrieval_top_k), 1)
            if config.embedding.enabled:
                self.embedding_client = StableOpenAICompatibleEmbeddingClient(
                    config,
                    artifacts_dir=self.artifacts_dir or Path.cwd(),
                )

    def search(self, query: str, *, top_k: int = 6) -> list[RetrievalHit]:
        normalized_query = _clean_text(query)
        if not normalized_query:
            return []

        lexical_rows: list[dict[str, Any]] = []
        for candidate in self.candidates:
            search_text = _clean_text(candidate.get("search_text"))
            if not search_text:
                continue
            lexical_score = _semantic_similarity(normalized_query, search_text)
            if lexical_score <= 0.0:
                continue
            lexical_rows.append({"candidate": candidate, "lexical_score": lexical_score})

        lexical_rows.sort(
            key=lambda row: (
                float(row["lexical_score"]),
                _clean_text(row["candidate"].get("title")),
                _clean_text(row["candidate"].get("hit_id")),
            ),
            reverse=True,
        )
        candidate_budget = max(int(top_k) * 4, self.retrieval_top_k)
        shortlisted = lexical_rows[:candidate_budget]
        if not shortlisted:
            return []

        embedding_scores: dict[str, float] = {}
        if self.embedding_client is not None:
            batch_texts = [normalized_query, *[_clean_text(row["candidate"].get("search_text")) for row in shortlisted]]
            response = self.embedding_client.embed_texts(
                request_key=f"{self.lane}_retrieval_rerank",
                texts=batch_texts,
            )
            query_vector = response.vectors[0]
            for row, candidate_vector in zip(shortlisted, response.vectors[1:], strict=True):
                hit_id = _clean_text(row["candidate"].get("hit_id"))
                embedding_scores[hit_id] = _score_with_embedding(query_vector, candidate_vector)

        hits: list[RetrievalHit] = []
        for row in shortlisted:
            candidate = row["candidate"]
            lexical_score = float(row["lexical_score"])
            hit_id = _clean_text(candidate.get("hit_id"))
            embedding_score = float(embedding_scores.get(hit_id, 0.0))
            final_score = lexical_score if not embedding_scores else round((0.58 * lexical_score) + (0.42 * embedding_score), 4)
            hits.append(
                RetrievalHit(
                    lane=self.lane,
                    hit_id=hit_id,
                    category=_clean_text(candidate.get("category")),
                    title=_clean_text(candidate.get("title")),
                    text=_clean_text(candidate.get("text")),
                    score=final_score,
                    lexical_score=lexical_score,
                    embedding_score=embedding_score,
                    source_ref=_clean_text(candidate.get("source_ref")),
                    evidence_refs=list(candidate.get("evidence_refs", []) or []),
                    metadata=dict(candidate.get("metadata", {})),
                )
            )

        hits.sort(key=lambda hit: (hit.score, hit.title, hit.hit_id), reverse=True)
        return hits[: max(int(top_k), 1)]


class StyleRetriever(_OfflineRetrieverBase):
    def __init__(
        self,
        style_bible_dir: str | Path,
        *,
        config: StableProjectConfig | None = None,
        artifacts_dir: str | Path | None = None,
    ) -> None:
        style_root = Path(style_bible_dir).resolve()
        style_bible_path = style_root / STYLE_BIBLE_FILE
        if not style_bible_path.exists():
            raise FileNotFoundError(f"Style bible file not found: {style_bible_path}")
        payload = read_json(style_bible_path)
        if not isinstance(payload, dict):
            raise ValueError(f"Style bible payload must be an object: {style_bible_path}")
        style_bible = StyleBibleResultV2.model_validate(payload)
        candidates = self._collect_candidates(style_bible.model_dump(mode="json", by_alias=True))
        super().__init__("style", candidates, config=config, artifacts_dir=artifacts_dir)

    @staticmethod
    def _append_rule_candidate(
        candidates: list[dict[str, Any]],
        *,
        category: str,
        item: dict[str, Any],
    ) -> None:
        text = _clean_text(item.get("text"))
        if not text:
            return
        rule_id = _first_nonempty(item.get("rule_id"), f"{category}:{len(candidates) + 1:03d}")
        search_parts = [
            text,
            item.get("trigger"),
            item.get("constraint"),
            item.get("query_feature_matcher"),
            item.get("route_target_action"),
            item.get("forbidden_action"),
            item.get("correction_guideline"),
        ]
        title = _first_nonempty(item.get("rule_id"), category)
        evidence_refs = [_clean_text(ref) for ref in list(item.get("evidence_refs", []) or []) if _clean_text(ref)]
        candidates.append(
            {
                "hit_id": f"{category}:{rule_id}",
                "category": category,
                "title": title,
                "text": text,
                "search_text": " | ".join(part for part in map(_clean_text, search_parts) if part),
                "source_ref": evidence_refs[0] if evidence_refs else "",
                "evidence_refs": evidence_refs,
                "metadata": {
                    "reasoning_ref": _clean_text(item.get("_reasoning_ref") or item.get("reasoning_ref")),
                },
            }
        )

    @classmethod
    def _collect_candidates(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        list_paths = (
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
        scalar_paths = (
            "narrative_system.perspective",
            "narrative_system.distance",
            "narrative_system.temporality",
            "voice_contract.narrator_voice",
            "voice_contract.inner_monologue_mode",
        )

        for path in list_paths:
            node: Any = payload
            for part in path.split("."):
                if not isinstance(node, dict):
                    node = []
                    break
                node = node.get(part, [])
            if not isinstance(node, list):
                continue
            for item in node:
                if isinstance(item, dict):
                    cls._append_rule_candidate(candidates, category=path, item=item)

        for path in scalar_paths:
            node = payload
            for part in path.split("."):
                if not isinstance(node, dict):
                    node = None
                    break
                node = node.get(part)
            if isinstance(node, dict):
                cls._append_rule_candidate(candidates, category=path, item=node)

        return candidates


class WorldGraphRetriever(_OfflineRetrieverBase):
    def __init__(
        self,
        world_graph_dir: str | Path,
        *,
        config: StableProjectConfig | None = None,
        artifacts_dir: str | Path | None = None,
    ) -> None:
        world_root = Path(world_graph_dir).resolve()
        nodes_path = world_root / "world_graph_nodes.jsonl"
        edges_path = world_root / "world_graph_edges.jsonl"
        communities_path = world_root / "world_graph_communities.jsonl"
        summaries_path = world_root / "world_graph_node_summaries.jsonl"
        if not nodes_path.exists():
            raise FileNotFoundError(f"World graph nodes file not found: {nodes_path}")
        node_rows = [row for row in read_jsonl(nodes_path) if isinstance(row, dict)]
        edge_rows = [row for row in read_jsonl(edges_path)] if edges_path.exists() else []
        community_rows = [row for row in read_jsonl(communities_path)] if communities_path.exists() else []
        summary_rows = [row for row in read_jsonl(summaries_path)] if summaries_path.exists() else []
        candidates = self._collect_candidates(
            [row for row in node_rows if isinstance(row, dict)],
            [row for row in edge_rows if isinstance(row, dict)],
            [row for row in community_rows if isinstance(row, dict)],
            [row for row in summary_rows if isinstance(row, dict)],
        )
        super().__init__("world", candidates, config=config, artifacts_dir=artifacts_dir)

    @staticmethod
    def _collect_candidates(
        node_rows: list[dict[str, Any]],
        edge_rows: list[dict[str, Any]],
        community_rows: list[dict[str, Any]],
        summary_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        title_lookup = {
            _clean_text(row.get("node_id")): _first_nonempty(row.get("title"), row.get("node_id"))
            for row in node_rows
            if _clean_text(row.get("node_id"))
        }

        for row in node_rows:
            node_id = _clean_text(row.get("node_id"))
            title = _first_nonempty(row.get("title"), node_id)
            text = _first_nonempty(row.get("summary"), title)
            candidates.append(
                {
                    "hit_id": f"node:{node_id}",
                    "category": _first_nonempty(row.get("node_type"), "node"),
                    "title": title,
                    "text": text,
                    "search_text": " | ".join(
                        part
                        for part in (
                            row.get("title"),
                            row.get("summary"),
                            row.get("entity_type"),
                            ", ".join(list(row.get("aliases", []) or [])),
                        )
                        if _clean_text(part)
                    ),
                    "source_ref": _first_nonempty(row.get("scene_id"), row.get("chapter_id"), row.get("story_node_id")),
                    "evidence_refs": [],
                    "metadata": {"node_type": _clean_text(row.get("node_type"))},
                }
            )

        for row in summary_rows:
            summary_id = _clean_text(row.get("summary_id"))
            title = _first_nonempty(row.get("title"), row.get("node_id"), summary_id)
            text = _clean_text(row.get("retrieval_text"))
            if not text:
                continue
            candidates.append(
                {
                    "hit_id": f"node_summary:{summary_id}",
                    "category": "node_summary",
                    "title": title,
                    "text": text,
                    "search_text": text,
                    "source_ref": _first_nonempty(row.get("chapter_id"), row.get("story_node_id")),
                    "evidence_refs": [],
                    "metadata": {"node_id": _clean_text(row.get("node_id"))},
                }
            )

        for row in edge_rows:
            edge_id = _clean_text(row.get("edge_id"))
            source_id = _clean_text(row.get("source_id"))
            target_id = _clean_text(row.get("target_id"))
            source_title = title_lookup.get(source_id, source_id)
            target_title = title_lookup.get(target_id, target_id)
            relation_label = _clean_text(row.get("relation_label"))
            support_text = _clean_text(row.get("support_text"))
            text = " | ".join(part for part in (source_title, relation_label, target_title, support_text) if part)
            if not text:
                continue
            candidates.append(
                {
                    "hit_id": f"edge:{edge_id}",
                    "category": _first_nonempty(row.get("edge_type"), "edge"),
                    "title": f"{source_title} -> {target_title}",
                    "text": text,
                    "search_text": text,
                    "source_ref": _first_nonempty(row.get("scene_id"), row.get("chapter_id"), row.get("story_node_id")),
                    "evidence_refs": [],
                    "metadata": {"relation_label": relation_label},
                }
            )

        for row in community_rows:
            community_id = _clean_text(row.get("community_id"))
            title = _first_nonempty(row.get("title"), community_id)
            text = _first_nonempty(row.get("summary"), title)
            members = ", ".join(list(row.get("member_node_ids", []) or [])[:10])
            search_text = " | ".join(part for part in (title, text, members) if part)
            candidates.append(
                {
                    "hit_id": f"community:{community_id}",
                    "category": _first_nonempty(row.get("community_type"), "community"),
                    "title": title,
                    "text": text,
                    "search_text": search_text,
                    "source_ref": _first_nonempty(row.get("chapter_id"), row.get("story_node_id"), community_id),
                    "evidence_refs": [],
                    "metadata": {
                        "member_count": len(list(row.get("member_node_ids", []) or [])),
                        "community_type": _clean_text(row.get("community_type")),
                    },
                }
            )

        return candidates


class HybridRetriever:
    def __init__(
        self,
        style_bible_dir: str | Path,
        world_graph_dir: str | Path,
        *,
        config: StableProjectConfig | None = None,
        artifacts_dir: str | Path | None = None,
    ) -> None:
        self.style_bible_dir = Path(style_bible_dir).resolve()
        self.world_graph_dir = Path(world_graph_dir).resolve()
        self.config = config
        self.artifacts_root = Path(artifacts_dir).resolve() if artifacts_dir else (self.style_bible_dir.parent / "_hybrid_retriever")
        self._style_retriever: StyleRetriever | None = None
        self._world_retriever: WorldGraphRetriever | None = None

    def _get_style_retriever(self) -> StyleRetriever:
        if self._style_retriever is None:
            self._style_retriever = StyleRetriever(
                self.style_bible_dir,
                config=self.config,
                artifacts_dir=self.artifacts_root / "style_lane",
            )
        return self._style_retriever

    def _get_world_retriever(self) -> WorldGraphRetriever:
        if self._world_retriever is None:
            self._world_retriever = WorldGraphRetriever(
                self.world_graph_dir,
                config=self.config,
                artifacts_dir=self.artifacts_root / "world_lane",
            )
        return self._world_retriever

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 8,
        route_override: str = "",
    ) -> HybridRetrievalResult:
        route_decision, route_reason, route_debug = _route_query(query)
        if _clean_text(route_override) in {"style", "world", "hybrid"}:
            route_decision = _clean_text(route_override)
            route_reason = "Route override provided by caller."
            route_debug = {
                **route_debug,
                "final_decision_source": "route_override",
                "route_override": route_decision,
            }

        style_hits: list[RetrievalHit] = []
        world_hits: list[RetrievalHit] = []
        skipped_lanes: list[str] = []
        if route_decision in {"style", "hybrid"}:
            style_hits = self._get_style_retriever().search(query, top_k=max(int(top_k), 1))
        else:
            skipped_lanes.append("style")
        if route_decision in {"world", "hybrid"}:
            world_hits = self._get_world_retriever().search(query, top_k=max(int(top_k), 1))
        else:
            skipped_lanes.append("world")

        if route_decision == "style":
            merged_hits = list(style_hits)
        elif route_decision == "world":
            merged_hits = list(world_hits)
        else:
            combined = [*style_hits, *world_hits]
            combined.sort(key=lambda hit: (hit.score, hit.title, hit.hit_id), reverse=True)
            merged_hits = combined[: max(int(top_k), 1)]
        route_debug = {
            **route_debug,
            "skipped_lanes": skipped_lanes,
        }

        return HybridRetrievalResult(
            query=_clean_text(query),
            route_decision=route_decision,
            route_reason=route_reason,
            style_hits=style_hits,
            world_hits=world_hits,
            merged_hits=merged_hits,
            route_debug=route_debug,
        )


def _build_markdown_report(report: dict[str, Any]) -> str:
    route_debug = report.get("route_debug", {}) if isinstance(report.get("route_debug"), dict) else {}
    lines = [
        "# Hybrid Retriever Probe",
        "",
        f"- generated_at: {report.get('generated_at', '')}",
        f"- query: {report.get('query', '')}",
        f"- route_decision: {report.get('route_decision', '')}",
        f"- route_reason: {report.get('route_reason', '')}",
        f"- route_source: {route_debug.get('final_decision_source', '')}",
        "",
        "## Merged Hits",
        "",
    ]
    for hit in report.get("merged_hits", []):
        lines.append(
            f"- [{hit.get('lane', '')}] {hit.get('title', '')} | score={hit.get('score', 0.0)} | {hit.get('text', '')}"
        )
    lines.append("")
    return "\n".join(lines)


def run_hybrid_retrieval_probe(
    *,
    query: str,
    style_bible_dir: str | Path,
    world_graph_dir: str | Path,
    output_dir: str | Path,
    config: StableProjectConfig | None = None,
    route_override: str = "",
    top_k: int = 8,
) -> HybridRetrievalProbeResult:
    output_root = ensure_dir(output_dir).resolve()
    retriever = HybridRetriever(
        style_bible_dir,
        world_graph_dir,
        config=config,
        artifacts_dir=output_root / "_artifacts",
    )
    result = retriever.retrieve(query, top_k=top_k, route_override=route_override)
    report = {
        "generated_at": _utc_now_iso(),
        **result.to_dict(),
    }

    report_path = output_root / HYBRID_RETRIEVAL_PROBE_JSON_FILE
    markdown_path = output_root / HYBRID_RETRIEVAL_PROBE_MD_FILE
    write_json(report_path, report)
    write_markdown(markdown_path, _build_markdown_report(report))
    return HybridRetrievalProbeResult(
        output_dir=output_root,
        report_path=report_path,
        markdown_path=markdown_path,
        report=report,
    )


__all__ = [
    "HYBRID_RETRIEVAL_PROBE_JSON_FILE",
    "HYBRID_RETRIEVAL_PROBE_MD_FILE",
    "HybridRetriever",
    "HybridRetrievalProbeResult",
    "HybridRetrievalResult",
    "RetrievalHit",
    "StyleRetriever",
    "WorldGraphRetriever",
    "run_hybrid_retrieval_probe",
]
