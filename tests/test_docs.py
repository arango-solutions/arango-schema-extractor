from __future__ import annotations

from schema_analyzer.docs import generate_schema_docs

_SAMPLE_ANALYSIS = {
    "conceptualSchema": {
        "entities": [
            {"name": "User", "labels": ["User"], "properties": [{"name": "email"}]},
            {"name": "Post", "labels": ["Post"], "properties": []},
        ],
        "relationships": [
            {"type": "AUTHORED", "fromEntity": "User", "toEntity": "Post", "properties": []},
        ],
        "properties": [],
    },
    "physicalMapping": {
        "entities": {
            "User": {"style": "COLLECTION", "collectionName": "users"},
            "Post": {"style": "COLLECTION", "collectionName": "posts"},
        },
        "relationships": {
            "AUTHORED": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "authored"},
        },
    },
    "metadata": {"confidence": 0.85, "timestamp": "2025-01-01T00:00:00Z"},
}


def test_generate_schema_docs_contains_entities():
    md = generate_schema_docs(_SAMPLE_ANALYSIS)
    assert "**User**" in md
    assert "**Post**" in md
    assert "**Entities**: 2" in md


def test_generate_schema_docs_contains_relationships():
    md = generate_schema_docs(_SAMPLE_ANALYSIS)
    assert "**AUTHORED**" in md
    assert "User -> Post" in md


def test_generate_schema_docs_contains_mapping_summary():
    md = generate_schema_docs(_SAMPLE_ANALYSIS)
    assert "Entity mappings**: 2" in md
    assert "Relationship mappings**: 1" in md


def test_generate_schema_docs_empty_analysis():
    md = generate_schema_docs({"conceptualSchema": {"entities": [], "relationships": []}, "physicalMapping": {}})
    assert "**Entities**: 0" in md
    assert "**Relationships**: 0" in md
