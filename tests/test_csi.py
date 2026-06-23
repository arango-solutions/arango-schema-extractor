"""Tests for the Conceptual Schema Interchange (CSI) v1 contract."""

from __future__ import annotations

import json
from pathlib import Path

from schema_analyzer.csi import (
    CSI_VERSION,
    from_csi,
    load_csi_schema_v1,
    to_csi,
    validate_csi,
)
from schema_analyzer.diff import diff_analyses
from schema_analyzer.quality import compute_gold_comparison
from schema_analyzer.tool import run_tool
from schema_analyzer.tool_contract_v1 import validate_response_v1

ROOT = Path(__file__).resolve().parents[1]

ANALYSIS = {
    "conceptualSchema": {
        "entities": [{"name": "User", "labels": ["User"], "properties": [{"name": "email"}]}],
        "relationships": [{"type": "FOLLOWS", "fromEntity": "User", "toEntity": "User"}],
        "properties": [],
    },
    "physicalMapping": {
        "entities": {"User": {"style": "COLLECTION", "collectionName": "users", "source": "llm"}},
        "relationships": {"FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"}},
    },
    "metadata": {
        "confidence": 0.82,
        "timestamp": "2026-06-22T00:00:00Z",
        "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
        "detectedPatterns": [],
        "physicalSchemaFingerprint": "sha256:abc",
        "analysisCompletedAt": "2026-06-22T00:00:01Z",
    },
}


def test_to_csi_shape_and_provenance():
    csi = to_csi(ANALYSIS)
    assert csi["csiVersion"] == CSI_VERSION == "1"
    assert csi["conceptualModel"]["entities"][0]["name"] == "User"
    assert csi["arangoPhysicalMapping"]["entities"]["User"]["collectionName"] == "users"
    prov = csi["provenance"]
    assert prov["producer"] == "arango-schema-analyzer"
    assert prov["direction"] == "reverse"
    assert prov["source"] == {"kind": "arangodb", "fingerprint": "sha256:abc"}
    assert prov["confidence"] == 0.82
    assert prov["generatedAt"] == "2026-06-22T00:00:01Z"


def test_to_csi_validates_against_schema():
    assert validate_csi(to_csi(ANALYSIS)) == []


def test_forward_source_override():
    csi = to_csi(
        ANALYSIS,
        direction="forward",
        source={"kind": "relational", "ref": "northwind", "fingerprint": "sha256:rel"},
        producer="r2g",
        producer_version="9.9.9",
    )
    assert csi["provenance"]["direction"] == "forward"
    assert csi["provenance"]["producer"] == "r2g"
    assert csi["provenance"]["producerVersion"] == "9.9.9"
    assert csi["provenance"]["source"]["kind"] == "relational"
    assert validate_csi(csi) == []


def test_from_csi_roundtrip_is_consumable():
    csi = to_csi(ANALYSIS)
    back = from_csi(csi)
    assert back["conceptualSchema"]["entities"][0]["name"] == "User"
    assert back["physicalMapping"]["entities"]["User"]["collectionName"] == "users"
    assert back["metadata"]["confidence"] == 0.82
    assert back["metadata"]["physicalSchemaFingerprint"] == "sha256:abc"


def test_from_csi_output_feeds_diff_and_gold():
    # An R2G-style CSI (built independently) can be audited by this library.
    r2g_csi = {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [{"name": "User"}, {"name": "Post"}],
            "relationships": [{"type": "WROTE", "fromEntity": "User", "toEntity": "Post"}],
            "properties": [],
        },
        "arangoPhysicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "users"},
                "Post": {"style": "COLLECTION", "collectionName": "posts"},
            },
            "relationships": {"WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"}},
        },
        "provenance": {"producer": "r2g", "direction": "forward", "source": {"kind": "relational"}},
    }
    assert validate_csi(r2g_csi) == []
    analysis_shaped = from_csi(r2g_csi)
    # diff vs this library's analysis
    d = diff_analyses(ANALYSIS, analysis_shaped)
    assert d["entities"]["added"] == ["Post"]
    # gold comparison vs a reference
    gold = compute_gold_comparison(
        analysis_shaped["conceptualSchema"], {"entities": [{"name": "user"}, {"name": "post"}], "relationships": []}
    )
    assert gold["entities"]["f1"] == 1.0


def test_csi_rejects_relationship_with_collection_name():
    bad = to_csi(ANALYSIS)
    bad["arangoPhysicalMapping"]["relationships"]["FOLLOWS"]["collectionName"] = "follows"
    errors = validate_csi(bad)
    assert errors, "edges must use edgeCollectionName, not collectionName"


def test_csi_requires_core_blocks():
    assert validate_csi({"csiVersion": "1"})  # missing conceptualModel/arangoPhysicalMapping/provenance


# ── Schema parity: docs copy must equal bundled copy ─────────────────────


def test_csi_schema_docs_and_bundled_byte_identical():
    docs = (ROOT / "docs" / "csi" / "v1" / "csi.schema.json").read_bytes()
    bundled = (ROOT / "schema_analyzer" / "csi" / "v1" / "csi.schema.json").read_bytes()
    assert docs == bundled


def test_runtime_loader_matches_docs_copy():
    docs = json.loads((ROOT / "docs" / "csi" / "v1" / "csi.schema.json").read_text("utf-8"))
    assert load_csi_schema_v1() == docs


# ── Tool operation ───────────────────────────────────────────────────────


def test_csi_tool_operation():
    full_analysis = dict(ANALYSIS)
    resp = run_tool({"contractVersion": "1", "operation": "csi", "input": {"analysis": full_analysis}})
    assert resp["ok"] is True, resp
    assert resp["result"]["csi"]["csiVersion"] == "1"
    assert validate_response_v1(resp) == []


def test_csi_tool_requires_analysis():
    resp = run_tool({"contractVersion": "1", "operation": "csi", "input": {}})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_REQUEST"
