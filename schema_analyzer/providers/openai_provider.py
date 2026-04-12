from __future__ import annotations

from dataclasses import dataclass

from ..defaults import LLM_TEMPERATURE
from ..errors import SchemaAnalyzerError
from .base import LLMResponse


def _import_openai():
    try:
        import openai  # type: ignore

        return openai
    except Exception as e:  # pragma: no cover
        raise SchemaAnalyzerError(
            "OpenAI SDK not installed. Install extra: pip install -e '.[openai]'",
            code="PROVIDER_MISSING",
            cause=e,
        ) from e


@dataclass
class OpenAIProvider:
    api_key: str

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        openai = _import_openai()
        client = openai.OpenAI(api_key=self.api_key)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=LLM_TEMPERATURE,
                timeout=timeout_ms / 1000.0,
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenAI request failed", code="PROVIDER_ERROR", cause=e) from e

        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        openai = _import_openai()
        client = openai.AsyncOpenAI(api_key=self.api_key)
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=LLM_TEMPERATURE,
                timeout=timeout_ms / 1000.0,
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenAI async request failed", code="PROVIDER_ERROR", cause=e) from e

        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)
