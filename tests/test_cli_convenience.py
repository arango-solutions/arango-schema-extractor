"""Tests for the convenience CLI subcommands (snapshot/analyze/docs/owl)."""

from __future__ import annotations

import json

import pytest

import schema_analyzer.cli as cli

_ANALYSIS = {
    "conceptualSchema": {"entities": [{"name": "User"}], "relationships": [], "properties": []},
    "physicalMapping": {"entities": {"User": {"style": "COLLECTION", "collectionName": "users"}}, "relationships": {}},
    "metadata": {
        "confidence": 0.9,
        "timestamp": "2026-01-01T00:00:00Z",
        "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
        "detectedPatterns": [],
    },
}


def _fake_run_tool(captured):
    def _run(req):
        captured.append(req)
        op = req["operation"]
        if op == "snapshot":
            return {"ok": True, "result": {"snapshot": {"collections": [{"name": "users"}]}}}
        if op == "analyze":
            return {"ok": True, "result": {"analysis": _ANALYSIS}}
        if op == "docs":
            return {"ok": True, "result": {"markdown": "# Schema\n"}}
        if op == "owl":
            fmt = req.get("outputOptions", {}).get("owlFormat", "turtle")
            if fmt == "jsonld":
                return {"ok": True, "result": {"jsonld": {"@graph": []}}}
            return {"ok": True, "result": {"turtle": ": a owl:Ontology ."}}
        return {"ok": False, "error": {"code": "X", "message": "no"}}

    return _run


def test_snapshot_command(monkeypatch, capsys):
    captured: list = []
    monkeypatch.setattr(cli, "run_tool", _fake_run_tool(captured))
    rc = cli.main(["snapshot", "--url", "http://x:8529", "--database", "mydb", "--password", "p"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["collections"][0]["name"] == "users"
    assert captured[0]["connection"]["database"] == "mydb"
    assert captured[0]["connection"]["password"] == "p"


def test_analyze_command_with_llm(monkeypatch, capsys):
    captured: list = []
    monkeypatch.setattr(cli, "run_tool", _fake_run_tool(captured))
    rc = cli.main(
        [
            "analyze",
            "--database",
            "mydb",
            "--provider",
            "openai",
            "--model",
            "gpt-4o-mini",
            "--api-key-env-var",
            "OPENAI_API_KEY",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["conceptualSchema"]["entities"][0]["name"] == "User"
    assert captured[0]["llm"] == {"provider": "openai", "model": "gpt-4o-mini", "apiKeyEnvVar": "OPENAI_API_KEY"}


def test_docs_command_analyzes_then_docs(monkeypatch, capsys):
    captured: list = []
    monkeypatch.setattr(cli, "run_tool", _fake_run_tool(captured))
    rc = cli.main(["docs", "--database", "mydb"])
    assert rc == 0
    assert "# Schema" in capsys.readouterr().out
    assert [r["operation"] for r in captured] == ["analyze", "docs"]
    assert captured[1]["input"]["analysis"]["conceptualSchema"]["entities"][0]["name"] == "User"


def test_owl_command_jsonld(monkeypatch, capsys):
    captured: list = []
    monkeypatch.setattr(cli, "run_tool", _fake_run_tool(captured))
    rc = cli.main(["owl", "--database", "mydb", "--format", "jsonld"])
    assert rc == 0
    assert "@graph" in capsys.readouterr().out
    assert captured[1]["outputOptions"]["owlFormat"] == "jsonld"


def test_missing_database_errors(monkeypatch):
    monkeypatch.delenv("ARANGO_DB", raising=False)
    monkeypatch.setattr(cli, "run_tool", _fake_run_tool([]))
    with pytest.raises(SystemExit):
        cli.main(["analyze", "--url", "http://x:8529"])


def test_password_env_var_indirection(monkeypatch, capsys):
    captured: list = []
    monkeypatch.setattr(cli, "run_tool", _fake_run_tool(captured))
    cli.main(["snapshot", "--database", "mydb", "--password-env-var", "ARANGO_PASS"])
    conn = captured[0]["connection"]
    assert conn["passwordEnvVar"] == "ARANGO_PASS"
    assert "password" not in conn


def test_failed_op_returns_error_exit_code(monkeypatch, capsys):
    def _run(req):
        return {"ok": False, "error": {"code": "PROVIDER_MISSING", "message": "nope"}}

    monkeypatch.setattr(cli, "run_tool", _run)
    rc = cli.main(["analyze", "--database", "mydb"])
    assert rc == cli.TOOL_ERROR_EXIT_CODE
