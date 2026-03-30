import json

from schema_analyzer.analyzer import AgenticSchemaAnalyzer


class FakeProvider:
    def __init__(self, text: str):
        self._text = text

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int):
        class R:
            def __init__(self, t):
                self.text = t

        return R(self._text)


class FakeDB:
    def collections(self):
        class FakeCol:
            def __init__(self, col_type):
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


def test_analyze_with_mock_provider_parses_json(monkeypatch):
    payload = {
        "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
        "physicalMapping": {"entities": {}, "relationships": {}},
        "metadata": {
            "confidence": 0.8,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
            "detectedPatterns": [],
        },
    }
    text = "here you go\n" + json.dumps(payload) + "\nthanks"

    import schema_analyzer.analyzer as analyzer_mod

    def _fake_create_provider(name, *, api_key):
        return FakeProvider(text)

    monkeypatch.setattr(analyzer_mod, "create_provider", _fake_create_provider)

    analyzer = AgenticSchemaAnalyzer(llm_provider="openai", api_key="k", model="m")
    res = analyzer.analyze_physical_schema(FakeDB(), sample_limit_per_collection=0)
    assert res.metadata.confidence == 0.8
    assert res.metadata.review_required is False

