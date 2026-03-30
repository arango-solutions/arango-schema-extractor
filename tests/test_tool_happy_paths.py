"""Tests for run_tool happy paths that don't need a live DB (export, docs, owl)."""

from __future__ import annotations

from schema_analyzer.tool import run_tool

_ANALYSIS_INPUT = {
    "conceptualSchema": {
        "entities": [{"name": "User", "labels": ["User"], "properties": []}],
        "relationships": [
            {"type": "FOLLOWS", "fromEntity": "User", "toEntity": "User", "properties": []},
        ],
        "properties": [],
    },
    "physicalMapping": {
        "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
        "relationships": {
            "FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"},
        },
    },
    "metadata": {
        "confidence": 0.9,
        "timestamp": "2025-01-01T00:00:00Z",
        "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
        "detectedPatterns": [],
        "warnings": [],
        "assumptions": [],
    },
}


def test_run_tool_export_success():
    req = {
        "contractVersion": "1",
        "operation": "export",
        "input": {"analysis": _ANALYSIS_INPUT},
    }
    resp = run_tool(req)
    assert resp["ok"] is True
    assert "export" in resp["result"]
    assert resp["result"]["export"]["conceptualSchema"]["entities"][0]["name"] == "User"


def test_run_tool_docs_success():
    req = {
        "contractVersion": "1",
        "operation": "docs",
        "input": {"analysis": _ANALYSIS_INPUT},
    }
    resp = run_tool(req)
    assert resp["ok"] is True
    assert "markdown" in resp["result"]
    assert "User" in resp["result"]["markdown"]


def test_run_tool_owl_success():
    req = {
        "contractVersion": "1",
        "operation": "owl",
        "input": {"analysis": _ANALYSIS_INPUT},
    }
    resp = run_tool(req)
    assert resp["ok"] is True
    assert "turtle" in resp["result"]
    assert "owl:Class" in resp["result"]["turtle"]


def test_run_tool_with_request_id():
    req = {
        "contractVersion": "1",
        "operation": "docs",
        "requestId": "req-42",
        "input": {"analysis": _ANALYSIS_INPUT},
    }
    resp = run_tool(req)
    assert resp["ok"] is True
    assert resp["requestId"] == "req-42"


def test_run_tool_unexpected_error_returns_contract_error(monkeypatch):
    import schema_analyzer.tool as tool_mod

    def _boom(analysis, target="cypher"):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tool_mod, "export_mapping", _boom)

    req = {
        "contractVersion": "1",
        "operation": "export",
        "input": {"analysis": _ANALYSIS_INPUT},
    }
    resp = run_tool(req)
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INTERNAL_ERROR"
    assert "kaboom" in resp["error"]["message"]
