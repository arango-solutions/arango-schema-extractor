"""
Tests for issue #5: post-LLM reconciliation of missing snapshot collections.

Two layers:

* Unit tests for :mod:`schema_analyzer.reconcile` against synthetic data /
  snapshot inputs.
* End-to-end tests through :class:`AgenticSchemaAnalyzer` with a stubbed
  LLM provider that returns a deliberately incomplete physical mapping
  (acceptance criterion #6).
"""

from __future__ import annotations

import json

from schema_analyzer.analyzer import AgenticSchemaAnalyzer
from schema_analyzer.reconcile import (
    collections_referenced_by_mapping,
    reconcile_physical_mapping,
)

# ── collections_referenced_by_mapping ────────────────────────────────────


def test_collections_referenced_by_mapping_covers_entities_and_relationships() -> None:
    pm = {
        "entities": {
            "User": {"style": "COLLECTION", "collectionName": "users"},
            "Admin": {"style": "LABEL", "collectionName": "users", "typeField": "kind", "typeValue": "admin"},
        },
        "relationships": {
            "FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"},
            "LIKES": {"style": "DEDICATED_COLLECTION", "collectionName": "likes"},
        },
    }
    assert collections_referenced_by_mapping(pm) == {"users", "follows", "likes"}


def test_collections_referenced_by_mapping_handles_empty_and_garbage() -> None:
    assert collections_referenced_by_mapping({}) == set()
    assert collections_referenced_by_mapping({"entities": None, "relationships": 42}) == set()
    assert (
        collections_referenced_by_mapping(
            {"entities": {"X": "not a dict"}, "relationships": {"Y": {"noCollectionKey": True}}}
        )
        == set()
    )


# ── reconcile_physical_mapping (unit) ─────────────────────────────────────


def _minimal_snapshot(*collections: dict) -> dict:
    return {"collections": list(collections), "graphs": []}


def _doc_col(name: str, *, inferred_entity_type: str | None = None) -> dict:
    entry: dict = {
        "name": name,
        "type": "document",
        "count": 10,
        "candidate_type_fields": [],
        "sample_field_value_counts": {},
        "observed_fields": {"fields": []},
    }
    if inferred_entity_type is not None:
        entry["inferred_entity_type"] = inferred_entity_type
    return entry


def _edge_col(name: str, *, inferred_relationship_type: str | None = None) -> dict:
    entry: dict = {
        "name": name,
        "type": "edge",
        "count": 5,
        "candidate_type_fields": [],
        "sample_field_value_counts": {},
        "observed_fields": {"fields": []},
        "edge_endpoints": {"from_collections": [], "to_collections": []},
    }
    if inferred_relationship_type is not None:
        entry["inferred_relationship_type"] = inferred_relationship_type
    return entry


def test_reconcile_returns_none_when_mapping_already_complete() -> None:
    snapshot = _minimal_snapshot(_doc_col("users"))
    data = {
        "conceptualSchema": {"entities": [{"name": "User"}], "relationships": []},
        "physicalMapping": {
            "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
            "relationships": {},
        },
    }
    assert reconcile_physical_mapping(data, snapshot) is None
    # data must not be mutated
    assert "reconciliation" not in data.get("metadata", {})


def test_reconcile_backfills_missing_entity_collections() -> None:
    snapshot = _minimal_snapshot(
        _doc_col("users"),
        _doc_col("audit_log"),
        _doc_col("session_cache"),
    )
    data = {
        "conceptualSchema": {
            "entities": [{"name": "User", "labels": ["User"], "properties": []}],
            "relationships": [],
        },
        "physicalMapping": {
            "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
            "relationships": {},
        },
    }

    summary = reconcile_physical_mapping(data, snapshot)

    assert summary is not None
    assert summary["strategy"] == "baseline_per_missing_collection"
    assert summary["snapshot_collections"] == 3
    assert summary["llm_covered_collections"] == 1
    assert summary["backfilled_collections"] == ["audit_log", "session_cache"]

    # Physical mapping now references all three collections.
    covered = collections_referenced_by_mapping(data["physicalMapping"])
    assert covered == {"users", "audit_log", "session_cache"}

    # Conceptual schema got backfilled entities too.
    entity_names = {e["name"] for e in data["conceptualSchema"]["entities"]}
    assert "User" in entity_names
    assert len(entity_names) >= 3  # at minimum one baseline entity per backfilled collection


def test_reconcile_backfills_missing_edge_collection() -> None:
    snapshot = _minimal_snapshot(
        _doc_col("users"),
        _doc_col("posts"),
        _edge_col("authored", inferred_relationship_type="AUTHORED"),
    )
    data = {
        "conceptualSchema": {
            "entities": [{"name": "User"}, {"name": "Post"}],
            "relationships": [],
        },
        "physicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "users"},
                "Post": {"style": "COLLECTION", "collectionName": "posts"},
            },
            "relationships": {},
        },
    }

    summary = reconcile_physical_mapping(data, snapshot)

    assert summary is not None
    assert summary["backfilled_collections"] == ["authored"]
    rels = data["physicalMapping"]["relationships"]
    assert "AUTHORED" in rels
    assert rels["AUTHORED"]["style"] == "DEDICATED_COLLECTION"
    assert rels["AUTHORED"]["edgeCollectionName"] == "authored"


