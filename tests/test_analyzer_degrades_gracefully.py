from schema_analyzer.analyzer import AgenticSchemaAnalyzer


class FakeDB:
    def collections(self):
        # mimic python-arango: collections() returns dict name->collection
        class FakeCol:
            def __init__(self, name, col_type):
                self._name = name
                self._type = col_type

            def properties(self):
                return {"type": self._type}

            def count(self):
                return 0

            def indexes(self):
                return []

        return {
            "users": FakeCol("users", 2),
            "follows": FakeCol("follows", 3),
            "_system": FakeCol("_system", 2),
        }

    def graphs(self):
        return []


def test_analyze_without_provider_returns_empty_with_review_required(tmp_path):
    analyzer = AgenticSchemaAnalyzer(
        llm_provider=None, api_key=None, cache={"type": "filesystem", "directory": tmp_path}
    )
    res = analyzer.analyze_physical_schema(FakeDB(), sample_limit_per_collection=0)
    assert res.metadata.review_required is True
    assert res.metadata.confidence <= 0.2
    assert "baseline" in " ".join(res.metadata.warnings).lower()
    # Baseline should produce a usable, non-empty output.
    assert (res.conceptual_schema.get("entities") or []) != []
