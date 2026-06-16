"""Tests for element-level source provenance (PRD §3.13.2)."""

from __future__ import annotations

from schema_analyzer.provenance import annotate_provenance


def _data(metadata=None):
    return {
        "conceptualSchema": {
            "entities": [{"name": "User"}, {"name": "Audit"}],
            "relationships": [{"type": "WROTE", "fromEntity": "User", "toEntity": "User"}],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "users"},
                "Audit": {"style": "COLLECTION", "collectionName": "audit_log"},
            },
            "relationships": {"WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"}},
        },
        "metadata": metadata or {},
    }


def test_provenance_tags_baseline_when_used_baseline():
    data = _data()
    annotate_provenance(data, used_baseline=True)
    assert data["physicalMapping"]["entities"]["User"]["source"] == "baseline"
    assert data["conceptualSchema"]["entities"][0]["source"] == "baseline"
    assert data["conceptualSchema"]["relationships"][0]["source"] == "baseline"


def test_provenance_tags_llm_when_llm_run():
    data = _data()
    annotate_provenance(data, used_baseline=False)
    assert data["physicalMapping"]["entities"]["User"]["source"] == "llm"
    assert data["physicalMapping"]["relationships"]["WROTE"]["source"] == "llm"


def test_provenance_backfilled_collection_is_baseline_even_on_llm_run():
    data = _data(metadata={"reconciliation": {"backfilled_collections": ["audit_log"]}})
    annotate_provenance(data, used_baseline=False)
    # Audit's collection was backfilled -> baseline; User stays llm.
    assert data["physicalMapping"]["entities"]["Audit"]["source"] == "baseline"
    assert data["physicalMapping"]["entities"]["User"]["source"] == "llm"
    # Conceptual entity inherits its mapping's source.
    audit = next(e for e in data["conceptualSchema"]["entities"] if e["name"] == "Audit")
    assert audit["source"] == "baseline"


def test_provenance_preserves_human_tag():
    data = _data()
    data["physicalMapping"]["entities"]["User"]["source"] = "human"
    annotate_provenance(data, used_baseline=False)
    assert data["physicalMapping"]["entities"]["User"]["source"] == "human"
