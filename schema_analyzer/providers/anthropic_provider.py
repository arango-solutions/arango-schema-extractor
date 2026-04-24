from __future__ import annotations

from dataclasses import dataclass

from ..defaults import ANTHROPIC_MAX_TOKENS, LLM_TEMPERATURE
from .base import LLMResponse, import_optional_sdk, wrap_provider_call


def _import_anthropic():
    return import_optional_sdk("anthropic", "anthropic")


def _extract_text(resp) -> str:
    text = ""
    try:
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
    except Exception:
        text = str(resp)
    return text


@dataclass
class AnthropicProvider:
    api_key: str

    def _request_kwargs(self, model: str, system: str, prompt: str, timeout_ms: int) -> dict:
        return {
            "model": model,
            "system": system,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": LLM_TEMPERATURE,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout_ms / 1000.0,
        }

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        anthropic = _import_anthropic()
        client = anthropic.Anthropic(api_key=self.api_key)
        with wrap_provider_call("Anthropic request"):
            resp = client.messages.create(**self._request_kwargs(model, system, prompt, timeout_ms))
        return LLMResponse(text=_extract_text(resp), raw=resp)

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        anthropic = _import_anthropic()
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        with wrap_provider_call("Anthropic async request"):
            resp = await client.messages.create(**self._request_kwargs(model, system, prompt, timeout_ms))
        return LLMResponse(text=_extract_text(resp), raw=resp)
