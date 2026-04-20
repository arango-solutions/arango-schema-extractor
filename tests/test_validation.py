from __future__ import annotations

from schema_analyzer.validation import validate_analysis_output


def _valid_output() -> dict:
    return {
        "conceptualSchema": {
            "entities": [{"name": "Foo", "labels": ["Foo"], "properties": []}],
            "relationships": [{"type": "BAR", "fromEntity": "Foo", "toEntity": "Foo", "properties": []}],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {"Foo": {"style": "COLLECTION", "collectionName": "foos"}},
            "relationships": {"BAR": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "bars"}},
        },
        "metadata": {
            "confidence": 0.8,
            "timestamp": "2025-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
            "detectedPatterns": [],
        },
    }


def test_valid_output_passes():
    assert validate_analysis_output(_valid_output()) == []


def test_missing_conceptual_schema():
    data = _valid_output()
    del data["conceptualSchema"]
    errors = validate_analysis_output(data)
    assert len(errors) > 0


def test_invalid_mapping_style():
    data = _valid_output()
    data["physicalMapping"]["entities"]["Foo"]["style"] = "INVALID"
    errors = validate_analysis_output(data)
    assert any("style" in e.lower() or "INVALID" in e for e in errors)


def test_empty_entity_name_rejected():
    data = _valid_output()
    data["conceptualSchema"]["entities"][0]["name"] = ""
    errors = validate_analysis_output(data)
    assert len(errors) > 0
