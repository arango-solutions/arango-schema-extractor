"""Unit tests for the sharding-profile classifier (``metadata.shardingProfile``).

Covers every style enumerated in ``docs/PRD.md`` §6.2 bullet 3:

* ``OneShard`` — database-level ``sharding == "single"``, incl. the
  ``distributeShardsLike`` leader heuristic.
* ``DisjointSmartGraph`` — at least one graph with
  ``isSmart == true`` AND ``isDisjoint == true``, coexisting with
  satellite reference collections (tenant-sharding + metadata graph).
* ``SmartGraph`` — smart graph that is *not* disjoint; satellites
  still listed in the evidence block.
* ``SatelliteGraph`` — every user collection is a satellite.
* ``Sharded`` — the fall-through default with neither smart nor
  satellite collections.

Plus the degraded paths:

* Missing ``graphs_detailed`` / graph probe errors → ``status == "degraded"``
  with a human-readable reason.
* Missing ``database`` block → still classifies by graph / collection
  evidence, but never emits ``OneShard`` without explicit evidence.
* Empty / missing ``collections`` → returns ``None``.

These are pure unit tests against synthetic snapshot payloads shaped
identically to what :func:`schema_analyzer.snapshot.snapshot_physical_schema`
emits. End-to-end coverage through :class:`AgenticSchemaAnalyzer`
lives in :mod:`tests.test_analyzer_with_mock_provider`.
"""

from __future__ import annotations

import pytest

from schema_analyzer.sharding_profile import classify_sharding_profile

# ── Snapshot builders ───────────────────────────────────────────────────────


def _collection(
    name: str,
    *,
    kind: str = "regular",
    shard_keys: list[str] | None = None,
    number_of_shards: int = 3,
    replication_factor: int | str = 3,
    distribute_shards_like: str | None = None,
    smart_graph_attribute: str | None = None,
    is_smart: bool | None = None,
    is_disjoint: bool | None = None,
    is_satellite: bool | None = None,
    is_system: bool = False,
    edge: bool = False,
) -> dict:
    """Build a per-collection snapshot entry.

    ``kind`` is a shorthand that pre-populates the right properties
    for the requested role:

    * ``"smartgraph"`` → ``isSmart=True``, ``smartGraphAttribute`` set,
      ``shardKeys`` default to the smart attribute.
    * ``"smartgraph_disjoint"`` → same + ``isDisjoint=True``.
    * ``"satellite"`` → ``replicationFactor="satellite"``.
    * ``"regular"`` → no special flags.
    """
    props: dict = {
        "numberOfShards": number_of_shards,
        "replicationFactor": replication_factor,
    }
    if is_system:
        props["isSystem"] = True
    if kind in ("smartgraph", "smartgraph_disjoint"):
        props["isSmart"] = True
        attr = smart_graph_attribute or "TENANT_HEX_ID"
        props["smartGraphAttribute"] = attr
        props["shardKeys"] = shard_keys or [attr]
        if kind == "smartgraph_disjoint":
            props["isDisjoint"] = True
    elif kind == "satellite":
        props["replicationFactor"] = "satellite"
        props["isSatellite"] = True
    else:
        if shard_keys is not None:
            props["shardKeys"] = shard_keys
    if distribute_shards_like is not None:
        props["distributeShardsLike"] = distribute_shards_like
    if is_smart is not None:
        props["isSmart"] = is_smart
    if is_disjoint is not None:
        props["isDisjoint"] = is_disjoint
    if is_satellite is not None:
        props["isSatellite"] = is_satellite
    props["type"] = 3 if edge else 2

    return {
        "name": name,
        "type": "edge" if edge else "document",
        "count": 0,
        "properties": props,
        "indexes": [],
        "candidate_type_fields": [],
    }


def _graph(
    name: str,
    *,
    is_smart: bool = False,
    is_disjoint: bool = False,
    is_satellite: bool = False,
    smart_graph_attribute: str | None = None,
    edge_definitions: list[dict] | None = None,
    orphan_collections: list[str] | None = None,
) -> dict:
    g: dict = {"name": name}
    if is_smart:
        g["isSmart"] = True
    if is_disjoint:
        g["isDisjoint"] = True
    if is_satellite:
        g["isSatellite"] = True
    if smart_graph_attribute:
        g["smartGraphAttribute"] = smart_graph_attribute
    g["edge_definitions"] = edge_definitions or []
    g["orphan_collections"] = orphan_collections or []
    return g


