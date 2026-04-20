from __future__ import annotations

from schema_analyzer.owl_export import export_conceptual_model_as_owl_turtle

_SAMPLE = {
    "conceptualSchema": {
        "entities": [
            {"name": "User", "labels": ["User"], "properties": []},
        ],
        "relationships": [
            {"type": "FOLLOWS", "fromEntity": "User", "toEntity": "User", "properties": []},
        ],
    },
    "physicalMapping": {
        "entities": {
            "User": {"style": "COLLECTION", "collectionName": "users"},
        },
        "relationships": {
            "FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"},
        },
    },
}


def test_owl_export_contains_class():
    ttl = export_conceptual_model_as_owl_turtle(_SAMPLE)
    assert ":User a owl:Class" in ttl
    assert 'rdfs:label "User"' in ttl


def test_owl_export_contains_object_property():
    ttl = export_conceptual_model_as_owl_turtle(_SAMPLE)
    assert ":FOLLOWS a owl:ObjectProperty" in ttl
    assert "rdfs:domain :User" in ttl
    assert "rdfs:range :User" in ttl


def test_owl_export_contains_physical_annotations():
    ttl = export_conceptual_model_as_owl_turtle(_SAMPLE)
    assert 'phys:mappingStyle "COLLECTION"' in ttl
    assert 'phys:collectionName "users"' in ttl
    assert 'phys:edgeCollectionName "follows"' in ttl


def test_owl_export_custom_iris():
    ttl = export_conceptual_model_as_owl_turtle(
        _SAMPLE, base_iri="http://example.org/schema#", phys_iri="http://example.org/phys#"
    )
    assert "@prefix : <http://example.org/schema#>" in ttl
    assert "@prefix phys: <http://example.org/phys#>" in ttl


def test_owl_export_empty():
    ttl = export_conceptual_model_as_owl_turtle({"conceptualSchema": {}, "physicalMapping": {}})
    assert "owl:Ontology" in ttl
    assert ":User" not in ttl
