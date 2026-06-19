"""Tests for RDF-topology (RPT) detection + TRIPLE mapping style (PRD §6.1/§6.2)."""

from __future__ import annotations

from schema_analyzer.rdf_topology import detect_rdf_topology


def _data(entities=None, relationships=None):
    return {
        "physicalMapping": {"entities": entities or {}, "relationships": relationships or {}},
        "metadata": {},
    }


def test_detects_triple_collection_by_field_signature():
    snapshot = {
        "collections": [
            {
                "name": "facts",
                "type": "document",
                "observed_fields": {"fields": ["subject", "predicate", "object"]},
            }
        ]
    }
    data = _data(entities={"Fact": {"style": "COLLECTION", "collectionName": "facts"}})
    block = detect_rdf_topology(data, snapshot)
    assert block["isRdfTopology"] is True
    assert block["tripleCollections"][0]["collection"] == "facts"
    assert block["tripleCollections"][0]["signature"] == "object_predicate_subject"
    # entity mapping annotated with the TRIPLE style
    fact = data["physicalMapping"]["entities"]["Fact"]
    assert fact["tripleCandidate"] is True
    assert fact["triple"] == {"style": "TRIPLE"}


def test_detects_triple_collection_by_name():
    snapshot = {
        "collections": [{"name": "_triples", "type": "document", "observed_fields": {"fields": ["s", "p", "o"]}}]
    }
    block = detect_rdf_topology(_data(), snapshot)
    assert block["isRdfTopology"] is True
    names = {c["collection"] for c in block["tripleCollections"]}
    assert "_triples" in names


def test_detects_rdf_type_edges():
    snapshot = {
        "collections": [
            {
                "name": "assertions",
                "type": "edge",
                "sample_field_value_counts": {"predicate": [{"value": "rdf:type", "count": 5}]},
            }
        ]
    }
    block = detect_rdf_topology(_data(), snapshot)
    assert block["isRdfTopology"] is True
    assert block["typeEdges"][0]["collection"] == "assertions"
    assert "rdf:type" in block["typeEdges"][0]["typeValues"]


def test_property_graph_is_not_rdf():
    snapshot = {
        "collections": [
            {"name": "users", "type": "document", "observed_fields": {"fields": ["name", "email"]}},
            {
                "name": "follows",
                "type": "edge",
                "sample_field_value_counts": {"relation": [{"value": "FOLLOWS", "count": 3}]},
            },
        ]
    }
    block = detect_rdf_topology(_data(), snapshot)
    assert block == {"status": "ok", "isRdfTopology": False}


def test_no_collections_returns_none():
    assert detect_rdf_topology(_data(), {"collections": []}) is None


def test_relationship_in_triple_collection_annotated():
    snapshot = {
        "collections": [
            {"name": "spo", "type": "edge", "observed_fields": {"by_type": {"X": ["subject", "predicate", "object"]}}}
        ]
    }
    data = _data(relationships={"R": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "spo"}})
    detect_rdf_topology(data, snapshot)
    rel = data["physicalMapping"]["relationships"]["R"]
    assert rel["tripleCandidate"] is True
    assert rel["style"] == "DEDICATED_COLLECTION"  # underlying style preserved
