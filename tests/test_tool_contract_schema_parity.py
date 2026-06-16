"""Guard against drift between the two copies of the v1 tool-contract schemas.

The JSON Schemas live in two places on purpose:

* ``docs/tool-contract/v1/`` — the human-facing source referenced by the PRD
  and shipped in the sdist.
* ``schema_analyzer/tool_contract/v1/`` — the copy bundled into the wheel and
  loaded at runtime by ``tool_contract_v1._load_schema_from_package``.

Agent and RPC consumers rely on the documented schema matching the one the
library actually enforces. These tests fail loudly if the copies diverge so the
mismatch is caught in CI instead of in a downstream integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs" / "tool-contract" / "v1"
BUNDLED_DIR = ROOT / "schema_analyzer" / "tool_contract" / "v1"

SCHEMA_FILES = ["request.schema.json", "response.schema.json"]


@pytest.mark.parametrize("filename", SCHEMA_FILES)
def test_contract_schema_is_byte_identical(filename: str) -> None:
    docs_bytes = (DOCS_DIR / filename).read_bytes()
    bundled_bytes = (BUNDLED_DIR / filename).read_bytes()
    assert docs_bytes == bundled_bytes, (
        f"{filename} differs between docs/tool-contract/v1 and "
        f"schema_analyzer/tool_contract/v1. Update both copies together so the "
        f"documented contract matches the one the library enforces at runtime."
    )


@pytest.mark.parametrize("filename", SCHEMA_FILES)
def test_contract_schema_parses_as_json(filename: str) -> None:
    for directory in (DOCS_DIR, BUNDLED_DIR):
        json.loads((directory / filename).read_text("utf-8"))


def test_runtime_loader_matches_docs_copy() -> None:
    from schema_analyzer.tool_contract_v1 import load_contract_schemas_v1

    schemas = load_contract_schemas_v1()
    docs_request = json.loads((DOCS_DIR / "request.schema.json").read_text("utf-8"))
    docs_response = json.loads((DOCS_DIR / "response.schema.json").read_text("utf-8"))

    assert schemas.request_schema == docs_request
    assert schemas.response_schema == docs_response
