from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET_TEXT_PATHS = (
    ROOT / "src",
    ROOT / "config",
    ROOT / "prompts",
    ROOT / "scripts",
    ROOT / "README.md",
    ROOT / "README_CN.md",
)

TEXT_SUFFIXES = {".py", ".toml", ".md", ".ps1"}

BANNED_TOKENS = (
    "StyleBibleRuleItem",
    "positive_cues",
    "build_compact_contract_fragment",
    "semantic_shadow_enabled",
    "evaluate-style-bible-ragas",
)

REMOVED_FILES = (
    ROOT / "src" / "novel_pipeline_stable" / "client.py",
    ROOT / "src" / "novel_pipeline_stable" / "openai_client.py",
    ROOT / "src" / "novel_pipeline_stable" / "style_bible_reducer.py",
)


def _iter_target_files() -> list[Path]:
    files: list[Path] = []
    for path in TARGET_TEXT_PATHS:
        if path.is_file():
            files.append(path)
            continue
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix in TEXT_SUFFIXES:
                files.append(candidate)
    return sorted(files)


class TrueRefactorAcceptanceTest(unittest.TestCase):
    def test_removed_files_are_absent(self) -> None:
        for path in REMOVED_FILES:
            self.assertFalse(path.exists(), f"Removed transitional file still exists: {path}")

    def test_banned_tokens_are_absent_from_runtime_surfaces(self) -> None:
        for path in _iter_target_files():
            text = path.read_text(encoding="utf-8")
            for token in BANNED_TOKENS:
                self.assertNotIn(token, text, f"Found banned token {token!r} in {path}")


if __name__ == "__main__":
    unittest.main()
