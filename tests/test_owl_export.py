from __future__ import annotations

from schema_analyzer.owl_export import (
    export_conceptual_model_as_jsonld,
    export_conceptual_model_as_owl_turtle,
)

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


# ── Richer OWL (PRD §6.3) ────────────────────────────────────────────────

_RICH = {
    "conceptualSchema": {
        "entities": [
            {"name": "IBEXDocument"},
            {"name": "MOR1KXDocument"},
            {"name": "Author"},
        ],
        "relationships": [
            {"type": "WROTE", "fromEntity": "Author", "toEntity": "IBEXDocument"},
            {"type": "AUTHORED_BY", "fromEntity": "IBEXDocument", "toEntity": "Author", "inverseOf": "WROTE"},
        ],
    },
    "physicalMapping": {
        "entities": {
            "IBEXDocument": {"style": "COLLECTION", "collectionName": "IBEX_Documents"},
            "MOR1KXDocument": {"style": "COLLECTION", "collectionName": "MOR1KX_Documents"},
            "Author": {"style": "COLLECTION", "collectionName": "authors"},
        },
        "relationships": {
            "WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"},
            "AUTHORED_BY": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "authored_by"},
        },
        "shardFamilies": [
            {
                "name": "Document",
                "members": [{"entity": "IBEXDocument"}, {"entity": "MOR1KXDocument"}],
            }
        ],
    },
    "metadata": {
        "statistics": {
            "relationships": {
                "WROTE": {"cardinality_pattern": "1:N"},
                "AUTHORED_BY": {"cardinality_pattern": "N:1"},
            }
        }
    },
}


def test_owl_subclassof_from_shard_families():
    ttl = export_conceptual_model_as_owl_turtle(_RICH)
    assert ":Document a owl:Class" in ttl
    assert ":IBEXDocument rdfs:subClassOf :Document ." in ttl
    assert ":MOR1KXDocument rdfs:subClassOf :Document ." in ttl


def test_owl_cardinality_characteristics():
    ttl = export_conceptual_model_as_owl_turtle(_RICH)
    # WROTE is 1:N -> inverse-functional, not functional
    assert ':WROTE phys:observedCardinality "1:N"' in ttl
    assert ":WROTE a owl:InverseFunctionalProperty ." in ttl
    # AUTHORED_BY is N:1 -> functional, not inverse-functional
    assert ":AUTHORED_BY a owl:FunctionalProperty ." in ttl


def test_owl_inverse_of():
    ttl = export_conceptual_model_as_owl_turtle(_RICH)
    assert ":AUTHORED_BY owl:inverseOf :WROTE ." in ttl


def test_jsonld_export_structure():
    doc = export_conceptual_model_as_jsonld(_RICH)
    assert "@context" in doc and "@graph" in doc
    by_id = {n["@id"]: n for n in doc["@graph"]}
    assert by_id["IBEXDocument"]["@type"] == "owl:Class"
    assert by_id["IBEXDocument"]["rdfs:subClassOf"] == {"@id": "Document"}
    assert "owl:FunctionalProperty" in by_id["AUTHORED_BY"]["@type"]
    assert by_id["AUTHORED_BY"]["owl:inverseOf"] == {"@id": "WROTE"}
    assert by_id["WROTE"]["rdfs:domain"] == {"@id": "Author"}


def test_owl_tool_op_jsonld_format():
    from schema_analyzer.tool import run_tool

    analysis = {
        "conceptualSchema": {"entities": [{"name": "User"}], "relationships": [], "properties": []},
        "physicalMapping": {
            "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
            "relationships": {},
        },
        "metadata": {
            "confidence": 0.9,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }
    resp = run_tool(
        {
            "contractVersion": "1",
            "operation": "owl",
            "input": {"analysis": analysis},
            "outputOptions": {"owlFormat": "jsonld"},
        }
    )
    assert resp["ok"] is True, resp
    assert "@graph" in resp["result"]["jsonld"]

    resp_ttl = run_tool({"contractVersion": "1", "operation": "owl", "input": {"analysis": analysis}})
    assert resp_ttl["ok"] is True
    assert "owl:Ontology" in resp_ttl["result"]["turtle"]
