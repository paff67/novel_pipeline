from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx
from openai import OpenAI

from novel_pipeline_stable.config import GatewayConfig, StableProjectConfig
from novel_pipeline_stable.io_utils import ensure_dir, read_json, write_json


@dataclass(slots=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    model_name: str
    usage_metadata: dict[str, Any]
    request_metrics: dict[str, Any]


@dataclass(slots=True)
class _EmbeddingGatewayHandle:
    index: int
    label: str
    base_url: str
    client: OpenAI
    http_client: httpx.Client


class StableOpenAICompatibleEmbeddingClient:
    def __init__(self, config: StableProjectConfig, *, artifacts_dir: str | Path):
        self.project_config = config
        self.embedding_config = config.embedding
        if not self.embedding_config.enabled:
            raise RuntimeError("Embedding client requested while embedding is disabled.")
        if not str(self.embedding_config.model or "").strip():
            raise RuntimeError("Embedding client requested without embedding.model configured.")

        timeout = httpx.Timeout(
            connect=config.stability.connect_timeout_seconds,
            read=config.stability.read_timeout_seconds,
            write=config.stability.write_timeout_seconds,
            pool=config.stability.pool_timeout_seconds,
        )
        self._gateways = self._build_gateway_handles(config, timeout=timeout)
        self.model_name = str(self.embedding_config.model).strip()
        self.max_batch_size = max(int(self.embedding_config.max_batch_size), 1)
        self.artifacts_dir = ensure_dir(artifacts_dir).resolve()
        self.metrics_path = self.artifacts_dir / "embedding_request_metrics.jsonl"
        cache_dirname = config.stability.local_request_cache_dirname or "_request_cache"
        self.cache_dir = ensure_dir(self.artifacts_dir / cache_dirname / "embeddings").resolve()
        self._memory_cache: dict[str, tuple[float, ...]] = {}

    def _build_gateway_handles(self, config: StableProjectConfig, *, timeout: httpx.Timeout) -> list[_EmbeddingGatewayHandle]:
        raw_gateways = list(config.embedding_gateways or config.gateways)
        if not raw_gateways:
            api_key = str(config.embedding_api_key or config.api_key or "").strip()
            base_url = str(config.embedding_base_url or config.base_url or "").strip()
            if api_key and base_url:
                raw_gateways = [
                    GatewayConfig(
                        label=config.embedding.env_profile or config.model.env_profile or "embedding",
                        api_key=api_key,
                        base_url=base_url,
                    )
                ]
        if not raw_gateways:
            raise RuntimeError("Missing embedding gateway configuration.")

        handles: list[_EmbeddingGatewayHandle] = []
        for index, gateway in enumerate(raw_gateways):
            http_client = httpx.Client(
                timeout=timeout,
                headers={"User-Agent": config.stability.user_agent},
            )
            client = OpenAI(
                api_key=gateway.api_key,
                base_url=gateway.base_url,
                http_client=http_client,
                max_retries=0,
            )
            handles.append(
                _EmbeddingGatewayHandle(
                    index=index,
                    label=gateway.label,
                    base_url=gateway.base_url,
                    client=client,
                    http_client=http_client,
                )
            )
        return handles

    def _cache_key_for_text(self, text: str) -> str:
        payload = {"model": self.model_name, "text": text}
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_path_for_text(self, text: str) -> Path:
        return self.cache_dir / f"{self._cache_key_for_text(text)}.json"

    def _load_cached_vector(self, text: str) -> list[float] | None:
        cached = self._memory_cache.get(text)
        if cached is not None:
            return [float(value) for value in cached]
        path = self._cache_path_for_text(text)
        try:
            payload = read_json(path)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        vector = payload.get("vector")
        if not isinstance(vector, list):
            return None
        try:
            normalized_vector = tuple(float(value) for value in vector)
        except (TypeError, ValueError):
            return None
        self._memory_cache[text] = normalized_vector
        return [float(value) for value in normalized_vector]

    def _store_cached_vector(self, text: str, vector: Sequence[float]) -> None:
        normalized_vector = tuple(float(value) for value in vector)
        path = self._cache_path_for_text(text)
        ensure_dir(path.parent)
        payload = {
            "model": self.model_name,
            "text": text,
            "vector": [float(value) for value in normalized_vector],
        }
        write_json(path, payload)
        self._memory_cache[text] = normalized_vector

    def _usage_tokens(self, usage: dict[str, Any], key: str) -> int:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
        return 0

    def _merge_usage(self, base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key in ("prompt_tokens", "input_tokens", "total_tokens"):
            merged[key] = self._usage_tokens(base, key) + self._usage_tokens(delta, key)
        return merged

    def _request_batch(self, *, request_key: str, batch_texts: list[str]) -> tuple[list[list[float]], dict[str, Any], list[dict[str, Any]]]:
        last_error: Exception | None = None
        attempt_rows: list[dict[str, Any]] = []
        for gateway in self._gateways:
            started = time.perf_counter()
            try:
                response = gateway.client.embeddings.create(model=self.model_name, input=batch_texts)
                data = list(getattr(response, "data", []) or [])
                vectors = [list(getattr(item, "embedding", []) or []) for item in data]
                if len(vectors) != len(batch_texts):
                    raise RuntimeError(
                        f"Embedding gateway returned {len(vectors)} vectors for {len(batch_texts)} texts."
                    )
                usage = getattr(response, "usage", None)
                usage_payload = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage or {})
                attempt_rows.append(
                    {
                        "gateway_label": gateway.label,
                        "gateway_base_url": gateway.base_url,
                        "status": "success",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "input_count": len(batch_texts),
                    }
                )
                return vectors, usage_payload, attempt_rows
            except Exception as exc:  # pragma: no cover - exercised through retry path
                last_error = exc
                attempt_rows.append(
                    {
                        "gateway_label": gateway.label,
                        "gateway_base_url": gateway.base_url,
                        "status": "error",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "input_count": len(batch_texts),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
        if last_error is None:
            raise RuntimeError("Embedding request failed without an exception.")
        raise last_error

    def _append_metrics(self, payload: dict[str, Any]) -> None:
        if not self.project_config.stability.record_request_metrics:
            return
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with self.metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def embed_texts(self, *, request_key: str, texts: Iterable[str]) -> EmbeddingResponse:
        normalized_texts = [str(text or "").strip() for text in texts]
        if not normalized_texts:
            raise ValueError("embed_texts requires at least one input string.")

        started = time.perf_counter()
        vectors: list[list[float] | None] = [None] * len(normalized_texts)
        missing_indexes_by_text: dict[str, list[int]] = {}
        cache_hit_count = 0
        for index, text in enumerate(normalized_texts):
            cached_vector = self._load_cached_vector(text)
            if cached_vector is not None:
                vectors[index] = cached_vector
                cache_hit_count += 1
                continue
            missing_indexes_by_text.setdefault(text, []).append(index)

        usage_payload: dict[str, Any] = {"prompt_tokens": 0, "input_tokens": 0, "total_tokens": 0}
        attempt_rows: list[dict[str, Any]] = []
        missing_texts = list(missing_indexes_by_text.keys())
        for batch_start in range(0, len(missing_texts), self.max_batch_size):
            batch_texts = missing_texts[batch_start : batch_start + self.max_batch_size]
            batch_vectors, batch_usage, batch_attempts = self._request_batch(
                request_key=request_key,
                batch_texts=batch_texts,
            )
            usage_payload = self._merge_usage(usage_payload, batch_usage)
            attempt_rows.extend(batch_attempts)
            for text, vector in zip(batch_texts, batch_vectors, strict=True):
                for index in missing_indexes_by_text.get(text, []):
                    vectors[index] = [float(value) for value in vector]
                self._store_cached_vector(text, vector)

        final_vectors = [
            [float(value) for value in vector]
            for vector in vectors
            if vector is not None
        ]
        if len(final_vectors) != len(normalized_texts):
            raise RuntimeError("Embedding client failed to resolve vectors for every requested text.")

        request_metrics = {
            "request_key": request_key,
            "stage": "embedding",
            "model": self.model_name,
            "input_count": len(normalized_texts),
            "unique_input_count": len(set(normalized_texts)),
            "batched_text_count": len(missing_texts),
            "cache_hit_count": cache_hit_count,
            "cache_miss_count": len(normalized_texts) - cache_hit_count,
            "gateway_count": len(self._gateways),
            "attempts": attempt_rows,
            "total_elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        usage_metadata = {
            "stage": "embedding",
            "prompt_tokens": self._usage_tokens(usage_payload, "prompt_tokens"),
            "input_tokens": self._usage_tokens(usage_payload, "input_tokens")
            or self._usage_tokens(usage_payload, "prompt_tokens"),
            "total_tokens": self._usage_tokens(usage_payload, "total_tokens"),
            "cache_hit_count": cache_hit_count,
            "memory_cache_size": len(self._memory_cache),
            "raw_usage_metadata": usage_payload,
        }
        self._append_metrics(request_metrics)
        return EmbeddingResponse(
            vectors=final_vectors,
            model_name=self.model_name,
            usage_metadata=usage_metadata,
            request_metrics=request_metrics,
        )


__all__ = [
    "EmbeddingResponse",
    "StableOpenAICompatibleEmbeddingClient",
]
