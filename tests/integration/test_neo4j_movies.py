"""
Integration tests: verify the analyzer produces the correct ontology from
both PG-style and LPG-style Neo4j Movies databases.

Requires a running ArangoDB instance and ``RUN_INTEGRATION=1``.
"""

from __future__ import annotations

import contextlib

import pytest

from schema_analyzer import AgenticSchemaAnalyzer
from schema_analyzer.baseline import infer_baseline_from_snapshot
from schema_analyzer.snapshot import snapshot_physical_schema

from ..conftest import (
    connect_root,
    ensure_fresh_database,
    env,
    skip_if_integration_not_enabled,
    wait_for_arango,
)
from .datasets import seed_movies_hybrid, seed_movies_lpg, seed_movies_pg

pytestmark = pytest.mark.integration

# ── Expected ontology (shared by both physical styles) ──────────────────

EXPECTED_ENTITIES = {"Movie", "Person"}
EXPECTED_RELATIONSHIPS = {"ACTED_IN", "DIRECTED", "FOLLOWS", "PRODUCED", "REVIEWED", "WROTE"}

EXPECTED_MOVIE_PROPS = {"title", "released", "tagline"}
EXPECTED_PERSON_PROPS = {"name", "born"}

EXPECTED_ENDPOINTS = {
    "ACTED_IN": ("Person", "Movie"),
    "DIRECTED": ("Person", "Movie"),
    "FOLLOWS": ("Person", "Person"),
    "PRODUCED": ("Person", "Movie"),
    "REVIEWED": ("Person", "Movie"),
    "WROTE": ("Person", "Movie"),
}


def _ensure_db(sys_db, db_name: str):
    ensure_fresh_database(sys_db, db_name)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def arango_sys():
    skip_if_integration_not_enabled()
    client, sys_db = connect_root()
    wait_for_arango(sys_db)
    return client, sys_db


@pytest.fixture(scope="module")
def pg_db(arango_sys):
    client, sys_db = arango_sys
    db_name = "neo4j_movies_pg_test"
    _ensure_db(sys_db, db_name)
    db = client.db(db_name, username=env("ARANGO_USER", "root"), password=env("ARANGO_PASS", "openSesame"))
    seed_movies_pg(db)
    yield db
    with contextlib.suppress(Exception):
        sys_db.delete_database(db_name)


@pytest.fixture(scope="module")
def lpg_db(arango_sys):
    client, sys_db = arango_sys
    db_name = "neo4j_movies_lpg_test"
    _ensure_db(sys_db, db_name)
    db = client.db(db_name, username=env("ARANGO_USER", "root"), password=env("ARANGO_PASS", "openSesame"))
    seed_movies_lpg(db)
    yield db
    with contextlib.suppress(Exception):
        sys_db.delete_database(db_name)


@pytest.fixture(scope="module")
def hybrid_db(arango_sys):
    client, sys_db = arango_sys
    db_name = "neo4j_movies_hybrid_test"
    _ensure_db(sys_db, db_name)
    db = client.db(db_name, username=env("ARANGO_USER", "root"), password=env("ARANGO_PASS", "openSesame"))
    seed_movies_hybrid(db)
    yield db
    with contextlib.suppress(Exception):
        sys_db.delete_database(db_name)


@pytest.fixture(scope="module")
def pg_snapshot(pg_db):
    return snapshot_physical_schema(pg_db)


@pytest.fixture(scope="module")
def lpg_snapshot(lpg_db):
    return snapshot_physical_schema(lpg_db)


@pytest.fixture(scope="module")
def hybrid_snapshot(hybrid_db):
    return snapshot_physical_schema(hybrid_db)


@pytest.fixture(scope="module")
def pg_baseline(pg_snapshot):
    return infer_baseline_from_snapshot(pg_snapshot)


@pytest.fixture(scope="module")
def lpg_baseline(lpg_snapshot):
    return infer_baseline_from_snapshot(lpg_snapshot)


@pytest.fixture(scope="module")
def hybrid_baseline(hybrid_snapshot):
    return infer_baseline_from_snapshot(hybrid_snapshot)


# ── PG tests ────────────────────────────────────────────────────────────


