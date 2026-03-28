from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..errors import SchemaAnalyzerError
from .base import LLMResponse


@dataclass
class OpenRouterProvider:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    http_referer: str | None = None
    x_title: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.x_title:
            headers["X-Title"] = self.x_title
        return headers

    def _payload(self, model: str, system: str, prompt: str) -> bytes:
        return json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 4096,
        }).encode("utf-8")

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url, data=self._payload(model, system, prompt), headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:  # pragma: no cover
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            raise SchemaAnalyzerError(
                f"OpenRouter request failed (HTTP {e.code})",
                code="PROVIDER_ERROR",
                cause=SchemaAnalyzerError(body[:2000] if body else str(e)),
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenRouter request failed", code="PROVIDER_ERROR", cause=e)

        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenRouter response parse failed", code="PROVIDER_ERROR", cause=e)

        return LLMResponse(text=text, raw=data)

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        import asyncio

        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.generate(model=model, system=system, prompt=prompt, timeout_ms=timeout_ms),
        )
