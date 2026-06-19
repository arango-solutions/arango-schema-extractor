from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator

CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class ContractSchemas:
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]


def _load_schema_from_package(rel_path: str) -> dict[str, Any]:
    """
    Load a JSON schema bundled in the installed package.
    """
    try:
        from importlib.resources import files

        p = files("schema_analyzer.tool_contract.v1").joinpath(rel_path)
        return cast("dict[str, Any]", json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        raise RuntimeError(f"Failed to load bundled tool contract schema: {rel_path}") from e


def load_contract_schemas_v1() -> ContractSchemas:
    return ContractSchemas(
        request_schema=_load_schema_from_package("request.schema.json"),
        response_schema=_load_schema_from_package("response.schema.json"),
    )


_schemas = load_contract_schemas_v1()
_request_validator = Draft202012Validator(_schemas.request_schema)
_response_validator = Draft202012Validator(_schemas.response_schema)


def validate_request_v1(request: dict[str, Any]) -> list[str]:
    return [err.message for err in sorted(_request_validator.iter_errors(request), key=str)]


def validate_response_v1(response: dict[str, Any]) -> list[str]:
    return [err.message for err in sorted(_response_validator.iter_errors(response), key=str)]
