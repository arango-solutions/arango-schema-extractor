from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    text: str
    raw: Any | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous LLM provider protocol.

    Implementations must supply a ``generate`` method that sends a prompt to
    the model and returns an :class:`LLMResponse`.
    """

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse: ...


@runtime_checkable
class AsyncLLMProvider(Protocol):
    """Asynchronous LLM provider protocol.

    Mirror of :class:`LLMProvider` for ``async``/``await`` callers.
    Implementations must supply an ``agenerate`` coroutine with the same
    parameter contract as :meth:`LLMProvider.generate`.
    """

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse: ...
