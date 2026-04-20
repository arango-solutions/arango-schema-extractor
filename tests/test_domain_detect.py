"""Tests for schema_analyzer.domain_detect — business-domain detection from snapshots."""
from __future__ import annotations

import pytest

from schema_analyzer.domain_detect import (
    DomainHint,
    _build_spec_keywords,
    _extract_signal_tokens,
    _score,
    detect_domain,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _make_snapshot(
    *,
    collection_names: list[str] | None = None,
    type_values: dict[str, list[str]] | None = None,
    field_names: dict[str, list[str]] | None = None,
    graph_names: list[str] | None = None,
) -> dict:
    """Minimal snapshot for domain detection testing."""
    collections = []
    for name in (collection_names or []):
        col: dict = {
            "name": name,
            "type": "document",
            "count": 100,
        }
        if type_values and name in type_values:
            col["candidate_type_fields"] = ["type"]
            col["sample_field_value_counts"] = {
                "type": [{"value": v, "count": 10} for v in type_values[name]]
            }
        if field_names and name in field_names:
            col["observed_fields"] = {"fields": field_names[name]}
        collections.append(col)

    return {
        "version": 1,
        "generated_at": None,
        "collections": collections,
        "graphs": [{"name": g} for g in (graph_names or [])],
        "graphs_detailed": [],
    }


# ── Signal extraction ─────────────────────────────────────────────────

class TestExtractSignalTokens:
    def test_extracts_collection_names(self):
        snap = _make_snapshot(collection_names=["movies", "actors"])
        tokens = _extract_signal_tokens(snap)
        assert "movies" in tokens
        assert "actors" in tokens

    def test_splits_underscore_names(self):
        snap = _make_snapshot(collection_names=["fraud_alerts"])
        tokens = _extract_signal_tokens(snap)
        assert "fraud_alerts" in tokens
        assert "fraud" in tokens
        assert "alerts" in tokens

    def test_extracts_type_values(self):
        snap = _make_snapshot(
            collection_names=["nodes"],
            type_values={"nodes": ["Movie", "Person"]},
        )
        tokens = _extract_signal_tokens(snap)
        assert "movie" in tokens
        assert "person" in tokens

    def test_extracts_field_names(self):
        snap = _make_snapshot(
            collection_names=["transactions"],
            field_names={"transactions": ["amount", "currency", "timestamp"]},
        )
        tokens = _extract_signal_tokens(snap)
        assert "amount" in tokens
        assert "currency" in tokens

    def test_extracts_graph_names(self):
        snap = _make_snapshot(graph_names=["fraud_detection"])
        tokens = _extract_signal_tokens(snap)
        assert "fraud_detection" in tokens
        assert "fraud" in tokens

    def test_empty_snapshot_returns_empty(self):
        tokens = _extract_signal_tokens({"collections": [], "graphs": []})
        assert tokens == set()


# ── Scoring ───────────────────────────────────────────────────────────

class TestScoring:
    def test_perfect_overlap(self):
        score, matched = _score({"a", "b", "c"}, {"a", "b", "c"})
        assert score == 1.0
        assert set(matched) == {"a", "b", "c"}

    def test_partial_overlap(self):
        score, matched = _score({"a", "b", "x"}, {"a", "b", "c", "d"})
        assert score == pytest.approx(0.5)
        assert set(matched) == {"a", "b"}

    def test_no_overlap(self):
        score, matched = _score({"x", "y"}, {"a", "b"})
        assert score == 0.0
        assert matched == []

    def test_empty_keywords(self):
        score, matched = _score({"a"}, set())
        assert score == 0.0


# ── Spec keyword building ────────────────────────────────────────────

class TestBuildSpecKeywords:
    def test_includes_entity_names(self):
        spec = {
            "domain": "test",
            "entities": [{"name": "Account"}, {"name": "Transaction"}],
            "relationships": [],
        }
        kw = _build_spec_keywords(spec)
        assert "account" in kw
        assert "transaction" in kw

    def test_includes_relationship_types(self):
        spec = {
            "domain": "test",
            "entities": [],
            "relationships": [{"type": "ACTED_IN"}, {"type": "DIRECTED"}],
        }
        kw = _build_spec_keywords(spec)
        assert "acted_in" in kw
        assert "acted" in kw
        assert "directed" in kw

    def test_includes_property_names(self):
        spec = {
            "domain": "test",
            "entities": [{"name": "X", "properties": ["amount", "currency"]}],
            "relationships": [],
        }
        kw = _build_spec_keywords(spec)
        assert "amount" in kw
        assert "currency" in kw


# ── Full detection (against builtin vocabularies) ─────────────────────

class TestDetectDomain:
    def test_detects_movies_domain(self):
        snap = _make_snapshot(
            collection_names=["movies", "actors"],
            type_values={"movies": ["Movie"], "actors": ["Actor"]},
            field_names={
                "movies": ["title", "released", "tagline"],
                "actors": ["name"],
            },
        )
        hint = detect_domain(snap)
        assert hint is not None
        assert hint.domain == "entertainment_movies"
        assert hint.confidence > 0.0
        assert "movie" in hint.matched_signals or "movies" in hint.matched_signals

    def test_detects_graphrag_domain(self):
        snap = _make_snapshot(
            collection_names=["chunks", "documents", "entities", "mentions"],
            graph_names=["graphrag"],
        )
        hint = detect_domain(snap)
        assert hint is not None
        assert hint.domain == "graphrag"

    def test_detects_ecommerce_domain(self):
        snap = _make_snapshot(
            collection_names=["customers", "orders", "products"],
            field_names={
                "orders": ["price", "quantity"],
                "products": ["sku", "inventory"],
            },
        )
        hint = detect_domain(snap)
        assert hint is not None
        assert hint.domain == "ecommerce"

    def test_detects_social_network_domain(self):
        snap = _make_snapshot(
            collection_names=["users", "posts", "comments"],
            field_names={"users": ["profile"], "posts": ["feed"]},
        )
        hint = detect_domain(snap)
        assert hint is not None
        assert hint.domain == "social_network"

    def test_returns_none_for_unrecognized_schema(self):
        snap = _make_snapshot(collection_names=["xyzzy", "plugh"])
        hint = detect_domain(snap)
        assert hint is None

    def test_empty_snapshot_returns_none(self):
        hint = detect_domain({"collections": [], "graphs": []})
        assert hint is None

    def test_domain_spec_match_has_spec_attached(self):
        snap = _make_snapshot(
            collection_names=["accounts", "transactions", "merchants", "alerts"],
            field_names={
                "accounts": ["accountId", "status"],
                "transactions": ["amount", "currency", "timestamp"],
            },
        )
        hint = detect_domain(snap)
        assert hint is not None
        assert hint.domain == "financial_fraud_detection"
        assert hint.spec is not None
        assert any(e["name"] == "Account" for e in hint.spec.get("entities", []))

    def test_healthcare_domain_from_spec(self):
        snap = _make_snapshot(
            collection_names=["patients", "encounters", "diagnoses", "medications"],
            field_names={
                "patients": ["patientId", "name", "dob"],
                "encounters": ["encounterId", "startTime"],
            },
        )
        hint = detect_domain(snap)
        assert hint is not None
        assert hint.domain == "healthcare"
        assert hint.spec is not None


# ── Prompt context formatting ─────────────────────────────────────────

class TestPromptContext:
    def test_prompt_context_includes_domain_name(self):
        hint = DomainHint(
            domain="healthcare",
            description="Clinical operations",
            confidence=0.8,
            matched_signals=["patient", "encounter"],
            spec={
                "entities": [{"name": "Patient"}, {"name": "Encounter"}],
                "relationships": [{"type": "HAS_ENCOUNTER"}],
            },
        )
        ctx = hint.prompt_context()
        assert "healthcare" in ctx
        assert "Clinical operations" in ctx
        assert "Patient" in ctx
        assert "HAS_ENCOUNTER" in ctx

    def test_prompt_context_without_spec(self):
        hint = DomainHint(
            domain="entertainment_movies",
            description="Movies domain",
            confidence=0.5,
        )
        ctx = hint.prompt_context()
        assert "entertainment_movies" in ctx
        assert "Typical entity types" not in ctx
