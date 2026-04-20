"""MCP (Model Context Protocol) server wrapping the v1 JSON tool contract.

Requires optional dependency: ``pip install 'arangodb-schema-analyzer[mcp]'``.

Run (stdio, default for Cursor):

    arangodb-schema-analyzer-mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _require_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover
        print(
            "The MCP server requires the 'mcp' package. Install with:\n  pip install 'arangodb-schema-analyzer[mcp]'",
            file=sys.stderr,
        )
        raise SystemExit(1) from e
    return FastMCP


def build_app():
    FastMCP = _require_fastmcp()

    from .tool import run_tool

    mcp = FastMCP(
        "arangodb-schema-analyzer",
        instructions=(
            "ArangoDB schema analyzer: call run with a v1 tool-contract request dict "
            "(contractVersion, operation, connection for snapshot/analyze, etc.). "
            "See docs/tool-contract/v1/request.schema.json."
        ),
    )

    @mcp.tool()
    def arangodb_schema_analyzer_run(request: dict[str, Any]) -> dict[str, Any]:
        """Execute one schema-analyzer operation. The ``request`` object must match
        ``docs/tool-contract/v1/request.schema.json`` (e.g. operation analyze | snapshot | export | docs | owl).
        """
        return run_tool(request)

    @mcp.tool()
    def arangodb_schema_analyzer_run_json(request_json: str) -> dict[str, Any]:
        """Same as arangodb_schema_analyzer_run but accepts a JSON string (convenient for some MCP clients)."""
        try:
            req = json.loads(request_json)
        except json.JSONDecodeError as e:
            return {
                "contractVersion": "1",
                "operation": None,
                "ok": False,
                "error": {"code": "INVALID_REQUEST", "message": f"Invalid JSON: {e}"},
            }
        if not isinstance(req, dict):
            return {
                "contractVersion": "1",
                "operation": None,
                "ok": False,
                "error": {"code": "INVALID_REQUEST", "message": "request_json must be a JSON object"},
            }
        return run_tool(req)

    return mcp


def main() -> None:
    build_app().run(transport="stdio")


if __name__ == "__main__":
    main()
