from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.io_utils import read_json, read_jsonl, write_json, write_jsonl
from novel_pipeline_stable.world_graph_builder import build_world_graph
from novel_pipeline_stable.world_graph_graphrag_export import export_world_graph_graphrag


class WorldGraphGraphRAGExportTest(unittest.TestCase):
    def test_export_world_graph_graphrag_writes_byog_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            canon_dir = Path(tmpdir) / "canon"
            world_graph_dir = Path(tmpdir) / "world_graph"
            export_dir = Path(tmpdir) / "graphrag_export"
            canon_dir.mkdir(parents=True, exist_ok=True)

            write_jsonl(
                canon_dir / "entities.jsonl",
                [
                    {
                        "entity_id": "character_lin_qing",
                        "name": "Lin Qing",
                        "entity_type": "character",
                        "aliases": ["A Qing"],
                        "first_seen_chapter": "0001",
                        "supporting_scene_ids": ["scene_0001_001"],
                        "notes": ["Starts in the labor queue."],
                    },
                    {
                        "entity_id": "faction_outer_sect",
                        "name": "Outer Sect",
                        "entity_type": "faction",
                        "aliases": [],
                        "first_seen_chapter": "0001",
                        "supporting_scene_ids": ["scene_0001_001"],
                        "notes": ["Admission is tied to quota."],
                    },
                ],
            )
            write_jsonl(
                canon_dir / "facts.jsonl",
                [
                    {
                        "fact_id": "fact_outer_membership",
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "subject": "Lin Qing",
                        "predicate": "belongs_to",
                        "object": "Outer Sect",
                        "fact_type": "explicit",
                        "confidence": "high",
                    }
                ],
            )
            write_jsonl(
                canon_dir / "events.jsonl",
                [
                    {
                        "event_id": "event_shift",
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "name": "Mine Shift",
                        "summary": "Lin Qing is assigned to a quota-bound mine shift.",
                        "event_type": "labor_assignment",
                        "participants": ["Lin Qing"],
                        "location": "",
                        "outcomes": ["Keeps provisional admission status."],
                    }
                ],
            )
            write_jsonl(
                canon_dir / "relationship_changes.jsonl",
                [
                    {
                        "relationship_change_id": "rel_outer_dependency",
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "source": "Lin Qing",
                        "target": "Outer Sect",
                        "relation": "depends_on",
                        "change": "Moves from observer to dependent worker.",
                        "evidence": {},
                    }
                ],
            )
            write_jsonl(
                canon_dir / "power_system_notes.jsonl",
                [
                    {
                        "power_system_note_id": "power_quota_rule",
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "topic": "Outer Sect",
                        "note": "Quota completion gates continued admission.",
                        "evidence": {},
                    }
                ],
            )
            write_jsonl(
                canon_dir / "chapter_summaries.jsonl",
                [
                    {
                        "chapter_id": "0001",
                        "chapter_title": "Mine Entry",
                        "scene_count": 1,
                        "scene_summaries": ["Lin Qing enters the mine and faces quota rules."],
                        "open_questions": ["Can Lin Qing keep admission status?"],
                    }
                ],
            )
            write_jsonl(
                canon_dir / "plot_nodes_draft.jsonl",
                [
                    {
                        "node_id": "plot_node_0001",
                        "chapter_id": "0001",
                        "chapter_title": "Mine Entry",
                        "node_type": "chapter_draft",
                        "title": "Mine Entry",
                        "summary": "The mine quota becomes the first institutional gate.",
                        "event_names": ["Mine Shift"],
                        "event_ids": ["event_shift"],
                        "scene_ids": ["0001_001"],
                        "participants": ["Lin Qing"],
                        "locations": [],
                        "open_questions": ["Can Lin Qing keep admission status?"],
                        "plot_relevance_hint": "high",
                        "source": "derived_from_fact_extraction",
                    }
                ],
            )
            write_json(
                canon_dir / "canon_index.json",
                {
                    "entity_count": 2,
                    "fact_count": 1,
                    "event_count": 1,
                    "chapter_summary_count": 1,
                    "style_window_count": 0,
                    "plot_node_count": 1,
                    "relationship_change_count": 1,
                    "power_system_note_count": 1,
                },
            )

            build_world_graph(canon_dir, world_graph_dir)
            result = export_world_graph_graphrag(world_graph_dir, export_dir)

            entities = read_jsonl(result.entity_path)
            relationships = read_jsonl(result.relationship_path)
            text_units = read_jsonl(result.text_unit_path)
            community_reports = read_jsonl(result.community_report_path)
            manifest = read_json(result.manifest_path)

            self.assertGreaterEqual(len(entities), 4)
            self.assertGreaterEqual(len(relationships), 4)
            self.assertGreaterEqual(len(text_units), 4)
            self.assertGreaterEqual(len(community_reports), 2)

            self.assertTrue(any(row["graph_node_type"] == "entity" for row in entities))
            self.assertTrue(any(row["relationship_type"] == "fact_relation" for row in relationships))
            self.assertTrue(any(row["text"] for row in text_units))
            self.assertTrue(any(int(row["level"]) > 1 for row in community_reports))
            self.assertEqual(manifest["counts"]["entities"], len(entities))
            self.assertEqual(manifest["counts"]["relationships"], len(relationships))
            self.assertEqual(manifest["counts"]["text_units"], len(text_units))
            self.assertEqual(manifest["counts"]["community_reports"], len(community_reports))


if __name__ == "__main__":
    unittest.main()
