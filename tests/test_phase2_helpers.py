"""Phase 2 regression tests for M1 (collection-name allowlist) and M4
(shared ``entity_property_names`` helper / tenant_scope dict-shape fix).
"""

from __future__ import annotations

from schema_analyzer.reconcile import (
    snapshot_collection_names,
    strip_unknown_collection_names,
)
from schema_analyzer.tenant_scope import annotate_tenant_scope
from schema_analyzer.utils import entity_property_names

# ---------- M1 ----------


def _snapshot_with(*names: str) -> dict:
    return {"collections": [{"name": n, "type": "document"} for n in names]}


def test_m1_snapshot_collection_names():
    snap = _snapshot_with("users", "orders")
    assert snapshot_collection_names(snap) == {"users", "orders"}


def test_m1_strip_keeps_known_names():
    snap = _snapshot_with("users", "follows")
    data = {
        "physicalMapping": {
            "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
            "relationships": {
                "FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"},
            },
        }
    }
    warnings = strip_unknown_collection_names(data, snap)
    assert warnings == []
    assert data["physicalMapping"]["entities"]["User"]["collectionName"] == "users"
    assert data["physicalMapping"]["relationships"]["FOLLOWS"]["edgeCollectionName"] == "follows"


def test_m1_strip_removes_hallucinated_entity_collection():
    snap = _snapshot_with("real_users")
    data = {
        "physicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "../../etc/passwd"},
            },
            "relationships": {},
        }
    }
    warnings = strip_unknown_collection_names(data, snap)
    assert len(warnings) == 1
    assert "User" in warnings[0]
    assert "collectionName" not in data["physicalMapping"]["entities"]["User"]


def test_m1_strip_removes_hallucinated_relationship_collection():
    snap = _snapshot_with("users")
    data = {
        "physicalMapping": {
            "entities": {},
            "relationships": {
                "BOGUS": {
                    "style": "DEDICATED_COLLECTION",
                    "edgeCollectionName": "fake_edges",
                    "collectionName": "also_fake",
                },
            },
        }
    }
    warnings = strip_unknown_collection_names(data, snap)
    assert len(warnings) == 2
    rel = data["physicalMapping"]["relationships"]["BOGUS"]
    assert "edgeCollectionName" not in rel
    assert "collectionName" not in rel


def test_m1_strip_is_noop_when_snapshot_has_no_collections():
    data = {
        "physicalMapping": {
            "entities": {"X": {"collectionName": "anything"}},
            "relationships": {},
        }
    }
    assert strip_unknown_collection_names(data, {}) == []
    assert data["physicalMapping"]["entities"]["X"]["collectionName"] == "anything"


# ---------- M4: shared helper ----------


def test_m4_helper_handles_dict_shape():
    entity = {"name": "Foo", "properties": {"TENANT_ID": {"field": "TENANT_ID"}, "name": {}}}
    assert sorted(entity_property_names(entity)) == ["TENANT_ID", "name"]


def test_m4_helper_handles_list_of_dicts():
    entity = {"name": "Foo", "properties": [{"name": "TENANT_ID"}, {"name": "name"}]}
    assert entity_property_names(entity) == ["TENANT_ID", "name"]


def test_m4_helper_handles_list_of_strings():
    entity = {"name": "Foo", "properties": ["TENANT_ID", "name"]}
    assert entity_property_names(entity) == ["TENANT_ID", "name"]


def test_m4_helper_returns_empty_for_unknown_shape():
    assert entity_property_names({"properties": "nope"}) == []
    assert entity_property_names({"properties": None}) == []
    assert entity_property_names({}) == []


# ---------- M4: tenant_scope no longer misses dict-shaped properties ----------


def test_m4_tenant_scope_finds_denorm_field_when_props_are_dict():
    """Latent bug fixed in M4: previously ``tenant_scope._entity_property_names``
    only handled list-shaped properties, so a conceptual entity emitted with
    dict-shaped properties (e.g. by an LLM that copied the physicalMapping
    convention) would silently fail to detect the denormalised tenant field
    and be classified as ``global``. After M4 the dict shape is recognised.
    """
    data = {
        "conceptualSchema": {
            "entities": [
                {"name": "Tenant", "labels": ["Tenant"], "properties": {}},
                {
                    "name": "Order",
                    "labels": ["Order"],
                    "properties": {"tenant_id": {"type": "string"}, "total": {"type": "number"}},
                },
            ],
            "relationships": [],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {
                "Tenant": {"style": "COLLECTION", "collectionName": "Tenant"},
                "Order": {"style": "COLLECTION", "collectionName": "orders"},
            },
            "relationships": {},
        },
    }
    summary = annotate_tenant_scope(data)
    assert summary is not None
    order_scope = data["physicalMapping"]["entities"]["Order"]["tenantScope"]
    assert order_scope["role"] == "tenant_scoped"
    assert order_scope["tenantField"] == "tenant_id"
