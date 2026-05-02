from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from novel_pipeline_stable.embedding_client import StableOpenAICompatibleEmbeddingClient


def _cleanup_temp_tree(path: Path) -> None:
    raw = str(path.resolve())
    if os.name == "nt" and not raw.startswith("\\\\?\\"):
        raw = "\\\\?\\" + raw
    shutil.rmtree(raw, ignore_errors=True)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        embedding=SimpleNamespace(
            enabled=True,
            model="Qwen3-Embedding-8B",
            max_batch_size=8,
            env_profile="silicon-flaw",
        ),
        stability=SimpleNamespace(
            connect_timeout_seconds=15.0,
            read_timeout_seconds=180.0,
            write_timeout_seconds=30.0,
            pool_timeout_seconds=30.0,
            user_agent="novel-pipeline-tests",
            local_request_cache_dirname="_request_cache",
            record_request_metrics=False,
        ),
        embedding_gateways=[SimpleNamespace(label="silicon", api_key="test", base_url="https://example.invalid/v1")],
        gateways=[],
        embedding_api_key="test",
        embedding_base_url="https://example.invalid/v1",
        api_key="",
        base_url="",
    )


class EmbeddingClientTest(unittest.TestCase):
    def test_embed_texts_batches_unique_misses_and_hits_memory_cache(self) -> None:
        request_batches: list[list[str]] = []

        def fake_request_batch(self, *, request_key: str, batch_texts: list[str]):
            request_batches.append(list(batch_texts))
            vectors = [[float(index + 1), float(index + 2)] for index, _ in enumerate(batch_texts)]
            usage = {"prompt_tokens": len(batch_texts), "input_tokens": len(batch_texts), "total_tokens": len(batch_texts)}
            attempts = [{"request_key": request_key, "status": "success", "input_count": len(batch_texts)}]
            return vectors, usage, attempts

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(StableOpenAICompatibleEmbeddingClient, "_request_batch", new=fake_request_batch):
                client = StableOpenAICompatibleEmbeddingClient(_config(), artifacts_dir=Path(tmpdir))
                first = client.embed_texts(
                    request_key="embedding_test_first",
                    texts=["alpha text", "beta text", "alpha text"],
                )
                second = client.embed_texts(
                    request_key="embedding_test_second",
                    texts=["alpha text", "beta text"],
                )

        self.assertEqual(len(request_batches), 1)
        self.assertEqual(request_batches[0], ["alpha text", "beta text"])
        self.assertEqual(len(first.vectors), 3)
        self.assertEqual(first.vectors[0], first.vectors[2])
        self.assertEqual(first.request_metrics["batched_text_count"], 2)
        self.assertEqual(second.request_metrics["batched_text_count"], 0)
        self.assertEqual(second.request_metrics["cache_hit_count"], 2)
        self.assertGreaterEqual(second.usage_metadata["memory_cache_size"], 2)

    def test_store_cached_vector_recreates_missing_cache_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = StableOpenAICompatibleEmbeddingClient(_config(), artifacts_dir=Path(tmpdir))
            cache_parent = client.cache_dir
            if cache_parent.exists():
                cache_parent.rmdir()

            client._store_cached_vector("alpha text", [1.0, 2.0, 3.0])

            self.assertTrue(cache_parent.exists())
            self.assertIsNotNone(client._load_cached_vector("alpha text"))

    def test_store_cached_vector_supports_long_cache_paths(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        try:
            long_root = tmpdir
            while len(str(long_root / "_request_cache" / "embeddings" / ("x" * 64 + ".json"))) <= 280:
                long_root = long_root / "section_densify_resume_cache_segment"
            client = StableOpenAICompatibleEmbeddingClient(_config(), artifacts_dir=long_root)

            self.assertGreater(len(str(client._cache_path_for_text("alpha text"))), 260)
            client._store_cached_vector("alpha text", [1.0, 2.0, 3.0])

            self.assertEqual(client._load_cached_vector("alpha text"), [1.0, 2.0, 3.0])
        finally:
            _cleanup_temp_tree(tmpdir)


if __name__ == "__main__":
    unittest.main()
