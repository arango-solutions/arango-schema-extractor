"""Tests for multitenancy detection (PRD §6.2 bullet 4).

Covers each of the five detection styles plus the no-signal and
degraded paths in :mod:`schema_analyzer.multitenancy`, and the
analyzer-integration helper :func:`schema_analyzer.analyzer._apply_multitenancy`.

Detection is pure (no DB / no LLM), so every test constructs a hand-rolled
``snapshot`` + ``data`` pair and asserts on the returned classification block.
The integration tests follow the same pattern as ``test_sharding_profile.py``:
build a minimal pair, run the helper, assert what was stamped under
``data["metadata"]``.
"""

from __future__ import annotations

from typing import Any

from schema_analyzer.analyzer import _apply_multitenancy
from schema_analyzer.multitenancy import classify_multitenancy


def _col(
    name: str,
    *,
    shard_keys: list[str] | None = None,
    is_system: bool = False,
    samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if shard_keys is not None:
        props["shardKeys"] = shard_keys
    if is_system:
        props["isSystem"] = True
    entry: dict[str, Any] = {"name": name, "properties": props}
    if samples is not None:
        entry["sample_documents"] = samples
    return entry


def _entity(*prop_names: str, collection_name: str | None = None) -> dict[str, Any]:
    return {
        "style": "COLLECTION",
        "collectionName": collection_name,
        "properties": {p: {"field": p} for p in prop_names},
    }


def _data(entities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"physicalMapping": {"entities": entities, "relationships": {}}}


def _snapshot(
    cols: list[dict[str, Any]],
    *,
    db_name: str | None = None,
) -> dict[str, Any]:
    snap: dict[str, Any] = {"collections": cols}
    if db_name is not None:
        snap["database"] = {"name": db_name}
    return snap


# ---------------------------------------------------------------------------
# Style 1: disjoint_smartgraph
# ---------------------------------------------------------------------------


class TestDisjointSmartgraph:
    def test_extracts_smart_attribute_as_tenant_key(self):
        snap = _snapshot([_col("Doc")])
        sharding = {
            "style": "DisjointSmartGraph",
            "graphs": [
                {
                    "name": "tenant_graph",
                    "isSmart": True,
                    "isDisjoint": True,
                    "smartGraphAttribute": "tenantId",
                }
            ],
        }
        block = classify_multitenancy(_data({"Doc": _entity("p")}), snap, sharding_profile=sharding)
        assert block is not None
        assert block["style"] == "disjoint_smartgraph"
        assert block["physicalEnforcement"] is True
        assert block["tenantKey"] == ["tenantId"]
        assert block["evidence"]["disjointGraphs"] == ["tenant_graph"]
        assert block["status"] == "ok"

    def test_degraded_when_smart_attribute_missing(self):
        snap = _snapshot([_col("Doc")])
        sharding = {
            "style": "DisjointSmartGraph",
            "graphs": [{"name": "g", "isSmart": True, "isDisjoint": True}],
        }
        block = classify_multitenancy(_data({"Doc": _entity("p")}), snap, sharding_profile=sharding)
        assert block is not None
        assert block["style"] == "disjoint_smartgraph"
        assert block["status"] == "degraded"
        assert block["tenantKey"] == []

    def test_skipped_when_sharding_style_differs(self):
        """SmartGraph (non-disjoint) must NOT trigger this branch."""
        snap = _snapshot([_col("Doc", shard_keys=["x"])])
        sharding = {
            "style": "SmartGraph",
            "graphs": [{"name": "g", "isSmart": True, "smartGraphAttribute": "x"}],
        }
        block = classify_multitenancy(_data({"Doc": _entity("p")}), snap, sharding_profile=sharding)
        assert block is not None
        assert block["style"] != "disjoint_smartgraph"


# ---------------------------------------------------------------------------
# Style 2: shard_key
# ---------------------------------------------------------------------------


class TestShardKey:
    def test_shared_tenant_shard_key_across_collections(self):
        snap = _snapshot(
            [
                _col("Document", shard_keys=["tenantId"]),
                _col("Person", shard_keys=["tenantId"]),
                _col("Event", shard_keys=["tenantId"]),
            ]
        )
        block = classify_multitenancy(
            _data({"Document": _entity("p"), "Person": _entity("p"), "Event": _entity("p")}),
            snap,
        )
        assert block is not None
        assert block["style"] == "shard_key"
        assert block["physicalEnforcement"] is True
        assert block["tenantKey"] == ["tenantId"]
        assert len(block["tenantKeyCollections"]) == 3

    def test_org_id_recognised_as_tenant_keyish(self):
        snap = _snapshot(
            [
                _col("Document", shard_keys=["org_id"]),
                _col("Person", shard_keys=["org_id"]),
            ]
        )
        block = classify_multitenancy(_data({}), snap)
        assert block is not None
        assert block["style"] == "shard_key"
        assert block["tenantKey"] == ["org_id"]

    def test_non_tenant_shard_key_does_not_trigger(self):
        """Shard keys that don't look tenant-ish must NOT trigger
        shard_key classification."""
        snap = _snapshot(
            [
                _col("Document", shard_keys=["userId"]),
                _col("Person", shard_keys=["userId"]),
            ]
        )
        block = classify_multitenancy(_data({}), snap)
        assert block is not None
        assert block["style"] == "none"

    def test_single_collection_does_not_trigger(self):
        """One collection's shard key isn't strong enough evidence."""
        snap = _snapshot([_col("Document", shard_keys=["tenantId"])])
        block = classify_multitenancy(_data({}), snap)
        assert block is not None
        assert block["style"] == "none"


# ---------------------------------------------------------------------------
# Style 3: discriminator_field
# ---------------------------------------------------------------------------


class TestDiscriminatorField:
    def test_high_coverage_field_triggers_classification(self):
        snap = _snapshot(
            [
                _col("Document"),
                _col("Person"),
                _col("Event"),
                _col("Reference"),
            ]
        )
        data = _data(
            {
                "Document": _entity("name", "tenantId", collection_name="Document"),
                "Person": _entity("name", "tenantId", collection_name="Person"),
                "Event": _entity("name", "tenantId", collection_name="Event"),
                "Reference": _entity("label", collection_name="Reference"),
            }
        )
        block = classify_multitenancy(data, snap)
        assert block is not None
        assert block["style"] == "discriminator_field"
        assert block["physicalEnforcement"] is False
        assert block["tenantKey"] == ["tenantId"]
        assert block["evidence"]["fraction"] == 0.75
        assert "warning" in block["evidence"]
        assert len(block["tenantKeyCollections"]) == 3

    def test_below_coverage_threshold_does_not_trigger(self):
        snap = _snapshot([_col("A"), _col("B"), _col("C"), _col("D")])
        data = _data(
            {
                "A": _entity("tenantId", collection_name="A"),
                "B": _entity("name", collection_name="B"),
                "C": _entity("name", collection_name="C"),
                "D": _entity("name", collection_name="D"),
            }
        )
        block = classify_multitenancy(data, snap)
        assert block is not None
        assert block["style"] == "none"

    def test_sample_documents_drive_coverage_and_cardinality(self):
        """When samples are present, coverage is per-doc and cardinality
        is the distinct-value count from the sample."""
        snap = _snapshot(
            [
                _col(
                    "Document",
                    samples=[
                        {"tenantId": "t1", "name": "a"},
                        {"tenantId": "t2", "name": "b"},
                        {"tenantId": "t1", "name": "c"},
                        {"name": "d"},  # missing tenantId
                    ],
                ),
                _col(
                    "Person",
                    samples=[
                        {"tenantId": "t1"},
                        {"tenantId": "t3"},
                    ],
                ),
            ]
        )
        data = _data(
            {
                "Document": _entity("name", "tenantId", collection_name="Document"),
                "Person": _entity("tenantId", collection_name="Person"),
            }
        )
        block = classify_multitenancy(data, snap)
        assert block is not None
        assert block["style"] == "discriminator_field"
        records = {r["collection"]: r for r in block["tenantKeyCollections"]}
        assert records["Document"]["coverage"] == 0.75
        assert records["Document"]["distinctValues"] == 2
        assert records["Document"]["sampleValues"] == ["t1", "t2"]
        assert records["Person"]["coverage"] == 1.0
        assert records["Person"]["distinctValues"] == 2

    def test_loses_to_shard_key_when_both_signals_present(self):
        """shard_key is checked first ⇒ wins when both fire."""
        snap = _snapshot(
            [
                _col("Document", shard_keys=["tenantId"]),
                _col("Person", shard_keys=["tenantId"]),
            ]
        )
        data = _data(
            {
                "Document": _entity("tenantId"),
                "Person": _entity("tenantId"),
            }
        )
        block = classify_multitenancy(data, snap)
        assert block["style"] == "shard_key"


# ---------------------------------------------------------------------------
# Style 4: collection_per_tenant
# ---------------------------------------------------------------------------


class TestCollectionPerTenant:
    def test_double_underscore_pattern(self):
        snap = _snapshot(
            [
                _col("Document__acme"),
                _col("Person__acme"),
                _col("Document__globex"),
                _col("Person__globex"),
            ]
        )
        block = classify_multitenancy(_data({}), snap)
        assert block is not None
        assert block["style"] == "collection_per_tenant"
        assert block["physicalEnforcement"] is True
        ev = block["evidence"]
        assert ev["tenantCount"] == 2
        assert ev["baseCount"] == 2
        assert sorted(ev["tenants"]) == ["acme", "globex"]
        assert sorted(ev["bases"]) == ["Document", "Person"]

    def test_single_tenant_does_not_trigger(self):
        """Need at least 2 tenants — one tenant is just a base name."""
        snap = _snapshot(
            [
                _col("Document__acme"),
                _col("Person__acme"),
            ]
        )
        block = classify_multitenancy(_data({}), snap)
        assert block is not None
        assert block["style"] == "none"


# ---------------------------------------------------------------------------
# Style 5: unknown_single_db (database_per_tenant hint)
# ---------------------------------------------------------------------------


class TestUnknownSingleDb:
    def test_db_name_matches_tenant_pattern(self):
        snap = _snapshot([_col("Document")], db_name="tenant_acme")
        block = classify_multitenancy(_data({"Document": _entity("p")}), snap)
        assert block is not None
        assert block["style"] == "unknown_single_db"
        assert block["physicalEnforcement"] is True
        assert block["evidence"]["databaseName"] == "tenant_acme"
        assert "matchedPattern" in block["evidence"]

    def test_neutral_db_name_does_not_trigger(self):
        snap = _snapshot([_col("Document")], db_name="production")
        block = classify_multitenancy(_data({"Document": _entity("p")}), snap)
        assert block is not None
        assert block["style"] == "none"


# ---------------------------------------------------------------------------
# Style 0: none
# ---------------------------------------------------------------------------


class TestNoneStyle:
    def test_no_signals_returns_none_style(self):
        snap = _snapshot([_col("Document"), _col("Person")])
        data = _data({"Document": _entity("name"), "Person": _entity("name")})
        block = classify_multitenancy(data, snap)
        assert block is not None
        assert block["style"] == "none"
        assert block["physicalEnforcement"] is False
        assert block["tenantKey"] == []
        assert block["status"] == "ok"

    def test_returns_none_for_empty_snapshot(self):
        """Distinguishes 'didn't run' from 'ran, found no tenancy'."""
        assert classify_multitenancy({}, {}) is None
        assert classify_multitenancy({}, {"collections": []}) is None
        assert classify_multitenancy({}, {"collections": [_col("_users", is_system=True)]}) is None


# ---------------------------------------------------------------------------
# _apply_multitenancy — analyzer integration
# ---------------------------------------------------------------------------


class TestApplyMultitenancyIntegration:
    def test_stamps_block_and_status(self):
        data = _data(
            {
                "Document": _entity("tenantId"),
                "Person": _entity("tenantId"),
            }
        )
        snap = _snapshot(
            [
                _col("Document", shard_keys=["tenantId"]),
                _col("Person", shard_keys=["tenantId"]),
            ]
        )
        _apply_multitenancy(data, snap)
        meta = data.get("metadata") or {}
        assert meta["multitenancy"]["style"] == "shard_key"
        assert meta["multitenancyStatus"] == "ok"

    def test_consumes_existing_sharding_profile(self):
        data: dict[str, Any] = {
            "physicalMapping": {"entities": {"Doc": _entity("p")}, "relationships": {}},
            "metadata": {
                "shardingProfile": {
                    "style": "DisjointSmartGraph",
                    "graphs": [
                        {
                            "name": "tg",
                            "isSmart": True,
                            "isDisjoint": True,
                            "smartGraphAttribute": "tenantId",
                        }
                    ],
                }
            },
        }
        snap = _snapshot([_col("Doc")])
        _apply_multitenancy(data, snap)
        assert data["metadata"]["multitenancy"]["style"] == "disjoint_smartgraph"
        assert data["metadata"]["multitenancy"]["tenantKey"] == ["tenantId"]

    def test_no_op_when_classify_returns_none(self):
        data: dict[str, Any] = {}
        _apply_multitenancy(data, {"collections": []})
        assert "metadata" not in data
