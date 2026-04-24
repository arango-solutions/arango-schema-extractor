# Tool Contract v1 (Schema Analyzer)

This document defines the **v1 JSON contract** for calling this project as a non-interactive tool from other agentic workflows.

## Overview

- **Request schema**: `request.schema.json`
- **Response schema**: `response.schema.json`
- **Examples**: `examples/`

The tool is designed to be callable via:

- **Library**: `schema_analyzer.tool.run_tool(request_dict) -> response_dict`
  (also re-exported as `schema_analyzer.run_tool`)
- **CLI**: `arangodb-schema-analyzer` (stdin JSON → stdout JSON)
- **MCP**: `arangodb-schema-analyzer-mcp` — stdio MCP server that exposes the
  same operations to MCP-capable clients (requires the `[mcp]` install extra)

## Key principles

- **Stable**: `contractVersion` is required and must be `"1"`.
- **Non-interactive**: no prompts; all inputs are in the request JSON.
- **Structured errors**: failures return `ok=false` and an `error` object.
- **Secrets**: prefer `*EnvVar` fields instead of embedding secrets in JSON.

## Operations

- `snapshot`: connect to ArangoDB and return a deterministic physical schema snapshot
- `analyze`: snapshot + run the agentic analyzer, returning analysis JSON
- `export`: export analysis to a stable JSON contract for transpilers
  (currently only the `cypher` target is supported; additional targets
  may be added under the same operation without bumping `contractVersion`)
- `docs`: produce Markdown documentation from an analysis result
- `owl`: export conceptual schema + physical mapping as OWL Turtle

## Request / Response

See the JSON Schema files:

- `request.schema.json`
- `response.schema.json`

## Examples

- `examples/request.analyze.json`
- `examples/response.analyze.json`

