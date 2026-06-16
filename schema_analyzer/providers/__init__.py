from __future__ import annotations

from typing import Any, cast

from ..defaults import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
)
from ..errors import SchemaAnalyzerError
from .base import LLMProvider, LLMResponse

_REGISTRY: dict[str, dict[str, Any]] = {
    "openai": {
        "module": "schema_analyzer.providers.openai_provider",
        "class": "OpenAIProvider",
        "env_var": "OPENAI_API_KEY",
        "default_model": DEFAULT_OPENAI_MODEL,
    },
    "anthropic": {
        "module": "schema_analyzer.providers.anthropic_provider",
        "class": "AnthropicProvider",
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": DEFAULT_ANTHROPIC_MODEL,
    },
    "openrouter": {
        "module": "schema_analyzer.providers.openrouter_provider",
        "class": "OpenRouterProvider",
        "env_var": "OPENROUTER_API_KEY",
        "default_model": DEFAULT_OPENROUTER_MODEL,
    },
}


def register_provider(
    name: str,
    *,
    module: str,
    class_name: str,
    env_var: str,
    default_model: str,
) -> None:
    _REGISTRY[name.lower()] = {
        "module": module,
        "class": class_name,
        "env_var": env_var,
        "default_model": default_model,
    }


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_provider_env_var(name: str) -> str | None:
    entry = _REGISTRY.get(name.lower())
    return entry["env_var"] if entry else None


def get_default_model(name: str) -> str:
    entry = _REGISTRY.get(name.lower())
    if not entry:
        raise SchemaAnalyzerError(f"Unknown llm_provider: {name}", code="INVALID_ARGUMENT")
    return cast(str, entry["default_model"])


def create_provider(name: str, *, api_key: str) -> LLMProvider:
    key = name.lower()
    entry = _REGISTRY.get(key)
    if not entry:
        raise SchemaAnalyzerError(f"Unknown llm_provider: {name}", code="INVALID_ARGUMENT")

    import importlib

    mod = importlib.import_module(entry["module"])
    cls = getattr(mod, entry["class"])
    return cast("LLMProvider", cls(api_key=api_key))


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "register_provider",
    "list_providers",
    "get_provider_env_var",
    "get_default_model",
    "create_provider",
]
