"""Tests for GraphRAG template detection (PRD §6.2)."""

from __future__ import annotations

from schema_analyzer.graphrag import detect_graphrag


def _data(entities=None, relationships=None):
    return {
        "physicalMapping": {"entities": entities or {}, "relationships": relationships or {}},
        "metadata": {},
    }


def _full_graphrag_snapshot():
    return {
        "collections": [
            {
                "name": "chunks",
                "type": "document",
                "observed_fields": {"fields": ["text", "embedding", "document_id"]},
                "indexes": [{"type": "vector", "fields": ["embedding"]}],
            },
            {
                "name": "entities",
                "type": "document",
                "observed_fields": {"fields": ["name", "type", "description"]},
            },
            {
                "name": "similarities",
                "type": "edge",
                "observed_fields": {"fields": ["score"]},
            },
            {
                "name": "mentions",
                "type": "edge",
                "observed_fields": {"fields": []},
            },
        ]
    }


def test_full_graphrag_high_confidence():
    snap = _full_graphrag_snapshot()
    data = _data(
        entities={
            "Chunk": {"style": "COLLECTION", "collectionName": "chunks"},
            "Entity": {"style": "COLLECTION", "collectionName": "entities"},
        },
        relationships={
            "SIMILAR_TO": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "similarities"},
            "MENTIONS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "mentions"},
        },
    )
    block = detect_graphrag(data, snap)
    assert block["isGraphRag"] is True
    assert block["confidence"] == "high"
    assert block["chunkCollections"] == ["chunks"]
    assert block["entityCollections"] == ["entities"]
    assert block["similarityEdges"] == ["similarities"]
    assert block["mentionEdges"] == ["mentions"]
    assert block["vectorIndexes"] == [{"collection": "chunks", "fields": ["embedding"]}]
    # Mapping entries tagged with roles.
    assert data["physicalMapping"]["entities"]["Chunk"]["graphRagRole"] == "chunk"
    assert data["physicalMapping"]["entities"]["Entity"]["graphRagRole"] == "entity"
    assert data["physicalMapping"]["relationships"]["SIMILAR_TO"]["graphRagRole"] == "similarity"
    assert data["physicalMapping"]["relationships"]["MENTIONS"]["graphRagRole"] == "mention"


def test_chunk_detected_via_embedding_field_without_name():
    snap = {
        "collections": [
            {"name": "passages", "type": "document", "observed_fields": {"fields": ["body", "vector"]}},
            {"name": "knn", "type": "edge", "observed_fields": {"fields": ["distance"]}},
        ]
    }
    block = detect_graphrag(_data(), snap)
    assert block["isGraphRag"] is True
    assert block["chunkCollections"] == ["passages"]
    assert block["similarityEdges"] == ["knn"]


def test_single_category_is_not_graphrag():
    # Only chunk collections, nothing else -> not enough signal.
    snap = {
        "collections": [
            {"name": "chunks", "type": "document", "observed_fields": {"fields": ["text", "embedding"]}},
        ]
    }
    assert detect_graphrag(_data(), snap) == {"status": "ok", "isGraphRag": False}


def test_plain_property_graph_is_not_graphrag():
    snap = {
        "collections": [
            {"name": "users", "type": "document", "observed_fields": {"fields": ["name", "email"]}},
            {"name": "follows", "type": "edge", "observed_fields": {"fields": []}},
        ]
    }
    assert detect_graphrag(_data(), snap)["isGraphRag"] is False


def test_no_collections_returns_none():
    assert detect_graphrag(_data(), {"collections": []}) is None


def test_medium_confidence_chunks_plus_entities_no_edges():
    snap = {
        "collections": [
            {"name": "chunks", "type": "document", "observed_fields": {"fields": ["text", "embedding"]}},
            {"name": "entities", "type": "document", "observed_fields": {"fields": ["name", "type"]}},
        ]
    }
    block = detect_graphrag(_data(), snap)
    assert block["isGraphRag"] is True
    assert block["confidence"] in {"medium", "high"}


def test_system_collections_ignored():
    snap = {
        "collections": [
            {"name": "_chunks", "type": "document", "observed_fields": {"fields": ["text", "embedding"]}},
            {"name": "_similarities", "type": "edge", "observed_fields": {"fields": ["score"]}},
        ]
    }
    assert detect_graphrag(_data(), snap)["isGraphRag"] is False
