"""Tests for shard-family detection (PRD §6.2 bullet 5).

Covers the deterministic detection logic in
:mod:`schema_analyzer.shard_families` and the analyzer integration
helper :func:`schema_analyzer.analyzer._apply_shard_families`.

Detection is pure (no DB / no LLM), so every test is constructed from
hand-rolled ``physicalMapping.entities`` dicts. Integration tests use
the same pattern as ``test_sharding_profile.py``: build a minimal
``data`` dict, call the helper, assert on what's stamped under
``data["physicalMapping"]``.
"""

from __future__ import annotations

from typing import Any

from schema_analyzer.analyzer import _apply_shard_families
from schema_analyzer.shard_families import (
    _common_suffix,
    _word_boundary_starts,
    detect_shard_families,
)


def _entity(*prop_names: str, collection_name: str | None = None) -> dict[str, Any]:
    return {
        "style": "COLLECTION",
        "collectionName": collection_name,
        "properties": {p: {"field": p} for p in prop_names},
    }


def _data_with_entities(entities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"physicalMapping": {"entities": entities, "relationships": {}}}


# ---------------------------------------------------------------------------
# _word_boundary_starts
# ---------------------------------------------------------------------------


class TestWordBoundaryStarts:
    def test_simple_camelcase(self):
        assert _word_boundary_starts("FooBar") == {0, 3}

    def test_acronym_then_word(self):
        assert _word_boundary_starts("IBEXDocument") == {0, 4}

    def test_trailing_acronym(self):
        starts = _word_boundary_starts("XmlHTTP")
        assert 0 in starts
        assert 3 in starts

    def test_snake_case_boundary(self):
        starts = _word_boundary_starts("foo_bar_baz")
        assert {0, 4, 8}.issubset(starts)

    def test_digit_to_upper(self):
        starts = _word_boundary_starts("MOR1KXDocument")
        assert 6 in starts

    def test_single_word_lowercase(self):
        assert _word_boundary_starts("foobar") == {0}


# ---------------------------------------------------------------------------
# _common_suffix
# ---------------------------------------------------------------------------


class TestCommonSuffix:
    def test_canonical_ibex_marocchino(self):
        names = ["IBEXDocument", "MAROCCHINODocument", "MOR1KXDocument", "OR1200Document"]
        assert _common_suffix(names, min_len=4) == "Document"

    def test_walks_back_past_mid_word_match(self):
        """Longest literal common suffix is mid-word; algorithm must
        walk back to the first word-boundary-aligned candidate."""
        names = ["FooDocument", "BarDocument"]
        assert _common_suffix(names, min_len=4) == "Document"

    def test_returns_none_when_no_common_suffix(self):
        names = ["Foo", "Bar"]
        assert _common_suffix(names, min_len=3) is None

    def test_returns_none_when_too_short(self):
        names = ["XOp", "YOp"]
        assert _common_suffix(names, min_len=4) is None

    def test_snake_case_suffix(self):
        names = ["west_Golden_Entities", "east_Golden_Entities"]
        assert _common_suffix(names, min_len=4) == "_Golden_Entities"

    def test_empty_input(self):
        assert _common_suffix([], min_len=4) is None

    def test_suffix_equals_name(self):
        """Member whose entire name IS the suffix is allowed (boundary
        index 0 is always in the start set)."""
        names = ["Document", "FooDocument"]
        assert _common_suffix(names, min_len=4) == "Document"


# ---------------------------------------------------------------------------
# detect_shard_families — happy path
# ---------------------------------------------------------------------------


class TestDetectShardFamiliesHappyPath:
    def test_canonical_four_member_family_with_repo_field(self):
        props = ("doc_version", "label", "path", "repo")
        entities = {
            "IBEXDocument": _entity(*props, collection_name="IBEX_Documents"),
            "MAROCCHINODocument": _entity(*props, collection_name="MAROCCHINO_Documents"),
            "MOR1KXDocument": _entity(*props, collection_name="MOR1KX_Documents"),
            "OR1200Document": _entity(*props, collection_name="OR1200_Documents"),
        }
        families = detect_shard_families(_data_with_entities(entities))
        assert families is not None
        assert len(families) == 1
        family = families[0]
        assert family["name"] == "Document"
        assert family["suffix"] == "Document"
        assert family["discriminator"] == {"source": "field", "field": "repo"}
        assert family["sharedProperties"] == ["doc_version", "label", "path", "repo"]
        assert [m["entity"] for m in family["members"]] == [
            "IBEXDocument",
            "MAROCCHINODocument",
            "MOR1KXDocument",
            "OR1200Document",
        ]
        assert [m["discriminatorValue"] for m in family["members"]] == [
            "IBEX",
            "MAROCCHINO",
            "MOR1KX",
            "OR1200",
        ]
        assert all(m["collectionName"] == m["entity"].replace("Document", "_Documents") for m in family["members"])

    def test_discriminator_falls_back_to_collection_prefix(self):
        """No member carries a candidate discriminator field → source == collection_prefix."""
        entities = {
            "IBEXDocument": _entity("doc_version", "label", "path", collection_name="IBEX_Documents"),
            "MAROCCHINODocument": _entity("doc_version", "label", "path", collection_name="MAROCCHINO_Documents"),
        }
        families = detect_shard_families(_data_with_entities(entities))
        assert families is not None
        assert families[0]["discriminator"] == {"source": "collection_prefix"}
        assert "field" not in families[0]["discriminator"]

    def test_two_member_family_meets_min_size(self):
        entities = {
            "IBEXDocument": _entity("a", "b", collection_name="IBEX_Documents"),
            "MAROCCHINODocument": _entity("a", "b", collection_name="MAROCCHINO_Documents"),
        }
        families = detect_shard_families(_data_with_entities(entities))
        assert families is not None and len(families) == 1
        assert len(families[0]["members"]) == 2

    def test_multiple_families_sorted_deterministically(self):
        entities = {
            # Family A: ...Document
            "AlphaDocument": _entity("p1", "p2", collection_name="alpha_docs"),
            "BetaDocument": _entity("p1", "p2", collection_name="beta_docs"),
            # Family B: ...Event (different property set ⇒ separate bucket)
            "AlphaEvent": _entity("e1", "e2", collection_name="alpha_events"),
            "BetaEvent": _entity("e1", "e2", collection_name="beta_events"),
        }
        families = detect_shard_families(_data_with_entities(entities))
        assert families is not None and len(families) == 2
        assert [f["name"] for f in families] == ["Document", "Event"]


