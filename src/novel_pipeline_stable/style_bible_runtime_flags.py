from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip()
    return cleaned or default


@dataclass(frozen=True, slots=True)
class StyleBibleRuntimeFlags:
    router_semantic_cutover_enabled: bool = False
    router_lexical_fallback_enabled: bool = True
    selective_cutover_target: str = "router"

    def as_dict(self) -> dict[str, Any]:
        return {
            "router_semantic_cutover_enabled": bool(self.router_semantic_cutover_enabled),
            "router_lexical_fallback_enabled": bool(self.router_lexical_fallback_enabled),
            "selective_cutover_target": self.selective_cutover_target,
        }


def load_style_bible_runtime_flags() -> StyleBibleRuntimeFlags:
    return StyleBibleRuntimeFlags(
        router_semantic_cutover_enabled=_env_bool("NOVEL_PIPELINE_ROUTER_SEMANTIC_CUTOVER_ENABLED", False),
        router_lexical_fallback_enabled=_env_bool("NOVEL_PIPELINE_ROUTER_LEXICAL_FALLBACK_ENABLED", True),
        selective_cutover_target=_env_text("NOVEL_PIPELINE_STYLE_BIBLE_SELECTIVE_CUTOVER_TARGET", "router"),
    )


DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS = load_style_bible_runtime_flags()


__all__ = [
    "DEFAULT_STYLE_BIBLE_RUNTIME_FLAGS",
    "StyleBibleRuntimeFlags",
    "load_style_bible_runtime_flags",
]
