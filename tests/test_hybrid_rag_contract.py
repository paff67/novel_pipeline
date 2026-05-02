from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.hybrid_rag_contract import build_hybrid_rag_contract
from novel_pipeline_stable.io_utils import read_json, write_json, write_jsonl


class HybridRAGContractTest(unittest.TestCase):
    def test_build_hybrid_rag_contract_writes_style_world_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            style_dir = Path(tmpdir) / "style_bible"
            world_dir = Path(tmpdir) / "world_graph"
            output_dir = Path(tmpdir) / "contract"
            style_dir.mkdir(parents=True, exist_ok=True)
            world_dir.mkdir(parents=True, exist_ok=True)

            write_json(
                style_dir / "style_bible_final.json",
                {
                    "style_id": "style.demo",
                    "scope": "demo",
                    "narrative_system": {
                        "engine": [
                            {
                                "rule_id": "engine_01",
                                "text": "Use a deadpan procedural voice when describing quota pressure.",
                                "trigger": "when quota pressure appears",
                                "constraint": "keep the narration cold and procedural",
                                "_reasoning_ref": "reasoning_engine_01",
                                "evidence_refs": ["scene:0001_001"],
                            }
                        ],
                        "perspective": {
                            "rule_id": "perspective_01",
                            "text": "close_third_person",
                            "_reasoning_ref": "reasoning_perspective_01",
                            "evidence_refs": ["scene:0001_001"],
                        },
                    },
                    "voice_contract": {
                        "narrator_voice": {
                            "rule_id": "voice_01",
                            "text": "deadpan_procedural",
                            "_reasoning_ref": "reasoning_voice_01",
                            "evidence_refs": ["scene:0001_001"],
                        }
                    },
                    "worldbook_binding": {
                        "routing_hints": [
                            {
                                "rule_id": "routing_01",
                                "text": "When an approval notice appears, route to admissions checklist.",
                                "query_feature_matcher": "approval notice, admissions checklist",
                                "route_target_action": "route to admissions checklist",
                                "_reasoning_ref": "reasoning_routing_01",
                                "evidence_refs": ["scene:0001_001"],
                            }
                        ]
                    },
                },
            )
            write_jsonl(world_dir / "world_graph_nodes.jsonl", [{"node_id": "entity_outer_sect", "title": "Outer Sect"}])
            write_jsonl(world_dir / "world_graph_edges.jsonl", [])
            write_jsonl(world_dir / "world_graph_communities.jsonl", [])
            write_jsonl(world_dir / "world_graph_node_summaries.jsonl", [])
            write_json(
                world_dir / "world_graph_manifest.json",
                {
                    "story_node_scope": {"node_id": "main_01", "label": "Main Arc"},
                    "output_counts": {"nodes": 1, "edges": 0, "communities": 0, "node_summaries": 0},
                },
            )

            result = build_hybrid_rag_contract(style_dir, world_dir, output_dir)
            contract = read_json(result.contract_path)

            self.assertEqual(contract["schema_version"], 1)
            self.assertEqual(contract["style_lane"]["style_id"], "style.demo")
            self.assertIn("style_rule_lookup", contract["style_lane"]["supported_query_modes"])
            self.assertEqual(contract["world_lane"]["graph_counts"]["nodes"], 1)
            self.assertEqual(contract["world_lane"]["story_node_scope"]["node_id"], "main_01")
            self.assertEqual(contract["hybrid_policy"]["default_route"], "hybrid")


if __name__ == "__main__":
    unittest.main()