def test_reconcile_is_idempotent_on_second_call() -> None:
    snapshot = _minimal_snapshot(_doc_col("a"), _doc_col("b"))
    data = {
        "conceptualSchema": {"entities": [], "relationships": []},
        "physicalMapping": {"entities": {}, "relationships": {}},
    }
    first = reconcile_physical_mapping(data, snapshot)
    assert first is not None
    assert set(first["backfilled_collections"]) == {"a", "b"}

    second = reconcile_physical_mapping(data, snapshot)
    assert second is None


# ── analyzer end-to-end (acceptance criterion #6) ────────────────────────


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int):
        class _R:
            def __init__(self, t: str) -> None:
                self.text = t

        return _R(self._text)


class _FakeCollection:
    def __init__(self, col_type: int) -> None:
        self._type = col_type

    def properties(self) -> dict:
        return {"type": self._type}

    def count(self) -> int:
        return 0

    def indexes(self) -> list:
        return []


class _FakeDBWithTenCollections:
    """Mimics a DB with 10 non-system document collections."""

    def __init__(self) -> None:
        self._cols = {f"col_{i:02d}": _FakeCollection(2) for i in range(10)}

    def collections(self) -> dict:
        return self._cols

    def graphs(self) -> list:
        return []


def _llm_payload_with_seven_of_ten() -> str:
    """LLM-style response emitting only 7 of 10 collections."""
    payload = {
        "conceptualSchema": {
            "entities": [{"name": f"Col{i:02d}", "labels": [f"Col{i:02d}"], "properties": []} for i in range(7)],
            "relationships": [],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {
                f"Col{i:02d}": {
                    "style": "COLLECTION",
                    "collectionName": f"col_{i:02d}",
                }
                for i in range(7)
            },
            "relationships": {},
        },
        "metadata": {
            "confidence": 0.85,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 10, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }
    return json.dumps(payload)


def test_analyzer_reconciles_when_llm_omits_collections(monkeypatch) -> None:
    import schema_analyzer.analyzer as analyzer_mod

    def _fake_create_provider(name, *, api_key):
        return _FakeProvider(_llm_payload_with_seven_of_ten())

    monkeypatch.setattr(analyzer_mod, "create_provider", _fake_create_provider)

    analyzer = AgenticSchemaAnalyzer(llm_provider="openai", api_key="k", model="m")
    res = analyzer.analyze_physical_schema(_FakeDBWithTenCollections(), sample_limit_per_collection=0, use_cache=False)

    pm = res.physical_mapping
    covered = collections_referenced_by_mapping(pm)
    assert covered == {f"col_{i:02d}" for i in range(10)}

    reconciliation = res.metadata.reconciliation
    assert reconciliation is not None
    assert reconciliation["snapshot_collections"] == 10
    assert reconciliation["llm_covered_collections"] == 7
    assert reconciliation["backfilled_collections"] == [
        "col_07",
        "col_08",
        "col_09",
    ]
    assert reconciliation["strategy"] == "baseline_per_missing_collection"

    # Warning must surface the backfill for tool-contract consumers.
    assert any("backfilled" in w for w in res.metadata.warnings)


def test_analyzer_omits_reconciliation_when_llm_coverage_is_complete(monkeypatch) -> None:
    import schema_analyzer.analyzer as analyzer_mod

    class _FakeDBTwo:
        def collections(self) -> dict:
            return {"users": _FakeCollection(2), "follows": _FakeCollection(3)}

        def graphs(self) -> list:
            return []

    complete_payload = {
        "conceptualSchema": {
            "entities": [{"name": "User", "labels": ["User"], "properties": []}],
            "relationships": [{"type": "FOLLOWS", "fromEntity": "User", "toEntity": "User", "properties": []}],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
            "relationships": {"FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"}},
        },
        "metadata": {
            "confidence": 0.9,
            "timestamp": "2026-01-01T00:00:00Z",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
            "detectedPatterns": [],
        },
    }

    def _fake_create_provider(name, *, api_key):
        return _FakeProvider(json.dumps(complete_payload))

    monkeypatch.setattr(analyzer_mod, "create_provider", _fake_create_provider)

    analyzer = AgenticSchemaAnalyzer(llm_provider="openai", api_key="k", model="m")
    res = analyzer.analyze_physical_schema(_FakeDBTwo(), sample_limit_per_collection=0, use_cache=False)

    assert res.metadata.reconciliation is None
    assert not any("backfilled" in w for w in res.metadata.warnings)


def test_baseline_only_path_does_not_attach_reconciliation(monkeypatch) -> None:
    """When the LLM workflow fails, the baseline-fallback path is already
    complete by construction and must not carry a reconciliation summary."""
    import schema_analyzer.analyzer as analyzer_mod

    def _fake_create_provider(name, *, api_key):
        return _FakeProvider("not valid json at all")

    monkeypatch.setattr(analyzer_mod, "create_provider", _fake_create_provider)

    analyzer = AgenticSchemaAnalyzer(llm_provider="openai", api_key="k", model="m")
    res = analyzer.analyze_physical_schema(_FakeDBWithTenCollections(), sample_limit_per_collection=0, use_cache=False)

    assert res.metadata.reconciliation is None
    assert res.metadata.used_baseline is True


def test_no_llm_provider_path_does_not_attach_reconciliation() -> None:
    analyzer = AgenticSchemaAnalyzer()  # no provider
    res = analyzer.analyze_physical_schema(_FakeDBWithTenCollections(), sample_limit_per_collection=0, use_cache=False)

    assert res.metadata.reconciliation is None
    assert res.metadata.used_baseline is True
