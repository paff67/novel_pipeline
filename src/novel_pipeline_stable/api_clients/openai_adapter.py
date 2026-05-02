from __future__ import annotations

from .base import (
    StableOpenAICompatibleStructuredClient,
    StructuredGenerationError,
    StructuredResponse,
)


OpenAICompatibleStructuredClient = StableOpenAICompatibleStructuredClient


__all__ = [
    "OpenAICompatibleStructuredClient",
    "StableOpenAICompatibleStructuredClient",
    "StructuredGenerationError",
    "StructuredResponse",
]
