from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.hybrid_retriever import HybridRetriever, run_hybrid_retrieval_probe
from novel_pipeline_stable.io_utils import read_json, write_json, write_jsonl


class HybridRetrieverTest(unittest.TestCase):
    def _prepare_assets(self, root: Path) -> tuple[Path, Path]:
        style_dir = root / "style_bible"
        world_dir = root / "world_graph"
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
                            "text": "书写审批通知和入门流程时，使用冷面手续化口吻推进动作。",
                            "trigger": "当审批通知或入门流程出现时",
                            "constraint": "保持冷面、手续化、可执行的叙述口吻",
                            "_reasoning_ref": "reasoning_engine_01",
                            "evidence_refs": ["scene:0001_001"],
                        }
                    ]
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
                            "text": "当配额入门通知出现时，路由到入门清单。",
                            "query_feature_matcher": "配额入门通知，入门清单",
                            "route_target_action": "路由到入门清单",
                            "_reasoning_ref": "reasoning_routing_01",
                            "evidence_refs": ["scene:0001_001"],
                        }
                    ]
                },
            },
        )
        write_jsonl(
            world_dir / "world_graph_nodes.jsonl",
            [
                {
                    "node_id": "entity_outer_sect",
                    "node_type": "entity",
                    "entity_type": "faction",
                    "title": "外门",
                    "summary": "外门资格依赖配额劳动和筛选规则。",
                    "aliases": [],
                    "chapter_id": "0001",
                    "story_node_id": "main_01",
                },
                {
                    "node_id": "power_rule_quota",
                    "node_type": "power_rule",
                    "title": "配额规则",
                    "summary": "完成配额才能维持外门资格。",
                    "chapter_id": "0001",
                    "story_node_id": "main_01",
                },
            ],
        )
        write_jsonl(
            world_dir / "world_graph_edges.jsonl",
            [
                {
                    "edge_id": "edge_quota",
                    "edge_type": "about_topic",
                    "source_id": "power_rule_quota",
                    "target_id": "entity_outer_sect",
                    "relation_label": "资格规则",
                    "support_text": "完成配额才能维持外门资格。",
                    "chapter_id": "0001",
                    "story_node_id": "main_01",
                }
            ],
        )
        write_jsonl(
            world_dir / "world_graph_communities.jsonl",
            [
                {
                    "community_id": "community_0001",
                    "community_type": "chapter_scope",
                    "title": "矿场入门",
                    "summary": "配额劳动成为第一道制度门槛。",
                    "chapter_id": "0001",
                    "story_node_id": "main_01",
                    "member_node_ids": ["entity_outer_sect", "power_rule_quota"],
                    "edge_ids": ["edge_quota"],
                },
                {
                    "community_id": "community_scope",
                    "community_type": "story_node_scope",
                    "title": "主线阶段",
                    "summary": "开篇阶段里，入门资格、配额和制度筛选共同决定生存。",
                    "chapter_id": "0001",
                    "story_node_id": "main_01",
                    "member_node_ids": ["entity_outer_sect", "power_rule_quota"],
                    "edge_ids": ["edge_quota"],
                },
            ],
        )
        write_jsonl(
            world_dir / "world_graph_node_summaries.jsonl",
            [
                {
                    "summary_id": "summary_outer_sect",
                    "node_id": "entity_outer_sect",
                    "node_type": "entity",
                    "title": "外门",
                    "chapter_id": "0001",
                    "story_node_id": "main_01",
                    "retrieval_text": "外门 | 外门资格依赖配额劳动和筛选规则。",
                }
            ],
        )
        return style_dir, world_dir

    def test_hybrid_retriever_routes_style_and_world_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            style_dir, world_dir = self._prepare_assets(Path(tmpdir))
            retriever = HybridRetriever(style_dir, world_dir)

            style_result = retriever.retrieve("这种审批通知应该怎么写，语气要冷面一点", top_k=3)
            world_result = retriever.retrieve("外门资格和配额规则是什么", top_k=3)

            self.assertEqual(style_result.route_decision, "style")
            self.assertEqual(style_result.route_debug["final_decision_source"], "lexical_prior")
            self.assertTrue(style_result.route_debug["matched_vocab_ids"])
            self.assertTrue(style_result.merged_hits)
            self.assertEqual(style_result.merged_hits[0].lane, "style")
            self.assertEqual(style_result.world_hits, [])
            self.assertIn("world", style_result.route_debug["skipped_lanes"])

            self.assertEqual(world_result.route_decision, "world")
            self.assertEqual(world_result.route_debug["final_decision_source"], "lexical_prior")
            self.assertTrue(world_result.route_debug["matched_vocab_ids"])
            self.assertTrue(world_result.merged_hits)
            self.assertEqual(world_result.merged_hits[0].lane, "world")
            self.assertEqual(world_result.style_hits, [])
            self.assertIn("style", world_result.route_debug["skipped_lanes"])

    def test_single_route_does_not_require_non_target_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            style_dir, _world_dir = self._prepare_assets(root)
            missing_world_dir = root / "missing_world_graph"
            retriever = HybridRetriever(style_dir, missing_world_dir)

            result = retriever.retrieve("这种审批通知应该怎么写，语气要冷面一点", top_k=3, route_override="style")

            self.assertEqual(result.route_decision, "style")
            self.assertTrue(result.merged_hits)
            self.assertEqual(result.world_hits, [])
            self.assertIn("world", result.route_debug["skipped_lanes"])

    def test_run_hybrid_retrieval_probe_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            style_dir, world_dir = self._prepare_assets(root)
            output_dir = root / "probe"

            result = run_hybrid_retrieval_probe(
                query="审批通知出现时要怎么写外门资格规则",
                style_bible_dir=style_dir,
                world_graph_dir=world_dir,
                output_dir=output_dir,
                top_k=4,
            )
            report = read_json(result.report_path)

            self.assertEqual(report["route_decision"], "hybrid")
            self.assertEqual(report["route_debug"]["final_decision_source"], "lexical_prior")
            self.assertGreaterEqual(len(report["style_hits"]), 1)
            self.assertGreaterEqual(len(report["world_hits"]), 1)
            self.assertGreaterEqual(len(report["merged_hits"]), 2)


if __name__ == "__main__":
    unittest.main()
