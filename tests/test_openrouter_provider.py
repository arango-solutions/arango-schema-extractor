"""Unit tests for the stdlib-only OpenRouter provider.

Covers the security-relevant helpers (secret scrubbing, header construction)
and the request/response happy path with a mocked opener so no network is
touched. Also exercises ``agenerate`` to confirm a shipped provider satisfies
the async protocol.
"""

from __future__ import annotations

import asyncio
import json

from schema_analyzer.providers import openrouter_provider as orp
from schema_analyzer.providers.base import AsyncLLMProvider, LLMProvider
from schema_analyzer.providers.openrouter_provider import OpenRouterProvider, _scrub_secrets


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._body


class _FakeOpener:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.last_req = None

    def open(self, req, timeout=None):
        self.last_req = req
        return _FakeResp(self.body)


def test_scrub_secrets_redacts_bearer_token():
    out = _scrub_secrets("Authorization: Bearer sk-supersecretvalue1234")
    assert "supersecretvalue" not in out
    assert "REDACTED" in out


def test_scrub_secrets_redacts_api_key_and_sk_pattern():
    assert "abcd1234efgh5678" not in _scrub_secrets('api_key="abcd1234efgh5678"')
    assert "sk-0123456789abcdef0123" not in _scrub_secrets("token sk-0123456789abcdef0123")


def test_headers_include_auth_and_optional_fields():
    p = OpenRouterProvider(api_key="key123", http_referer="https://x.test", x_title="My App")
    headers = p._headers()
    assert headers["Authorization"] == "Bearer key123"
    assert headers["Content-Type"] == "application/json"
    assert headers["HTTP-Referer"] == "https://x.test"
    assert headers["X-Title"] == "My App"


def test_headers_omit_optional_when_absent():
    headers = OpenRouterProvider(api_key="k")._headers()
    assert "HTTP-Referer" not in headers
    assert "X-Title" not in headers


def test_payload_shape():
    payload = json.loads(OpenRouterProvider(api_key="k")._payload("model-x", "sys", "usr"))
    assert payload["model"] == "model-x"
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["messages"][1] == {"role": "user", "content": "usr"}
    assert "temperature" in payload and "max_tokens" in payload


def test_generate_happy_path(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "hello world"}}]}).encode("utf-8")
    fake = _FakeOpener(body)
    monkeypatch.setattr(orp, "_OPENER", fake)

    resp = OpenRouterProvider(api_key="k").generate(model="m", system="s", prompt="p", timeout_ms=5000)
    assert resp.text == "hello world"
    assert fake.last_req is not None
    assert fake.last_req.full_url.endswith("/chat/completions")


def test_agenerate_delegates_to_generate(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "async-ok"}}]}).encode("utf-8")
    monkeypatch.setattr(orp, "_OPENER", _FakeOpener(body))

    provider = OpenRouterProvider(api_key="k")
    resp = asyncio.run(provider.agenerate(model="m", system="s", prompt="p", timeout_ms=5000))
    assert resp.text == "async-ok"


def test_openrouter_satisfies_both_protocols():
    p = OpenRouterProvider(api_key="k")
    assert isinstance(p, LLMProvider)
    assert isinstance(p, AsyncLLMProvider)
