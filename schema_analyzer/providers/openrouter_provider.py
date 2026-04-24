from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..defaults import (
    ANTHROPIC_MAX_TOKENS,
    LLM_TEMPERATURE,
    OPENROUTER_BASE_URL,
    OPENROUTER_ERROR_BODY_MAX_CHARS,
)
from ..errors import SchemaAnalyzerError
from .base import LLMResponse

# Best-effort scrubbing of obvious secret-like substrings from upstream
# error bodies before they get embedded in a SchemaAnalyzerError. The
# common case is the bearer token leaking back via an error envelope.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"',}]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*[\"']?)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
)


def _scrub_secrets(text: str) -> str:
    out = text
    for rx in _SECRET_PATTERNS:
        out = rx.sub(lambda m: (m.group(1) if m.lastindex else "") + "***REDACTED***", out)
    return out


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block HTTP redirects so a compromised / typo'd ``base_url`` cannot
    silently shuttle credentials to a different host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]  # pragma: no cover
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"redirect blocked ({code} -> {newurl})",
            headers,
            fp,
        )


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


@dataclass
class OpenRouterProvider:
    api_key: str
    base_url: str = OPENROUTER_BASE_URL
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
        return json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": LLM_TEMPERATURE,
                "max_tokens": ANTHROPIC_MAX_TOKENS,
            }
        ).encode("utf-8")

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=self._payload(model, system, prompt),
            headers=self._headers(),
            method="POST",
        )
        try:
            with _OPENER.open(req, timeout=timeout_ms / 1000.0) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:  # pragma: no cover
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            scrubbed = _scrub_secrets(body)[:OPENROUTER_ERROR_BODY_MAX_CHARS] if body else str(e)
            raise SchemaAnalyzerError(
                f"OpenRouter request failed (HTTP {e.code})",
                code="PROVIDER_ERROR",
                cause=SchemaAnalyzerError(scrubbed),
            ) from e
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenRouter request failed", code="PROVIDER_ERROR", cause=e) from e

        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenRouter response parse failed", code="PROVIDER_ERROR", cause=e) from e

        return LLMResponse(text=text, raw=data)

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        import asyncio

        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self.generate(model=model, system=system, prompt=prompt, timeout_ms=timeout_ms),
        )
