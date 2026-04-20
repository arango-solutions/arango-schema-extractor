import pytest


def test_build_mcp_app_registers_tools():
    pytest.importorskip("mcp")
    from schema_analyzer.mcp_server import build_app

    app = build_app()
    assert app.name == "arangodb-schema-analyzer"
