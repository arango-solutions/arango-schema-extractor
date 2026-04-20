import json


def test_cli_returns_2_on_error_and_writes_json(monkeypatch, capsys):
    from schema_analyzer import cli

    # Force stdin to be an invalid request (missing operation)
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: json.dumps({"contractVersion": "1"})})())
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == 2
    data = json.loads(out)
    assert data["ok"] is False
