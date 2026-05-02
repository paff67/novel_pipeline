from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal, TypeVar, get_args, get_origin
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, InternalServerError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from novel_pipeline_stable.io_utils import ensure_dir
from novel_pipeline_stable.config import GatewayConfig, StableProjectConfig
from novel_pipeline_stable.utils.json_repair import loads_json_fragment, repair_instruction_rules

T = TypeVar("T", bound=BaseModel)
RESPONSES_RAW_STREAM_HOSTS = {"api.0-0.pro"}
TRANSIENT_400_MARKERS = (
    "current provider response failed",
    "provider response failed",
    "store must be set to false",
    "all api keys are rate limited",
    "stream must be set to true",
    "temporarily unavailable",
    "service unavailable",
    "upstream",
    "backend",
    "gateway",
    "overloaded",
    "try again later",
    "timed out",
    "timeout",
)
UPSTREAM_RETRY_BONUS_STATUSES = {408, 429, 500, 502, 503, 504}
ERROR_BODY_EXCERPT_LIMIT = 800
RAW_ARTIFACT_PATH_LIMIT = 240


@dataclass(slots=True)
class AttemptMetrics:
    attempt: int
    gateway_label: str
    gateway_base_url: str
    used_stream: bool
    transport_mode: str
    outcome: str
    elapsed_seconds: float
    first_chunk_seconds: float | None = None
    retryable: bool = False
    status_code: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_body_excerpt: str = ""
    error_response_path: str = ""
    retry_after_seconds: float | None = None
    retry_strategy: str = ""
    response_chars: int = 0
    repair_used: bool = False
    raw_response_path: str = ""


@dataclass(slots=True)
class StructuredResponse:
    parsed: BaseModel
    raw_text: str
    model_name: str
    usage_metadata: dict[str, Any]
    request_metrics: dict[str, Any]


@dataclass(slots=True)
class CachedStructuredResponse:
    parsed: BaseModel
    raw_text: str
    model_name: str
    source_usage_metadata: dict[str, Any]
    cache_key: str
    cache_path: Path


class StructuredGenerationError(RuntimeError):
    def __init__(self, message: str, *, request_metrics: dict[str, Any]):
        super().__init__(message)
        self.request_metrics = request_metrics


@dataclass(slots=True)
class GatewayHandle:
    index: int
    label: str
    base_url: str
    client: OpenAI
    http_client: httpx.Client


