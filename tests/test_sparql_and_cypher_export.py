"""Tests for the SPARQL vocabulary export and the Cypher resolution adapter."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from schema_analyzer.exports import build_cypher_resolution_index, export_mapping
from schema_analyzer.tool import run_tool

ANALYSIS = {
    "conceptualSchema": {
        "entities": [
            {"name": "User", "properties": [{"name": "email"}]},
            {"name": "Post", "properties": []},
        ],
        "relationships": [
            {"type": "WROTE", "fromEntity": "User", "toEntity": "Post"},
            {"type": "LIKED", "fromEntity": "User", "toEntity": "Post"},
        ],
        "properties": [],
    },
    "physicalMapping": {
        "entities": {
            "User": {"style": "COLLECTION", "collectionName": "users"},
            "Post": {"style": "LABEL", "collectionName": "nodes", "typeField": "type", "typeValue": "Post"},
        },
        "relationships": {
            "WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"},
            "LIKED": {
                "style": "GENERIC_WITH_TYPE",
                "edgeCollectionName": "edges",
                "typeField": "relType",
                "typeValue": "LIKED",
            },
        },
    },
    "metadata": {
        "confidence": 0.9,
        "timestamp": "2026-01-01T00:00:00Z",
        "analyzedCollectionCounts": {"documentCollections": 2, "edgeCollections": 2},
        "detectedPatterns": [],
    },
}


# --------------------------------------------------------------------------
# SPARQL export
# --------------------------------------------------------------------------


def test_sparql_export_classes_and_properties():
    out = export_mapping(ANALYSIS, target="sparql")
    assert out["target"] == "sparql"
    assert "" in out["prefixes"]

    classes = {c["label"]: c for c in out["classes"]}
    assert set(classes) == {"User", "Post"}
    assert classes["User"]["iri"].endswith("User")
    assert classes["User"]["physical"]["collectionName"] == "users"
    assert classes["Post"]["physical"]["typeValue"] == "Post"

    props = {p["label"]: p for p in out["objectProperties"]}
    assert set(props) == {"WROTE", "LIKED"}
    assert props["WROTE"]["domain"].endswith("User")
    assert props["WROTE"]["range"].endswith("Post")
    assert props["LIKED"]["physical"]["typeField"] == "relType"


def test_sparql_export_datatype_properties():
    out = export_mapping(ANALYSIS, target="sparql")
    dps = out["datatypeProperties"]
    # User has one literal attribute (email); Post has none.
    assert len(dps) == 1
    email = dps[0]
    assert email["label"] == "email"
    assert email["attribute"] == "email"
    assert email["domain"].endswith("User")
    assert email["iri"].endswith("email")
    # carries the owning entity's physical resolution
    assert email["physical"]["collectionName"] == "users"


def test_sparql_export_datatype_properties_repeat_across_entities():
    analysis = {
        "conceptualSchema": {
            "entities": [
                {"name": "User", "properties": [{"name": "name"}]},
                {"name": "Org", "properties": [{"name": "name"}]},
            ],
            "relationships": [],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "users"},
                "Org": {"style": "COLLECTION", "collectionName": "orgs"},
            },
            "relationships": {},
        },
    }
    out = export_mapping(analysis, target="sparql")
    by_domain = {d["domain"].rsplit("/", 1)[-1].rsplit("#", 1)[-1]: d for d in out["datatypeProperties"]}
    # same predicate IRI reused, but one entry per owning class (distinct domains)
    assert set(by_domain) == {"User", "Org"}
    assert by_domain["User"]["iri"] == by_domain["Org"]["iri"]
    assert by_domain["User"]["physical"]["collectionName"] == "users"
    assert by_domain["Org"]["physical"]["collectionName"] == "orgs"


def test_sparql_export_validates_against_response_contract():
    out = export_mapping(ANALYSIS, target="sparql")
    schema_path = (
        Path(__file__).resolve().parents[1] / "schema_analyzer" / "tool_contract" / "v1" / "response.schema.json"
    )
    full = json.loads(schema_path.read_text("utf-8"))
    # Validate the export payload against the SparqlExport $def directly.
    sparql_def = full["$defs"]["SparqlExport"]
    sparql_def["$defs"] = full["$defs"]
    errors = list(Draft202012Validator(sparql_def).iter_errors(out))
    assert errors == [], errors


def test_export_via_tool_contract_sparql():
    request = {
        "contractVersion": "1",
        "operation": "export",
        "input": {"analysis": ANALYSIS},
        "outputOptions": {"exportTarget": "sparql"},
    }
    resp = run_tool(request)
    assert resp["ok"] is True, resp
    assert resp["result"]["export"]["target"] == "sparql"


def test_export_via_tool_contract_cypher_still_works():
    request = {
        "contractVersion": "1",
        "operation": "export",
        "input": {"analysis": ANALYSIS},
        "outputOptions": {"exportTarget": "cypher"},
    }
    resp = run_tool(request)
    assert resp["ok"] is True, resp
    assert "conceptualSchema" in resp["result"]["export"]


# --------------------------------------------------------------------------
# Cypher resolution adapter
# --------------------------------------------------------------------------


def test_cypher_resolution_index_builds_aql_fragments():
    idx = build_cypher_resolution_index(ANALYSIS)
    assert idx["target"] == "cypher"

    user = idx["entities"]["User"]
    assert "FOR n IN @@collection" in user["aql"]["query"]
    assert user["aql"]["bindVars"]["@collection"] == "users"

    post = idx["entities"]["Post"]
    assert "FILTER n[@typeField] == @typeValue" in post["aql"]["query"]
    assert post["aql"]["bindVars"]["typeValue"] == "Post"

    wrote = idx["relationships"]["WROTE"]
    assert wrote["aql"]["bindVars"]["@edgeCollection"] == "wrote"
    assert wrote["aql"]["edgeVariable"] == "e"

    liked = idx["relationships"]["LIKED"]
    assert "FILTER e[@typeField] == @typeValue" in liked["aql"]["query"]


def test_cypher_resolution_index_reports_incomplete_mapping():
    broken = {
        "conceptualSchema": {"entities": [{"name": "X"}], "relationships": [], "properties": []},
        # LABEL style missing typeField/typeValue -> aql helper raises -> error block
        "physicalMapping": {"entities": {"X": {"style": "LABEL", "collectionName": "c"}}, "relationships": {}},
        "metadata": {"confidence": 0.5},
    }
    idx = build_cypher_resolution_index(broken)
    assert "aql" not in idx["entities"]["X"]
    assert idx["entities"]["X"]["error"]["code"] == "INVALID_MAPPING"