class TestPGBaseline:
    def test_entity_types(self, pg_baseline):
        entities = {e["name"] for e in pg_baseline["conceptualSchema"]["entities"]}
        assert entities == EXPECTED_ENTITIES

    def test_relationship_types(self, pg_baseline):
        rels = {r["type"] for r in pg_baseline["conceptualSchema"]["relationships"]}
        assert rels == EXPECTED_RELATIONSHIPS

    def test_entity_properties(self, pg_baseline):
        ents = {e["name"]: e for e in pg_baseline["conceptualSchema"]["entities"]}
        movie_props = {p["name"] for p in ents["Movie"]["properties"]}
        person_props = {p["name"] for p in ents["Person"]["properties"]}
        assert movie_props >= EXPECTED_MOVIE_PROPS
        assert person_props >= EXPECTED_PERSON_PROPS

    def test_domain_range(self, pg_baseline):
        rels = {r["type"]: r for r in pg_baseline["conceptualSchema"]["relationships"]}
        for rel_type, (from_ent, to_ent) in EXPECTED_ENDPOINTS.items():
            assert rels[rel_type]["fromEntity"] == from_ent, f"{rel_type} fromEntity"
            assert rels[rel_type]["toEntity"] == to_ent, f"{rel_type} toEntity"

    def test_physical_mapping_uses_collection_style(self, pg_baseline):
        pm = pg_baseline["physicalMapping"]
        for ent_name in EXPECTED_ENTITIES:
            assert pm["entities"][ent_name]["style"] == "COLLECTION"

    def test_physical_mapping_uses_dedicated_collection(self, pg_baseline):
        pm = pg_baseline["physicalMapping"]
        for rel_type in EXPECTED_RELATIONSHIPS:
            assert pm["relationships"][rel_type]["style"] == "DEDICATED_COLLECTION"

    def test_detected_patterns(self, pg_baseline):
        patterns = pg_baseline.get("detectedPatterns", [])
        assert "PG_ENTITY_COLLECTION" in patterns
        assert "PG_DEDICATED_EDGE" in patterns


# ── LPG tests ───────────────────────────────────────────────────────────


class TestLPGBaseline:
    def test_entity_types(self, lpg_baseline):
        entities = {e["name"] for e in lpg_baseline["conceptualSchema"]["entities"]}
        assert entities == EXPECTED_ENTITIES
        assert "Node" not in entities

    def test_relationship_types(self, lpg_baseline):
        rels = {r["type"] for r in lpg_baseline["conceptualSchema"]["relationships"]}
        assert rels == EXPECTED_RELATIONSHIPS
        assert "EDGES" not in rels

    def test_entity_properties(self, lpg_baseline):
        ents = {e["name"]: e for e in lpg_baseline["conceptualSchema"]["entities"]}
        movie_props = {p["name"] for p in ents["Movie"]["properties"]}
        person_props = {p["name"] for p in ents["Person"]["properties"]}
        assert movie_props >= EXPECTED_MOVIE_PROPS
        assert person_props >= EXPECTED_PERSON_PROPS

    def test_domain_range(self, lpg_baseline):
        rels = {r["type"]: r for r in lpg_baseline["conceptualSchema"]["relationships"]}
        for rel_type, (from_ent, to_ent) in EXPECTED_ENDPOINTS.items():
            assert rels[rel_type]["fromEntity"] == from_ent, f"{rel_type} fromEntity"
            assert rels[rel_type]["toEntity"] == to_ent, f"{rel_type} toEntity"

    def test_physical_mapping_uses_label_style(self, lpg_baseline):
        pm = lpg_baseline["physicalMapping"]
        for ent_name in EXPECTED_ENTITIES:
            assert pm["entities"][ent_name]["style"] == "LABEL"
            assert pm["entities"][ent_name]["collectionName"] == "nodes"
            assert pm["entities"][ent_name]["typeField"] == "type"
            assert pm["entities"][ent_name]["typeValue"] == ent_name

    def test_physical_mapping_uses_generic_with_type(self, lpg_baseline):
        pm = lpg_baseline["physicalMapping"]
        for rel_type in EXPECTED_RELATIONSHIPS:
            assert pm["relationships"][rel_type]["style"] == "GENERIC_WITH_TYPE"
            assert pm["relationships"][rel_type]["edgeCollectionName"] == "edges"
            assert pm["relationships"][rel_type]["typeField"] == "relation"
            assert pm["relationships"][rel_type]["typeValue"] == rel_type

    def test_detected_patterns(self, lpg_baseline):
        patterns = lpg_baseline.get("detectedPatterns", [])
        assert "LPG_LABEL" in patterns
        assert "LPG_GENERIC_EDGE" in patterns

    def test_relationship_properties(self, lpg_baseline):
        rels = {r["type"]: r for r in lpg_baseline["conceptualSchema"]["relationships"]}
        acted_in_props = {p["name"] for p in rels["ACTED_IN"]["properties"]}
        assert "roles" in acted_in_props
        reviewed_props = {p["name"] for p in rels["REVIEWED"]["properties"]}
        assert {"summary", "rating"} <= reviewed_props


# ── Hybrid tests ─────────────────────────────────────────────────────────


