from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.io_utils import read_json, read_jsonl, write_json
from novel_pipeline_stable.style_bible_ragas_eval import run_style_bible_semantic_eval


def _semantic_style_bible_payload() -> dict[str, object]:
    return {
        "style_id": "style.demo",
        "scope": "demo",
        "narrative_system": {},
        "expression_system": {},
        "aesthetics_system": {},
        "voice_contract": {},
        "character_arc_rules": [],
        "worldbook_binding": {
            "routing_hints": [
                {
                    "rule_id": "routing_01",
                    "text": "When an intake notice appears, route to the admissions checklist and quota gate.",
                    "query_feature_matcher": "intake notice, admissions checklist, quota gate",
                    "route_target_action": "route to admissions checklist and quota gate",
                    "_reasoning_ref": "reasoning_routing_01",
                    "evidence_refs": ["scene:0001_001"],
                }
            ],
            "worldbook_worthy": [
                {
                    "rule_id": "worldbook_01",
                    "text": "The Outer Sect keeps admission only if quota labor stays on schedule.",
                    "trigger": "outer sect admission and quota labor obligations",
                    "constraint": "Preserve the admission rule only while quota labor remains on schedule.",
                    "_reasoning_ref": "reasoning_worldbook_01",
                    "evidence_refs": ["scene:0001_001"],
                }
            ],
            "rag_worthy": [
                {
                    "rule_id": "rag_01",
                    "text": "Retrieve quota-gated admission rules whenever intake eligibility is questioned.",
                    "trigger": "intake eligibility disputes and quota-gated admission",
                    "constraint": "Fetch the quota and debt gate rules whenever eligibility becomes disputed.",
                    "_reasoning_ref": "reasoning_rag_01",
                    "evidence_refs": ["scene:0001_001"],
                }
            ],
        },
        "negative_rules": [],
        "supporting_evidence": [
            {
                "claim": "The sect ties admission to labor quota compliance.",
                "evidence_text": "The Outer Sect binds continued admission to labor quota completion.",
                "source_ref": "scene:0001_001",
            }
        ],
        "metadata": {"degradation_status": {"mode": "complete"}},
    }


class StyleBibleSemanticEvalTest(unittest.TestCase):
    def test_run_style_bible_semantic_eval_writes_rows_dataset_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            style_dir = Path(tmpdir) / "style_bible"
            output_dir = Path(tmpdir) / "semantic_eval"
            style_dir.mkdir(parents=True, exist_ok=True)

            write_json(style_dir / "style_bible_final.json", _semantic_style_bible_payload())
            write_json(
                style_dir / "style_bible_source_bundle.json",
                {
                    "scene_rows": [
                        {
                            "scene_id": "0001_001",
                            "scene_summary": "The Outer Sect binds continued admission to labor quota completion.",
                            "source_ref": "scene:0001_001",
                        }
                    ]
                },
            )
            write_json(
                style_dir / "style_bible_reduce_trace.json",
                {
                    "evidence_map": [
                        {
                            "evidence_refs": ["scene:0001_001"],
                            "claim": "Quota labor decides admission.",
                        }
                    ]
                },
            )

            result = run_style_bible_semantic_eval(
                style_dir,
                output_dir,
                semantic_judge_model="qwen-semantic-judge",
            )

            assert result is not None
            rows = read_jsonl(result.rows_path)
            dataset = read_json(result.dataset_path)
            report = read_json(result.report_path)

            self.assertEqual(len(rows), 3)
            self.assertEqual(report["summary"]["total_rules"], 3)
            self.assertEqual(report["decision_source"], "offline_semantic_rule_engine")
            self.assertEqual(report["judge_model_name"], "offline_semantic_rule_engine")
            self.assertEqual(report["requested_semantic_judge_model"], "qwen-semantic-judge")
            self.assertEqual(len(dataset["rows"]), 3)
            self.assertTrue(all("specificity" in row for row in rows))
            self.assertTrue(all("actionability" in row for row in rows))
            self.assertTrue(all("grounding" in row for row in rows))
            self.assertTrue(all("overall_score" in row for row in rows))
            self.assertTrue(all("status" in row for row in rows))
            self.assertTrue(all("semantic_scores" in row for row in dataset["rows"]))
            self.assertTrue(all(float(row["overall_score"]) > 0.0 for row in rows))
            self.assertNotIn("semantic_observability", report)
            self.assertNotIn("semantic_sidecar", report)
            self.assertNotIn("semantic_sidecar_score", rows[0])
            self.assertNotIn("reference_free_proxies", dataset["rows"][0])

    def test_run_style_bible_semantic_eval_accepts_new_refs_from_reduce_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            style_dir = Path(tmpdir) / "style_bible"
            output_dir = Path(tmpdir) / "semantic_eval"
            style_dir.mkdir(parents=True, exist_ok=True)

            payload = _semantic_style_bible_payload()
            payload["worldbook_binding"] = {
                "routing_hints": [],
                "worldbook_worthy": [],
                "rag_worthy": [
                    {
                        "rule_id": "rag_new_ref_01",
                        "text": "Retrieve the debt note whenever chapter 0141_0142 pressure resurfaces.",
                        "trigger": "chapter 0141_0142 debt pressure",
                        "constraint": "Load the debt note whenever the chapter 0141_0142 pressure pattern returns.",
                        "_reasoning_ref": "reasoning_rag_new_ref_01",
                        "evidence_refs": ["0141_0142"],
                    }
                ],
            }
            payload["supporting_evidence"] = []

            write_json(style_dir / "style_bible_final.json", payload)
            write_json(style_dir / "style_bible_source_bundle.json", {"scene_rows": []})
            write_json(
                style_dir / "style_bible_reduce_trace.json",
                {
                    "evidence_map": [
                        {
                            "source_ref": "0141_0142",
                            "claim": "Debt pressure spikes again in chapter 0141_0142.",
                        }
                    ]
                },
            )

            result = run_style_bible_semantic_eval(style_dir, output_dir)

            assert result is not None
            rows = read_jsonl(result.rows_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["evidence_refs"], ["0141_0142"])
            self.assertEqual(rows[0]["valid_ref_count"], 1)
            self.assertGreaterEqual(float(rows[0]["grounding"]), 0.55)


if __name__ == "__main__":
    unittest.main()
