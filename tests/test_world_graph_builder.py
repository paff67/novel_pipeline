from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.io_utils import read_json, read_jsonl, write_json, write_jsonl
from novel_pipeline_stable.world_graph_builder import build_world_graph


class WorldGraphBuilderTest(unittest.TestCase):
    def test_build_world_graph_writes_offline_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            canon_dir = Path(tmpdir) / "canon"
            output_dir = Path(tmpdir) / "world_graph"
            canon_dir.mkdir(parents=True, exist_ok=True)

            write_jsonl(
                canon_dir / "entities.jsonl",
                [
                    {
                        "entity_id": "character_linqing",
                        "name": "林清",
                        "entity_type": "character",
                        "aliases": ["阿清"],
                        "first_seen_chapter": "0001",
                        "supporting_scene_ids": ["scene_0001_001"],
                        "notes": ["矿场出身，账压明显"],
                    },
                    {
                        "entity_id": "faction_outer",
                        "name": "外门",
                        "entity_type": "faction",
                        "aliases": [],
                        "first_seen_chapter": "0001",
                        "supporting_scene_ids": ["scene_0001_001"],
                        "notes": ["资格和配额挂钩"],
                    },
                    {
                        "entity_id": "location_mine",
                        "name": "矿场",
                        "entity_type": "location",
                        "aliases": [],
                        "first_seen_chapter": "0001",
                        "supporting_scene_ids": ["scene_0001_001"],
                        "notes": ["劳动与修行重叠"],
                    },
                ],
            )
            write_jsonl(
                canon_dir / "facts.jsonl",
                [
                    {
                        "fact_id": "fact_join_outer",
                        "chapter_id": "0001",
                        "scene_id": "scene_0001_001",
                        "subject": "林清",
                        "predicate": "隶属于",
                        "object": "外门",
                        "fact_type": "explicit",
                        "confidence": "high",
                    }
                ],
            )
            write_jsonl(
                canon_dir / "events.jsonl",
                [
                    {
                        "event_id": "event_mine_shift",
                        "chapter_id": "0001",
                        "scene_id": "scene_0001_001",
                        "name": "矿场轮值",
                        "summary": "林清被编入矿场轮值，开始按配额劳动。",
                        "event_type": "labor_assignment",
                        "participants": ["林清"],
                        "location": "矿场",
                        "outcomes": ["获得外门轮值资格"],
                    }
                ],
            )
            write_jsonl(
                canon_dir / "relationship_changes.jsonl",
                [
                    {
                        "relationship_change_id": "relchg_outer_dependency",
                        "chapter_id": "0001",
                        "scene_id": "scene_0001_001",
                        "source": "林清",
                        "target": "外门",
                        "relation": "依附",
                        "change": "从外围观察转为被制度正式吸纳",
                        "evidence": {},
                    }
                ],
            )
            write_jsonl(
                canon_dir / "power_system_notes.jsonl",
                [
                    {
                        "power_system_note_id": "power_note_quota",
                        "chapter_id": "0001",
                        "scene_id": "scene_0001_001",
                        "topic": "外门",
                        "note": "外门资格与矿工配额直接绑定，欠配额会失去资格。",
                        "evidence": {},
                    }
                ],
            )
            write_jsonl(
                canon_dir / "chapter_summaries.jsonl",
                [
                    {
                        "chapter_id": "0001",
                        "chapter_title": "矿场入门",
                        "scene_count": 1,
                        "scene_summaries": ["林清进入矿场，开始按配额劳动。"],
                        "open_questions": ["如何保住外门资格"],
                    }
                ],
            )
            write_jsonl(
                canon_dir / "plot_nodes_draft.jsonl",
                [
                    {
                        "node_id": "plot_node_ch0001",
                        "chapter_id": "0001",
                        "chapter_title": "矿场入门",
                        "node_type": "chapter_draft",
                        "title": "矿场入门",
                        "summary": "矿场配额与外门资格绑定，生存压力成为修行入口。",
                        "event_names": ["矿场轮值"],
                        "event_ids": ["event_mine_shift"],
                        "scene_ids": ["scene_0001_001"],
                        "participants": ["林清"],
                        "locations": ["矿场"],
                        "open_questions": ["如何保住外门资格"],
                        "plot_relevance_hint": "high",
                        "source": "derived_from_fact_extraction",
                    }
                ],
            )
            write_json(
                canon_dir / "canon_index.json",
                {
                    "entity_count": 3,
                    "fact_count": 1,
                    "event_count": 1,
                    "chapter_summary_count": 1,
                    "style_window_count": 0,
                    "plot_node_count": 1,
                    "relationship_change_count": 1,
                    "power_system_note_count": 1,
                },
            )
            write_json(
                canon_dir / "story_node_scope.json",
                {
                    "scope_type": "story_node",
                    "node_id": "main_01",
                    "label": "一层阶段",
                    "start_chapter": "0001",
                    "end_chapter": "0270",
                },
            )

            result = build_world_graph(canon_dir, output_dir)

            nodes = read_jsonl(result.node_path)
            edges = read_jsonl(result.edge_path)
            communities = read_jsonl(result.community_path)
            node_summaries = read_jsonl(result.node_summary_path)
            manifest = read_json(result.manifest_path)

            self.assertGreaterEqual(len(nodes), 6)
            self.assertGreaterEqual(len(edges), 6)
            self.assertEqual(len(communities), 2)
            self.assertEqual(len(node_summaries), len(nodes))

            node_types = {row["node_type"] for row in nodes}
            self.assertIn("entity", node_types)
            self.assertIn("fact", node_types)
            self.assertIn("event", node_types)
            self.assertIn("plot_node", node_types)
            self.assertIn("power_rule", node_types)

            edge_types = {row["edge_type"] for row in edges}
            self.assertIn("fact_relation", edge_types)
            self.assertIn("relationship_change", edge_types)
            self.assertIn("participates_in", edge_types)
            self.assertIn("contains_event", edge_types)

            community_types = {row["community_type"] for row in communities}
            self.assertIn("chapter_scope", community_types)
            self.assertIn("story_node_scope", community_types)

            self.assertEqual(manifest["output_counts"]["nodes"], len(nodes))
            self.assertEqual(manifest["output_counts"]["edges"], len(edges))
            self.assertEqual(manifest["output_counts"]["communities"], len(communities))
            self.assertEqual(manifest["story_node_scope"]["node_id"], "main_01")
            self.assertEqual(manifest["output_counts"]["community_type_counts"]["story_node_scope"], 1)


if __name__ == "__main__":
    unittest.main()
