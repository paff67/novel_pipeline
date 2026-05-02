from __future__ import annotations

from .base import StableOpenAICompatibleStructuredClient


class SiliconflowStructuredClient(StableOpenAICompatibleStructuredClient):
    """SiliconFlow currently shares the same OpenAI-compatible structured transport."""


__all__ = ["SiliconflowStructuredClient"]
