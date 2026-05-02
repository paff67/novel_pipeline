from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    fact_model: str = "gemini-2.5-pro"
    style_model: str = "gemini-3-pro-preview"
    style_bible_model: str = ""
    semantic_judge_model: str = ""
    env_profile: str = ""
    api_route: str = Field(default="chat_completions", pattern="^(chat_completions|responses)$")
    reasoning_effort: str = ""
    fact_temperature: float = 0.1
    style_temperature: float = 0.3
    style_bible_temperature: float | None = None
    fact_max_output_tokens: int = 8192
    style_max_output_tokens: int = 8192
    style_bible_max_output_tokens: int | None = None
    timeout_seconds: int = 120
    retry_count: int = 3
    response_format: str = "json_schema"
    max_requests_per_minute: float = 2.0

    @property
    def resolved_style_bible_model(self) -> str:
        return _first_nonempty(self.style_bible_model, self.style_model)

    @property
    def resolved_semantic_judge_model(self) -> str:
        return _first_nonempty(self.semantic_judge_model, self.style_bible_model, self.style_model)


class SceneSplitConfig(BaseModel):
    min_chars: int = 500
    target_chars: int = 900
    max_chars: int = 1400


class StyleWindowConfig(BaseModel):
    window_size: int = 5
    stride: int = 3


class PathConfig(BaseModel):
    prompt_dir: str = "prompts"


class StabilityConfig(BaseModel):
    stream: bool = True
    user_agent: str = "novel-pipeline/0.1"
    connect_timeout_seconds: float = 15.0
    read_timeout_seconds: float = 180.0
    write_timeout_seconds: float = 30.0
    pool_timeout_seconds: float = 30.0
    base_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 20.0
    upstream_retry_bonus_attempts: int = 3
    upstream_retry_min_backoff_seconds: float = 8.0
    upstream_retry_max_backoff_seconds: float = 45.0
    cooldown_after_failures: int = 3
    cooldown_seconds: float = 45.0
    compact_json: bool = True
    omit_empty_normalization_applied: bool = True
    enable_json_repair: bool = True
    repair_max_output_tokens: int = 2048
    facts_two_pass_enabled: bool = True
    facts_two_pass_scene_char_threshold: int = 1200
    facts_two_pass_request_char_threshold: int = 3800
    facts_two_pass_on_failure: bool = True
    facts_two_pass_include_primary_context: bool = True
    raw_response_mode: str = Field(default="errors_only", pattern="^(never|errors_only|all)$")
    enable_local_request_cache: bool = True
    local_request_cache_dirname: str = "_request_cache"
    local_request_cache_version: str = "v1"
    record_request_metrics: bool = True


class StyleBibleReduceConfig(BaseModel):
    mode: str = Field(default="hierarchical", pattern="^hierarchical$")
    local_reduce_concurrency: int = Field(default=1, ge=1)
    critical_buckets: list[str] = Field(default_factory=list)
    max_failed_bucket_count: int = Field(default=1, ge=0)
    max_failed_bucket_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    supporting_evidence_soft_cap: int = Field(default=18, ge=1)
    supporting_evidence_hard_cap: int = Field(default=20, ge=1)


class GatewayConfig(BaseModel):
    label: str = ""
    api_key: str = ""
    base_url: str = ""


