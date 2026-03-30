from __future__ import annotations

import pytest

from schema_analyzer.errors import SchemaAnalyzerError
from schema_analyzer.providers import (
    create_provider,
    get_default_model,
    get_provider_env_var,
    list_providers,
    register_provider,
)


def test_list_providers_includes_builtins():
    providers = list_providers()
    assert "openai" in providers
    assert "anthropic" in providers
    assert "openrouter" in providers


def test_get_provider_env_var():
    assert get_provider_env_var("openai") == "OPENAI_API_KEY"
    assert get_provider_env_var("anthropic") == "ANTHROPIC_API_KEY"
    assert get_provider_env_var("unknown") is None


def test_get_default_model():
    assert "gpt" in get_default_model("openai").lower() or "4o" in get_default_model("openai")
    assert "claude" in get_default_model("anthropic").lower() or "sonnet" in get_default_model("anthropic").lower()


def test_get_default_model_unknown():
    with pytest.raises(SchemaAnalyzerError, match="Unknown llm_provider"):
        get_default_model("nonexistent")


def test_create_provider_unknown():
    with pytest.raises(SchemaAnalyzerError, match="Unknown llm_provider"):
        create_provider("nonexistent", api_key="fake")


def test_register_and_create_custom_provider():
    register_provider(
        "test_custom",
        module="schema_analyzer.providers.openai_provider",
        class_name="OpenAIProvider",
        env_var="CUSTOM_KEY",
        default_model="custom-model",
    )
    assert "test_custom" in list_providers()
    assert get_provider_env_var("test_custom") == "CUSTOM_KEY"
    assert get_default_model("test_custom") == "custom-model"
