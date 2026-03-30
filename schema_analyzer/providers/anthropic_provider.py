from __future__ import annotations

from dataclasses import dataclass

from ..defaults import ANTHROPIC_MAX_TOKENS, LLM_TEMPERATURE
from ..errors import SchemaAnalyzerError
from .base import LLMResponse


def _import_anthropic():
    try:
        import anthropic  # type: ignore
        return anthropic
    except Exception as e:  # pragma: no cover
        raise SchemaAnalyzerError(
            "Anthropic SDK not installed. Install extra: pip install -e '.[anthropic]'",
            code="PROVIDER_MISSING",
            cause=e,
        ) from e


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

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        anthropic = _import_anthropic()
        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            resp = client.messages.create(
                model=model,
                system=system,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_ms / 1000.0,
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("Anthropic request failed", code="PROVIDER_ERROR", cause=e) from e

        return LLMResponse(text=_extract_text(resp), raw=resp)

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        anthropic = _import_anthropic()
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        try:
            resp = await client.messages.create(
                model=model,
                system=system,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_ms / 1000.0,
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("Anthropic async request failed", code="PROVIDER_ERROR", cause=e) from e

        return LLMResponse(text=_extract_text(resp), raw=resp)

