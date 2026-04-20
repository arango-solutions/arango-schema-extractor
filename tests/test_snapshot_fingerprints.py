"""
Tests for the cheap db-keyed fingerprint probes introduced for issue #7:

* ``fingerprint_physical_shape(db)``    — shape-only (collections + indexes)
* ``fingerprint_physical_counts(db)``   — shape + per-collection counts

These tests exercise the probes directly against an in-memory fake DB so the
invariants documented in the acceptance criteria stay honest even without a
live ArangoDB. An integration test against a real server is intentionally
scoped out here and left for the existing Integration workflow.
"""

from __future__ import annotations

from typing import Any

import pytest

from schema_analyzer import (
    fingerprint_physical_counts,
    fingerprint_physical_schema,
    fingerprint_physical_shape,
)
from schema_analyzer.snapshot import (
    _iter_user_collections,
    _stable_index_digest,
    snapshot_physical_schema,
)

# ──────────────────────────────────────────────────────────────────────────
# Fake python-arango surface
# ──────────────────────────────────────────────────────────────────────────


class _FakeCollection:
    def __init__(self, name: str, *, indexes: list[dict[str, Any]] | None = None, count: int = 0):
        self.name = name
        self._indexes = list(indexes or [])
        self._count = int(count)

    def indexes(self) -> list[dict[str, Any]]:
        return list(self._indexes)

    def count(self) -> int:
        return self._count

    def properties(self) -> dict[str, Any]:
        return {"name": self.name}


class _ExplodingCollection(_FakeCollection):
    """Collection whose ``indexes()`` and ``count()`` always raise."""

    def indexes(self) -> list[dict[str, Any]]:  # pragma: no cover - exercised via probes
        raise RuntimeError("boom-indexes")

    def count(self) -> int:  # pragma: no cover - exercised via probes
        raise RuntimeError("boom-count")


class _FakeDB:
    def __init__(self, name: str = "analyzer_test"):
        self.name = name
        self._cols: dict[str, _FakeCollection] = {}
        self._col_types: dict[str, str] = {}
        self._collections_raises = False

    def add_doc(self, name: str, **kw: Any) -> _FakeCollection:
        col = _FakeCollection(name, **kw)
        self._cols[name] = col
        self._col_types[name] = "document"
        return col

    def add_edge(self, name: str, **kw: Any) -> _FakeCollection:
        col = _FakeCollection(name, **kw)
        self._cols[name] = col
        self._col_types[name] = "edge"
        return col

    def add_exploding(self, name: str, *, edge: bool = False) -> _ExplodingCollection:
        col = _ExplodingCollection(name)
        self._cols[name] = col
        self._col_types[name] = "edge" if edge else "document"
        return col

    def drop(self, name: str) -> None:
        self._cols.pop(name, None)
        self._col_types.pop(name, None)

    def collection(self, name: str) -> _FakeCollection:
        return self._cols[name]

    def collections(self) -> list[dict[str, Any]]:
        if self._collections_raises:
            raise RuntimeError("boom-collections")
        out: list[dict[str, Any]] = [
            {"name": "_system_bookkeeping", "type": "document"},  # system → must be filtered out
        ]
        for name in self._cols:
            out.append({"name": name, "type": self._col_types[name]})
        return out


# ──────────────────────────────────────────────────────────────────────────
# Helper digests
# ──────────────────────────────────────────────────────────────────────────


def test_stable_index_digest_ignores_autogen_name_and_id():
    a = {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False, "name": "idx_1", "id": "x/42"}
    b = {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False, "name": "idx_999", "id": "x/99"}
    assert _stable_index_digest(a) == _stable_index_digest(b)


def test_stable_index_digest_responds_to_identity_fields():
    base = {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False}
    assert _stable_index_digest(base) != _stable_index_digest({**base, "unique": False})
    assert _stable_index_digest(base) != _stable_index_digest({**base, "sparse": True})
    assert _stable_index_digest(base) != _stable_index_digest({**base, "fields": ["emails"]})
    assert _stable_index_digest(base) != _stable_index_digest({**base, "type": "inverted"})
    assert _stable_index_digest(base) != _stable_index_digest({**base, "vci": True})
    # deduplicate True is the default, so only explicit False should perturb.
    assert _stable_index_digest(base) == _stable_index_digest({**base, "deduplicate": True})
    assert _stable_index_digest(base) != _stable_index_digest({**base, "deduplicate": False})


