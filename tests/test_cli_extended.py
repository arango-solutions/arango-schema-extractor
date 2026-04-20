"""Extended CLI unit tests for success paths and argument parsing."""

from __future__ import annotations

import json


def test_cli_tool_pretty_output(monkeypatch, capsys, tmp_path):
    from schema_analyzer import cli

    analysis = {
        "conceptualSchema": {
            "entities": [{"name": "A", "labels": ["A"], "properties": []}],
            "relationships": [],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {"A": {"style": "COLLECTION", "collectionName": "a"}},
            "relationships": {},
        },
        "metadata": {
            "confidence": 0.9,
            "timestamp": "2025-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
            "detectedPatterns": [],
            "warnings": [],
            "assumptions": [],
        },
    }

    req = json.dumps(
        {
            "contractVersion": "1",
            "operation": "docs",
            "input": {"analysis": analysis},
        }
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: req})())

    rc = cli.main(["--pretty"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["ok"] is True


def test_cli_tool_output_to_file(monkeypatch, tmp_path):
    from schema_analyzer import cli

    req = json.dumps(
        {
            "contractVersion": "1",
            "operation": "docs",
            "input": {
                "analysis": {
                    "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
                    "physicalMapping": {"entities": {}, "relationships": {}},
                    "metadata": {
                        "confidence": 0.5,
                        "timestamp": "2025-01-01",
                        "analyzedCollectionCounts": {"documentCollections": 0, "edgeCollections": 0},
                        "detectedPatterns": [],
                    },
                }
            },
        }
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: req})())

    out_file = str(tmp_path / "out.json")
    rc = cli.main(["--out", out_file])
    assert rc == 0

    data = json.loads((tmp_path / "out.json").read_text())
    assert data["ok"] is True


def test_cli_request_file(monkeypatch, tmp_path):
    from schema_analyzer import cli

    req = {
        "contractVersion": "1",
        "operation": "docs",
        "input": {
            "analysis": {
                "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
                "physicalMapping": {"entities": {}, "relationships": {}},
                "metadata": {
                    "confidence": 0.5,
                    "timestamp": "2025-01-01",
                    "analyzedCollectionCounts": {"documentCollections": 0, "edgeCollections": 0},
                    "detectedPatterns": [],
                },
            }
        },
    }
    req_file = tmp_path / "req.json"
    req_file.write_text(json.dumps(req))

    rc = cli.main(["--request", str(req_file)])
    assert rc == 0
