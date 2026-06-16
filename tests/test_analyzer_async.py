"""End-to-end coverage for the async analyzer entrypoint.

Confirms ``analyze_physical_schema_async`` is correctly wired through
``_prepare_analysis`` -> ``async_generate_validate_repair`` -> ``_build_result``
using a fake async provider, and that it degrades to baseline inference with
no LLM configured (same contract as the sync path).
"""

from __future__ import annotations

import asyncio
import json

from schema_analyzer.analyzer import AgenticSchemaAnalyzer


class FakeAsyncProvider:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def agenerate(self, *, model: str, system: str, prompt: str, timeout_ms: int):
        self.calls += 1

        class R:
            def __init__(self, t: str) -> None:
                self.text = t

        return R(self._text)


class FakeDB:
    def collections(self):
        class FakeCol:
            def __init__(self, col_type: int) -> None:
                self._type = col_type

            def properties(self):
                return {"type": self._type}

            def count(self):
                return 0

            def indexes(self):
                return []

        return {"users": FakeCol(2), "follows": FakeCol(3)}

    def graphs(self):
        return []


def _payload() -> dict:
    return {
        "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
        "physicalMapping": {"entities": {}, "relationships": {}},
        "metadata": {
            "confidence": 0.77,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
            "detectedPatterns": [],
        },
    }


def test_async_analyze_with_mock_provider(monkeypatch):
    provider = FakeAsyncProvider(json.dumps(_payload()))

    import schema_analyzer.analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "create_provider", lambda name, *, api_key: provider)

    analyzer = AgenticSchemaAnalyzer(llm_provider="openai", api_key="k", model="m")
    res = asyncio.run(analyzer.analyze_physical_schema_async(FakeDB(), use_cache=False))

    assert provider.calls == 1
    assert res.metadata.confidence == 0.77
    assert res.metadata.review_required is False
    assert res.metadata.used_baseline is False


def test_async_analyze_without_llm_falls_back_to_baseline():
    # No provider/api key -> baseline path (shared with sync analyzer).
    analyzer = AgenticSchemaAnalyzer()
    res = asyncio.run(analyzer.analyze_physical_schema_async(FakeDB(), use_cache=False))
    assert res.metadata.used_baseline is True
    assert res.metadata.review_required is True