def test_stable_index_digest_primary_is_empty():
    assert _stable_index_digest({"type": "primary", "fields": ["_key"], "unique": True}) == ""


def test_iter_user_collections_filters_and_excludes():
    db = _FakeDB()
    db.add_doc("persons")
    db.add_doc("movies")
    db.add_doc("my_cache")
    db.add_edge("acted_in")

    names = [c["name"] for c in _iter_user_collections(db)]
    assert names == sorted(names)  # sorted for determinism
    assert "_system_bookkeeping" not in names  # system is filtered
    assert "my_cache" in names  # without exclusion, present

    names2 = [c["name"] for c in _iter_user_collections(db, exclude_collections={"my_cache"})]
    assert "my_cache" not in names2
    assert set(names2) == {"persons", "movies", "acted_in"}


def test_iter_user_collections_tolerates_collections_exception():
    db = _FakeDB()
    db._collections_raises = True
    assert _iter_user_collections(db) == []


# ──────────────────────────────────────────────────────────────────────────
# Shape fingerprint
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded_db() -> _FakeDB:
    db = _FakeDB()
    db.add_doc(
        "persons",
        count=10,
        indexes=[
            {"type": "primary", "fields": ["_key"], "unique": True, "name": "primary"},
            {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False, "name": "idx_email"},
        ],
    )
    db.add_doc(
        "movies",
        count=5,
        indexes=[
            {"type": "primary", "fields": ["_key"], "unique": True},
            {"type": "persistent", "fields": ["title"], "unique": False, "sparse": False, "name": "idx_title"},
        ],
    )
    db.add_edge(
        "acted_in",
        count=7,
        indexes=[
            {"type": "primary", "fields": ["_key"], "unique": True},
            {"type": "persistent", "fields": ["roles"], "unique": False, "sparse": False, "name": "idx_roles"},
        ],
    )
    return db


