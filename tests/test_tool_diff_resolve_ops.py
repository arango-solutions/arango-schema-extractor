"""Tool-contract coverage for the diff and resolve operations."""

from __future__ import annotations

from schema_analyzer.tool import run_tool
from schema_analyzer.tool_contract_v1 import validate_response_v1


def _analysis(entities, relationships, pm_entities, pm_rels):
    return {
        "conceptualSchema": {"entities": entities, "relationships": relationships, "properties": []},
        "physicalMapping": {"entities": pm_entities, "relationships": pm_rels},
        "metadata": {
            "confidence": 0.9,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }


PREV = _analysis(
    [{"name": "User"}, {"name": "Legacy"}],
    [],
    {"User": {"style": "COLLECTION", "collectionName": "users"}},
    {},
)
CURR = _analysis(
    [{"name": "User"}, {"name": "Post"}],
    [{"type": "WROTE", "fromEntity": "User", "toEntity": "Post"}],
    {
        "User": {"style": "COLLECTION", "collectionName": "users"},
        "Post": {"style": "COLLECTION", "collectionName": "posts"},
    },
    {"WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"}},
)


def test_diff_operation():
    resp = run_tool(
        {
            "contractVersion": "1",
            "operation": "diff",
            "input": {"previousAnalysis": PREV, "analysis": CURR},
        }
    )
    assert resp["ok"] is True, resp
    diff = resp["result"]["diff"]
    assert diff["entities"]["added"] == ["Post"]
    assert diff["entities"]["removed"] == ["Legacy"]
    assert diff["relationships"]["added"] == ["WROTE"]
    assert validate_response_v1(resp) == []


def test_diff_requires_previous_analysis():
    resp = run_tool({"contractVersion": "1", "operation": "diff", "input": {"analysis": CURR}})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_REQUEST"


def test_resolve_operation():
    resp = run_tool({"contractVersion": "1", "operation": "resolve", "input": {"analysis": CURR}})
    assert resp["ok"] is True, resp
    resolution = resp["result"]["resolution"]
    assert resolution["target"] == "cypher"
    assert "FOR n IN @@collection" in resolution["entities"]["User"]["aql"]["query"]
    assert resolution["relationships"]["WROTE"]["aql"]["bindVars"]["@edgeCollection"] == "wrote"
    assert validate_response_v1(resp) == []


def test_resolve_requires_analysis():
    resp = run_tool({"contractVersion": "1", "operation": "resolve", "input": {}})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_REQUEST"
