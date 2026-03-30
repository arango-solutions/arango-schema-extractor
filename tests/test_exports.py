from __future__ import annotations

import pytest

from schema_analyzer.exports import export_mapping

_SAMPLE = {
    "conceptualSchema": {"entities": [{"name": "Foo"}], "relationships": [], "properties": []},
    "physicalMapping": {"entities": {"Foo": {"style": "COLLECTION", "collectionName": "foos"}}, "relationships": {}},
    "metadata": {"confidence": 0.9},
}


def test_export_mapping_cypher():
    result = export_mapping(_SAMPLE, target="cypher")
    assert result["conceptualSchema"] == _SAMPLE["conceptualSchema"]
    assert result["physicalMapping"] == _SAMPLE["physicalMapping"]
    assert result["metadata"] == _SAMPLE["metadata"]


def test_export_mapping_unsupported_target():
    with pytest.raises(ValueError, match="Unsupported export target"):
        export_mapping(_SAMPLE, target="sparql")