class TestHybridBaseline:
    """Hybrid: PG entity collections + LPG shared edge collection."""

    def test_entity_types(self, hybrid_baseline):
        entities = {e["name"] for e in hybrid_baseline["conceptualSchema"]["entities"]}
        assert entities == EXPECTED_ENTITIES

    def test_relationship_types(self, hybrid_baseline):
        rels = {r["type"] for r in hybrid_baseline["conceptualSchema"]["relationships"]}
        assert rels == EXPECTED_RELATIONSHIPS

    def test_entity_properties(self, hybrid_baseline):
        ents = {e["name"]: e for e in hybrid_baseline["conceptualSchema"]["entities"]}
        movie_props = {p["name"] for p in ents["Movie"]["properties"]}
        person_props = {p["name"] for p in ents["Person"]["properties"]}
        assert movie_props >= EXPECTED_MOVIE_PROPS
        assert person_props >= EXPECTED_PERSON_PROPS

    def test_domain_range(self, hybrid_baseline):
        rels = {r["type"]: r for r in hybrid_baseline["conceptualSchema"]["relationships"]}
        for rel_type, (from_ent, to_ent) in EXPECTED_ENDPOINTS.items():
            assert rels[rel_type]["fromEntity"] == from_ent, f"{rel_type} fromEntity"
            assert rels[rel_type]["toEntity"] == to_ent, f"{rel_type} toEntity"

    def test_physical_mapping_entities_use_collection_style(self, hybrid_baseline):
        pm = hybrid_baseline["physicalMapping"]
        for ent_name in EXPECTED_ENTITIES:
            assert pm["entities"][ent_name]["style"] == "COLLECTION"

    def test_physical_mapping_relationships_use_generic_with_type(self, hybrid_baseline):
        pm = hybrid_baseline["physicalMapping"]
        for rel_type in EXPECTED_RELATIONSHIPS:
            assert pm["relationships"][rel_type]["style"] == "GENERIC_WITH_TYPE"
            assert pm["relationships"][rel_type]["edgeCollectionName"] == "edges"
            assert pm["relationships"][rel_type]["typeField"] == "relation"
            assert pm["relationships"][rel_type]["typeValue"] == rel_type

    def test_detected_patterns(self, hybrid_baseline):
        patterns = hybrid_baseline.get("detectedPatterns", [])
        assert "PG_ENTITY_COLLECTION" in patterns
        assert "LPG_GENERIC_EDGE" in patterns


# ── Cross-style equivalence ─────────────────────────────────────────────


class TestCrossStyleEquivalence:
    """All three physical styles must produce the exact same conceptual ontology."""

    def test_same_entity_names(self, pg_baseline, lpg_baseline, hybrid_baseline):
        pg_ents = {e["name"] for e in pg_baseline["conceptualSchema"]["entities"]}
        lpg_ents = {e["name"] for e in lpg_baseline["conceptualSchema"]["entities"]}
        hybrid_ents = {e["name"] for e in hybrid_baseline["conceptualSchema"]["entities"]}
        assert pg_ents == lpg_ents == hybrid_ents

    def test_same_relationship_types(self, pg_baseline, lpg_baseline, hybrid_baseline):
        pg_rels = {r["type"] for r in pg_baseline["conceptualSchema"]["relationships"]}
        lpg_rels = {r["type"] for r in lpg_baseline["conceptualSchema"]["relationships"]}
        hybrid_rels = {r["type"] for r in hybrid_baseline["conceptualSchema"]["relationships"]}
        assert pg_rels == lpg_rels == hybrid_rels

    def test_same_domain_range(self, pg_baseline, lpg_baseline, hybrid_baseline):
        def _rel_map(baseline):
            return {r["type"]: (r["fromEntity"], r["toEntity"]) for r in baseline["conceptualSchema"]["relationships"]}

        pg_rels = _rel_map(pg_baseline)
        lpg_rels = _rel_map(lpg_baseline)
        hybrid_rels = _rel_map(hybrid_baseline)
        assert pg_rels == lpg_rels == hybrid_rels


# ── Full analyzer (baseline mode, no LLM) ──────────────────────────────


class TestAnalyzerNoLLM:
    """Run the full AgenticSchemaAnalyzer in baseline mode against all three DBs."""

    def test_pg_analysis(self, pg_db):
        analyzer = AgenticSchemaAnalyzer(llm_provider=None, api_key=None)
        result = analyzer.analyze_physical_schema(pg_db, use_cache=False)
        assert result.metadata.used_baseline is True
        entities = {e["name"] for e in result.conceptual_schema["entities"]}
        assert entities == EXPECTED_ENTITIES

    def test_lpg_analysis(self, lpg_db):
        analyzer = AgenticSchemaAnalyzer(llm_provider=None, api_key=None)
        result = analyzer.analyze_physical_schema(lpg_db, use_cache=False)
        assert result.metadata.used_baseline is True
        entities = {e["name"] for e in result.conceptual_schema["entities"]}
        assert entities == EXPECTED_ENTITIES
        assert "Node" not in entities

    def test_hybrid_analysis(self, hybrid_db):
        analyzer = AgenticSchemaAnalyzer(llm_provider=None, api_key=None)
        result = analyzer.analyze_physical_schema(hybrid_db, use_cache=False)
        assert result.metadata.used_baseline is True
        entities = {e["name"] for e in result.conceptual_schema["entities"]}
        assert entities == EXPECTED_ENTITIES
