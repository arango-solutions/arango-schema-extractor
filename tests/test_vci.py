"""Tests for vertex-centric-index (VCI) detection (PRD §6.1/§6.2)."""

from __future__ import annotations

from schema_analyzer.vci import detect_vci

SNAPSHOT = {
    "collections": [
        {
            "name": "users",
            "type": "document",
            "observed_fields": {"fields": ["name", "tier"]},
        },
        {
            "name": "follows",
            "type": "edge",
            "observed_fields": {"by_type": {"FRIEND": ["tier", "validFrom"]}},
            "edge_endpoints": {"from_collections": ["users"], "to_collections": ["users"]},
            "indexes": [
                {"type": "edge", "fields": ["_from", "_to"]},
                {"type": "persistent", "fields": ["_from", "type", "validFrom"], "unique": False, "sparse": False},
                {"type": "persistent", "fields": ["_to", "type"], "unique": True, "sparse": True},
                {"type": "persistent", "fields": ["tier"]},  # not rooted at _from/_to -> ignored
            ],
        },
    ]
}


def _data():
    return {
        "physicalMapping": {
            "entities": {},
            "relationships": {"FRIEND": {"style": "GENERIC_WITH_TYPE", "edgeCollectionName": "follows"}},
        },
        "metadata": {},
    }


def test_detect_vci_index_level():
    data = _data()
    summary = detect_vci(data, SNAPSHOT)
    assert summary == {"status": "ok", "relationships": ["FRIEND"]}

    vci = data["physicalMapping"]["relationships"]["FRIEND"]["vci"]
    assert vci["indexLevel"]["accessPattern"] == "both"
    idx_fields = {tuple(i["fields"]): i for i in vci["indexLevel"]["indexes"]}
    assert ("_from", "type", "validFrom") in idx_fields
    assert idx_fields[("_from", "type", "validFrom")]["discriminatorFields"] == ["type", "validFrom"]
    assert idx_fields[("_to", "type")]["unique"] is True
    assert idx_fields[("_to", "type")]["sparse"] is True


def test_detect_vci_denormalization():
    data = _data()
    detect_vci(data, SNAPSHOT)
    denorm = data["physicalMapping"]["relationships"]["FRIEND"]["vci"]["denormalization"]["duplicatedFields"]
    # 'tier' is on both the edge and the endpoint vertex 'users'; 'validFrom' is edge-only.
    assert denorm == [{"field": "tier", "sourceCollections": ["users"]}]


def test_detect_vci_sets_candidate_flag_without_overwriting_style():
    data = _data()
    detect_vci(data, SNAPSHOT)
    rel = data["physicalMapping"]["relationships"]["FRIEND"]
    assert rel["style"] == "GENERIC_WITH_TYPE"  # underlying style preserved
    assert rel["vciCandidate"] is True


def test_detect_vci_no_signal_returns_none():
    data = {
        "physicalMapping": {
            "entities": {},
            "relationships": {"R": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "plain"}},
        },
        "metadata": {},
    }
    snapshot = {
        "collections": [{"name": "plain", "type": "edge", "indexes": [{"type": "edge", "fields": ["_from", "_to"]}]}]
    }
    assert detect_vci(data, snapshot) is None
    assert "vci" not in data["physicalMapping"]["relationships"]["R"]


def test_detect_vci_no_relationships_returns_none():
    assert detect_vci({"physicalMapping": {"entities": {}, "relationships": {}}}, SNAPSHOT) is None


def test_access_pattern_out_edge_only():
    snapshot = {
        "collections": [
            {
                "name": "e",
                "type": "edge",
                "indexes": [{"type": "persistent", "fields": ["_from", "type"]}],
            }
        ]
    }
    data = {
        "physicalMapping": {
            "entities": {},
            "relationships": {"R": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "e"}},
        },
        "metadata": {},
    }
    detect_vci(data, snapshot)
    assert data["physicalMapping"]["relationships"]["R"]["vci"]["indexLevel"]["accessPattern"] == "out-edge"