class EmbeddingConfig(BaseModel):
    enabled: bool = False
    model: str = ""
    env_profile: str = ""
    max_batch_size: int = Field(default=16, ge=1)
    retrieval_top_k: int = Field(default=12, ge=1)
    dedupe_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    slot_match_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class ProjectConfig(BaseModel):
    project_root: Path
    model: ModelConfig = Field(default_factory=ModelConfig)
    scene_split: SceneSplitConfig = Field(default_factory=SceneSplitConfig)
    style_windows: StyleWindowConfig = Field(default_factory=StyleWindowConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    style_bible_reduce: StyleBibleReduceConfig = Field(default_factory=StyleBibleReduceConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    api_key: str = ""
    base_url: str = ""
    gateways: list[GatewayConfig] = Field(default_factory=list)
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_gateways: list[GatewayConfig] = Field(default_factory=list)

    @property
    def prompt_dir(self) -> Path:
        return (self.project_root / self.paths.prompt_dir).resolve()


@dataclass(slots=True)
class StableProjectConfig:
    base: ProjectConfig
    stability: StabilityConfig
    config_path: Path

    @property
    def model(self) -> ModelConfig:
        return self.base.model

    @property
    def scene_split(self) -> SceneSplitConfig:
        return self.base.scene_split

    @property
    def style_windows(self) -> StyleWindowConfig:
        return self.base.style_windows

    @property
    def paths(self) -> PathConfig:
        return self.base.paths

    @property
    def prompt_dir(self) -> Path:
        return self.base.prompt_dir

    @property
    def style_bible_reduce(self) -> StyleBibleReduceConfig:
        return self.base.style_bible_reduce

    @property
    def project_root(self) -> Path:
        return self.base.project_root

    @property
    def api_key(self) -> str:
        return self.base.api_key

    @property
    def base_url(self) -> str:
        return self.base.base_url

    @property
    def gateways(self) -> list[GatewayConfig]:
        return self.base.gateways

    @property
    def embedding(self) -> EmbeddingConfig:
        return self.base.embedding

    @property
    def embedding_api_key(self) -> str:
        return self.base.embedding_api_key

    @property
    def embedding_base_url(self) -> str:
        return self.base.embedding_base_url

    @property
    def embedding_gateways(self) -> list[GatewayConfig]:
        return self.base.embedding_gateways

    @property
    def semantic_judge_model(self) -> str:
        return self.base.model.resolved_semantic_judge_model

    def as_project_config(self) -> ProjectConfig:
        return self.base


def _normalize_base_url(value: str) -> str:
    if not value:
        return value
    parts = urlsplit(value.strip())
    normalized_path = "/" + "/".join(part for part in parts.path.split("/") if part)
    if not normalized_path.strip("/"):
        normalized_path = ""
    return urlunsplit((parts.scheme, parts.netloc, normalized_path, parts.query, parts.fragment))


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))


def _apply_model_env_overrides(model: ModelConfig) -> ModelConfig:
    rpm_override = os.getenv("NOVEL_PIPELINE_MAX_RPM") or os.getenv("NOVEL_PIPELINE_MAX_REQUESTS_PER_MINUTE")
    if rpm_override:
        model.max_requests_per_minute = float(rpm_override)
    return model


def _first_nonempty(*values: str) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _slugify_profile_name(value: str) -> str:
    collapsed = re.sub(r"[^0-9a-zA-Z]+", "-", value.strip().lower()).strip("-")
    return collapsed or "default"


@dataclass(slots=True)
class EnvProfileSection:
    label: str
    canonical: str
    aliases: set[str]
    values: dict[str, str]


def _build_profile_aliases(label: str) -> set[str]:
    aliases = {label.strip().casefold(), _slugify_profile_name(label)}
    lowered = label.casefold()
    if "gemini" in lowered:
        aliases.add("gemini")
    if "gpt" in lowered:
        aliases.add("gpt")
    if "openai" in lowered:
        aliases.add("openai")
    return {alias for alias in aliases if alias}


def _parse_env_profiles(env_path: Path) -> list[EnvProfileSection]:
    sections: list[EnvProfileSection] = []
    current_label: str | None = None
    current_values: dict[str, str] = {}

    def commit_section() -> None:
        nonlocal current_label, current_values
        if not current_label:
            current_values = {}
            return
        sections.append(
            EnvProfileSection(
                label=current_label,
                canonical=_slugify_profile_name(current_label),
                aliases=_build_profile_aliases(current_label),
                values=dict(current_values),
            )
        )
        current_values = {}

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            commit_section()
            label = line.lstrip("#").strip()
            current_label = label or None
            continue
        if "=" not in raw_line:
            continue

        key, value = raw_line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]

        if current_label:
            current_values[key] = value

    commit_section()
    return sections


def _resolve_profile_sections(env_path: Path | None, env_profile: str) -> list[EnvProfileSection]:
    if not env_path or not env_path.exists() or not env_profile:
        return []

    requested = env_profile.strip().casefold()
    normalized = _slugify_profile_name(env_profile)
    matches: list[EnvProfileSection] = []
    for section in _parse_env_profiles(env_path):
        if requested in section.aliases or normalized in section.aliases:
            matches.append(section)
    return matches


def _resolve_profile_setting(
    *,
    env_path: Path | None,
    env_profile: str,
    keys: list[str] | tuple[str, ...],
) -> str:
    for section in _resolve_profile_sections(env_path, env_profile):
        for key in keys:
            value = _first_nonempty(section.values.get(key, ""))
            if value:
                return value
    return ""


def _resolve_env_setting(keys: list[str] | tuple[str, ...]) -> str:
    for key in keys:
        value = _first_nonempty(os.getenv(key, ""))
        if value:
            return value
    return ""


