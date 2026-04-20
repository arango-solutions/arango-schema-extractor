"""Tests for index extraction and property-level physical mapping in baseline inference."""

from __future__ import annotations

from schema_analyzer.baseline import (
    _build_index_lookup,
    _build_property_mapping,
    _extract_indexes_for_mapping,
    _extract_properties,
    infer_baseline_from_snapshot,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _col_with_indexes(indexes: list[dict], fields: list[str] | None = None) -> dict:
    """Minimal collection dict with indexes and optional observed fields."""
    col: dict = {
        "name": "test_col",
        "type": "document",
        "indexes": indexes,
    }
    if fields is not None:
        col["observed_fields"] = {"fields": fields}
    return col


# ── _build_index_lookup ───────────────────────────────────────────────


class TestBuildIndexLookup:
    def test_skips_primary_index(self):
        col = _col_with_indexes(
            [
                {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False},
            ]
        )
        assert _build_index_lookup(col) == {}

    def test_persistent_index_single_field(self):
        col = _col_with_indexes(
            [
                {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False, "name": "idx_email"},
            ]
        )
        lookup = _build_index_lookup(col)
        assert "email" in lookup
        assert lookup["email"]["indexType"] == "persistent"
        assert lookup["email"]["unique"] is True
        assert "compound" not in lookup["email"]

    def test_compound_index(self):
        col = _col_with_indexes(
            [
                {"type": "persistent", "fields": ["tenantId", "accountId"], "unique": True, "sparse": False},
            ]
        )
        lookup = _build_index_lookup(col)
        assert "tenantId" in lookup
        assert lookup["tenantId"]["compound"] == ["tenantId", "accountId"]
        assert lookup["tenantId"]["positionInCompound"] == 0
        assert "accountId" in lookup
        assert lookup["accountId"]["positionInCompound"] == 1

    def test_unique_index_wins_over_non_unique(self):
        col = _col_with_indexes(
            [
                {"type": "persistent", "fields": ["email"], "unique": False, "sparse": False},
                {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False},
            ]
        )
        lookup = _build_index_lookup(col)
        assert lookup["email"]["unique"] is True

    def test_empty_indexes(self):
        col = _col_with_indexes([])
        assert _build_index_lookup(col) == {}


# ── _extract_indexes_for_mapping ──────────────────────────────────────


class TestExtractIndexesForMapping:
    def test_excludes_primary(self):
        col = _col_with_indexes(
            [
                {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False, "name": "primary"},
                {"type": "persistent", "fields": ["name"], "unique": False, "sparse": False, "name": "idx_name"},
            ]
        )
        result = _extract_indexes_for_mapping(col)
        assert len(result) == 1
        assert result[0]["type"] == "persistent"
        assert result[0]["fields"] == ["name"]
        assert result[0]["name"] == "idx_name"
        assert "unique" not in result[0]

    def test_includes_unique_and_sparse_flags(self):
        col = _col_with_indexes(
            [
                {"type": "persistent", "fields": ["email"], "unique": True, "sparse": True, "name": "idx_email"},
            ]
        )
        result = _extract_indexes_for_mapping(col)
        assert result[0]["unique"] is True
        assert result[0]["sparse"] is True

    def test_empty_returns_empty(self):
        col = _col_with_indexes([])
        assert _extract_indexes_for_mapping(col) == []


# ── _extract_properties with index info ───────────────────────────────


class TestExtractPropertiesWithIndexes:
    def test_marks_indexed_field(self):
        col = _col_with_indexes(
            [{"type": "persistent", "fields": ["email"], "unique": False, "sparse": False}],
            fields=["name", "email", "age"],
        )
        props = _extract_properties(col)
        email_prop = next(p for p in props if p["name"] == "email")
        assert email_prop["indexed"] is True
        name_prop = next(p for p in props if p["name"] == "name")
        assert "indexed" not in name_prop

    def test_marks_unique_field(self):
        col = _col_with_indexes(
            [{"type": "persistent", "fields": ["email"], "unique": True, "sparse": False}],
            fields=["email"],
        )
        props = _extract_properties(col)
        assert props[0]["indexed"] is True
        assert props[0]["unique"] is True

    def test_no_indexes_no_flags(self):
        col = _col_with_indexes([], fields=["name", "age"])
        props = _extract_properties(col)
        for p in props:
            assert "indexed" not in p
            assert "unique" not in p


# ── _build_property_mapping ───────────────────────────────────────────


class TestBuildPropertyMapping:
    def test_basic_mapping(self):
        props = [
            {"name": "title", "type": "string"},
            {"name": "released", "type": "string", "indexed": True},
        ]
        mapping = _build_property_mapping(props)
        assert "title" in mapping
        assert mapping["title"]["field"] == "title"
        assert "indexed" not in mapping["title"]
        assert mapping["released"]["indexed"] is True

    def test_unique_propagated(self):
        props = [{"name": "email", "type": "string", "indexed": True, "unique": True}]
        mapping = _build_property_mapping(props)
        assert mapping["email"]["unique"] is True

    def test_empty_props(self):
        assert _build_property_mapping([]) == {}


# ── Full baseline: indexes and properties in physical mapping ─────────


class TestBaselineIndexMapping:
    def test_pg_entity_gets_indexes_in_mapping(self):
        snapshot = {
            "version": 1,
            "collections": [
                {
                    "name": "movies",
                    "type": "document",
                    "inferred_entity_type": "Movie",
                    "indexes": [
                        {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False, "name": "primary"},
                        {
                            "type": "persistent",
                            "fields": ["title"],
                            "unique": False,
                            "sparse": False,
                            "name": "idx_title",
                        },  # noqa: E501
                        {
                            "type": "persistent",
                            "fields": ["released"],
                            "unique": False,
                            "sparse": False,
                            "name": "idx_released",
                        },  # noqa: E501
                    ],
                    "observed_fields": {"fields": ["title", "released", "tagline"]},
                },
            ],
            "graphs": [],
        }
        result = infer_baseline_from_snapshot(snapshot)
        pm = result["physicalMapping"]
        movie_mapping = pm["entities"]["Movie"]
        assert movie_mapping["style"] == "COLLECTION"

        assert "indexes" in movie_mapping
        assert len(movie_mapping["indexes"]) == 2
        idx_names = {i["name"] for i in movie_mapping["indexes"]}
        assert idx_names == {"idx_title", "idx_released"}

        assert "properties" in movie_mapping
        assert movie_mapping["properties"]["title"]["field"] == "title"
        assert movie_mapping["properties"]["title"]["indexed"] is True
        assert "indexed" not in movie_mapping["properties"]["tagline"]

    def test_pg_entity_no_indexes_omits_key(self):
        snapshot = {
            "version": 1,
            "collections": [
                {
                    "name": "movies",
                    "type": "document",
                    "inferred_entity_type": "Movie",
                    "indexes": [
                        {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False},
                    ],
                    "observed_fields": {"fields": ["title"]},
                },
            ],
            "graphs": [],
        }
        result = infer_baseline_from_snapshot(snapshot)
        movie_mapping = result["physicalMapping"]["entities"]["Movie"]
        assert "indexes" not in movie_mapping

    def test_edge_collection_gets_indexes_in_mapping(self):
        snapshot = {
            "version": 1,
            "collections": [
                {
                    "name": "acted_in",
                    "type": "edge",
                    "inferred_relationship_type": "ACTED_IN",
                    "indexes": [
                        {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False},
                        {
                            "type": "persistent",
                            "fields": ["roles"],
                            "unique": False,
                            "sparse": False,
                            "name": "idx_roles",
                        },  # noqa: E501
                    ],
                    "observed_fields": {"fields": ["roles"]},
                },
            ],
            "graphs": [],
        }
        result = infer_baseline_from_snapshot(snapshot)
        rel_mapping = result["physicalMapping"]["relationships"]["ACTED_IN"]
        assert rel_mapping["style"] == "DEDICATED_COLLECTION"
        assert len(rel_mapping["indexes"]) == 1
        assert rel_mapping["indexes"][0]["fields"] == ["roles"]

    def test_lpg_entity_gets_indexes(self):
        snapshot = {
            "version": 1,
            "collections": [
                {
                    "name": "nodes",
                    "type": "document",
                    "candidate_type_fields": ["type"],
                    "sample_field_value_counts": {
                        "type": [
                            {"value": "Movie", "count": 10},
                            {"value": "Person", "count": 20},
                        ]
                    },
                    "indexes": [
                        {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False},
                        {
                            "type": "persistent",
                            "fields": ["name"],
                            "unique": False,
                            "sparse": False,
                            "name": "idx_name",
                        },  # noqa: E501
                    ],
                    "observed_fields": {
                        "by_type": {
                            "Movie": ["title", "released"],
                            "Person": ["name", "born"],
                        }
                    },
                },
            ],
            "graphs": [],
        }
        result = infer_baseline_from_snapshot(snapshot)

        movie_mapping = result["physicalMapping"]["entities"]["Movie"]
        assert movie_mapping["style"] == "LABEL"
        assert len(movie_mapping["indexes"]) == 1
        assert movie_mapping["indexes"][0]["name"] == "idx_name"
        assert "properties" in movie_mapping
        assert "title" in movie_mapping["properties"]
        assert movie_mapping["properties"]["title"]["field"] == "title"

        person_mapping = result["physicalMapping"]["entities"]["Person"]
        assert "name" in person_mapping["properties"]
        assert person_mapping["properties"]["name"]["indexed"] is True

    def test_conceptual_property_has_indexed_flag(self):
        snapshot = {
            "version": 1,
            "collections": [
                {
                    "name": "users",
                    "type": "document",
                    "inferred_entity_type": "User",
                    "indexes": [
                        {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False},
                        {"type": "persistent", "fields": ["email"], "unique": True, "sparse": False},
                    ],
                    "observed_fields": {"fields": ["email", "name", "age"]},
                },
            ],
            "graphs": [],
        }
        result = infer_baseline_from_snapshot(snapshot)
        cs = result["conceptualSchema"]
        user_ent = cs["entities"][0]
        email_prop = next(p for p in user_ent["properties"] if p["name"] == "email")
        name_prop = next(p for p in user_ent["properties"] if p["name"] == "name")
        assert email_prop["indexed"] is True
        assert email_prop["unique"] is True
        assert "indexed" not in name_prop

    def test_unique_index_on_compound_fields(self):
        snapshot = {
            "version": 1,
            "collections": [
                {
                    "name": "accounts",
                    "type": "document",
                    "inferred_entity_type": "Account",
                    "indexes": [
                        {"type": "primary", "fields": ["_key"], "unique": True, "sparse": False},
                        {
                            "type": "persistent",
                            "fields": ["tenantId", "accountId"],
                            "unique": True,
                            "sparse": False,
                            "name": "idx_tenant_account",
                        },  # noqa: E501
                    ],
                    "observed_fields": {"fields": ["tenantId", "accountId", "status"]},
                },
            ],
            "graphs": [],
        }
        result = infer_baseline_from_snapshot(snapshot)
        pm = result["physicalMapping"]
        acct = pm["entities"]["Account"]
        assert len(acct["indexes"]) == 1
        assert acct["indexes"][0]["fields"] == ["tenantId", "accountId"]
        assert acct["indexes"][0]["unique"] is True

        assert acct["properties"]["tenantId"]["indexed"] is True
        assert acct["properties"]["tenantId"]["unique"] is True
        assert "indexed" not in acct["properties"]["status"]
