from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from novel_pipeline_stable.io_utils import read_json, write_json


def _cleanup_temp_tree(path: Path) -> None:
    raw = str(path.resolve())
    if os.name == "nt" and not raw.startswith("\\\\?\\"):
        raw = "\\\\?\\" + raw
    shutil.rmtree(raw, ignore_errors=True)


class IOUtilsTest(unittest.TestCase):
    def test_write_json_supports_long_nested_paths(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        try:
            nested = tmpdir
            while len(str(nested / "section_densify_summary.json")) <= 280:
                nested = nested / "section_densify_output_segment"
            target = nested / "section_densify_summary.json"

            write_json(target, {"status": "ok", "path": str(target)})
            payload = read_json(target)

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["path"], str(target))
        finally:
            _cleanup_temp_tree(tmpdir)


if __name__ == "__main__":
    unittest.main()
