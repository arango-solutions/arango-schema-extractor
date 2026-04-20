import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _load(p: Path):
    return json.loads(p.read_text("utf-8"))


def test_v1_contract_examples_validate():
    base = Path(__file__).resolve().parents[1] / "docs" / "tool-contract" / "v1"
    request_schema = _load(base / "request.schema.json")
    response_schema = _load(base / "response.schema.json")

    req = _load(base / "examples" / "request.analyze.json")
    resp = _load(base / "examples" / "response.analyze.json")

    req_errors = [e.message for e in Draft202012Validator(request_schema).iter_errors(req)]
    resp_errors = [e.message for e in Draft202012Validator(response_schema).iter_errors(resp)]

    assert req_errors == []
    assert resp_errors == []
