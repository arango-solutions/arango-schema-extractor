"""Unit tests for structural + grounding quality metrics and health score."""

from __future__ import annotations

from schema_analyzer.quality import (
    build_quality_block,
    compute_grounding_metrics,
    compute_health_score,
    compute_structural_metrics,
)

CONCEPTUAL = {
    "entities": [
        {"name": "User", "properties": [{"name": "email"}, {"name": "age"}]},
        {"name": "Post", "properties": [{"name": "title"}]},
        {"name": "Orphan", "properties": []},
    ],
    "relationships": [
        {"type": "WROTE", "fromEntity": "User", "toEntity": "Post"},
    ],
    "properties": [],
}

MAPPING = {
    "entities": {
        "User": {"style": "COLLECTION", "collectionName": "users"},
        "Post": {"style": "COLLECTION", "collectionName": "posts"},
        "Orphan": {"style": "COLLECTION", "collectionName": "ghosts"},
    },
    "relationships": {
        "WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"},
    },
}

SNAPSHOT = {
    "collections": [
        {"name": "users", "type": "document"},
        {"name": "posts", "type": "document"},
        {"name": "wrote", "type": "edge"},
        # note: "ghosts" intentionally absent -> ungrounded
    ]
}


def test_structural_metrics_basic():
    m = compute_structural_metrics(CONCEPTUAL)
    assert m["entityCount"] == 3
    assert m["relationshipCount"] == 1
    assert m["connectedEntityCount"] == 2  # User + Post
    assert m["connectedEntityRatio"] == round(2 / 3, 4)
    assert m["orphanEntityCount"] == 1  # Orphan
    assert m["orphanEntityRatio"] == round(1 / 3, 4)
    assert m["danglingRelationshipCount"] == 0
    assert m["danglingRelationshipRatio"] == 0.0
    assert m["avgPropertiesPerEntity"] == round(3 / 3, 4)


def test_structural_metrics_empty_schema_uses_none_ratios():
    m = compute_structural_metrics({"entities": [], "relationships": [], "properties": []})
    assert m["entityCount"] == 0
    assert m["connectedEntityRatio"] is None
    assert m["orphanEntityRatio"] is None
    assert m["avgPropertiesPerEntity"] is None
    assert m["danglingRelationshipRatio"] is None


def test_dangling_relationship_detected():
    conceptual = {
        "entities": [{"name": "A"}],
        "relationships": [{"type": "R", "fromEntity": "A", "toEntity": "Missing"}],
        "properties": [],
    }
    m = compute_structural_metrics(conceptual)
    assert m["danglingRelationshipCount"] == 1
    assert m["danglingRelationshipRatio"] == 1.0


def test_grounding_metrics_flags_ungrounded_collection():
    g = compute_grounding_metrics(CONCEPTUAL, MAPPING, SNAPSHOT)
    assert g["mappedCollectionCount"] == 4  # users, posts, ghosts, wrote
    assert g["groundedCollectionCount"] == 3
    assert g["mappingGroundingRatio"] == round(3 / 4, 4)
    assert g["ungroundedCollections"] == ["ghosts"]
    assert g["unmappedEntityCount"] == 0


def test_grounding_metrics_flags_unmapped_entity():
    conceptual = {"entities": [{"name": "A"}, {"name": "B"}], "relationships": [], "properties": []}
    mapping = {"entities": {"A": {"style": "COLLECTION", "collectionName": "a"}}, "relationships": {}}
    snapshot = {"collections": [{"name": "a", "type": "document"}]}
    g = compute_grounding_metrics(conceptual, mapping, snapshot)
    assert g["unmappedEntityCount"] == 1
    assert g["unmappedEntities"] == ["B"]


def test_health_score_perfect_schema_high():
    structural = compute_structural_metrics(CONCEPTUAL)
    grounding = compute_grounding_metrics(CONCEPTUAL, MAPPING, SNAPSHOT)
    health = compute_health_score(structural, grounding, confidence=1.0)
    assert 0 <= health["score"] <= 100
    assert "confidence" in health["components"]
    assert "connectivity" in health["components"]
    assert "consistency" in health["components"]
    assert "grounding" in health["components"]


def test_health_score_drops_inapplicable_components():
    # Relationship-free schema: connectivity + consistency must be absent and
    # the score should rest only on confidence + grounding.
    conceptual = {"entities": [{"name": "A"}], "relationships": [], "properties": []}
    mapping = {"entities": {"A": {"style": "COLLECTION", "collectionName": "a"}}, "relationships": {}}
    snapshot = {"collections": [{"name": "a", "type": "document"}]}
    structural = compute_structural_metrics(conceptual)
    grounding = compute_grounding_metrics(conceptual, mapping, snapshot)
    health = compute_health_score(structural, grounding, confidence=1.0)
    assert "connectivity" not in health["components"]
    assert "consistency" not in health["components"]
    assert health["components"]["grounding"] == 1.0
    # confidence=1.0 and grounding=1.0 -> perfect
    assert health["score"] == 100


def test_health_score_no_components_is_zero():
    health = compute_health_score({"relationshipCount": 0}, {"mappedCollectionCount": 0}, confidence=0.0)
    # Only confidence applies, and it is 0.
    assert health["score"] == 0


def test_build_quality_block_shape():
    quality, score = build_quality_block(CONCEPTUAL, MAPPING, SNAPSHOT, confidence=0.8)
    assert set(quality.keys()) == {"structural", "grounding", "healthScoreComponents"}
    assert isinstance(score, int)
    assert 0 <= score <= 100
