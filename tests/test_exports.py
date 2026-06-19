from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from schema_analyzer.baseline import infer_baseline_from_snapshot
from schema_analyzer.exports import export_mapping
from schema_analyzer.validation import ANALYSIS_OUTPUT_SCHEMA

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
        export_mapping(_SAMPLE, target="gremlin")


# ── Issue #6 — key naming regression tests ──────────────────────────


@pytest.fixture()
def _pg_snapshot():
    """A PG-style snapshot with one entity collection (with indexed props)
    and one edge collection, exercising both ``field`` and
    ``edgeCollectionName`` emission."""
    return {
        "version": 1,
        "collections": [
            {
                "name": "persons",
                "type": "document",
                "inferred_entity_type": "Person",
                "observed_fields": {"fields": ["name", "age"]},
                "indexes": [{"type": "persistent", "fields": ["name"], "unique": True, "sparse": False}],
            },
            {
                "name": "acted_in",
                "type": "edge",
                "inferred_relationship_type": "ACTED_IN",
                "edge_endpoints": {
                    "from_collections": ["persons"],
                    "to_collections": ["movies"],
                },
                "observed_fields": {"fields": ["roles"]},
            },
            {
                "name": "movies",
                "type": "document",
                "inferred_entity_type": "Movie",
                "observed_fields": {"fields": ["title"]},
            },
        ],
        "graphs": [],
    }


def test_baseline_property_mapping_uses_field_not_physical_field_name(_pg_snapshot) -> None:
    out = infer_baseline_from_snapshot(_pg_snapshot)
    person_props = out["physicalMapping"]["entities"]["Person"]["properties"]
    assert person_props["name"]["field"] == "name"
    assert "physicalFieldName" not in person_props["name"]


def test_baseline_relationship_uses_edge_collection_name(_pg_snapshot) -> None:
    out = infer_baseline_from_snapshot(_pg_snapshot)
    acted_in = out["physicalMapping"]["relationships"]["ACTED_IN"]
    assert acted_in["edgeCollectionName"] == "acted_in"
    assert "collectionName" not in acted_in


def test_baseline_generic_edge_uses_edge_collection_name() -> None:
    snapshot = {
        "version": 1,
        "collections": [
            {
                "name": "edges",
                "type": "edge",
                "candidate_type_fields": ["relation"],
                "sample_field_value_counts": {"relation": [{"value": "KNOWS", "count": 2}]},
            }
        ],
        "graphs": [],
    }
    out = infer_baseline_from_snapshot(snapshot)
    knows = out["physicalMapping"]["relationships"]["KNOWS"]
    assert knows["style"] == "GENERIC_WITH_TYPE"
    assert knows["edgeCollectionName"] == "edges"
    assert "collectionName" not in knows


def test_schema_rejects_relationship_with_collection_name() -> None:
    """A relationship that carries the pre-#6 ``collectionName`` key must now
    be rejected by the analyzer's JSON schema (AC3)."""
    bad = {
        "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
        "physicalMapping": {
            "entities": {},
            "relationships": {
                "REL": {
                    "style": "DEDICATED_COLLECTION",
                    "edgeCollectionName": "rel",
                    "collectionName": "rel",  # forbidden post-#6
                }
            },
        },
        "metadata": {
            "confidence": 0.5,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 0, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }
    validator = Draft202012Validator(ANALYSIS_OUTPUT_SCHEMA)
    errors = list(validator.iter_errors(bad))
    assert errors, "schema should reject relationship mapping with collectionName"


def test_schema_rejects_property_with_physical_field_name() -> None:
    """A property that carries the pre-#6 ``physicalFieldName`` key is no
    longer sufficient — the required key is now ``field`` (AC1)."""
    bad = {
        "conceptualSchema": {"entities": [{"name": "P"}], "relationships": [], "properties": []},
        "physicalMapping": {
            "entities": {
                "P": {
                    "style": "COLLECTION",
                    "collectionName": "p",
                    "properties": {"name": {"physicalFieldName": "name"}},
                }
            },
            "relationships": {},
        },
        "metadata": {
            "confidence": 0.5,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }
    validator = Draft202012Validator(ANALYSIS_OUTPUT_SCHEMA)
    errors = list(validator.iter_errors(bad))
    assert errors, "schema should require 'field' on property entries"


def test_external_schema_file_matches_inline_schema() -> None:
    """The tool-contract JSON schema file and the inline validator schema must
    stay in lock-step — both the property-level ``field`` rename and the
    relationship-level ``collectionName`` ban depend on this."""
    schema_path = (
        Path(__file__).resolve().parents[1] / "schema_analyzer" / "tool_contract" / "v1" / "response.schema.json"
    )
    ext = json.loads(schema_path.read_text())
    # Spot-check: the property items now require "field"
    pm = ext["$defs"]["AnalysisOutput"]["properties"]["physicalMapping"]
    ent_item = pm["properties"]["entities"]["additionalProperties"]
    rel_item = pm["properties"]["relationships"]["additionalProperties"]
    assert ent_item["properties"]["properties"]["additionalProperties"]["required"] == ["field"]
    assert rel_item["properties"]["properties"]["additionalProperties"]["required"] == ["field"]
    assert rel_item["not"] == {"required": ["collectionName"]}
