"""Tests for LLM-egress snapshot redaction (PRD §4.3)."""

from __future__ import annotations

import json

from schema_analyzer.analyzer import AgenticSchemaAnalyzer
from schema_analyzer.redaction import RedactionOptions, redact_snapshot_for_egress

SNAPSHOT = {
    "collections": [
        {
            "name": "users",
            "type": "document",
            "observed_fields": {"fields": ["email", "status"]},
            "sample_documents": [{"email": "real@person.com", "status": "active"}],
            "sample_field_value_counts": {
                "status": [{"value": "active", "count": 10}, {"value": "banned", "count": 2}]
            },
        }
    ],
    "graphs": [],
}


def test_no_options_returns_same_object():
    assert redact_snapshot_for_egress(SNAPSHOT, None) is SNAPSHOT
    assert redact_snapshot_for_egress(SNAPSHOT, RedactionOptions()) is SNAPSHOT


def test_strip_samples_removes_sample_documents():
    out = redact_snapshot_for_egress(SNAPSHOT, RedactionOptions(strip_samples=True))
    coll = out["collections"][0]
    assert "sample_documents" not in coll
    # original is untouched (deep copy)
    assert "sample_documents" in SNAPSHOT["collections"][0]
    # field names + value counts preserved
    assert coll["observed_fields"]["fields"] == ["email", "status"]
    assert coll["sample_field_value_counts"]["status"][0]["value"] == "active"


def test_mask_field_values_hides_values_keeps_shape():
    out = redact_snapshot_for_egress(SNAPSHOT, RedactionOptions(mask_field_values=True))
    counts = out["collections"][0]["sample_field_value_counts"]["status"]
    assert [c["value"] for c in counts] == ["<redacted>:0", "<redacted>:1"]
    assert [c["count"] for c in counts] == [10, 2]
    # sample_documents still present (not stripped) but original values untouched
    assert SNAPSHOT["collections"][0]["sample_field_value_counts"]["status"][0]["value"] == "active"


def test_mask_field_values_masks_by_type_keys_and_edge_endpoints():
    snap = {
        "collections": [
            {
                "name": "nodes",
                "type": "document",
                "observed_fields": {"by_type": {"SECRETPERSON": ["ssn"], "SECRETORG": []}},
                "sample_field_value_counts": {
                    "type": [{"value": "SECRETPERSON", "count": 1}, {"value": "SECRETORG", "count": 1}]
                },
            },
            {
                "name": "edges",
                "type": "edge",
                "observed_fields": {"by_type": {"SECRETREL": []}},
                "edge_endpoints": {
                    "from_collections": ["nodes"],
                    "to_collections": ["nodes"],
                    "entity_types_by_relation": {
                        "SECRETREL": {"from_entity_types": ["SECRETPERSON"], "to_entity_types": ["SECRETORG"]}
                    },
                },
                "sample_field_value_counts": {"relation": [{"value": "SECRETREL", "count": 1}]},
            },
        ]
    }
    out = redact_snapshot_for_egress(snap, RedactionOptions(mask_field_values=True))
    blob = json.dumps(out)
    # No sensitive discriminator value leaks anywhere in the egress payload.
    for secret in ("SECRETPERSON", "SECRETORG", "SECRETREL"):
        assert secret not in blob, f"{secret} leaked: {blob}"
    # Field names + collection names + structure are preserved.
    assert "ssn" in blob
    assert out["collections"][1]["edge_endpoints"]["from_collections"] == ["nodes"]
    # Same value masks to the same token everywhere (stable map): the by_type
    # keys must equal the masked values seen in sample_field_value_counts.
    nodes = out["collections"][0]
    assert set(nodes["observed_fields"]["by_type"].keys()) == {
        c["value"] for c in nodes["sample_field_value_counts"]["type"]
    }


def test_both_modes_compose():
    out = redact_snapshot_for_egress(SNAPSHOT, RedactionOptions(strip_samples=True, mask_field_values=True))
    coll = out["collections"][0]
    assert "sample_documents" not in coll
    assert coll["sample_field_value_counts"]["status"][0]["value"].startswith("<redacted>")


def test_options_from_dict():
    opts = RedactionOptions.from_dict({"stripSamples": True, "maskFieldValues": False})
    assert opts.strip_samples is True
    assert opts.mask_field_values is False
    assert opts.active is True
    assert RedactionOptions.from_dict(None).active is False


# --------------------------------------------------------------------------
# Analyzer wiring: redaction must apply to the LLM prompt only.
# --------------------------------------------------------------------------


class _CapturingProvider:
    def __init__(self, text: str) -> None:
        self._text = text
        self.last_prompt = ""

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int):
        self.last_prompt = prompt

        class R:
            def __init__(self, t: str) -> None:
                self.text = t

        return R(self._text)


class _FakeDB:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    # The analyzer accepts a pre-built snapshot via the private _snapshot arg,
    # so these are only needed to satisfy the snapshot path; unused here.
    def collections(self):
        return {}

    def graphs(self):
        return []


_VALID = json.dumps(
    {
        "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
        "physicalMapping": {"entities": {}, "relationships": {}},
        "metadata": {
            "confidence": 0.8,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }
)


def test_analyzer_redacts_prompt_when_enabled(monkeypatch):
    provider = _CapturingProvider(_VALID)
    import schema_analyzer.analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "create_provider", lambda name, *, api_key: provider)

    analyzer = AgenticSchemaAnalyzer(
        llm_provider="openai",
        api_key="k",
        model="m",
        redaction=RedactionOptions(strip_samples=True, mask_field_values=True),
    )
    analyzer.analyze_physical_schema(_FakeDB(SNAPSHOT), use_cache=False, _snapshot=dict(SNAPSHOT))

    assert "real@person.com" not in provider.last_prompt
    assert "active" not in provider.last_prompt  # masked categorical value
    assert "<redacted>" in provider.last_prompt
    # field names still present so the model can reason about structure
    assert "status" in provider.last_prompt


def test_analyzer_prompt_unredacted_by_default(monkeypatch):
    provider = _CapturingProvider(_VALID)
    import schema_analyzer.analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "create_provider", lambda name, *, api_key: provider)

    analyzer = AgenticSchemaAnalyzer(llm_provider="openai", api_key="k", model="m")
    analyzer.analyze_physical_schema(_FakeDB(SNAPSHOT), use_cache=False, _snapshot=dict(SNAPSHOT))

    assert "real@person.com" in provider.last_prompt
