from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..errors import SchemaAnalyzerError


@dataclass
class LLMResponse:
    text: str
    raw: Any | None = None


def import_optional_sdk(module_name: str, extra_name: str) -> Any:
    """Import an optional provider SDK or raise PROVIDER_MISSING.

    Centralises the boilerplate that every provider used to repeat. The
    ``extra_name`` is what the user has to install via
    ``pip install -e '.[<extra_name>]'``.
    """
    try:
        return importlib.import_module(module_name)
    except Exception as e:  # pragma: no cover - exercised via providers
        raise SchemaAnalyzerError(
            f"{module_name} SDK not installed. Install extra: pip install -e '.[{extra_name}]'",
            code="PROVIDER_MISSING",
            cause=e,
        ) from e


@contextmanager
def wrap_provider_call(label: str) -> Iterator[None]:
    """Convert any exception raised inside the block to a PROVIDER_ERROR.

    ``label`` is rendered into the user-visible message (e.g. ``"OpenAI request"``).
    Lets every provider share a single error-translation policy.
    """
    try:
        yield
    except SchemaAnalyzerError:
        raise
    except Exception as e:  # pragma: no cover - exercised via providers
        raise SchemaAnalyzerError(f"{label} failed", code="PROVIDER_ERROR", cause=e) from e


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
