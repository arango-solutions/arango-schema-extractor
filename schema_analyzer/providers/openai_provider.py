from __future__ import annotations

from dataclasses import dataclass

from ..defaults import LLM_TEMPERATURE
from .base import LLMResponse, import_optional_sdk, wrap_provider_call


def _import_openai():
    return import_optional_sdk("openai", "openai")


@dataclass
class OpenAIProvider:
    api_key: str

    def _messages(self, system: str, prompt: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        openai = _import_openai()
        client = openai.OpenAI(api_key=self.api_key)
        with wrap_provider_call("OpenAI request"):
            resp = client.chat.completions.create(
                model=model,
                messages=self._messages(system, prompt),
                temperature=LLM_TEMPERATURE,
                timeout=timeout_ms / 1000.0,
            )

        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        openai = _import_openai()
        client = openai.AsyncOpenAI(api_key=self.api_key)
        with wrap_provider_call("OpenAI async request"):
            resp = await client.chat.completions.create(
                model=model,
                messages=self._messages(system, prompt),
                temperature=LLM_TEMPERATURE,
                timeout=timeout_ms / 1000.0,
            )

        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)
