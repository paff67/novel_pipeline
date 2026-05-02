from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from novel_pipeline_stable.config import load_stable_project_config


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


class ConfigEnvLoadingTest(unittest.TestCase):
    def _build_project(self, root: Path, *, env_text: str, config_text: str) -> Path:
        _write_text(root / ".env", env_text)
        config_path = root / "config" / "test.toml"
        _write_text(config_path, config_text)
        return config_path

    def test_embedding_model_reads_model_name_from_profile_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            config_path = self._build_project(
                project_root,
                env_text="""
                #gpt方案网关
                OPENAI_COMPAT_API_KEY=gpt-key
                OPENAI_COMPAT_BASE_URL=https://gpt.example/v1
                #silicon flaw方案网关
                OPENAI_COMPAT_API_KEY=silicon-key
                OPENAI_COMPAT_BASE_URL=https://silicon.example/v1
                MODEL_NAME=Qwen/Qwen3-Embedding-8B
                """,
                config_text="""
                [models]
                fact_model = "gpt-5.4"
                style_model = "gpt-5.4"
                style_bible_model = "gpt-5.4"
                env_profile = "gpt"

                [embedding]
                enabled = true
                env_profile = "silicon-flaw"
                """,
            )
            with patch.dict(os.environ, {}, clear=True):
                config = load_stable_project_config(config_path)

        self.assertTrue(config.embedding.enabled)
        self.assertEqual(config.embedding.model, "Qwen/Qwen3-Embedding-8B")
        self.assertEqual(len(config.embedding_gateways), 1)
        self.assertEqual(config.embedding_gateways[0].label, "silicon flaw方案网关")
        self.assertEqual(config.embedding_gateways[0].base_url, "https://silicon.example/v1")

    def test_profile_gateways_do_not_append_process_env_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            config_path = self._build_project(
                project_root,
                env_text="""
                #gpt方案网关
                OPENAI_COMPAT_API_KEY=gpt-key
                OPENAI_COMPAT_BASE_URL=https://gpt.example/v1
                #silicon flaw方案网关
                OPENAI_COMPAT_API_KEY=silicon-key
                OPENAI_COMPAT_BASE_URL=https://silicon.example/v1
                MODEL_NAME=Qwen/Qwen3-Embedding-8B
                """,
                config_text="""
                [models]
                fact_model = "gpt-5.4"
                style_model = "gpt-5.4"
                style_bible_model = "gpt-5.4"
                env_profile = "gpt"

                [embedding]
                enabled = true
                env_profile = "silicon-flaw"
                """,
            )
            with patch.dict(
                os.environ,
                {
                    "OPENAI_COMPAT_API_KEY": "env-fallback-key",
                    "OPENAI_COMPAT_BASE_URL": "https://env-fallback.example/v1",
                },
                clear=True,
            ):
                config = load_stable_project_config(config_path)

        self.assertEqual([(gateway.label, gateway.base_url) for gateway in config.gateways], [("gpt方案网关", "https://gpt.example/v1")])
        self.assertEqual(
            [(gateway.label, gateway.base_url) for gateway in config.embedding_gateways],
            [("silicon flaw方案网关", "https://silicon.example/v1")],
        )

    def test_process_embedding_model_override_has_priority_over_profile_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            config_path = self._build_project(
                project_root,
                env_text="""
                #silicon flaw方案网关
                OPENAI_COMPAT_API_KEY=silicon-key
                OPENAI_COMPAT_BASE_URL=https://silicon.example/v1
                MODEL_NAME=Qwen/Qwen3-Embedding-8B
                """,
                config_text="""
                [models]
                fact_model = "gpt-5.4"
                style_model = "gpt-5.4"
                style_bible_model = "gpt-5.4"

                [embedding]
                enabled = true
                env_profile = "silicon-flaw"
                model = "fallback-model"
                """,
            )
            with patch.dict(os.environ, {"NOVEL_PIPELINE_EMBEDDING_MODEL": "override-embedding-model"}, clear=True):
                config = load_stable_project_config(config_path)

        self.assertEqual(config.embedding.model, "override-embedding-model")

    def test_global_gateway_filters_do_not_apply_to_embedding_gateways(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            config_path = self._build_project(
                project_root,
                env_text="""
                #gpt方案网关
                OPENAI_COMPAT_API_KEY=gpt-key-1
                OPENAI_COMPAT_BASE_URL=https://gpt-1.example/v1
                #gpt方案网关2
                OPENAI_COMPAT_API_KEY=gpt-key-2
                OPENAI_COMPAT_BASE_URL=https://gpt-2.example/v1
                #silicon flaw方案网关
                OPENAI_COMPAT_API_KEY=silicon-key
                OPENAI_COMPAT_BASE_URL=https://silicon.example/v1
                MODEL_NAME=Qwen/Qwen3-Embedding-8B
                """,
                config_text="""
                [models]
                fact_model = "gpt-5.4"
                style_model = "gpt-5.4"
                style_bible_model = "gpt-5.4"
                env_profile = "gpt"

                [embedding]
                enabled = true
                env_profile = "silicon-flaw"
                """,
            )
            with patch.dict(
                os.environ,
                {"NOVEL_PIPELINE_ALLOWED_GATEWAY_INDEXES": "2"},
                clear=True,
            ):
                config = load_stable_project_config(config_path)

        self.assertEqual([(gateway.label, gateway.base_url) for gateway in config.gateways], [("gpt方案网关2", "https://gpt-2.example/v1")])
        self.assertEqual(
            [(gateway.label, gateway.base_url) for gateway in config.embedding_gateways],
            [("silicon flaw方案网关", "https://silicon.example/v1")],
        )


if __name__ == "__main__":
    unittest.main()