def _apply_embedding_env_overrides(
    embedding: EmbeddingConfig,
    *,
    env_path: Path | None,
    default_env_profile: str = "",
) -> EmbeddingConfig:
    effective_profile = _first_nonempty(embedding.env_profile, default_env_profile)
    embedding_model = _first_nonempty(
        _resolve_env_setting(("NOVEL_PIPELINE_EMBEDDING_MODEL", "EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL")),
        _resolve_profile_setting(
            env_path=env_path,
            env_profile=effective_profile,
            keys=("EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL", "MODEL_NAME"),
        ),
    )
    if embedding_model:
        embedding.model = embedding_model
    return embedding


def _build_gateway_configs(
    *,
    env_path: Path | None,
    env_profile: str,
) -> list[GatewayConfig]:
    gateways: list[GatewayConfig] = []
    seen: set[tuple[str, str]] = set()

    profile_sections = _resolve_profile_sections(env_path, env_profile)
    for section in profile_sections:
        api_key = section.values.get("OPENAI_COMPAT_API_KEY") or section.values.get("OPENAI_API_KEY") or ""
        base_url = section.values.get("OPENAI_COMPAT_BASE_URL") or section.values.get("OPENAI_BASE_URL") or ""
        normalized_url = _normalize_base_url(base_url)
        if not api_key or not normalized_url:
            continue
        fingerprint = (normalized_url, api_key)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        gateways.append(GatewayConfig(label=section.label, api_key=api_key, base_url=normalized_url))

    if not gateways:
        env_api_key = os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        env_base_url = _normalize_base_url(os.getenv("OPENAI_COMPAT_BASE_URL") or os.getenv("OPENAI_BASE_URL", ""))
        if env_api_key and env_base_url:
            fingerprint = (env_base_url, env_api_key)
            if fingerprint not in seen:
                fallback_label = env_profile or "environment"
                gateways.append(GatewayConfig(label=fallback_label, api_key=env_api_key, base_url=env_base_url))

    return gateways


def _apply_gateway_filter_overrides(
    gateways: list[GatewayConfig],
    *,
    allowed_labels_env: str = "NOVEL_PIPELINE_ALLOWED_GATEWAY_LABELS",
    blocked_labels_env: str = "NOVEL_PIPELINE_BLOCKED_GATEWAY_LABELS",
    allowed_indexes_env: str = "NOVEL_PIPELINE_ALLOWED_GATEWAY_INDEXES",
    blocked_indexes_env: str = "NOVEL_PIPELINE_BLOCKED_GATEWAY_INDEXES",
) -> list[GatewayConfig]:
    if not gateways:
        return gateways

    allowed_labels_raw = (os.getenv(allowed_labels_env) or "").strip()
    blocked_labels_raw = (os.getenv(blocked_labels_env) or "").strip()
    allowed_indexes_raw = (os.getenv(allowed_indexes_env) or "").strip()
    blocked_indexes_raw = (os.getenv(blocked_indexes_env) or "").strip()

    allowed_labels = {label.strip() for label in allowed_labels_raw.split(",") if label.strip()}
    blocked_labels = {label.strip() for label in blocked_labels_raw.split(",") if label.strip()}
    allowed_indexes = {int(value.strip()) for value in allowed_indexes_raw.split(",") if value.strip()}
    blocked_indexes = {int(value.strip()) for value in blocked_indexes_raw.split(",") if value.strip()}

    filtered = gateways
    if allowed_indexes:
        filtered = [
            gateway
            for index, gateway in enumerate(filtered, start=1)
            if index in allowed_indexes
        ]
        if not filtered:
            raise RuntimeError(
                f"{allowed_indexes_env} did not match any configured gateways."
            )

    if blocked_indexes:
        filtered = [
            gateway
            for index, gateway in enumerate(filtered, start=1)
            if index not in blocked_indexes
        ]
        if not filtered:
            raise RuntimeError(
                f"{blocked_indexes_env} excluded all configured gateways."
            )

    if allowed_labels:
        filtered = [gateway for gateway in filtered if gateway.label.strip() in allowed_labels]
        if not filtered:
            raise RuntimeError(
                f"{allowed_labels_env} did not match any configured gateways."
            )

    if blocked_labels:
        filtered = [gateway for gateway in filtered if gateway.label.strip() not in blocked_labels]
        if not filtered:
            raise RuntimeError(
                f"{blocked_labels_env} excluded all configured gateways."
            )

    return filtered


