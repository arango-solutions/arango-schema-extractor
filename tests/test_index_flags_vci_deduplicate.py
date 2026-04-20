"""
Tests for issue #2: `vci`, `deduplicate`, and `storedValues` flags on
exported physical-mapping indexes.
"""

from __future__ import annotations

from schema_analyzer.baseline import (
    _build_index_lookup,
    _extract_indexes_for_mapping,
)


def _col(indexes: list[dict]) -> dict:
    return {"name": "c", "type": "document", "indexes": indexes}


def test_baseline_index_shape_unchanged_for_plain_persistent_index() -> None:
    idx = {
        "type": "persistent",
        "fields": ["email"],
        "unique": True,
        "sparse": False,
        "name": "idx_email",
    }
    out = _extract_indexes_for_mapping(_col([idx]))

    assert out == [
        {
            "type": "persistent",
            "fields": ["email"],
            "unique": True,
            "name": "idx_email",
        }
    ]


def test_vci_flag_emitted_when_index_has_vci_true() -> None:
    idx = {
        "type": "persistent",
        "fields": ["_from", "priority"],
        "vci": True,
    }
    out = _extract_indexes_for_mapping(_col([idx]))

    assert out[0]["vci"] is True


def test_vci_flag_emitted_for_known_vci_type_aliases() -> None:
    for idx_type in ("vci", "vertex_centric_index"):
        out = _extract_indexes_for_mapping(_col([{"type": idx_type, "fields": ["_from"]}]))
        assert out[0]["vci"] is True


def test_vci_flag_absent_for_non_vci_edge_index() -> None:
    out = _extract_indexes_for_mapping(_col([{"type": "persistent", "fields": ["_from"]}]))
    assert "vci" not in out[0]


def test_deduplicate_only_emitted_when_explicitly_false() -> None:
    explicit_false = _extract_indexes_for_mapping(
        _col([{"type": "persistent", "fields": ["tags[*]"], "deduplicate": False}])
    )
    assert explicit_false[0]["deduplicate"] is False

    default_true = _extract_indexes_for_mapping(
        _col([{"type": "persistent", "fields": ["tags[*]"], "deduplicate": True}])
    )
    assert "deduplicate" not in default_true[0]

    missing = _extract_indexes_for_mapping(_col([{"type": "persistent", "fields": ["tags[*]"]}]))
    assert "deduplicate" not in missing[0]


def test_stored_values_round_trip_when_non_empty() -> None:
    out = _extract_indexes_for_mapping(
        _col(
            [
                {
                    "type": "persistent",
                    "fields": ["name"],
                    "storedValues": ["email", "status"],
                }
            ]
        )
    )
    assert out[0]["storedValues"] == ["email", "status"]


def test_stored_values_omitted_when_empty_or_missing_or_bad_type() -> None:
    for extra in ({}, {"storedValues": []}, {"storedValues": "not-a-list"}, {"storedValues": [1, 2]}):
        out = _extract_indexes_for_mapping(_col([{"type": "persistent", "fields": ["name"], **extra}]))
        assert "storedValues" not in out[0]


def test_vci_index_does_not_flip_property_indexed_flag() -> None:
    """
    A VCI is an edge-traversal index; it must not mark the covered property
    as a point-lookup index on the entity mapping.
    """
    col = _col(
        [
            {"type": "vci", "fields": ["_from", "priority"]},
            {"type": "persistent", "fields": ["status"]},
        ]
    )
    lookup = _build_index_lookup(col)

    assert "status" in lookup
    assert "priority" not in lookup
    assert "_from" not in lookup


def test_full_flag_set_composes_on_single_index_entry() -> None:
    idx = {
        "type": "persistent",
        "fields": ["_from", "label"],
        "unique": False,
        "sparse": True,
        "name": "idx_edge",
        "vci": True,
        "deduplicate": False,
        "storedValues": ["weight"],
    }
    out = _extract_indexes_for_mapping(_col([idx]))

    assert out == [
        {
            "type": "persistent",
            "fields": ["_from", "label"],
            "sparse": True,
            "name": "idx_edge",
            "vci": True,
            "deduplicate": False,
            "storedValues": ["weight"],
        }
    ]