def test_shape_fingerprint_is_stable_across_repeated_calls(seeded_db: _FakeDB):
    a = fingerprint_physical_shape(seeded_db)
    b = fingerprint_physical_shape(seeded_db)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_shape_fingerprint_stable_under_document_writes(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    # Simulate document writes — count changes, no index change.
    seeded_db.collection("persons")._count = 999
    seeded_db.collection("movies")._count = 123
    after = fingerprint_physical_shape(seeded_db)
    assert before == after, "shape fingerprint must not flip on pure count changes"


def test_shape_fingerprint_changes_on_collection_add(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    seeded_db.add_doc("studios", indexes=[{"type": "primary", "fields": ["_key"], "unique": True}])
    after = fingerprint_physical_shape(seeded_db)
    assert before != after


def test_shape_fingerprint_changes_on_collection_drop(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    seeded_db.drop("movies")
    after = fingerprint_physical_shape(seeded_db)
    assert before != after


def test_shape_fingerprint_changes_when_doc_becomes_edge():
    db = _FakeDB()
    db.add_doc("rel")
    before = fingerprint_physical_shape(db)
    db.drop("rel")
    db.add_edge("rel")
    after = fingerprint_physical_shape(db)
    assert before != after


def test_shape_fingerprint_changes_on_index_add(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    seeded_db.collection("persons")._indexes.append(
        {"type": "persistent", "fields": ["ssn"], "unique": True, "sparse": False, "name": "idx_ssn"}
    )
    after = fingerprint_physical_shape(seeded_db)
    assert before != after


def test_shape_fingerprint_changes_on_index_drop(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    seeded_db.collection("persons")._indexes = [
        i for i in seeded_db.collection("persons")._indexes if i.get("type") == "primary"
    ]
    after = fingerprint_physical_shape(seeded_db)
    assert before != after


def test_shape_fingerprint_ignores_autogen_index_name_rename(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    for idx in seeded_db.collection("persons")._indexes:
        if idx.get("type") == "persistent":
            idx["name"] = "renamed_by_arangodb"
            idx["id"] = "persons/9999"
    after = fingerprint_physical_shape(seeded_db)
    assert before == after, "shape fingerprint must be insensitive to auto-generated index name/id"


def test_shape_fingerprint_changes_on_vci_flag(seeded_db: _FakeDB):
    before = fingerprint_physical_shape(seeded_db)
    for idx in seeded_db.collection("persons")._indexes:
        if idx.get("type") == "persistent":
            idx["vci"] = True
    after = fingerprint_physical_shape(seeded_db)
    assert before != after


def test_shape_fingerprint_excludes_named_collection(seeded_db: _FakeDB):
    seeded_db.add_doc("my_cache", count=0, indexes=[{"type": "primary", "fields": ["_key"], "unique": True}])

    # Without exclusion: presence of my_cache should perturb the fingerprint.
    with_cache = fingerprint_physical_shape(seeded_db)
    without_cache = fingerprint_physical_shape(seeded_db, exclude_collections={"my_cache"})
    assert with_cache != without_cache

    # Dropping my_cache while excluding it must leave the fingerprint unchanged.
    before = fingerprint_physical_shape(seeded_db, exclude_collections={"my_cache"})
    seeded_db.drop("my_cache")
    after = fingerprint_physical_shape(seeded_db, exclude_collections={"my_cache"})
    assert before == after


def test_shape_fingerprint_tolerates_exploding_collection():
    db = _FakeDB()
    db.add_doc("good", indexes=[{"type": "primary", "fields": ["_key"], "unique": True}])
    db.add_exploding("bad")
    # Must not raise; should still produce a stable hex digest.
    fp1 = fingerprint_physical_shape(db)
    fp2 = fingerprint_physical_shape(db)
    assert fp1 == fp2
    assert len(fp1) == 64


# ──────────────────────────────────────────────────────────────────────────
# Counts fingerprint
# ──────────────────────────────────────────────────────────────────────────


def test_counts_fingerprint_changes_on_count_change(seeded_db: _FakeDB):
    before = fingerprint_physical_counts(seeded_db)
    seeded_db.collection("persons")._count += 1
    after = fingerprint_physical_counts(seeded_db)
    assert before != after


def test_counts_fingerprint_unchanged_when_nothing_moves(seeded_db: _FakeDB):
    before = fingerprint_physical_counts(seeded_db)
    after = fingerprint_physical_counts(seeded_db)
    assert before == after


def test_counts_fingerprint_changes_when_shape_changes(seeded_db: _FakeDB):
    before = fingerprint_physical_counts(seeded_db)
    seeded_db.add_doc("studios", count=0, indexes=[{"type": "primary", "fields": ["_key"], "unique": True}])
    after = fingerprint_physical_counts(seeded_db)
    assert before != after


def test_counts_fingerprint_ignores_excluded_collection_writes(seeded_db: _FakeDB):
    seeded_db.add_doc("my_cache", count=0, indexes=[{"type": "primary", "fields": ["_key"], "unique": True}])
    before = fingerprint_physical_counts(seeded_db, exclude_collections={"my_cache"})
    seeded_db.collection("my_cache")._count = 42
    after = fingerprint_physical_counts(seeded_db, exclude_collections={"my_cache"})
    assert before == after


def test_counts_fingerprint_tolerates_failing_count():
    db = _FakeDB()
    db.add_doc("good", count=3, indexes=[{"type": "primary", "fields": ["_key"], "unique": True}])
    db.add_exploding("bad")
    fp = fingerprint_physical_counts(db)
    assert len(fp) == 64


# ──────────────────────────────────────────────────────────────────────────
# Coexistence with the existing snapshot-based fingerprint
# ──────────────────────────────────────────────────────────────────────────


def test_snapshot_fingerprint_and_shape_fingerprint_are_both_stable(seeded_db: _FakeDB):
    snap = snapshot_physical_schema(seeded_db, sample_limit_per_collection=0)
    snap_fp_a = fingerprint_physical_schema(snap)
    snap_fp_b = fingerprint_physical_schema(snap)
    shape_a = fingerprint_physical_shape(seeded_db)
    shape_b = fingerprint_physical_shape(seeded_db)
    assert snap_fp_a == snap_fp_b
    assert shape_a == shape_b
    # They hash different inputs, so they are expected to differ.
    assert snap_fp_a != shape_a