def _snapshot(
    *,
    sharding: str | None = None,
    collections: list[dict],
    graphs_detailed: list[dict] | None = None,
    replication_factor: int | None = None,
    graphs_error: str | None = None,
) -> dict:
    snap: dict = {
        "version": 1,
        "generated_at": None,
        "collections": collections,
        "graphs": [g.get("name") for g in (graphs_detailed or [])],
        "graphs_detailed": graphs_detailed or [],
        "database": {},
    }
    if sharding is not None:
        snap["database"]["sharding"] = sharding
    if replication_factor is not None:
        snap["database"]["replicationFactor"] = replication_factor
    if graphs_error is not None:
        snap["graphs_error"] = graphs_error
    return snap


# ── None / empty-input contract ─────────────────────────────────────────────


def test_non_dict_snapshot_returns_none() -> None:
    assert classify_sharding_profile(None) is None  # type: ignore[arg-type]
    assert classify_sharding_profile("not a snapshot") is None  # type: ignore[arg-type]


def test_missing_collections_key_returns_none() -> None:
    assert classify_sharding_profile({"version": 1}) is None


def test_collections_not_a_list_returns_none() -> None:
    assert classify_sharding_profile({"collections": "oops"}) is None


# ── Sharded (default fall-through) ──────────────────────────────────────────


