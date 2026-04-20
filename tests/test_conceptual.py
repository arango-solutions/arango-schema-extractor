from __future__ import annotations

from schema_analyzer.conceptual import ConceptualSchema


def test_empty():
    cs = ConceptualSchema.empty()
    assert cs.entities == []
    assert cs.relationships == []
    assert cs.properties == []


def test_from_json_roundtrip():
    data = {
        "entities": [{"name": "A", "labels": ["A"], "properties": []}],
        "relationships": [{"type": "R", "fromEntity": "A", "toEntity": "A"}],
        "properties": [{"name": "p"}],
    }
    cs = ConceptualSchema.from_json(data)
    out = cs.to_json()
    assert out["entities"] == data["entities"]
    assert out["relationships"] == data["relationships"]
    assert out["properties"] == data["properties"]


def test_from_json_handles_bad_types():
    cs = ConceptualSchema.from_json({"entities": "not-a-list", "relationships": 42})
    assert cs.entities == []
    assert cs.relationships == []


def test_get_entity_by_label():
    cs = ConceptualSchema(entities=[{"name": "User", "labels": ["User", "Person"]}])
    assert cs.get_entity_by_label("Person") is not None
    assert cs.get_entity_by_label("Missing") is None


def test_has_relationship_type():
    cs = ConceptualSchema(relationships=[{"type": "FOLLOWS"}])
    assert cs.has_relationship_type("FOLLOWS") is True
    assert cs.has_relationship_type("NOPE") is False


def test_validate_pattern_valid():
    cs = ConceptualSchema(
        entities=[{"name": "User", "labels": ["User"]}],
        relationships=[{"type": "FOLLOWS"}],
    )
    result = cs.validate_pattern(
        {
            "nodes": [{"variable": "u", "labels": ["User"]}],
            "relationships": [{"variable": "r", "type": "FOLLOWS"}],
        }
    )
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_pattern_unknown_label():
    cs = ConceptualSchema(entities=[{"name": "User", "labels": ["User"]}])
    result = cs.validate_pattern(
        {
            "nodes": [{"variable": "u", "labels": ["Unknown"]}],
            "relationships": [],
        }
    )
    assert result["valid"] is False
    assert any(e["code"] == "UNKNOWN_LABEL" for e in result["errors"])


def test_validate_pattern_unknown_rel():
    cs = ConceptualSchema(relationships=[{"type": "FOLLOWS"}])
    result = cs.validate_pattern(
        {
            "nodes": [],
            "relationships": [{"variable": "r", "type": "NOPE"}],
        }
    )
    assert result["valid"] is False
    assert any(e["code"] == "UNKNOWN_REL_TYPE" for e in result["errors"])


def test_validate_pattern_not_dict():
    cs = ConceptualSchema.empty()
    result = cs.validate_pattern("bad")
    assert result["valid"] is False