def _apply_gateway_preference_overrides(
    gateways: list[GatewayConfig],
    *,
    preferred_label_env: str = "NOVEL_PIPELINE_PRIMARY_GATEWAY_LABEL",
    preferred_index_env: str = "NOVEL_PIPELINE_PRIMARY_GATEWAY_INDEX",
) -> list[GatewayConfig]:
    if len(gateways) <= 1:
        return gateways

    preferred_label = (os.getenv(preferred_label_env) or "").strip()
    preferred_index_raw = (os.getenv(preferred_index_env) or "").strip()
    preferred_pos: int | None = None

    if preferred_label:
        for index, gateway in enumerate(gateways):
            if gateway.label.strip() == preferred_label:
                preferred_pos = index
                break

    if preferred_pos is None and preferred_index_raw:
        try:
            preferred_index = int(preferred_index_raw)
        except ValueError:
            preferred_index = 0
        if 1 <= preferred_index <= len(gateways):
            preferred_pos = preferred_index - 1

    if preferred_pos is None or preferred_pos <= 0:
        return gateways
    return [gateways[preferred_pos], *gateways[:preferred_pos], *gateways[preferred_pos + 1 :]]


def load_project_config(config_path: str | Path) -> ProjectConfig:
    path = Path(config_path).resolve()
    project_root = path.parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    raw = _read_toml(path)
    model = _apply_model_env_overrides(ModelConfig.model_validate(raw.get("models", {})))
    embedding = _apply_embedding_env_overrides(
        EmbeddingConfig.model_validate(raw.get("embedding", {})),
        env_path=env_path if env_path.exists() else None,
        default_env_profile=model.env_profile,
    )
    gateways = _build_gateway_configs(
        env_path=env_path if env_path.exists() else None,
        env_profile=model.env_profile,
    )
    gateways = _apply_gateway_filter_overrides(gateways)
    gateways = _apply_gateway_preference_overrides(gateways)
    primary_gateway = gateways[0] if gateways else GatewayConfig()
    embedding_gateways = _build_gateway_configs(
        env_path=env_path if env_path.exists() else None,
        env_profile=embedding.env_profile or model.env_profile,
    )
    embedding_gateways = _apply_gateway_filter_overrides(
        embedding_gateways,
        allowed_labels_env="NOVEL_PIPELINE_EMBEDDING_ALLOWED_GATEWAY_LABELS",
        blocked_labels_env="NOVEL_PIPELINE_EMBEDDING_BLOCKED_GATEWAY_LABELS",
        allowed_indexes_env="NOVEL_PIPELINE_EMBEDDING_ALLOWED_GATEWAY_INDEXES",
        blocked_indexes_env="NOVEL_PIPELINE_EMBEDDING_BLOCKED_GATEWAY_INDEXES",
    )
    embedding_gateways = _apply_gateway_preference_overrides(
        embedding_gateways,
        preferred_label_env="NOVEL_PIPELINE_EMBEDDING_PRIMARY_GATEWAY_LABEL",
        preferred_index_env="NOVEL_PIPELINE_EMBEDDING_PRIMARY_GATEWAY_INDEX",
    )
    primary_embedding_gateway = embedding_gateways[0] if embedding_gateways else primary_gateway
    return ProjectConfig(
        project_root=project_root,
        model=model,
        scene_split=SceneSplitConfig.model_validate(raw.get("scene_split", {})),
        style_windows=StyleWindowConfig.model_validate(raw.get("style_windows", {})),
        paths=PathConfig.model_validate(raw.get("paths", {})),
        style_bible_reduce=StyleBibleReduceConfig.model_validate(raw.get("style_bible_reduce", {})),
        embedding=embedding,
        api_key=primary_gateway.api_key,
        base_url=primary_gateway.base_url,
        gateways=gateways,
        embedding_api_key=primary_embedding_gateway.api_key,
        embedding_base_url=primary_embedding_gateway.base_url,
        embedding_gateways=embedding_gateways,
    )


def load_stable_project_config(config_path: str | Path) -> StableProjectConfig:
    path = Path(config_path).resolve()
    base = load_project_config(path)
    raw = _read_toml(path)
    stability = StabilityConfig.model_validate(raw.get("stability", {}))
    return StableProjectConfig(base=base, stability=stability, config_path=path)


__all__ = [
    "ModelConfig",
    "SceneSplitConfig",
    "StyleWindowConfig",
    "PathConfig",
    "StabilityConfig",
    "StyleBibleReduceConfig",
    "EmbeddingConfig",
    "GatewayConfig",
    "ProjectConfig",
    "StableProjectConfig",
    "load_project_config",
    "load_stable_project_config",
]