def test_sharded_default_no_graphs_no_smart_no_satellite() -> None:
    snap = _snapshot(
        collections=[
            _collection("User"),
            _collection("Post"),
            _collection("AUTHORED", edge=True),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "Sharded"
    assert profile["status"] == "ok"
    assert profile["collectionKindCounts"]["regular"] == 3
    assert profile["collectionKindCounts"]["smartgraph"] == 0
    assert profile["collectionKindCounts"]["satellite"] == 0
    assert profile["graphs"] == []
    assert profile["satelliteCollections"] == []
    assert "User" in profile["collections"]
    assert profile["collections"]["User"]["kind"] == "regular"


def test_sharded_reports_per_collection_evidence() -> None:
    snap = _snapshot(
        collections=[
            _collection(
                "User",
                shard_keys=["org_id"],
                number_of_shards=12,
                replication_factor=3,
            ),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    ev = profile["collections"]["User"]
    assert ev["shardKeys"] == ["org_id"]
    assert ev["numberOfShards"] == 12
    assert ev["replicationFactor"] == 3


# ── OneShard ────────────────────────────────────────────────────────────────


def test_oneshard_database_sharding_single_wins() -> None:
    snap = _snapshot(
        sharding="single",
        collections=[
            _collection("User", distribute_shards_like="Tenant"),
            _collection("Post", distribute_shards_like="Tenant"),
            _collection("AUTHORED", edge=True, distribute_shards_like="Tenant"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "OneShard"
    assert profile["status"] == "ok"
    assert profile["database"]["sharding"] == "single"
    assert profile["oneShardLeader"] == "Tenant"


def test_oneshard_without_consistent_leader_omits_the_hint() -> None:
    snap = _snapshot(
        sharding="single",
        collections=[
            _collection("A", distribute_shards_like="B"),
            _collection("C", distribute_shards_like="D"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "OneShard"
    assert "oneShardLeader" not in profile


def test_oneshard_wins_even_when_smart_attribute_stale() -> None:
    # Defensive: if a bogus collection claims isSmart but the db is
    # single-shard, the db-level signal wins. Smart graphs can't
    # actually coexist with OneShard but a misconfigured snapshot
    # shouldn't escape the classifier.
    snap = _snapshot(
        sharding="single",
        collections=[
            _collection("A", kind="smartgraph"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "OneShard"


# ── SmartGraph ──────────────────────────────────────────────────────────────


def test_smartgraph_non_disjoint() -> None:
    snap = _snapshot(
        collections=[
            _collection("Tenant", kind="smartgraph"),
            _collection("Employee", kind="smartgraph"),
            _collection("EMPLOYEE_OF", edge=True, kind="smartgraph"),
        ],
        graphs_detailed=[
            _graph(
                "TenantGraph",
                is_smart=True,
                smart_graph_attribute="TENANT_HEX_ID",
                edge_definitions=[
                    {
                        "collection": "EMPLOYEE_OF",
                        "from": ["Employee"],
                        "to": ["Tenant"],
                    },
                ],
            ),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "SmartGraph"
    assert profile["status"] == "ok"
    assert len(profile["graphs"]) == 1
    g = profile["graphs"][0]
    assert g["name"] == "TenantGraph"
    assert g["isSmart"] is True
    assert g.get("isDisjoint") is not True
    assert g["smartGraphAttribute"] == "TENANT_HEX_ID"
    assert "Employee" in g["vertexCollections"]
    assert "EMPLOYEE_OF" in g["edgeCollections"]
    # Per-collection evidence mirrors it.
    assert profile["collections"]["Employee"]["kind"] == "smartgraph"
    assert profile["collections"]["Employee"]["graphName"] == "TenantGraph"


# ── DisjointSmartGraph ──────────────────────────────────────────────────────


def test_disjoint_smartgraph_with_satellites() -> None:
    snap = _snapshot(
        collections=[
            _collection("Tenant", kind="smartgraph_disjoint"),
            _collection("Employee", kind="smartgraph_disjoint"),
            _collection("EMPLOYEE_OF", edge=True, kind="smartgraph_disjoint"),
            _collection("Country", kind="satellite"),
            _collection("Cve", kind="satellite"),
        ],
        graphs_detailed=[
            _graph(
                "TenantGraph",
                is_smart=True,
                is_disjoint=True,
                smart_graph_attribute="TENANT_HEX_ID",
                edge_definitions=[
                    {
                        "collection": "EMPLOYEE_OF",
                        "from": ["Employee"],
                        "to": ["Tenant"],
                    },
                ],
            ),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "DisjointSmartGraph"
    assert profile["status"] == "ok"
    assert profile["graphs"][0]["isDisjoint"] is True
    assert profile["satelliteCollections"] == ["Country", "Cve"]
    assert profile["collectionKindCounts"]["smartgraph"] == 3
    assert profile["collectionKindCounts"]["satellite"] == 2


def test_disjoint_beats_smart_when_both_graphs_present() -> None:
    snap = _snapshot(
        collections=[
            _collection("A", kind="smartgraph"),
            _collection("B", kind="smartgraph_disjoint"),
        ],
        graphs_detailed=[
            _graph("SmartOnly", is_smart=True, smart_graph_attribute="x"),
            _graph("Disjoint", is_smart=True, is_disjoint=True, smart_graph_attribute="y"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "DisjointSmartGraph"


# ── SatelliteGraph ──────────────────────────────────────────────────────────


def test_satellite_graph_when_every_user_collection_is_satellite() -> None:
    snap = _snapshot(
        collections=[
            _collection("Country", kind="satellite"),
            _collection("Cve", kind="satellite"),
            _collection("MITIGATES", edge=True, kind="satellite"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "SatelliteGraph"
    assert profile["satelliteCollections"] == ["Country", "Cve", "MITIGATES"]
    assert profile["collectionKindCounts"]["satellite"] == 3


def test_satellite_graph_requires_all_collections_to_be_satellite() -> None:
    # Mixed: not SatelliteGraph.
    snap = _snapshot(
        collections=[
            _collection("Country", kind="satellite"),
            _collection("User"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "Sharded"
    assert profile["satelliteCollections"] == ["Country"]


# ── System collections are ignored ──────────────────────────────────────────


def test_system_collections_are_excluded_from_evidence_and_classification() -> None:
    snap = _snapshot(
        collections=[
            _collection("_users", is_system=True),
            _collection("User"),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == "Sharded"
    assert "_users" not in profile["collections"]
    assert profile["collectionKindCounts"]["regular"] == 1


# ── Degraded paths ──────────────────────────────────────────────────────────


def test_degraded_when_graph_probe_errored() -> None:
    snap = _snapshot(
        collections=[_collection("A")],
        graphs_detailed=[{"name": "BrokenGraph", "error": "ArangoError"}],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["status"] == "degraded"
    assert "probe failed" in profile["statusReason"]
    # Still classified, just with reduced confidence.
    assert profile["style"] == "Sharded"


def test_degraded_when_graphs_enumeration_errored() -> None:
    snap = _snapshot(
        collections=[_collection("A")],
        graphs_error="forbidden",
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["status"] == "degraded"
    assert "graph enumeration failed" in profile["statusReason"]


def test_degraded_when_no_user_collections_after_system_filter() -> None:
    snap = _snapshot(
        collections=[_collection("_users", is_system=True)],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["status"] == "degraded"
    assert "no user collections" in profile["statusReason"]
    # Style should fall back to Sharded (the PRD default).
    assert profile["style"] == "Sharded"


def test_missing_database_block_does_not_raise() -> None:
    # Emulate a pre-0.5 snapshot that predates the ``database`` top-level.
    snap = {
        "version": 1,
        "collections": [_collection("User")],
        "graphs": [],
        "graphs_detailed": [],
    }
    profile = classify_sharding_profile(snap)
    assert profile is not None
    # Without db-level info we can't claim OneShard — default fall-through.
    assert profile["style"] == "Sharded"
    assert profile["database"] == {}


# ── Idempotence ─────────────────────────────────────────────────────────────


def test_classifier_is_pure() -> None:
    snap = _snapshot(
        collections=[_collection("A"), _collection("B")],
    )
    before = repr(snap)
    classify_sharding_profile(snap)
    classify_sharding_profile(snap)
    assert repr(snap) == before


# ── End-to-end through _apply_sharding_profile ──────────────────────────────


def test_apply_sharding_profile_stamps_metadata_and_status_mirror() -> None:
    from schema_analyzer.analyzer import _apply_sharding_profile

    data: dict = {"metadata": {}}
    snap = _snapshot(
        sharding="single",
        collections=[_collection("A"), _collection("B", distribute_shards_like="A")],
    )
    _apply_sharding_profile(data, snap)
    assert data["metadata"]["shardingProfile"]["style"] == "OneShard"
    assert data["metadata"]["shardingProfileStatus"] == "ok"


def test_apply_sharding_profile_is_no_op_on_minimal_snapshot() -> None:
    from schema_analyzer.analyzer import _apply_sharding_profile

    data: dict = {"metadata": {}}
    _apply_sharding_profile(data, {"version": 1})
    assert "shardingProfile" not in data["metadata"]
    assert "shardingProfileStatus" not in data["metadata"]


def test_apply_sharding_profile_tolerates_missing_metadata_dict() -> None:
    from schema_analyzer.analyzer import _apply_sharding_profile

    data: dict = {}  # no metadata key at all
    snap = _snapshot(collections=[_collection("A")])
    _apply_sharding_profile(data, snap)
    assert data["metadata"]["shardingProfile"]["style"] == "Sharded"


# ── Regression: graph-by-collection index is deterministic ──────────────────


def test_collection_is_attributed_to_lexicographically_first_graph() -> None:
    snap = _snapshot(
        collections=[_collection("Shared", kind="smartgraph")],
        graphs_detailed=[
            _graph(
                "ZGraph",
                is_smart=True,
                smart_graph_attribute="x",
                edge_definitions=[{"collection": "E", "from": ["Shared"], "to": ["Shared"]}],
            ),
            _graph(
                "AGraph",
                is_smart=True,
                smart_graph_attribute="x",
                edge_definitions=[{"collection": "F", "from": ["Shared"], "to": ["Shared"]}],
            ),
        ],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["collections"]["Shared"]["graphName"] == "AGraph"


@pytest.mark.parametrize(
    "style,graph_kwargs",
    [
        ("SmartGraph", {"is_smart": True, "smart_graph_attribute": "x"}),
        (
            "DisjointSmartGraph",
            {"is_smart": True, "is_disjoint": True, "smart_graph_attribute": "x"},
        ),
    ],
)
def test_graph_evidence_preserves_flags(style, graph_kwargs) -> None:
    snap = _snapshot(
        collections=[
            _collection(
                "A",
                kind="smartgraph_disjoint" if "is_disjoint" in graph_kwargs else "smartgraph",
            ),
        ],
        graphs_detailed=[_graph("G", **graph_kwargs)],
    )
    profile = classify_sharding_profile(snap)
    assert profile is not None
    assert profile["style"] == style
    g = profile["graphs"][0]
    assert g["smartGraphAttribute"] == "x"
    assert g["isSmart"] is True
    if "is_disjoint" in graph_kwargs:
        assert g["isDisjoint"] is True
