"""
Seed helpers for Neo4j Movies dataset in PG, LPG, and hybrid physical styles.

Data fixtures live in tests/fixtures/datasets/movies/ and were imported from
the arango-cypher-py project (the canonical Neo4j Movies ArangoDB dataset).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "datasets" / "movies"


def _ensure_doc_collection(db: Any, name: str) -> Any:
    if not db.has_collection(name):
        return db.create_collection(name)
    col = db.collection(name)
    if col.properties().get("type") == 3:
        raise ValueError(f"Expected document collection but found edge collection: {name}")
    return col


def _ensure_edge_collection(db: Any, name: str) -> Any:
    if not db.has_collection(name):
        return db.create_collection(name, edge=True)
    col = db.collection(name)
    if col.properties().get("type") != 3:
        raise ValueError(f"Expected edge collection but found document collection: {name}")
    return col


def _ensure_persistent_index(col: Any, fields: list[str], *, name: str | None = None) -> None:
    existing = col.indexes()
    for idx in existing:
        if idx.get("type") == "persistent" and idx.get("fields") == fields:
            return
    kwargs: dict[str, Any] = {"type": "persistent", "fields": fields}
    if name:
        kwargs["name"] = name
    col.add_index(kwargs)


def seed_movies_lpg(db: Any) -> None:
    """Seed Neo4j Movies dataset in LPG format (shared nodes/edges collections)."""
    data = json.loads((_FIXTURES_ROOT / "lpg-data.json").read_text("utf-8"))
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    nodes_col = _ensure_doc_collection(db, "nodes")
    edges_col = _ensure_edge_collection(db, "edges")
    nodes_col.truncate()
    edges_col.truncate()

    if nodes:
        nodes_col.insert_many(nodes)
    if edges:
        edges_col.insert_many(edges)

    _ensure_persistent_index(nodes_col, ["type"], name="idx_nodes_type")
    _ensure_persistent_index(nodes_col, ["name"], name="idx_nodes_name")
    _ensure_persistent_index(nodes_col, ["title"], name="idx_nodes_title")
    _ensure_persistent_index(edges_col, ["relation"], name="idx_edges_relation")

    logger.info("Seeded movies LPG: %d nodes, %d edges", len(nodes), len(edges))


def seed_movies_pg(db: Any) -> None:
    """Seed Neo4j Movies dataset in PG format (separate collections per type)."""
    data = json.loads((_FIXTURES_ROOT / "pg-data.json").read_text("utf-8"))

    for coll_name, docs in data.get("collections", {}).items():
        col = _ensure_doc_collection(db, coll_name)
        col.truncate()
        if docs:
            col.insert_many(docs)

    for coll_name, docs in data.get("edge_collections", {}).items():
        col = _ensure_edge_collection(db, coll_name)
        col.truncate()
        if docs:
            col.insert_many(docs)

    _ensure_persistent_index(db.collection("persons"), ["name"], name="idx_persons_name")
    _ensure_persistent_index(db.collection("movies"), ["title"], name="idx_movies_title")

    logger.info(
        "Seeded movies PG: %d collections",
        len(data.get("collections", {})) + len(data.get("edge_collections", {})),
    )


def seed_movies_hybrid(db: Any) -> None:
    """Seed Neo4j Movies dataset in hybrid format (PG entity collections + LPG shared edge collection)."""
    data = json.loads((_FIXTURES_ROOT / "hybrid-data.json").read_text("utf-8"))

    for coll_name, docs in data.get("collections", {}).items():
        col = _ensure_doc_collection(db, coll_name)
        col.truncate()
        if docs:
            col.insert_many(docs)

    edges = data.get("edges") or []
    edges_col = _ensure_edge_collection(db, "edges")
    edges_col.truncate()
    if edges:
        edges_col.insert_many(edges)

    _ensure_persistent_index(db.collection("persons"), ["name"], name="idx_persons_name")
    _ensure_persistent_index(db.collection("movies"), ["title"], name="idx_movies_title")
    _ensure_persistent_index(edges_col, ["relation"], name="idx_edges_relation")

    logger.info(
        "Seeded movies hybrid: %d entities, %d edges",
        sum(len(v) for v in data.get("collections", {}).values()),
        len(edges),
    )
