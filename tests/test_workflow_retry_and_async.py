"""Retry/backoff, error-path, and async-path coverage for the LLM workflow.

Complements ``test_workflow_repair_loop.py`` (which covers the sync happy +
repair path) by exercising the transient-retry policy, the fatal parse /
validation branches, and the full async generate->validate->repair loop.
"""

from __future__ import annotations

import asyncio

import pytest

from schema_analyzer.errors import SchemaAnalyzerError
from schema_analyzer.providers.base import AsyncLLMProvider, LLMProvider, LLMResponse
from schema_analyzer.workflow import (
    _call_with_retry,
    _retry_decision,
    async_generate_validate_repair,
    run_generate_validate_repair,
)

VALID_OUTPUT = (
    "{"
    '"conceptualSchema":{"entities":[],"relationships":[],"properties":[]},'
    '"physicalMapping":{"entities":{},"relationships":{}},'
    '"metadata":{"confidence":0.5,"timestamp":"t",'
    '"analyzedCollectionCounts":{"documentCollections":0,"edgeCollections":0},'
    '"detectedPatterns":[]}'
    "}"
)


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeProvider:
    """Sync provider that yields queued outputs or raises queued exceptions."""

    def __init__(self, outputs: list[object]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        self.calls += 1
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(str(item))  # type: ignore[return-value]


class FakeAsyncProvider:
    def __init__(self, outputs: list[object]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        self.calls += 1
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(str(item))  # type: ignore[return-value]


def _transient() -> SchemaAnalyzerError:
    return SchemaAnalyzerError("boom", code="PROVIDER_ERROR")


# --------------------------------------------------------------------------
# _retry_decision policy
# --------------------------------------------------------------------------


def test_retry_decision_transient_returns_positive_backoff():
    delay = _retry_decision(_transient(), attempt=0, max_retries=2, base_delay=1.0)
    assert delay == 1.0
    delay2 = _retry_decision(_transient(), attempt=1, max_retries=2, base_delay=1.0)
    assert delay2 == 2.0  # exponential


def test_retry_decision_non_transient_returns_zero():
    err = SchemaAnalyzerError("nope", code="VALIDATION_ERROR")
    assert _retry_decision(err, attempt=0, max_retries=2, base_delay=1.0) == 0.0


def test_retry_decision_exhausted_returns_zero():
    assert _retry_decision(_transient(), attempt=2, max_retries=2, base_delay=1.0) == 0.0


# --------------------------------------------------------------------------
# sync retry loop
# --------------------------------------------------------------------------


def test_call_with_retry_recovers_after_transient(monkeypatch):
    monkeypatch.setattr("schema_analyzer.workflow.time.sleep", lambda _s: None)
    provider = FakeProvider([_transient(), VALID_OUTPUT])
    resp = _call_with_retry(
        provider, model="m", system="s", prompt="p", timeout_ms=1000, max_retries=2, base_delay=0.01
    )
    assert resp.text == VALID_OUTPUT
    assert provider.calls == 2


def test_call_with_retry_exhausts_and_raises(monkeypatch):
    monkeypatch.setattr("schema_analyzer.workflow.time.sleep", lambda _s: None)
    provider = FakeProvider([_transient(), _transient(), _transient()])
    with pytest.raises(SchemaAnalyzerError) as exc:
        _call_with_retry(provider, model="m", system="s", prompt="p", timeout_ms=1, max_retries=2, base_delay=0.01)
    assert exc.value.code == "PROVIDER_ERROR"
    assert provider.calls == 3


def test_non_transient_error_not_retried():
    provider = FakeProvider([SchemaAnalyzerError("fatal", code="INVALID_ARGUMENT")])
    with pytest.raises(SchemaAnalyzerError) as exc:
        _call_with_retry(provider, model="m", system="s", prompt="p", timeout_ms=1, max_retries=2, base_delay=0.0)
    assert exc.value.code == "INVALID_ARGUMENT"
    assert provider.calls == 1


# --------------------------------------------------------------------------
# fatal parse / validation branches
# --------------------------------------------------------------------------


def test_unparseable_output_raises_parse_error():
    provider = FakeProvider(["this is not json at all"])
    with pytest.raises(SchemaAnalyzerError) as exc:
        run_generate_validate_repair(provider=provider, model="m", system="s", prompt="p", timeout_ms=1000)
    assert exc.value.code == "PARSE_ERROR"


def test_validation_failure_after_max_repairs_raises():
    invalid = '{"conceptualSchema":{}, "physicalMapping":{}, "metadata":{}}'
    provider = FakeProvider([invalid, invalid, invalid])
    with pytest.raises(SchemaAnalyzerError) as exc:
        run_generate_validate_repair(
            provider=provider, model="m", system="s", prompt="p", timeout_ms=1000, max_repair_attempts=1
        )
    assert exc.value.code == "VALIDATION_ERROR"
    assert provider.calls == 2  # initial + 1 repair


# --------------------------------------------------------------------------
# async path
# --------------------------------------------------------------------------


def test_fake_async_provider_satisfies_protocol():
    assert isinstance(FakeAsyncProvider([]), AsyncLLMProvider)
    assert isinstance(FakeProvider([]), LLMProvider)


def test_async_generate_validate_repair_happy_path():
    provider = FakeAsyncProvider([VALID_OUTPUT])
    res = asyncio.run(
        async_generate_validate_repair(provider=provider, model="m", system="s", prompt="p", timeout_ms=1000)
    )
    assert res.repair_attempts == 0
    assert provider.calls == 1


def test_async_generate_validate_repair_repairs_then_succeeds():
    invalid = '{"conceptualSchema":{}, "physicalMapping":{}, "metadata":{}}'
    provider = FakeAsyncProvider([invalid, VALID_OUTPUT])
    res = asyncio.run(
        async_generate_validate_repair(
            provider=provider, model="m", system="s", prompt="p", timeout_ms=1000, max_repair_attempts=2
        )
    )
    assert res.repair_attempts == 1
    assert provider.calls == 2


def test_async_retry_recovers_after_transient(monkeypatch):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    provider = FakeAsyncProvider([_transient(), VALID_OUTPUT])
    res = asyncio.run(
        async_generate_validate_repair(
            provider=provider, model="m", system="s", prompt="p", timeout_ms=1000, max_retries=2
        )
    )
    assert res.repair_attempts == 0
    assert provider.calls == 2
