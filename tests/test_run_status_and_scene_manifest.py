from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.config import ProjectConfig, SceneSplitConfig
from novel_pipeline_stable.io_utils import read_json, write_json
from novel_pipeline_stable.monitoring import RunTracker
from novel_pipeline_stable.pipelines import _scene_input_files, _validate_fact_resume_artifact
from novel_pipeline_stable.splitter import run_scene_split


class RunStatusAndSceneManifestTest(unittest.TestCase):
    def test_finish_persists_final_fields_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "run"
            tracker = RunTracker(stage="unit", output_dir=output_dir, total_items=0)

            tracker.finish(
                "done with failures",
                status="completed_with_failures",
                manifest_count=3,
                outstanding_failures=1,
                success_count=2,
                failure_count=1,
            )

            status = read_json(output_dir / "run_status.json")
            self.assertEqual(status["status"], "completed_with_failures")
            self.assertEqual(status["manifest_count"], 3)
            self.assertEqual(status["outstanding_failures"], 1)
            self.assertEqual(status["success_count"], 2)
            self.assertEqual(status["failure_count"], 1)

    def test_split_manifest_writes_output_file_and_clear_removes_stale_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "chapters"
            output_dir = root / "scenes"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "chapter_0001.txt").write_text(
                "第1章 测试\n\n第一段推动情节。第二段继续推动情节。第三段完成场景。",
                encoding="utf-8",
            )
            write_json(output_dir / "scene_9999_001.json", {"scene_id": "stale"})

            config = ProjectConfig(
                project_root=root,
                scene_split=SceneSplitConfig(min_chars=5, target_chars=20, max_chars=80),
            )
            run_scene_split(config, input_dir, output_dir, clear=True)

            self.assertFalse((output_dir / "scene_9999_001.json").exists())
            manifest = read_json(output_dir / "manifest.json")
            self.assertTrue(manifest)
            self.assertTrue(all(row.get("output_file") for row in manifest))

            write_json(output_dir / "scene_9999_001.json", {"scene_id": "stale"})
            input_files = _scene_input_files(output_dir)
            self.assertNotIn(output_dir / "scene_9999_001.json", input_files)

    def test_fact_resume_validation_rejects_mismatched_fingerprint_but_allows_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scene_file = root / "scene_0001_001.json"
            output_file = root / "out" / scene_file.name
            output_file.parent.mkdir()
            scene = {
                "chapter_id": "0001",
                "chapter_title": "第1章",
                "scene_id": "0001_001",
                "scene_index": 1,
                "text": "正文",
            }
            write_json(
                output_file,
                {
                    "chapter_id": "0001",
                    "scene_id": "0001_001",
                    "scene_summary": "场景摘要",
                    "source_file": scene_file.name,
                },
            )
            legacy = _validate_fact_resume_artifact(
                output_file=output_file,
                scene_file=scene_file,
                scene=scene,
                manifest_row={"source_file": scene_file.name, "output_file": output_file.name},
                expected_fingerprint={"sha256": "expected"},
            )
            self.assertTrue(legacy.valid)
            self.assertTrue(legacy.legacy_without_fingerprint)

            write_json(
                output_file,
                {
                    "chapter_id": "0001",
                    "scene_id": "0001_001",
                    "scene_summary": "场景摘要",
                    "source_file": scene_file.name,
                    "artifact_fingerprint": {"sha256": "stale"},
                },
            )
            stale = _validate_fact_resume_artifact(
                output_file=output_file,
                scene_file=scene_file,
                scene=scene,
                manifest_row={"source_file": scene_file.name, "output_file": output_file.name},
                expected_fingerprint={"sha256": "expected"},
            )
            self.assertFalse(stale.valid)
            self.assertEqual(stale.reason, "artifact_fingerprint_mismatch")


if __name__ == "__main__":
    unittest.main()
