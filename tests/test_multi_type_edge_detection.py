"""
Tests for issue #4: detection of multi-type edge collections and per-type
``GENERIC_WITH_TYPE`` emission.

Covers Change A (discriminator-field detection via distribution shape) by
exercising ``_pick_best_type_field`` directly against synthetic snapshot
entries with controlled cardinality/coverage profiles, and Change B
(one mapping entry per distinct ``typeValue``) via a
``infer_baseline_from_snapshot`` end-to-end assertion.
"""

from __future__ import annotations

from schema_analyzer.baseline import infer_baseline_from_snapshot
from schema_analyzer.snapshot import _pick_best_type_field


def _edge_entry(
    *,
    name: str,
    count: int,
    candidate_type_fields: list[str],
    value_counts: dict[str, list[dict]],
) -> dict:
    return {
        "name": name,
        "type": "edge",
        "count": count,
        "candidate_type_fields": candidate_type_fields,
        "sample_field_value_counts": value_counts,
        "observed_fields": {"fields": []},
        "edge_endpoints": {"from_collections": [], "to_collections": []},
    }


# ── AC1: per-type emission of genuine multi-type edges ───────────────────


def test_multi_type_edge_emits_one_entry_per_type_value() -> None:
    snapshot = {
        "collections": [
            {
                "name": "people",
                "type": "document",
                "count": 2,
                "properties": {"type": 2},
                "candidate_type_fields": [],
                "sample_field_value_counts": {},
                "observed_fields": {"fields": ["name"]},
                "inferred_entity_type": "Person",
            },
            {
                "name": "movies",
                "type": "document",
                "count": 1,
                "properties": {"type": 2},
                "candidate_type_fields": [],
                "sample_field_value_counts": {},
                "observed_fields": {"fields": ["title"]},
                "inferred_entity_type": "Movie",
            },
            _edge_entry(
                name="edges",
                count=200,
                candidate_type_fields=["relation"],
                value_counts={
                    "relation": [
                        {"value": "ACTED_IN", "count": 100},
                        {"value": "DIRECTED", "count": 100},
                    ]
                },
            ),
        ],
        "graphs": [],
    }

    out = infer_baseline_from_snapshot(snapshot)
    rels = out["physicalMapping"]["relationships"]

    assert set(rels.keys()) == {"ACTED_IN", "DIRECTED"}
    for rel_type in ("ACTED_IN", "DIRECTED"):
        rel = rels[rel_type]
        assert rel["style"] == "GENERIC_WITH_TYPE"
        assert rel["typeField"] == "relation"
        assert rel["typeValue"] == rel_type


# ── AC2: discriminator detection broadened past CANDIDATE_TYPE_KEYS ───────


def test_non_allowlisted_discriminator_field_is_still_detected() -> None:
    """
    Field name ``category`` is not in CANDIDATE_TYPE_KEYS, but its value
    distribution (2 distinct short alphanumeric strings covering the whole
    collection) should still trigger GENERIC_WITH_TYPE classification.
    """
    entry = _edge_entry(
        name="edges",
        count=100,
        candidate_type_fields=["category"],
        value_counts={
            "category": [
                {"value": "A", "count": 50},
                {"value": "B", "count": 50},
            ]
        },
    )
    assert _pick_best_type_field(entry, is_edge=True) == "category"


# ── AC3: dedicated edge with no discriminator is not misclassified ────────


def test_dedicated_edge_with_no_discriminator_is_not_generic() -> None:
    entry = _edge_entry(
        name="acted_in",
        count=50,
        candidate_type_fields=[],
        value_counts={},
    )
    assert _pick_best_type_field(entry, is_edge=True) is None


def test_dedicated_edge_with_redundant_relation_field_is_not_generic() -> None:
    """
    A PG edge collection named ``mentions`` carrying a ``relation`` field
    whose single value is ``"mentions"`` (echoing the collection name)
    must not be promoted to GENERIC_WITH_TYPE.
    """
    entry = _edge_entry(
        name="mentions",
        count=10,
        candidate_type_fields=["relation"],
        value_counts={"relation": [{"value": "mentions", "count": 10}]},
    )
    assert _pick_best_type_field(entry, is_edge=True) is None


# ── AC4: high-cardinality ID-like fields are rejected ─────────────────────


def test_id_like_field_with_many_distinct_values_is_rejected() -> None:
    """
    A field with 1000 distinct values across 1000 edges looks like an ID,
    not a type discriminator. It must be rejected even if its shape passes
    the simple name filter.
    """
    entry = _edge_entry(
        name="edges",
        count=1000,
        candidate_type_fields=["tag"],
        value_counts={"tag": [{"value": f"v{i}", "count": 1} for i in range(64)]},
    )
    assert _pick_best_type_field(entry, is_edge=True) is None


def test_discriminator_rejected_when_coverage_fraction_is_too_low() -> None:
    """
    Top-K distinct values that cover only a small fraction of the
    collection (e.g. 10/10_000) do not indicate a true type tag.
    """
    entry = _edge_entry(
        name="edges",
        count=10_000,
        candidate_type_fields=["relation"],
        value_counts={
            "relation": [
                {"value": "ACTED_IN", "count": 5},
                {"value": "DIRECTED", "count": 5},
            ]
        },
    )
    assert _pick_best_type_field(entry, is_edge=True) is None


def test_discriminator_accepts_high_coverage_even_with_extra_distincts() -> None:
    entry = _edge_entry(
        name="edges",
        count=100,
        candidate_type_fields=["relation"],
        value_counts={
            "relation": [
                {"value": "ACTED_IN", "count": 50},
                {"value": "DIRECTED", "count": 45},
                {"value": "FOLLOWS", "count": 5},
            ]
        },
    )
    assert _pick_best_type_field(entry, is_edge=True) == "relation"


def test_discriminator_rejected_for_free_form_string_values() -> None:
    """Values with spaces / punctuation / long content are content, not tags."""
    entry = _edge_entry(
        name="edges",
        count=100,
        candidate_type_fields=["note"],
        value_counts={
            "note": [
                {"value": "some free-form sentence", "count": 50},
                {"value": "another free-form sentence", "count": 50},
            ]
        },
    )
    assert _pick_best_type_field(entry, is_edge=True) is None
