from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    text: str
    raw: Any | None = None


@runtime_checkable
class LLMProvider(Protocol):
    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse: ...


@runtime_checkable
class AsyncLLMProvider(Protocol):
    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse: ...

