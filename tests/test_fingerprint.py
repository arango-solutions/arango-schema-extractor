from schema_analyzer.snapshot import fingerprint_physical_schema


def test_fingerprint_excludes_samples_by_default():
    snapshot1 = {
        "version": 1,
        "collections": [
            {"name": "users", "type": "document", "sample_documents": [{"a": 1}]},
        ],
        "graphs": [],
    }
    snapshot2 = {
        "version": 1,
        "collections": [
            {"name": "users", "type": "document", "sample_documents": [{"a": 999}]},
        ],
        "graphs": [],
    }
    assert fingerprint_physical_schema(snapshot1, include_samples=False) == fingerprint_physical_schema(
        snapshot2, include_samples=False
    )


def test_fingerprint_includes_samples_when_enabled():
    snapshot1 = {
        "version": 1,
        "collections": [
            {"name": "users", "type": "document", "sample_documents": [{"a": 1}]},
        ],
        "graphs": [],
    }
    snapshot2 = {
        "version": 1,
        "collections": [
            {"name": "users", "type": "document", "sample_documents": [{"a": 999}]},
        ],
        "graphs": [],
    }
    assert fingerprint_physical_schema(snapshot1, include_samples=True) != fingerprint_physical_schema(
        snapshot2, include_samples=True
    )


def test_fingerprint_ignores_generated_at():
    snapshot1 = {
        "version": 1,
        "generated_at": "2026-02-17T00:00:00Z",
        "collections": [{"name": "users", "type": "document"}],
        "graphs": [],
    }
    snapshot2 = {
        "version": 1,
        "generated_at": "2026-02-18T00:00:00Z",
        "collections": [{"name": "users", "type": "document"}],
        "graphs": [],
    }
    assert fingerprint_physical_schema(snapshot1, include_samples=False) == fingerprint_physical_schema(
        snapshot2, include_samples=False
    )