class StableOpenAICompatibleStructuredClient:
    def __init__(self, config: StableProjectConfig, *, artifacts_dir: str | Path):
        if not config.gateways and not config.api_key:
            raise RuntimeError("Missing OPENAI_COMPAT_API_KEY or OPENAI_API_KEY.")
        if not config.gateways and not config.base_url:
            raise RuntimeError("Missing OPENAI_COMPAT_BASE_URL or OPENAI_BASE_URL.")

        self.project_config = config
        timeout = httpx.Timeout(
            connect=config.stability.connect_timeout_seconds,
            read=config.stability.read_timeout_seconds,
            write=config.stability.write_timeout_seconds,
            pool=config.stability.pool_timeout_seconds,
        )
        self._gateways = self._build_gateway_handles(config, timeout=timeout)
        self.client = self._gateways[0].client
        self.artifacts_dir = ensure_dir(artifacts_dir).resolve()
        self.metrics_path = self.artifacts_dir / "request_metrics.jsonl"
        self.raw_dir = ensure_dir(self.artifacts_dir / "_raw_responses")
        self.cache_dir: Path | None = None
        if self.project_config.stability.enable_local_request_cache:
            self.cache_dir = ensure_dir(
                self.artifacts_dir / self.project_config.stability.local_request_cache_dirname
            ).resolve()
        self._last_request_started_at = 0.0
        self._consecutive_retryable_failures = 0
        self._preferred_gateway_index = 0
        self._disabled_gateway_indices: set[int] = set()
        self._responses_non_stream_gateway_indices: set[int] = set()

    def generate_structured(
        self,
        *,
        request_key: str,
        model_name: str,
        response_model: type[T],
        system_instruction: str,
        user_payload: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        response_format_mode: Literal["json_object", "json_schema"] | None = None,
        output_contract_mode: Literal["auto", "blueprint", "none"] | None = None,
    ) -> StructuredResponse:
        preferred_gateway = self._gateways[self._preferred_gateway_index]
        use_stream = self._should_use_stream_for_request(gateway=preferred_gateway)
        attempts_per_gateway = max(int(self.project_config.model.retry_count), 1)
        base_attempts = max(attempts_per_gateway * self._active_gateway_count(), 1)
        upstream_retry_bonus_attempts = max(int(self.project_config.stability.upstream_retry_bonus_attempts), 0)
        attempts = max(base_attempts + upstream_retry_bonus_attempts, 1)
        effective_system_instruction = self._compose_system_instruction(
            system_instruction,
            response_model,
            response_format_mode=response_format_mode,
            output_contract_mode=output_contract_mode,
        )
        user_content = self._dumps_json(user_payload)
        response_format = self._build_response_format(
            response_model,
            response_format_mode=response_format_mode,
        )
        cache_key = self._build_local_request_cache_key(
            model_name=model_name,
            response_model=response_model,
            response_format=response_format,
            system_instruction=effective_system_instruction,
            user_content=user_content,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        request_metrics = {
            "request_key": request_key,
            "model": model_name,
            "api_route": self.project_config.model.api_route,
            "reasoning_effort": self.project_config.model.reasoning_effort,
            "used_stream": use_stream,
            "transport_mode": self._build_transport_mode(use_stream),
            "attempts_per_gateway": attempts_per_gateway,
            "base_attempts": base_attempts,
            "upstream_retry_bonus_attempts": upstream_retry_bonus_attempts,
            "max_attempts": attempts,
            "gateway_count": len(self._gateways),
            "configured_gateways": [
                {"label": gateway.label, "base_url": gateway.base_url}
                for gateway in self._gateways
            ],
            "system_chars": len(effective_system_instruction),
            "user_chars": len(user_content),
            "request_chars": len(effective_system_instruction) + len(user_content),
            "attempts": [],
            "completed": False,
            "response_chars": 0,
            "repair_used": False,
            "cache_enabled": self.cache_dir is not None,
            "cache_hit": False,
            "cache_source": "",
            "cache_key": cache_key,
            "cache_path": "",
            "usage_metadata": {},
            "total_elapsed_seconds": 0.0,
            "retry_budget_used": False,
        }
        overall_started = time.perf_counter()
        cached_response = self._load_cached_structured_response(
            cache_key=cache_key,
            response_model=response_model,
        )
        if cached_response is not None:
            cache_usage = self._build_cache_hit_usage_metadata(cached_response.source_usage_metadata)
            elapsed = round(time.perf_counter() - overall_started, 3)
            request_metrics["attempts"].append(
                asdict(
                    AttemptMetrics(
                        attempt=0,
                        gateway_label="local_request_cache",
                        gateway_base_url="",
                        used_stream=False,
                        transport_mode="local_cache",
                        outcome="success",
                        elapsed_seconds=elapsed,
                        retryable=False,
                        response_chars=len(cached_response.raw_text),
                    )
                )
            )
            request_metrics["completed"] = True
            request_metrics["response_chars"] = len(cached_response.raw_text)
            request_metrics["used_stream"] = False
            request_metrics["transport_mode"] = "local_cache"
            request_metrics["usage_metadata"] = cache_usage
            request_metrics["usage_summary"] = self._usage_summary(cache_usage)
            request_metrics["cache_hit"] = True
            request_metrics["cache_source"] = "local_request_cache"
            request_metrics["cache_path"] = str(cached_response.cache_path)
            request_metrics["gateway_label"] = "local_request_cache"
            request_metrics["gateway_base_url"] = ""
            request_metrics["total_elapsed_seconds"] = elapsed
            self._append_metrics(request_metrics)
            return StructuredResponse(
                parsed=cached_response.parsed,
                raw_text=cached_response.raw_text,
                model_name=cached_response.model_name,
                usage_metadata=cache_usage,
                request_metrics=request_metrics,
            )
        last_error: Exception | None = None
        attempted_count = 0
        for gateway in self._iter_gateway_attempts(attempts):
            if gateway.index in self._disabled_gateway_indices:
                continue
            attempt = attempted_count + 1
            attempted_count = attempt
            self._maybe_cooldown()
            self._respect_rate_limit()
            started = time.perf_counter()
            raw_text = ""
            usage: dict[str, Any] = {}
            first_chunk_seconds: float | None = None
            repair_used = False
            used_stream = self._should_use_stream_for_request(gateway=gateway)

            try:
                raw_text, first_chunk_seconds, usage, used_stream = self._request_text(
                    gateway=gateway,
                    client=gateway.client,
                    model_name=model_name,
                    system_instruction=effective_system_instruction,
                    user_content=user_content,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    response_model=response_model,
                )

                raw_response_path = self._persist_raw_text(
                    request_key=request_key,
                    attempt=attempt,
                    raw_text=raw_text,
                    success=True,
                )

                try:
                    parsed = self._validate_loaded_payload(self._loads_json(raw_text), response_model)
                except (ValueError, ValidationError) as parse_exc:
                    parse_error_raw_response_path = self._persist_raw_text(
                        request_key=request_key,
                        attempt=attempt,
                        raw_text=raw_text,
                        success=False,
                        suffix="parse_error",
                    )
                    if parse_error_raw_response_path is not None:
                        raw_response_path = parse_error_raw_response_path
                    if not self.project_config.stability.enable_json_repair:
                        raise
                    original_usage = self._clone_usage_payload(usage)
                    raw_text, repair_usage = self._repair_json(
                        gateway=gateway,
                        model_name=model_name,
                        response_model=response_model,
                        broken_json=raw_text,
                    )
                    repair_used = True
                    raw_response_path = self._persist_raw_text(
                        request_key=request_key,
                        attempt=attempt,
                        raw_text=raw_text,
                        success=False,
                        suffix="repaired",
                    )
                    parsed = self._validate_loaded_payload(self._loads_json(raw_text), response_model)
                    usage = self._merge_usage_metadata(original_usage, repair_usage)
                    if not isinstance(usage, dict):
                        usage = {}
                    usage["repaired"] = True
                    usage["repair_note"] = "Used JSON repair pass after parse/validation failure."
                    usage["repair_error_type"] = type(parse_exc).__name__
                    usage["repair_usage_metadata"] = repair_usage if isinstance(repair_usage, dict) else {}

                elapsed = time.perf_counter() - started
                request_metrics["attempts"].append(
                    asdict(
                        AttemptMetrics(
                            attempt=attempt,
                            gateway_label=gateway.label,
                            gateway_base_url=gateway.base_url,
                            used_stream=used_stream,
                            transport_mode=self._build_transport_mode(used_stream),
                            outcome="success",
                            elapsed_seconds=round(elapsed, 3),
                            first_chunk_seconds=round(first_chunk_seconds, 3) if first_chunk_seconds is not None else None,
                            retryable=False,
                            response_chars=len(raw_text),
                            repair_used=repair_used,
                            raw_response_path=str(raw_response_path) if raw_response_path else "",
                        )
                    )
                )
                request_metrics["completed"] = True
                request_metrics["response_chars"] = len(raw_text)
                request_metrics["repair_used"] = repair_used
                request_metrics["used_stream"] = used_stream
                request_metrics["transport_mode"] = self._build_transport_mode(used_stream)
                request_metrics["usage_metadata"] = usage
                request_metrics["usage_summary"] = self._usage_summary(usage)
                request_metrics["gateway_label"] = gateway.label
                request_metrics["gateway_base_url"] = gateway.base_url
                cache_path = self._store_cached_structured_response(
                    cache_key=cache_key,
                    request_key=request_key,
                    parsed=parsed,
                    raw_text=raw_text,
                    model_name=model_name,
                    source_usage_metadata=usage,
                )
                if cache_path is not None:
                    request_metrics["cache_path"] = str(cache_path)
                request_metrics["total_elapsed_seconds"] = round(time.perf_counter() - overall_started, 3)
                self._append_metrics(request_metrics)
                self._consecutive_retryable_failures = 0
                self._preferred_gateway_index = gateway.index
                return StructuredResponse(
                    parsed=parsed,
                    raw_text=raw_text,
                    model_name=model_name,
                    usage_metadata=usage,
                    request_metrics=request_metrics,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                error_details = self._extract_error_details(exc)
                retryable, status_code = self._classify_retryable(
                    exc,
                    status_code=error_details["status_code"],
                    error_text=error_details["classifier_text"],
                )
                status_code = error_details["status_code"] or status_code
                disable_gateway = self._should_disable_gateway_for_session(exc)
                if self._use_responses_api() and not used_stream and self._should_restore_responses_stream(exc):
                    self._responses_non_stream_gateway_indices.discard(gateway.index)
                    retryable = True
                if disable_gateway:
                    self._disabled_gateway_indices.add(gateway.index)
                if (
                    self._use_responses_api()
                    and used_stream
                    and not self._uses_raw_responses_stream(gateway)
                    and self._should_fallback_responses_stream(exc)
                ):
                    self._responses_non_stream_gateway_indices.add(gateway.index)
                apply_retry_bonus = retryable and self._should_apply_upstream_retry_bonus(
                    status_code=status_code,
                    error_text=error_details["classifier_text"],
                )
                attempt_limit = attempts if apply_retry_bonus else base_attempts
                retry_strategy = "upstream_bonus" if apply_retry_bonus else ("standard" if retryable else "none")
                self._consecutive_retryable_failures = self._consecutive_retryable_failures + 1 if retryable else 0
                elapsed = time.perf_counter() - started
                raw_response_path = self._persist_raw_text(
                    request_key=request_key,
                    attempt=attempt,
                    raw_text=raw_text,
                    success=False,
                )
                error_response_path = self._persist_error_details(
                    request_key=request_key,
                    attempt=attempt,
                    error_details=error_details,
                )
                request_metrics["attempts"].append(
                    asdict(
                        AttemptMetrics(
                            attempt=attempt,
                            gateway_label=gateway.label,
                            gateway_base_url=gateway.base_url,
                            used_stream=used_stream,
                            transport_mode=self._build_transport_mode(used_stream),
                            outcome="error",
                            elapsed_seconds=round(elapsed, 3),
                            first_chunk_seconds=round(first_chunk_seconds, 3) if first_chunk_seconds is not None else None,
                            retryable=retryable,
                            status_code=status_code,
                            error_type=error_details["error_type"],
                            error_code=error_details["error_code"],
                            error_message=error_details["message"],
                            error_body_excerpt=error_details["body_excerpt"],
                            error_response_path=str(error_response_path) if error_response_path else "",
                            retry_after_seconds=error_details["retry_after_seconds"],
                            retry_strategy=retry_strategy,
                            response_chars=len(raw_text),
                            repair_used=repair_used,
                            raw_response_path=str(raw_response_path) if raw_response_path else "",
                        )
                    )
                )
                request_metrics["retry_budget_used"] = bool(request_metrics.get("retry_budget_used")) or apply_retry_bonus
                request_metrics["last_error"] = {
                    "status_code": status_code,
                    "error_type": error_details["error_type"],
                    "error_code": error_details["error_code"],
                    "error_message": error_details["message"],
                    "error_response_path": str(error_response_path) if error_response_path else "",
                    "retry_after_seconds": error_details["retry_after_seconds"],
                    "retry_strategy": retry_strategy,
                }
                if disable_gateway and self._has_alternate_gateway(gateway.index) and attempted_count < attempts:
                    continue
                if not retryable:
                    if self._has_alternate_gateway(gateway.index) and attempted_count < attempts:
                        continue
                if attempted_count >= attempt_limit or not retryable:
                    request_metrics["completed"] = False
                    request_metrics["response_chars"] = len(raw_text)
                    request_metrics["used_stream"] = used_stream
                    request_metrics["transport_mode"] = self._build_transport_mode(used_stream)
                    request_metrics["last_gateway_label"] = gateway.label
                    request_metrics["last_gateway_base_url"] = gateway.base_url
                    request_metrics["total_elapsed_seconds"] = round(time.perf_counter() - overall_started, 3)
                    self._append_metrics(request_metrics)
                    raise StructuredGenerationError(
                        f"OpenAI-compatible request failed after {attempted_count} attempts: {error_details['message']}",
                        request_metrics=request_metrics,
                    ) from exc

                time.sleep(
                    self._compute_backoff_seconds(
                        attempt,
                        status_code=status_code,
                        retry_after_seconds=error_details["retry_after_seconds"],
                        apply_retry_bonus=apply_retry_bonus,
                    )
                )

        request_metrics["completed"] = False
        request_metrics["total_elapsed_seconds"] = round(time.perf_counter() - overall_started, 3)
        self._append_metrics(request_metrics)
        raise StructuredGenerationError(
            f"OpenAI-compatible request failed after {attempted_count} attempts: {last_error}",
            request_metrics=request_metrics,
        ) from last_error

    def run_probe(self, *, model_name: str) -> dict[str, Any]:
        text_payload = [
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "ping"},
        ]
        json_payload = [
            {"role": "system", "content": 'Return exactly one JSON object: {"status":"ok"}.'},
            {"role": "user", "content": "ping"},
        ]
        checks = [
            self._run_probe_check(
                gateway=self._gateways[self._preferred_gateway_index],
                mode="non_stream",
                model_name=model_name,
                raw_messages=text_payload,
                stream=False,
                max_output_tokens=8,
                expected_text="OK",
            ),
            self._run_probe_check(
                gateway=self._gateways[self._preferred_gateway_index],
                mode="stream",
                model_name=model_name,
                raw_messages=text_payload,
                stream=True,
                max_output_tokens=8,
                expected_text="OK",
            ),
            self._run_probe_check(
                gateway=self._gateways[self._preferred_gateway_index],
                mode="json_object_non_stream",
                model_name=model_name,
                raw_messages=json_payload,
                stream=False,
                max_output_tokens=64,
                response_format={"type": "json_object"},
                json_key="status",
                expected_text="ok",
            ),
            self._run_probe_check(
                gateway=self._gateways[self._preferred_gateway_index],
                mode="json_object_stream",
                model_name=model_name,
                raw_messages=json_payload,
                stream=True,
                max_output_tokens=64,
                response_format={"type": "json_object"},
                json_key="status",
                expected_text="ok",
            ),
        ]

        return {
            "model": model_name,
            "api_route": self.project_config.model.api_route,
            "reasoning_effort": self.project_config.model.reasoning_effort,
            "base_url": self._gateways[self._preferred_gateway_index].base_url,
            "gateways": [{"label": gateway.label, "base_url": gateway.base_url} for gateway in self._gateways],
            "user_agent": self.project_config.stability.user_agent,
            "stream_default": self._should_use_stream_for_request(gateway=self._gateways[self._preferred_gateway_index]),
            "checks": checks,
        }

    def _run_probe_check(
        self,
        *,
        gateway: GatewayHandle,
        mode: str,
        model_name: str,
        raw_messages: list[dict[str, str]],
        stream: bool,
        max_output_tokens: int,
        expected_text: str,
        response_format: dict[str, Any] | None = None,
        json_key: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        first_chunk_seconds: float | None = None
        try:
            raw_text, first_chunk_seconds, _usage, used_stream = self._request_text(
                gateway=gateway,
                client=gateway.client,
                model_name=model_name,
                system_instruction="",
                user_content="",
                temperature=0.0,
                max_output_tokens=max_output_tokens,
                response_format=response_format,
                raw_messages=raw_messages,
                force_stream=stream,
            )
            content = raw_text.strip()

            parsed: dict[str, Any] | None = None
            ok = content == expected_text
            if json_key:
                parsed = self._loads_json(content)
                value = str(parsed.get(json_key, "")).strip().lower()
                ok = value == expected_text.lower()

            result = {
                "mode": mode,
                "ok": ok,
                "content": content,
                "used_stream": used_stream,
                "transport_mode": self._build_transport_mode(used_stream),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
            if parsed is not None:
                result["parsed"] = parsed
            if first_chunk_seconds is not None:
                result["first_chunk_seconds"] = round(first_chunk_seconds, 3)
            return result
        except Exception as exc:  # noqa: BLE001
            error_details = self._extract_error_details(exc)
            retryable, status_code = self._classify_retryable(
                exc,
                status_code=error_details["status_code"],
                error_text=error_details["classifier_text"],
            )
            result = {
                "mode": mode,
                "ok": False,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error_type": type(exc).__name__,
                "error_message": error_details["message"],
                "retryable": retryable,
                "status_code": error_details["status_code"] or status_code,
            }
            if first_chunk_seconds is not None:
                result["first_chunk_seconds"] = round(first_chunk_seconds, 3)
            return result

    def _request_text(
        self,
        *,
        gateway: GatewayHandle | None,
        client: OpenAI,
        model_name: str,
        system_instruction: str,
        user_content: str,
        temperature: float,
        max_output_tokens: int,
        response_model: type[T] | None = None,
        response_format: dict[str, Any] | None = None,
        raw_messages: list[dict[str, str]] | None = None,
        force_stream: bool | None = None,
    ) -> tuple[str, float | None, dict[str, Any], bool]:
        effective_response_format = response_format
        if effective_response_format is None and response_model is not None:
            effective_response_format = self._build_response_format(response_model)

        use_stream = self._should_use_stream_for_request(gateway=gateway, force_stream=force_stream)
        if self._use_responses_api():
            raw_text, first_chunk_seconds, usage = self._responses_request(
                gateway=gateway,
                client=client,
                model_name=model_name,
                system_instruction=system_instruction,
                user_content=user_content,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_format=effective_response_format,
                raw_messages=raw_messages,
                stream=use_stream,
            )
            return raw_text, first_chunk_seconds, usage, use_stream
        if use_stream:
            raw_text, first_chunk_seconds, usage = self._stream_chat_completion(
                client=client,
                model_name=model_name,
                system_instruction=system_instruction,
                user_content=user_content,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_format=effective_response_format,
                raw_messages=raw_messages,
            )
            return raw_text, first_chunk_seconds, usage, use_stream

        request_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": raw_messages
            or [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if effective_response_format is not None:
            request_kwargs["response_format"] = effective_response_format
        response = client.chat.completions.create(**request_kwargs)
        return self._extract_response_text(response), None, self._extract_usage(response), use_stream

    def _responses_request(
        self,
        *,
        gateway: GatewayHandle | None,
        client: OpenAI,
        model_name: str,
        system_instruction: str,
        user_content: str,
        temperature: float,
        max_output_tokens: int,
        response_format: dict[str, Any] | None,
        raw_messages: list[dict[str, str]] | None,
        stream: bool,
    ) -> tuple[str, float | None, dict[str, Any]]:
        request_kwargs = self._build_responses_request_kwargs(
            model_name=model_name,
            system_instruction=system_instruction,
            user_content=user_content,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_format=response_format,
            raw_messages=raw_messages,
        )
        if stream:
            if gateway is None:
                raise RuntimeError("Missing gateway metadata for streaming /responses request.")
            return self._stream_responses_request(gateway=gateway, client=client, request_kwargs=request_kwargs)
        response = client.responses.create(**request_kwargs)
        return self._extract_response_text(response), None, self._extract_usage(response)

    def _build_responses_request_kwargs(
        self,
        *,
        model_name: str,
        system_instruction: str,
        user_content: str,
        temperature: float,
        max_output_tokens: int,
        response_format: dict[str, Any] | None,
        raw_messages: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        text_config = self._build_responses_text_config(response_format)
        instructions, input_messages = self._build_responses_messages(
            system_instruction=system_instruction,
            user_content=user_content,
            raw_messages=raw_messages,
            requires_json_hint=text_config is not None,
        )
        request_kwargs: dict[str, Any] = {
            "model": model_name,
            "input": input_messages,
            "max_output_tokens": max_output_tokens,
        }
        if instructions:
            request_kwargs["instructions"] = instructions
        if text_config is not None:
            request_kwargs["text"] = text_config
        reasoning = self._build_reasoning_config()
        if reasoning is not None:
            request_kwargs["reasoning"] = reasoning
        # Some OpenAI-compatible gateways reject temperature on /responses,
        # so the parameter is omitted for compatibility.
        return request_kwargs

    def _build_responses_messages(
        self,
        *,
        system_instruction: str,
        user_content: str,
        raw_messages: list[dict[str, str]] | None,
        requires_json_hint: bool,
    ) -> tuple[str, list[dict[str, Any]]]:
        instructions_parts: list[str] = []
        input_messages: list[dict[str, Any]] = []
        source_messages = raw_messages or [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content},
        ]
        for message in source_messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "system":
                if content.strip():
                    instructions_parts.append(content.strip())
                continue
            normalized_text = self._normalize_responses_input_text(
                content,
                requires_json_hint=requires_json_hint,
            )
            input_messages.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": normalized_text}],
                }
            )
        if not input_messages:
            input_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self._normalize_responses_input_text(
                                user_content,
                                requires_json_hint=requires_json_hint,
                            ),
                        }
                    ],
                }
            )
        return "\n\n".join(instructions_parts), input_messages

    @staticmethod
    def _normalize_responses_input_text(content: str, *, requires_json_hint: bool) -> str:
        text = content.strip()
        if not requires_json_hint:
            return text
        if "json" in text.casefold():
            return text
        return f"JSON payload:\n{text}"

    def _build_responses_text_config(self, response_format: dict[str, Any] | None) -> dict[str, Any] | None:
        if response_format is None:
            return None
        format_type = response_format.get("type")
        if format_type == "json_object":
            return {"format": {"type": "json_object"}}
        if format_type == "json_schema":
            json_schema = dict(response_format.get("json_schema") or {})
            format_payload: dict[str, Any] = {
                "type": "json_schema",
                "name": json_schema.get("name") or "response",
                "schema": json_schema.get("schema") or {},
                "strict": bool(json_schema.get("strict", True)),
            }
            return {"format": format_payload}
        return None

    def _stream_responses_request(
        self,
        *,
        gateway: GatewayHandle,
        client: OpenAI,
        request_kwargs: dict[str, Any],
    ) -> tuple[str, float | None, dict[str, Any]]:
        if self._uses_raw_responses_stream(gateway):
            return self._stream_responses_request_raw(gateway=gateway, request_kwargs=request_kwargs)
        started = time.perf_counter()
        first_chunk_seconds: float | None = None
        chunks: list[str] = []
        usage: dict[str, Any] = {}
        final_text: str | None = None
        stream = client.responses.create(**request_kwargs, stream=True)
        for event in stream:
            now = time.perf_counter()
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                if first_chunk_seconds is None:
                    first_chunk_seconds = now - started
                delta = getattr(event, "delta", None)
                if delta:
                    chunks.append(str(delta))
                continue
            if event_type == "response.output_text.done":
                text_value = getattr(event, "text", None)
                if first_chunk_seconds is None and text_value:
                    first_chunk_seconds = now - started
                if text_value:
                    final_text = str(text_value)
                continue
            if event_type == "response.completed":
                usage = self._extract_usage(getattr(event, "response", None)) or usage
        if chunks:
            return "".join(chunks).strip(), first_chunk_seconds, usage
        return (final_text or "").strip(), first_chunk_seconds, usage

    def _stream_responses_request_raw(
        self,
        *,
        gateway: GatewayHandle,
        request_kwargs: dict[str, Any],
    ) -> tuple[str, float | None, dict[str, Any]]:
        started = time.perf_counter()
        first_chunk_seconds: float | None = None
        chunks: list[str] = []
        usage: dict[str, Any] = {}
        final_text: str | None = None
        payload = dict(request_kwargs)
        payload["stream"] = True
        headers = {
            "Authorization": f"Bearer {gateway.client.api_key}",
            "Accept": "text/event-stream",
        }
        response_url = f"{gateway.base_url.rstrip('/')}/responses"
        with gateway.http_client.stream("POST", response_url, headers=headers, json=payload) as response:
            if response.is_error:
                response.read()
                response.raise_for_status()
            event_name = ""
            data_lines: list[str] = []
            for raw_line in response.iter_lines():
                line = raw_line if isinstance(raw_line, str) else str(raw_line or "")
                if not line:
                    event_type, payload_obj = self._finalize_sse_event(event_name, data_lines)
                    event_name = ""
                    data_lines = []
                    if payload_obj is None:
                        continue
                    now = time.perf_counter()
                    if event_type == "response.output_text.delta":
                        delta = payload_obj.get("delta")
                        if first_chunk_seconds is None and delta:
                            first_chunk_seconds = now - started
                        if delta:
                            chunks.append(str(delta))
                        continue
                    if event_type == "response.output_text.done":
                        text_value = payload_obj.get("text")
                        if first_chunk_seconds is None and text_value:
                            first_chunk_seconds = now - started
                        if text_value:
                            final_text = str(text_value)
                        continue
                    if event_type == "response.completed":
                        response_payload = payload_obj.get("response")
                        usage = self._extract_usage(response_payload) or usage
                        if not final_text:
                            final_text = self._extract_response_text(response_payload)
                    continue
                if line.startswith(":"):
                    continue
                field_name, _, field_value = line.partition(":")
                field_value = field_value.lstrip(" ")
                if field_name == "event":
                    event_name = field_value
                    continue
                if field_name == "data":
                    data_lines.append(field_value)
        if chunks:
            return "".join(chunks).strip(), first_chunk_seconds, usage
        return (final_text or "").strip(), first_chunk_seconds, usage

    @staticmethod
    def _finalize_sse_event(event_name: str, data_lines: list[str]) -> tuple[str, dict[str, Any] | None]:
        if not data_lines:
            return event_name, None
        payload_text = "\n".join(data_lines).strip()
        if not payload_text or payload_text == "[DONE]":
            return event_name, None
        payload = json.loads(payload_text)
        if isinstance(payload, dict):
            return str(payload.get("type") or event_name), payload
        return event_name, None

    def _stream_chat_completion(
        self,
        *,
        client: OpenAI,
        model_name: str,
        system_instruction: str,
        user_content: str,
        temperature: float,
        max_output_tokens: int,
        response_format: dict[str, Any] | None,
        raw_messages: list[dict[str, str]] | None = None,
    ) -> tuple[str, float | None, dict[str, Any]]:
        started = time.perf_counter()
        first_chunk_seconds: float | None = None
        chunks: list[str] = []
        usage: dict[str, Any] = {}
        request_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": raw_messages
            or [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "stream": True,
        }
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        stream = client.chat.completions.create(**request_kwargs)
        for chunk in stream:
            now = time.perf_counter()
            if first_chunk_seconds is None:
                first_chunk_seconds = now - started
            usage = self._extract_usage(chunk) or usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            chunks.extend(self._extract_delta_text(delta))
        return "".join(chunks).strip(), first_chunk_seconds, usage

    def _repair_json(
        self,
        *,
        gateway: GatewayHandle,
        model_name: str,
        response_model: type[T],
        broken_json: str,
    ) -> tuple[str, dict[str, Any]]:
        template = self._build_output_blueprint(response_model)
        prompt = {
            "template": template,
            "broken_json": broken_json,
            "repair_rules": repair_instruction_rules(),
        }
        raw_text, _first_chunk_seconds, usage, _used_stream = self._request_text(
            gateway=gateway,
            client=gateway.client,
            model_name=model_name,
            system_instruction="You repair invalid JSON into valid JSON that matches the provided template exactly.",
            user_content=self._dumps_json(prompt),
            temperature=0.0,
            max_output_tokens=self.project_config.stability.repair_max_output_tokens,
            response_format={"type": "json_object"},
        )
        return raw_text, usage

    def _build_local_request_cache_key(
        self,
        *,
        model_name: str,
        response_model: type[T],
        response_format: dict[str, Any],
        system_instruction: str,
        user_content: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        signature = {
            "cache_version": self.project_config.stability.local_request_cache_version,
            "api_route": self.project_config.model.api_route,
            "response_format_mode": self.project_config.model.response_format,
            "reasoning_effort": self.project_config.model.reasoning_effort,
            "model_name": model_name,
            "response_model_name": response_model.__name__,
            "response_schema": response_model.model_json_schema(by_alias=True),
            "response_format": response_format,
            "system_instruction": system_instruction,
            "user_content": user_content,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        canonical = self._canonical_json(signature)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _load_cached_structured_response(
        self,
        *,
        cache_key: str,
        response_model: type[T],
    ) -> CachedStructuredResponse | None:
        cache_path = self._local_request_cache_path(cache_key)
        if cache_path is None or not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            parsed_payload = payload.get("parsed_payload")
            parsed = self._validate_loaded_payload(parsed_payload, response_model)
            raw_text = str(payload.get("raw_text") or "").strip()
            if not raw_text:
                raw_text = self._dumps_json(parsed.model_dump(mode="json"))
            source_usage_metadata = payload.get("source_usage_metadata")
            if not isinstance(source_usage_metadata, dict):
                source_usage_metadata = {}
            cached_model_name = str(payload.get("model_name") or "")
            return CachedStructuredResponse(
                parsed=parsed,
                raw_text=raw_text,
                model_name=cached_model_name or response_model.__name__,
                source_usage_metadata=source_usage_metadata,
                cache_key=cache_key,
                cache_path=cache_path,
            )
        except Exception:  # noqa: BLE001
            return None

    def _store_cached_structured_response(
        self,
        *,
        cache_key: str,
        request_key: str,
        parsed: BaseModel,
        raw_text: str,
        model_name: str,
        source_usage_metadata: dict[str, Any],
    ) -> Path | None:
        cache_path = self._local_request_cache_path(cache_key)
        if cache_path is None:
            return None
        payload = {
            "cache_version": self.project_config.stability.local_request_cache_version,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "request_key": request_key,
            "model_name": model_name,
            "raw_text": raw_text,
            "parsed_payload": parsed.model_dump(mode="json"),
            "source_usage_metadata": source_usage_metadata,
        }
        try:
            ensure_dir(cache_path.parent)
            temp_path = cache_path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            temp_path.replace(cache_path)
            return cache_path
        except Exception:  # noqa: BLE001
            return None

    def _local_request_cache_path(self, cache_key: str) -> Path | None:
        if self.cache_dir is None or not cache_key:
            return None
        return self.cache_dir / cache_key[:2] / f"{cache_key}.json"

    def _build_cache_hit_usage_metadata(self, source_usage_metadata: dict[str, Any]) -> dict[str, Any]:
        usage = self._zero_numeric_values(source_usage_metadata)
        if not isinstance(usage, dict):
            usage = {}
        usage["cache_hit"] = True
        usage["cache_source"] = "local_request_cache"
        usage["source_usage_metadata"] = source_usage_metadata
        return usage

    @staticmethod
    def _usage_int(payload: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    @staticmethod
    def _usage_nested_int(payload: dict[str, Any], *path: str) -> int:
        current: Any = payload
        for part in path:
            if not isinstance(current, dict):
                return 0
            current = current.get(part)
        if isinstance(current, (int, float)):
            return int(current)
        return 0

    @classmethod
    def _usage_summary(cls, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        prompt_tokens = cls._usage_int(payload, "input_tokens", "prompt_tokens")
        output_tokens = cls._usage_int(payload, "output_tokens", "completion_tokens")
        total_tokens = cls._usage_int(payload, "total_tokens") or (prompt_tokens + output_tokens)
        cached_tokens = (
            cls._usage_nested_int(payload, "prompt_tokens_details", "cached_tokens")
            or cls._usage_nested_int(payload, "input_tokens_details", "cached_tokens")
        )
        return {
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit_ratio": round(cached_tokens / max(prompt_tokens, 1), 4) if prompt_tokens else 0.0,
        }

    @classmethod
    def _zero_numeric_values(cls, payload: Any) -> Any:
        if isinstance(payload, bool):
            return payload
        if isinstance(payload, int):
            return 0
        if isinstance(payload, float):
            return 0.0
        if isinstance(payload, dict):
            return {str(key): cls._zero_numeric_values(value) for key, value in payload.items()}
        if isinstance(payload, list):
            return [cls._zero_numeric_values(value) for value in payload]
        return payload

    @classmethod
    def _clone_usage_payload(cls, payload: Any) -> Any:
        if isinstance(payload, dict):
            return {str(key): cls._clone_usage_payload(value) for key, value in payload.items()}
        if isinstance(payload, list):
            return [cls._clone_usage_payload(value) for value in payload]
        return payload

    @classmethod
    def _merge_usage_metadata(cls, primary: Any, secondary: Any) -> Any:
        if primary is None or primary == "" or primary == [] or primary == {}:
            return cls._clone_usage_payload(secondary)
        if secondary is None or secondary == "" or secondary == [] or secondary == {}:
            return cls._clone_usage_payload(primary)

        if isinstance(primary, bool) or isinstance(secondary, bool):
            if isinstance(primary, bool) and isinstance(secondary, bool):
                return primary or secondary
            return cls._clone_usage_payload(primary)

        if isinstance(primary, int) and not isinstance(primary, bool) and isinstance(secondary, int) and not isinstance(secondary, bool):
            return primary + secondary
        if isinstance(primary, (int, float)) and isinstance(secondary, (int, float)):
            return float(primary) + float(secondary)

        if isinstance(primary, dict) and isinstance(secondary, dict):
            merged: dict[str, Any] = {}
            for key in {*(str(item) for item in primary.keys()), *(str(item) for item in secondary.keys())}:
                if key in primary and key in secondary:
                    merged[key] = cls._merge_usage_metadata(primary[key], secondary[key])
                elif key in primary:
                    merged[key] = cls._clone_usage_payload(primary[key])
                else:
                    merged[key] = cls._clone_usage_payload(secondary[key])
            return merged

        if isinstance(primary, list) and isinstance(secondary, list):
            return cls._clone_usage_payload(primary if primary else secondary)

        return cls._clone_usage_payload(primary if primary not in ("", [], {}) else secondary)

    @staticmethod
    def _canonical_json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    def _append_metrics(self, payload: dict[str, Any]) -> None:
        if not self.project_config.stability.record_request_metrics:
            return
        with self.metrics_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")

    @staticmethod
    def _sanitize_artifact_stem(request_key: str) -> str:
        return re.sub(r"[^0-9A-Za-z_.-]+", "_", request_key).strip("_") or "request"

    def _bounded_raw_artifact_path(self, *, request_key: str, suffix: str) -> Path:
        safe_key = self._sanitize_artifact_stem(request_key)
        raw_dir_text = str(self.raw_dir)
        available = RAW_ARTIFACT_PATH_LIMIT - len(raw_dir_text) - 1 - len(suffix)
        if available < len(safe_key):
            digest = hashlib.sha1(safe_key.encode("utf-8")).hexdigest()[:10]
            if available <= len(digest):
                safe_key = digest[: max(available, 1)]
            else:
                head_limit = max(available - len(digest) - 1, 1)
                head = safe_key[:head_limit].rstrip("._-")
                safe_key = f"{head}_{digest}" if head else digest[:available]
        return self.raw_dir / f"{safe_key}{suffix}"

    def _persist_raw_text(
        self,
        *,
        request_key: str,
        attempt: int,
        raw_text: str,
        success: bool,
        suffix: str = "",
    ) -> Path | None:
        if not raw_text:
            return None
        mode = self.project_config.stability.raw_response_mode
        if mode == "never":
            return None
        if mode == "errors_only" and success:
            return None
        extra = f"_{suffix}" if suffix else ""
        path = self._bounded_raw_artifact_path(
            request_key=request_key,
            suffix=f"_attempt{attempt}{extra}.txt",
        )
        path.write_text(raw_text, encoding="utf-8", newline="\n")
        return path

    def _persist_error_details(
        self,
        *,
        request_key: str,
        attempt: int,
        error_details: dict[str, Any],
    ) -> Path | None:
        mode = self.project_config.stability.raw_response_mode
        if mode == "never":
            return None
        payload = {
            "status_code": error_details.get("status_code"),
            "error_type": error_details.get("error_type"),
            "error_code": error_details.get("error_code"),
            "error_message": error_details.get("message"),
            "retry_after_seconds": error_details.get("retry_after_seconds"),
            "request_id": error_details.get("request_id"),
            "url": error_details.get("url"),
            "response_headers": error_details.get("headers") or {},
            "response_body": error_details.get("body_text") or "",
        }
        if not payload["error_message"] and not payload["response_body"]:
            return None
        path = self._bounded_raw_artifact_path(
            request_key=request_key,
            suffix=f"_attempt{attempt}_error.json",
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return path

    def _has_alternate_gateway(self, current_gateway_index: int) -> bool:
        return any(
            gateway.index != current_gateway_index and gateway.index not in self._disabled_gateway_indices
            for gateway in self._gateways
        )

    @classmethod
    def _extract_error_details(cls, exc: Exception) -> dict[str, Any]:
        status_code = cls._status_code_from_exception(exc)
        response = None
        if isinstance(exc, (httpx.HTTPStatusError, APIStatusError)):
            response = getattr(exc, "response", None)
        else:
            candidate_response = getattr(exc, "response", None)
            if candidate_response is not None:
                response = candidate_response
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        headers: dict[str, str] = {}
        url = ""
        body_text = ""
        if response is not None:
            headers = cls._response_headers(response)
            url = str(getattr(response, "url", "") or "")
            body_text = cls._extract_response_body_text(response)
        body_payload = getattr(exc, "body", None)
        if not body_text and body_payload not in (None, ""):
            body_text = cls._stringify_error_payload(body_payload)
        parsed_payload = cls._maybe_load_json(body_text)
        error_code = cls._extract_error_code(parsed_payload if parsed_payload is not None else body_payload)
        retry_after_seconds = cls._parse_retry_after_seconds(headers.get("retry-after"))
        request_id = headers.get("x-request-id") or headers.get("request-id") or ""
        body_excerpt = cls._truncate_text(body_text, limit=ERROR_BODY_EXCERPT_LIMIT)
        message = str(exc).strip()
        if body_excerpt and body_excerpt.casefold() not in message.casefold():
            message = f"{message}\nGateway body: {body_excerpt}"
        classifier_text = "\n".join(
            part
            for part in (
                str(exc).strip(),
                cls._stringify_error_payload(error_code),
                body_text,
            )
            if part
        )
        return {
            "status_code": status_code,
            "error_type": type(exc).__name__,
            "error_code": cls._stringify_error_payload(error_code),
            "message": message,
            "body_text": body_text,
            "body_excerpt": body_excerpt,
            "headers": headers,
            "url": url,
            "retry_after_seconds": retry_after_seconds,
            "request_id": request_id,
            "classifier_text": classifier_text,
        }

    @staticmethod
    def _response_headers(response: Any) -> dict[str, str]:
        try:
            return {
                str(key).casefold(): str(value)
                for key, value in getattr(response, "headers", {}).items()
            }
        except Exception:  # noqa: BLE001
            return {}

    @classmethod
    def _extract_response_body_text(cls, response: Any) -> str:
        if response is None:
            return ""
        try:
            if isinstance(response, httpx.Response):
                if not response.is_stream_consumed:
                    response.read()
                text = response.text
                if text:
                    return text.strip()
            else:
                text_value = getattr(response, "text", None)
                if isinstance(text_value, str) and text_value.strip():
                    return text_value.strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            json_payload = response.json()
        except Exception:  # noqa: BLE001
            return ""
        return cls._stringify_error_payload(json_payload)

    @staticmethod
    def _status_code_from_exception(exc: Exception) -> int | None:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, (int, float)):
            return int(status_code)
        response = getattr(exc, "response", None)
        candidate = getattr(response, "status_code", None)
        if isinstance(candidate, (int, float)):
            return int(candidate)
        return None

    @staticmethod
    def _maybe_load_json(value: str) -> Any | None:
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _extract_error_code(cls, payload: Any) -> str:
        if isinstance(payload, dict):
            if isinstance(payload.get("error"), dict):
                error_block = payload.get("error") or {}
                for key in ("code", "type", "error_code"):
                    value = error_block.get(key)
                    if value not in (None, ""):
                        return cls._stringify_error_payload(value)
            for key in ("code", "type", "error_code"):
                value = payload.get(key)
                if value not in (None, ""):
                    return cls._stringify_error_payload(value)
        return ""

    @staticmethod
    def _stringify_error_payload(value: Any) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value).strip()

    @staticmethod
    def _truncate_text(value: str, *, limit: int) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return f"{text[: limit - 3]}..."

    @classmethod
    def _parse_retry_after_seconds(cls, value: str | None) -> float | None:
        if not value:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        try:
            seconds = float(candidate)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(candidate)
            except Exception:  # noqa: BLE001
                return None
            now = datetime.now(timezone.utc)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - now).total_seconds()
        if seconds <= 0:
            return None
        return round(seconds, 3)

    def _maybe_cooldown(self) -> None:
        threshold = int(self.project_config.stability.cooldown_after_failures)
        if threshold <= 0 or self._consecutive_retryable_failures < threshold:
            return
        time.sleep(self.project_config.stability.cooldown_seconds)
        self._consecutive_retryable_failures = 0

    def _compute_backoff_seconds(
        self,
        attempt: int,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        apply_retry_bonus: bool = False,
    ) -> float:
        base = self.project_config.stability.base_backoff_seconds
        capped = min(base * (2 ** (attempt - 1)), self.project_config.stability.max_backoff_seconds)
        if apply_retry_bonus or status_code in UPSTREAM_RETRY_BONUS_STATUSES:
            base = max(base, float(self.project_config.stability.upstream_retry_min_backoff_seconds))
            capped = min(
                base * (2 ** (attempt - 1)),
                float(self.project_config.stability.upstream_retry_max_backoff_seconds),
            )
        if retry_after_seconds is not None:
            capped = max(capped, min(retry_after_seconds, float(self.project_config.stability.upstream_retry_max_backoff_seconds)))
        return round(capped * random.uniform(0.85, 1.15), 3)

    def _should_use_stream_for_request(
        self,
        *,
        gateway: GatewayHandle | None = None,
        force_stream: bool | None = None,
    ) -> bool:
        if force_stream is not None:
            return force_stream
        if self._use_responses_api():
            return self._should_stream_responses_for_gateway(gateway)
        return self.project_config.stability.stream

    def _use_responses_api(self) -> bool:
        return self.project_config.model.api_route == "responses"

    def _should_stream_responses_for_gateway(self, gateway: GatewayHandle | None) -> bool:
        target_gateway = gateway or self._gateways[self._preferred_gateway_index]
        return target_gateway.index not in self._responses_non_stream_gateway_indices

    def _build_transport_mode(self, used_stream: bool) -> str:
        route = "responses" if self._use_responses_api() else "chat_completions"
        stream_label = "stream" if used_stream else "non_stream"
        return f"{route}_{stream_label}"

    @staticmethod
    def _uses_raw_responses_stream(gateway: GatewayHandle) -> bool:
        hostname = (urlsplit(gateway.base_url).hostname or "").casefold()
        return hostname in RESPONSES_RAW_STREAM_HOSTS

    def _build_reasoning_config(self) -> dict[str, Any] | None:
        effort = (self.project_config.model.reasoning_effort or "").strip()
        if not effort:
            return None
        return {"effort": effort}

    def _respect_rate_limit(self) -> None:
        rpm = self.project_config.model.max_requests_per_minute
        if rpm <= 0:
            return
        min_interval = 60.0 / rpm
        now = time.monotonic()
        elapsed = now - self._last_request_started_at
        if self._last_request_started_at > 0 and elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_started_at = time.monotonic()

    @staticmethod
    def _classify_retryable(
        exc: Exception,
        *,
        status_code: int | None = None,
        error_text: str = "",
    ) -> tuple[bool, int | None]:
        if status_code is None:
            status_code = getattr(exc, "status_code", None)
        if status_code is None and isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
        message = (error_text or str(exc)).casefold()
        if isinstance(exc, (json.JSONDecodeError, ValidationError)):
            return True, status_code
        if StableOpenAICompatibleStructuredClient._is_retryable_json_contract_error(exc, message=message):
            return True, status_code
        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
            return True, status_code
        if isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.WriteTimeout,
                httpx.RemoteProtocolError,
            ),
        ):
            return True, status_code
        if status_code == 400 and any(
            marker in message
            for marker in TRANSIENT_400_MARKERS
        ):
            return True, status_code
        if status_code == 402 and any(
            marker in message
            for marker in (
                "rate limited",
                "rate-limit",
                "all api keys",
                "quota",
                "temporarily unavailable",
                "overloaded",
            )
        ):
            return True, status_code
        if status_code in {408, 409, 429, 500, 502, 503, 504}:
            return True, status_code
        return False, status_code

    @staticmethod
    def _should_apply_upstream_retry_bonus(*, status_code: int | None, error_text: str) -> bool:
        if status_code in UPSTREAM_RETRY_BONUS_STATUSES:
            return True
        if status_code == 400:
            normalized = error_text.casefold()
            return any(marker in normalized for marker in TRANSIENT_400_MARKERS)
        return False

    @staticmethod
    def _should_fallback_responses_stream(exc: Exception) -> bool:
        message = str(exc).casefold()
        if isinstance(exc, (json.JSONDecodeError, httpx.RemoteProtocolError)):
            return True
        if StableOpenAICompatibleStructuredClient._is_retryable_json_contract_error(exc, message=message):
            return True
        return any(
            marker in message
            for marker in (
                "stream_read_error",
                "incomplete chunked read",
                "response ended prematurely",
            )
        )

    @staticmethod
    def _is_retryable_json_contract_error(exc: Exception, *, message: str | None = None) -> bool:
        if isinstance(exc, (json.JSONDecodeError, ValidationError)):
            return True
        if not isinstance(exc, ValueError):
            return False
        normalized = (message or str(exc)).casefold()
        return any(
            marker in normalized
            for marker in (
                "empty content instead of json",
                "did not return valid json",
            )
        )

    @staticmethod
    def _should_restore_responses_stream(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        message = str(exc).casefold()
        return status_code == 400 and "stream must be set to true" in message

    def _build_gateway_handles(
        self,
        config: StableProjectConfig,
        *,
        timeout: httpx.Timeout,
    ) -> list[GatewayHandle]:
        gateway_configs = config.gateways or [GatewayConfig(label="default", api_key=config.api_key, base_url=config.base_url)]
        gateways: list[GatewayHandle] = []
        for gateway in gateway_configs:
            if not gateway.api_key or not gateway.base_url:
                continue
            gateway_index = len(gateways)
            http_client = httpx.Client(
                timeout=timeout,
                trust_env=False,
                follow_redirects=True,
                headers={"User-Agent": config.stability.user_agent},
            )
            gateways.append(
                GatewayHandle(
                    index=gateway_index,
                    label=gateway.label or f"gateway-{gateway_index + 1}",
                    base_url=gateway.base_url,
                    client=OpenAI(
                        api_key=gateway.api_key,
                        base_url=gateway.base_url,
                        timeout=config.model.timeout_seconds,
                        max_retries=0,
                        http_client=http_client,
                    ),
                    http_client=http_client,
                )
            )
        if not gateways:
            raise RuntimeError("Missing valid OPENAI-compatible gateway credentials.")
        return gateways

    def _iter_gateway_attempts(self, total_attempts: int) -> list[GatewayHandle]:
        available_gateways = [
            gateway
            for gateway in self._gateways
            if gateway.index not in self._disabled_gateway_indices
        ]
        if not available_gateways:
            available_gateways = list(self._gateways)
            self._disabled_gateway_indices.clear()
        gateway_count = len(available_gateways)
        preferred_index = self._preferred_gateway_index % len(self._gateways)
        preferred_gateway = self._gateways[preferred_index]
        if preferred_gateway.index in self._disabled_gateway_indices:
            start = 0
        else:
            start = next(
                (index for index, gateway in enumerate(available_gateways) if gateway.index == preferred_gateway.index),
                0,
            )
        return [
            available_gateways[(start + offset) % gateway_count]
            for offset in range(total_attempts)
        ]

    def _active_gateway_count(self) -> int:
        count = sum(1 for gateway in self._gateways if gateway.index not in self._disabled_gateway_indices)
        return count or len(self._gateways)

    @staticmethod
    def _should_disable_gateway_for_session(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        message = str(exc).casefold()
        if status_code == 403 and any(
            marker in message
            for marker in (
                "subscription_not_found",
                "no active subscription found",
                "model_not_found",
                "not available for this group",
            )
        ):
            return True
        return status_code == 400 and any(
            marker in message
            for marker in (
                "unsupported parameter: response_format",
                "invalid parameter: response_format",
                "response_format is not supported",
            )
        )

    def _dumps_json(self, payload: Any) -> str:
        if self.project_config.stability.compact_json:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _compose_system_instruction(
        self,
        system_instruction: str,
        response_model: type[T],
        *,
        response_format_mode: Literal["json_object", "json_schema"] | None = None,
        output_contract_mode: Literal["auto", "blueprint", "none"] | None = None,
    ) -> str:
        effective_mode = response_format_mode or self.project_config.model.response_format
        effective_contract_mode = output_contract_mode or "auto"
        if effective_contract_mode == "auto":
            effective_contract_mode = "none" if effective_mode == "json_schema" else "blueprint"

        if effective_contract_mode == "none":
            return system_instruction
        if effective_contract_mode != "blueprint":
            raise ValueError(f"Unsupported output_contract_mode: {effective_contract_mode}")

        blueprint_json = self._dumps_json(self._build_output_blueprint(response_model))
        return (
            f"{system_instruction}\n\n"
            "Output contract:\n"
            "- Return exactly one JSON object.\n"
            "- Do not wrap the JSON in markdown fences.\n"
            "- Do not add any prose before or after the JSON.\n"
            "- Use the exact field names from the template below.\n"
            "- Fill the template with concrete values extracted from the input payload.\n"
            "- If the prompt defines a refusal or fallback shape, follow that exact shape instead of inventing placeholders.\n"
            "- Otherwise keep empty strings or empty arrays only for fields the prompt allows to be empty.\n"
            "- Do not add extra keys.\n"
            "- Template:\n"
            f"{blueprint_json}"
        )

    def _build_response_format(
        self,
        response_model: type[T],
        *,
        response_format_mode: Literal["json_object", "json_schema"] | None = None,
    ) -> dict[str, Any]:
        effective_mode = response_format_mode or self.project_config.model.response_format
        if effective_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "strict": True,
                "schema": response_model.model_json_schema(by_alias=True),
            },
        }

    @classmethod
    def _field_external_name(cls, field_name: str, field_info: Any) -> str:
        for candidate in (
            getattr(field_info, "serialization_alias", None),
            getattr(field_info, "alias", None),
            field_name,
        ):
            if isinstance(candidate, str) and candidate:
                return candidate
        return field_name

    @classmethod
    def _field_input_keys(cls, field_name: str, field_info: Any) -> list[str]:
        keys: list[str] = []
        validation_alias = getattr(field_info, "validation_alias", None)
        if isinstance(validation_alias, str) and validation_alias:
            keys.append(validation_alias)
        else:
            choices = getattr(validation_alias, "choices", None)
            if isinstance(choices, (list, tuple)):
                for choice in choices:
                    if isinstance(choice, str) and choice and choice not in keys:
                        keys.append(choice)
        for candidate in (
            getattr(field_info, "serialization_alias", None),
            getattr(field_info, "alias", None),
            field_name,
        ):
            if isinstance(candidate, str) and candidate and candidate not in keys:
                keys.append(candidate)
        return keys

    @classmethod
    def _build_output_blueprint(cls, response_model: type[T]) -> dict[str, Any]:
        return {
            cls._field_external_name(field_name, field_info): cls._build_value_blueprint(field_info.annotation)
            for field_name, field_info in response_model.model_fields.items()
        }

    @classmethod
    def _build_value_blueprint(cls, annotation: Any) -> Any:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin in {list, tuple, set}:
            if not args:
                return []
            item_blueprint = cls._build_value_blueprint(args[0])
            return [item_blueprint] if isinstance(item_blueprint, dict) else []
        if origin is Literal:
            return " | ".join(str(item) for item in args)
        if origin is not None:
            non_none_args = [item for item in args if item is not type(None)]
            if non_none_args:
                return cls._build_value_blueprint(non_none_args[0])
        if annotation is bool:
            return False
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
        if annotation in {str, Any}:
            return ""
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return cls._build_output_blueprint(annotation)
        return ""

    @classmethod
    def _coerce_to_model_shape(cls, payload: Any, annotation: Any) -> Any:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if not isinstance(payload, dict):
                return payload
            coerced: dict[str, Any] = {}
            for field_name, field_info in annotation.model_fields.items():
                source_key = next(
                    (key for key in cls._field_input_keys(field_name, field_info) if key in payload),
                    None,
                )
                if source_key is None:
                    continue
                coerced[field_name] = cls._coerce_to_model_shape(payload[source_key], field_info.annotation)
            return coerced
        if origin in {list, tuple, set}:
            item_annotation = args[0] if args else Any
            if payload is None:
                return []
            if not isinstance(payload, list):
                payload = [payload]
            return [cls._coerce_to_model_shape(item, item_annotation) for item in payload]
        if origin is Literal:
            if isinstance(payload, (dict, list)):
                return cls._stringify_malformed_value(payload)
            return payload
        if origin is not None:
            non_none_args = [item for item in args if item is not type(None)]
            if non_none_args:
                return cls._coerce_to_model_shape(payload, non_none_args[0])
            return payload
        if annotation is str:
            if isinstance(payload, str):
                return payload
            return cls._stringify_malformed_value(payload)
        if annotation is bool:
            if isinstance(payload, bool):
                return payload
            if isinstance(payload, str):
                return payload.strip().lower() in {"true", "1", "yes", "y"}
            return bool(payload)
        if annotation is int:
            try:
                return int(payload)
            except Exception:  # noqa: BLE001
                return 0
        if annotation is float:
            try:
                return float(payload)
            except Exception:  # noqa: BLE001
                return 0.0
        return payload

    @classmethod
    def _validate_loaded_payload(cls, payload: Any, response_model: type[T]) -> T:
        try:
            return response_model.model_validate(payload)
        except (ValidationError, ValueError, TypeError):
            normalized_payload = cls._coerce_to_model_shape(payload, response_model)
            if normalized_payload is payload:
                raise
            return response_model.model_validate(normalized_payload)

    @classmethod
    def _stringify_malformed_value(cls, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            parts = [cls._stringify_malformed_value(item).strip() for item in payload]
            return "；".join(part for part in parts if part)
        if isinstance(payload, dict):
            preferred_keys = [
                "value",
                "text",
                "summary",
                "description",
                "explanation",
                "mechanism",
                "target",
                "pattern",
                "reason",
                "claim",
                "name",
                "note",
            ]
            primary = ""
            for key in preferred_keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    primary = value.strip()
                    break
            extra_parts = []
            for value in payload.values():
                text = cls._stringify_malformed_value(value).strip()
                if not text or text == primary:
                    continue
                extra_parts.append(text)
            if primary and extra_parts:
                return f"{primary}：{'；'.join(extra_parts)}"
            if primary:
                return primary
            return "；".join(extra_parts)
        return str(payload)

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        if isinstance(response, str):
            return response.strip()
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        output = getattr(response, "output", None)
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
                if item_type != "message":
                    continue
                content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
                if not isinstance(content, list):
                    continue
                for entry in content:
                    entry_type = entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                    if entry_type not in {"output_text", "text"}:
                        continue
                    text_value = entry.get("text") if isinstance(entry, dict) else getattr(entry, "text", None)
                    if text_value:
                        parts.append(str(text_value))
            if parts:
                return "".join(parts).strip()
        try:
            message = response.choices[0].message
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Unexpected response payload shape: {type(response).__name__}") from exc
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                    continue
                text_value = getattr(item, "text", None)
                if text_value:
                    parts.append(str(text_value))
            return "".join(parts).strip()
        return ""

    @staticmethod
    def _extract_delta_text(delta: Any) -> list[str]:
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
                    continue
                text_value = getattr(item, "text", None)
                if text_value:
                    parts.append(str(text_value))
            return parts
        return []

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict):
                return usage
            if usage is not None:
                return {"value": str(usage)}
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump(mode="json")
        if isinstance(usage, dict):
            return usage
        return {"value": str(usage)}

    @staticmethod
    def _loads_json(raw_text: str) -> Any:
        return loads_json_fragment(raw_text)

