"""Tests for gold-standard comparison + health-score folding (PRD §3.12.3)."""

from __future__ import annotations

from schema_analyzer.quality import build_quality_block, compute_gold_comparison

CONCEPTUAL = {
    "entities": [{"name": "Users"}, {"name": "Post"}, {"name": "Extra"}],
    "relationships": [{"type": "WROTE", "fromEntity": "Users", "toEntity": "Post"}],
    "properties": [],
}

# Gold has User (singular), Post, and a missing Comment entity; rel WROTE + missing LIKED.
REFERENCE = {
    "entities": [{"name": "user"}, {"name": "post"}, {"name": "comment"}],
    "relationships": [{"type": "WROTE"}, {"type": "LIKED"}],
}


def test_gold_comparison_normalizes_and_scores():
    gold = compute_gold_comparison(CONCEPTUAL, REFERENCE)
    # Predicted entities {user, post, extra} vs truth {user, post, comment}:
    # tp=2 -> precision 2/3, recall 2/3.
    assert gold["entities"]["precision"] == round(2 / 3, 4)
    assert gold["entities"]["recall"] == round(2 / 3, 4)
    # Predicted rels {wrote} vs truth {wrote, liked}: precision 1.0, recall 0.5.
    assert gold["relationships"]["precision"] == 1.0
    assert gold["relationships"]["recall"] == 0.5
    assert 0.0 <= gold["overlap"] <= 1.0
    assert gold["referenceEntityCount"] == 3
    assert gold["referenceRelationshipCount"] == 2


def test_perfect_match_overlap_one():
    conceptual = {"entities": [{"name": "User"}], "relationships": [{"type": "KNOWS"}], "properties": []}
    reference = {"entities": [{"name": "user"}], "relationships": [{"type": "knows"}]}
    gold = compute_gold_comparison(conceptual, reference)
    assert gold["entities"]["f1"] == 1.0
    assert gold["relationships"]["f1"] == 1.0
    assert gold["overlap"] == 1.0


def test_build_quality_block_without_reference_has_no_gold():
    quality, _ = build_quality_block(CONCEPTUAL, {"entities": {}, "relationships": {}}, {"collections": []}, 0.9)
    assert "gold" not in quality
    assert "gold" not in quality["healthScoreComponents"]


def test_build_quality_block_with_reference_folds_gold_into_health():
    snapshot = {"collections": []}
    mapping = {"entities": {}, "relationships": {}}
    q_no, h_no = build_quality_block(CONCEPTUAL, mapping, snapshot, 0.9)
    q_gold, h_gold = build_quality_block(CONCEPTUAL, mapping, snapshot, 0.9, REFERENCE)
    assert "gold" in q_gold
    assert "gold" in q_gold["healthScoreComponents"]
    # Imperfect gold overlap (<1) pulls the composite below the gold-free score.
    assert h_gold <= h_no


def test_empty_reference_is_ignored():
    quality, _ = build_quality_block(CONCEPTUAL, {"entities": {}, "relationships": {}}, {"collections": []}, 0.9, {})
    assert "gold" not in quality
