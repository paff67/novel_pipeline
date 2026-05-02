from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from novel_pipeline_stable.style_bible_builder import build_style_bible


class StyleBibleBuildResumeTest(unittest.TestCase):
    def test_build_style_bible_passes_resume_to_local_reduce(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "style_bible"
            phase_artifacts = SimpleNamespace(
                source_bundle={"style_bible_id_hint": "style.demo", "scope_hint": "novel"},
                source_bundle_path=output_dir / "style_bible_source_bundle.json",
                story_node_scope={},
                routed_index={},
                batch_plan={},
            )
            bucket_memo_result = SimpleNamespace(
                bucket_memos=["memo"],
                request_metrics={},
                usage_metadata={},
                memoed_item_ids=set(),
                memoed_chapter_ids=set(),
            )

            def _assert_resume_flag(*args: object, **kwargs: object) -> object:
                self.assertTrue(kwargs.get("resume_local_reduce"))
                raise RuntimeError("stop_after_resume_assert")

            with patch(
                "novel_pipeline_stable.style_bible_builder._prepare_style_bible_phase01_artifacts",
                return_value=phase_artifacts,
            ), patch(
                "novel_pipeline_stable.style_bible_builder.StyleBibleRoutedIndex.model_validate",
                return_value=SimpleNamespace(),
            ), patch(
                "novel_pipeline_stable.style_bible_builder.StyleBibleBatchPlan.model_validate",
                return_value=SimpleNamespace(),
            ), patch(
                "novel_pipeline_stable.style_bible_builder.build_style_bible_bucket_memos",
                return_value=bucket_memo_result,
            ), patch(
                "novel_pipeline_stable.style_bible_builder.reduce_style_bible_from_bucket_memos",
                side_effect=_assert_resume_flag,
            ):
                with self.assertRaisesRegex(RuntimeError, "stop_after_resume_assert"):
                    build_style_bible(
                        config=SimpleNamespace(),
                        facts_dir=Path(tmpdir) / "facts",
                        style_dir=Path(tmpdir) / "style",
                        canon_dir=Path(tmpdir) / "canon",
                        output_dir=output_dir,
                        resume=True,
                    )


if __name__ == "__main__":
    unittest.main()