# ---------------------------------------------------------------------------
# detect_shard_families — rejection / edge cases
# ---------------------------------------------------------------------------


class TestDetectShardFamiliesRejections:
    def test_singleton_buckets_skipped(self):
        entities = {
            "Foo": _entity("a", "b"),
            "Bar": _entity("c", "d"),
        }
        assert detect_shard_families(_data_with_entities(entities)) == []

    def test_empty_property_sets_excluded_from_buckets(self):
        """Two propertyless entities don't form a family — there's no
        structural fingerprint to bucket on."""
        entities = {
            "Foo": _entity(),
            "Bar": _entity(),
        }
        assert detect_shard_families(_data_with_entities(entities)) == []

    def test_short_suffix_rejected(self):
        """Common suffix shorter than MIN_SHARD_FAMILY_SUFFIX_LEN."""
        entities = {
            "XOp": _entity("a", "b"),
            "YOp": _entity("a", "b"),
        }
        assert detect_shard_families(_data_with_entities(entities)) == []

    def test_returns_none_for_no_entities_dict(self):
        """Distinguishes 'didn't run' from 'ran, found none' — caller
        relies on this to decide whether to write the key at all."""
        assert detect_shard_families({"physicalMapping": {}}) is None
        assert detect_shard_families({}) is None
        assert detect_shard_families({"physicalMapping": {"entities": {}}}) is None

    def test_size_below_threshold_skipped_via_param(self):
        entities = {
            "AlphaDocument": _entity("p"),
            "BetaDocument": _entity("p"),
        }
        assert detect_shard_families(_data_with_entities(entities), min_family_size=3) == []

    def test_different_property_sets_bucket_separately(self):
        entities = {
            "AlphaDocument": _entity("a", "b"),
            "BetaDocument": _entity("a", "c"),
        }
        assert detect_shard_families(_data_with_entities(entities)) == []


# ---------------------------------------------------------------------------
# detect_shard_families — output shape
# ---------------------------------------------------------------------------


class TestDetectShardFamiliesOutputShape:
    def test_member_records_carry_collection_name_or_fall_back_to_entity_name(self):
        entities = {
            "AlphaDocument": _entity("p", collection_name="alpha_docs"),
            "BetaDocument": _entity("p"),  # no collectionName
        }
        families = detect_shard_families(_data_with_entities(entities))
        assert families is not None and len(families) == 1
        members = {m["entity"]: m for m in families[0]["members"]}
        assert members["AlphaDocument"]["collectionName"] == "alpha_docs"
        # Fallback when the entity's mapping omits collectionName.
        assert members["BetaDocument"]["collectionName"] == "BetaDocument"

    def test_shared_properties_sorted_and_match_member_props(self):
        entities = {
            "AlphaDocument": _entity("z", "a", "m", collection_name="alpha"),
            "BetaDocument": _entity("a", "z", "m", collection_name="beta"),
        }
        families = detect_shard_families(_data_with_entities(entities))
        assert families is not None
        assert families[0]["sharedProperties"] == ["a", "m", "z"]


# ---------------------------------------------------------------------------
# _apply_shard_families — analyzer integration
# ---------------------------------------------------------------------------


class TestApplyShardFamiliesIntegration:
    def test_writes_shard_families_into_physical_mapping(self):
        data = _data_with_entities(
            {
                "AlphaDocument": _entity("p1", "p2"),
                "BetaDocument": _entity("p1", "p2"),
            }
        )
        _apply_shard_families(data)
        assert "shardFamilies" in data["physicalMapping"]
        assert len(data["physicalMapping"]["shardFamilies"]) == 1

    def test_writes_empty_list_when_detection_runs_but_finds_none(self):
        """Distinguishes 'ran' (empty list) from 'didn't run' (key absent)."""
        data = _data_with_entities(
            {
                "Foo": _entity("a"),  # singleton — no family
            }
        )
        _apply_shard_families(data)
        assert data["physicalMapping"].get("shardFamilies") == []

    def test_no_op_when_physical_mapping_missing(self):
        data: dict[str, Any] = {}
        _apply_shard_families(data)
        assert "physicalMapping" not in data

    def test_no_op_when_entities_missing(self):
        """If physicalMapping has no entities dict, key must NOT appear
        — preserves byte-identity with pre-detector output."""
        data = {"physicalMapping": {"relationships": {}}}
        _apply_shard_families(data)
        assert "shardFamilies" not in data["physicalMapping"]
