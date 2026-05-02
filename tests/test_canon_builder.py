from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.canon_builder import build_canon


class CanonBuilderTest(unittest.TestCase):
    def test_build_canon_ignores_metadata_json_artifacts_in_fact_and_style_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            facts_dir = root / "facts"
            style_dir = root / "style"
            output_dir = root / "canon"
            facts_dir.mkdir()
            style_dir.mkdir()

            (facts_dir / "scene_0001_001.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "scene_summary": "A settlement notice arrives before the reward is released.",
                        "chapter_title": "Chapter 1",
                        "entities": [],
                        "events": [],
                        "facts": [],
                        "relationship_changes": [],
                        "power_system_notes": [],
                        "style_markers": [],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (facts_dir / "manifest.empty.20260409T045533Z.json").write_text("", encoding="utf-8")
            (style_dir / "style_window_0001_0002.json").write_text(
                json.dumps(
                    {
                        "window_id": "0001_0002",
                        "chapter_ids": ["0001", "0002"],
                        "source_chapter_titles": ["Chapter 1", "Chapter 2"],
                        "surface_genre": ["赛博修仙校园文"],
                        "narrative_engine": ["叙事通过资格门槛、债务压力和制度流程推进。"],
                        "narrator_distance": "近距离第三人称，紧贴主角体感和吐槽，但在介绍规则时会短暂拉远。",
                        "humor_mechanisms": ["把修仙设定和现代金融术语硬拼，形成冷面黑色幽默。"],
                        "satire_targets": ["教育筛选制度与资源决定胜负的社会结构。"],
                        "characterization_mechanisms": ["人物通过花钱、欠债、身体代价与羞耻反应显形。"],
                        "dialogue_signature": ["对话像流程核验和催收。"],
                        "pacing_pattern": ["开篇用筛选建立规则，再用连续代价推进压力链。"],
                        "emotion_aftertaste": ["总体余味是窒息后的冷笑。"],
                        "why_nonstandard_xianxia": ["修仙门槛被改写成教育资格、消费能力和负债承受力。"],
                        "style_fingerprint": ["高频使用修仙术语和现代制度消费词的并置句法。"],
                        "supporting_evidence": [
                            {
                                "claim": "资格筛选和债务压力共同推动剧情。",
                                "evidence_text": "面试、补习、贷款与身体代价反复出现，构成连续的制度压力链。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (style_dir / "manifest.empty.20260409T045533Z.json").write_text("", encoding="utf-8")

            index = build_canon(facts_dir, style_dir, output_dir)

            self.assertEqual(index.chapter_summary_count, 1)
            self.assertEqual(index.style_window_count, 1)
            self.assertTrue((output_dir / "chapter_summaries.jsonl").exists())
            self.assertTrue((output_dir / "style_bible.json").exists())


if __name__ == "__main__":
    unittest.main()
