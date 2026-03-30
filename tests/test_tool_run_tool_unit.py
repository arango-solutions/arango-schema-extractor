

def test_run_tool_rejects_invalid_request():
    from schema_analyzer.tool import run_tool

    resp = run_tool({"contractVersion": "1", "operation": "analyze"})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_REQUEST"


def test_run_tool_export_docs_owl_require_input_analysis():
    from schema_analyzer.tool import run_tool

    for op in ("export", "docs", "owl"):
        resp = run_tool({"contractVersion": "1", "operation": op, "input": {}})
        assert resp["ok"] is False


def test_run_tool_analyze_missing_password_env_var_returns_error(monkeypatch):
    from schema_analyzer.tool import run_tool

    monkeypatch.delenv("ARANGO_PASS", raising=False)
    req = {
        "contractVersion": "1",
        "operation": "analyze",
        "connection": {
            "url": "http://localhost:8529",
            "database": "db",
            "username": "root",
            "passwordEnvVar": "ARANGO_PASS",
        },
    }
    resp = run_tool(req)
    assert resp["ok"] is False
    assert resp["error"]["code"] in ("INVALID_ARGUMENT", "ERROR")

