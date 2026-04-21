"""Tests for issue #13: per-entity tenant-scope annotations on the
physical-mapping export contract.

Covered:

* The three roles (``tenant_root``, ``tenant_scoped``, ``global``).
* The three discovery sources (explicit annotation, denorm-field
  heuristic, traversal reachability) and their precedence.
* The three configuration knobs (root names, denorm-field regex,
  max BFS hops) via both kwargs and environment variables, including
  the env-var fallback path when the user supplies an invalid value.
* Endpoint-shape tolerance on the conceptual relationship graph
  (legacy ``sourceEntity`` / ``targetEntity`` keys, dict endpoints).
* The fail-closed return contract for graphs that lack a tenant root
  (``annotate_tenant_scope`` returns ``None`` and does NOT mutate the
  payload).
* Idempotence of repeated invocations.

These are pure unit tests against synthetic ``conceptualSchema`` +
``physicalMapping`` payloads. End-to-end coverage through
:class:`AgenticSchemaAnalyzer` lives in
:mod:`tests.test_analyzer_with_mock_provider`.
"""

from __future__ import annotations

import re

import pytest

from schema_analyzer.tenant_scope import (
    DEFAULT_MAX_HOPS,
    DEFAULT_TENANT_ROOT_NAMES,
    annotate_tenant_scope,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _entity(name: str, *, properties: list[str] | None = None) -> dict:
    return {
        "name": name,
        "labels": [name],
        "properties": [{"name": p} for p in (properties or [])],
    }


def _rel(rel_type: str, src: str, dst: str) -> dict:
    return {"type": rel_type, "from": src, "to": dst}


def _pm_entity(collection: str) -> dict:
    return {"style": "COLLECTION", "collectionName": collection}


def _payload(
    *,
    conceptual_entities: list[dict],
    relationships: list[dict] | None = None,
    pm_entities: dict[str, dict] | None = None,
) -> dict:
    return {
        "conceptualSchema": {
            "entities": conceptual_entities,
            "relationships": relationships or [],
            "properties": [],
        },
        "physicalMapping": {
            "entities": pm_entities or {e["name"]: _pm_entity(e["name"].lower()) for e in conceptual_entities},
            "relationships": {},
        },
    }


# ── No-tenant-root short-circuit ─────────────────────────────────────────


def test_no_tenant_root_returns_none_and_does_not_mutate() -> None:
    data = _payload(
        conceptual_entities=[_entity("User"), _entity("Post")],
        relationships=[_rel("AUTHORED", "User", "Post")],
    )
    snapshot_before = repr(data)

    summary = annotate_tenant_scope(data)

    assert summary is None
    for entry in data["physicalMapping"]["entities"].values():
        assert "tenantScope" not in entry
    assert repr(data) == snapshot_before


def test_no_tenant_root_via_custom_root_names_still_returns_none() -> None:
    data = _payload(conceptual_entities=[_entity("User")])
    assert annotate_tenant_scope(data, tenant_root_names=("Account",)) is None


# ── tenant_root role ─────────────────────────────────────────────────────


def test_tenant_root_is_classified_when_named_tenant() -> None:
    data = _payload(conceptual_entities=[_entity("Tenant"), _entity("User")])
    summary = annotate_tenant_scope(data)
    assert summary is not None
    assert summary["tenantEntity"] == "Tenant"

    tenant_entry = data["physicalMapping"]["entities"]["Tenant"]
    assert tenant_entry["tenantScope"] == {"role": "tenant_root"}


def test_custom_root_name_via_kwarg_is_honoured() -> None:
    data = _payload(
        conceptual_entities=[
            _entity("Account"),
            _entity("User", properties=["ACCOUNT_ID"]),
        ],
    )
    summary = annotate_tenant_scope(
        data,
        tenant_root_names=("Account",),
        tenant_field_regex=re.compile(r"^account[_-]?id$", re.IGNORECASE),
    )
    assert summary is not None
    assert summary["tenantEntity"] == "Account"
    pm = data["physicalMapping"]["entities"]
    assert pm["Account"]["tenantScope"] == {"role": "tenant_root"}
    assert pm["User"]["tenantScope"] == {
        "role": "tenant_scoped",
        "tenantEntity": "Account",
        "tenantField": "ACCOUNT_ID",
    }


def test_custom_root_name_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    # First match in the configured list that exists in the schema wins.
    # "Org" isn't present in the payload, so the annotator falls through
    # to "Account".
    monkeypatch.setenv("SCHEMA_ANALYZER_TENANT_ROOT_NAMES", "Org, Account ")
    data = _payload(conceptual_entities=[_entity("Account")])
    summary = annotate_tenant_scope(data)
    assert summary is not None
    assert summary["tenantEntity"] == "Account"


# ── denorm field heuristic ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "field_name",
    ["TENANT_ID", "tenant_id", "tenantId", "tenant_key", "TENANT-ID"],
)
def test_default_regex_detects_common_tenant_id_spellings(field_name: str) -> None:
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Device", properties=[field_name, "name"]),
        ],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None

    device = data["physicalMapping"]["entities"]["Device"]
    assert device["tenantScope"] == {
        "role": "tenant_scoped",
        "tenantEntity": "Tenant",
        "tenantField": field_name,
    }
    assert summary["denormScopedCount"] == 1
    assert summary["traversalScopedCount"] == 0
    assert summary["discovery"]["fromDenormFieldHeuristic"] == 1


def test_custom_regex_via_env_var_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_TENANT_FIELD_REGEX", r"^owner_org$")
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Asset", properties=["owner_org", "TENANT_ID"]),
        ],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None
    asset = data["physicalMapping"]["entities"]["Asset"]
    # Custom regex matches owner_org. The vestigial TENANT_ID column
    # must NOT win once the operator narrowed the regex.
    assert asset["tenantScope"]["tenantField"] == "owner_org"
    assert summary["tenantFieldRegex"] == "^owner_org$"


def test_invalid_env_regex_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_TENANT_FIELD_REGEX", r"[invalid")
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Device", properties=["TENANT_ID"]),
        ],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None
    assert data["physicalMapping"]["entities"]["Device"]["tenantScope"]["tenantField"] == "TENANT_ID"


def test_property_list_of_bare_strings_is_supported() -> None:
    """Some upstream pipelines pass ``properties: ["TENANT_ID", "NAME"]``
    rather than the list-of-dicts shape. The annotator must tolerate
    both."""
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            {"name": "Device", "labels": ["Device"], "properties": ["TENANT_ID", "name"]},
        ],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None
    assert data["physicalMapping"]["entities"]["Device"]["tenantScope"]["tenantField"] == "TENANT_ID"


# ── traversal reachability ───────────────────────────────────────────────


def test_traversal_only_entity_is_classified_tenant_scoped_without_field() -> None:
    """Entities reachable from Tenant but without a TENANT_ID column
    are tenant-scoped via traversal — no ``tenantField`` is emitted
    so consumers know to bind ``:Tenant`` and traverse."""
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("TenantUser"),
            _entity("GSuiteUser", properties=["NAME"]),
        ],
        relationships=[
            _rel("HAS_USER", "Tenant", "TenantUser"),
            _rel("LINKED_TO", "TenantUser", "GSuiteUser"),
        ],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None

    pm = data["physicalMapping"]["entities"]
    assert pm["TenantUser"]["tenantScope"] == {
        "role": "tenant_scoped",
        "tenantEntity": "Tenant",
    }
    assert pm["GSuiteUser"]["tenantScope"] == {
        "role": "tenant_scoped",
        "tenantEntity": "Tenant",
    }
    assert summary["traversalScopedCount"] == 2
    assert summary["denormScopedCount"] == 0
    assert summary["discovery"]["fromTraversalReachability"] == 2


def test_max_hops_zero_makes_only_root_reachable() -> None:
    data = _payload(
        conceptual_entities=[_entity("Tenant"), _entity("User"), _entity("Cve")],
        relationships=[_rel("HAS_USER", "Tenant", "User")],
    )
    summary = annotate_tenant_scope(data, max_hops=0)
    assert summary is not None

    pm = data["physicalMapping"]["entities"]
    assert pm["Tenant"]["tenantScope"] == {"role": "tenant_root"}
    # User has no TENANT_ID and is no longer reachable at depth 0,
    # so it falls through to global.
    assert pm["User"]["tenantScope"] == {"role": "global"}
    assert pm["Cve"]["tenantScope"] == {"role": "global"}


def test_max_hops_limits_traversal_classification() -> None:
    # Tenant → A → B → C → D ; with max_hops=2, C and D fall to global.
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("A"),
            _entity("B"),
            _entity("C"),
            _entity("D"),
        ],
        relationships=[
            _rel("R1", "Tenant", "A"),
            _rel("R2", "A", "B"),
            _rel("R3", "B", "C"),
            _rel("R4", "C", "D"),
        ],
    )
    summary = annotate_tenant_scope(data, max_hops=2)
    assert summary is not None
    pm = data["physicalMapping"]["entities"]
    assert pm["A"]["tenantScope"]["role"] == "tenant_scoped"
    assert pm["B"]["tenantScope"]["role"] == "tenant_scoped"
    assert pm["C"]["tenantScope"]["role"] == "global"
    assert pm["D"]["tenantScope"]["role"] == "global"


def test_max_hops_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_TENANT_SCOPE_MAX_HOPS", "1")
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("A"),
            _entity("B"),
        ],
        relationships=[_rel("R1", "Tenant", "A"), _rel("R2", "A", "B")],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None
    pm = data["physicalMapping"]["entities"]
    assert pm["A"]["tenantScope"]["role"] == "tenant_scoped"
    assert pm["B"]["tenantScope"]["role"] == "global"


def test_invalid_env_max_hops_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEMA_ANALYZER_TENANT_SCOPE_MAX_HOPS", "not-a-number")
    data = _payload(
        conceptual_entities=[_entity("Tenant"), _entity("User")],
        relationships=[_rel("R", "Tenant", "User")],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None
    assert data["physicalMapping"]["entities"]["User"]["tenantScope"]["role"] == "tenant_scoped"
    assert DEFAULT_MAX_HOPS >= 1


# ── global classification ────────────────────────────────────────────────


def test_unrelated_entity_is_classified_global() -> None:
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Cve", properties=["CVE_ID", "DESCRIPTION"]),
        ],
    )
    summary = annotate_tenant_scope(data)
    assert summary is not None
    pm = data["physicalMapping"]["entities"]
    assert pm["Cve"]["tenantScope"] == {"role": "global"}
    assert summary["globalCount"] == 1


# ── explicit annotation precedence ───────────────────────────────────────


def test_explicit_global_annotation_wins_over_denorm_heuristic() -> None:
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("LegacyTable", properties=["TENANT_ID"]),
        ],
    )
    # Operator explicitly marks LegacyTable as global despite the
    # vestigial TENANT_ID column.
    data["physicalMapping"]["entities"]["LegacyTable"]["tenantScope"] = {
        "role": "global",
    }

    summary = annotate_tenant_scope(data)
    assert summary is not None
    legacy = data["physicalMapping"]["entities"]["LegacyTable"]
    assert legacy["tenantScope"] == {"role": "global"}
    assert summary["discovery"]["fromExplicitAnnotation"] >= 1


def test_explicit_tenant_scoped_with_field_overrides_detection() -> None:
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Device", properties=["customer_uuid"]),
        ],
    )
    data["physicalMapping"]["entities"]["Device"]["tenantScope"] = {
        "role": "tenant_scoped",
        "tenantField": "customer_uuid",
    }

    summary = annotate_tenant_scope(data)
    assert summary is not None
    device = data["physicalMapping"]["entities"]["Device"]
    assert device["tenantScope"] == {
        "role": "tenant_scoped",
        "tenantEntity": "Tenant",
        "tenantField": "customer_uuid",
    }


def test_malformed_explicit_annotation_falls_through_to_heuristic() -> None:
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Device", properties=["TENANT_ID"]),
        ],
    )
    data["physicalMapping"]["entities"]["Device"]["tenantScope"] = {
        "role": "not_a_real_role",
    }

    summary = annotate_tenant_scope(data)
    assert summary is not None
    device = data["physicalMapping"]["entities"]["Device"]
    assert device["tenantScope"]["role"] == "tenant_scoped"
    assert device["tenantScope"]["tenantField"] == "TENANT_ID"


# ── relationship endpoint shape tolerance ────────────────────────────────


def test_relationship_endpoints_accept_legacy_keys_and_dict_shape() -> None:
    """The annotator's BFS must work with both ``from``/``to`` and the
    legacy ``sourceEntity`` / ``targetEntity`` keys, and tolerate
    either bare strings or ``{label: ...}`` endpoint dicts."""
    data = {
        "conceptualSchema": {
            "entities": [
                _entity("Tenant"),
                _entity("Device"),
                _entity("Probe"),
            ],
            "relationships": [
                {
                    "type": "OWNS",
                    "sourceEntity": {"label": "Tenant"},
                    "targetEntity": {"name": "Device"},
                },
                {"type": "MEASURES", "from": "Device", "to": "Probe"},
            ],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {
                "Tenant": _pm_entity("tenant"),
                "Device": _pm_entity("device"),
                "Probe": _pm_entity("probe"),
            },
            "relationships": {},
        },
    }

    summary = annotate_tenant_scope(data)
    assert summary is not None
    pm = data["physicalMapping"]["entities"]
    assert pm["Device"]["tenantScope"]["role"] == "tenant_scoped"
    assert pm["Probe"]["tenantScope"]["role"] == "tenant_scoped"


# ── public surface / defaults sanity ─────────────────────────────────────


def test_public_default_constants_are_sane() -> None:
    assert "Tenant" in DEFAULT_TENANT_ROOT_NAMES
    assert isinstance(DEFAULT_MAX_HOPS, int) and DEFAULT_MAX_HOPS >= 1


def test_idempotent_on_repeat_invocation() -> None:
    """Annotation *state* must be stable across re-runs.

    Discovery counters legitimately move on re-run (annotations
    written by run #1 are read back as explicit overrides by run #2),
    but the per-entity ``tenantScope`` blocks themselves must not
    drift. This protects against the analyzer being invoked twice on
    the same payload (e.g. cache hit → re-validate) producing
    different exports."""
    data = _payload(
        conceptual_entities=[
            _entity("Tenant"),
            _entity("Device", properties=["TENANT_ID"]),
            _entity("Cve"),
        ],
    )
    annotate_tenant_scope(data)
    snapshot = {name: dict(entry["tenantScope"]) for name, entry in data["physicalMapping"]["entities"].items()}
    annotate_tenant_scope(data)
    after = {name: dict(entry["tenantScope"]) for name, entry in data["physicalMapping"]["entities"].items()}
    assert snapshot == after
    assert snapshot["Device"]["tenantField"] == "TENANT_ID"
    assert snapshot["Cve"] == {"role": "global"}
    assert snapshot["Tenant"] == {"role": "tenant_root"}


# ── garbage-in tolerance ─────────────────────────────────────────────────


def test_returns_none_when_payload_is_missing_required_blocks() -> None:
    assert annotate_tenant_scope({}) is None
    assert annotate_tenant_scope({"conceptualSchema": {}}) is None
    assert annotate_tenant_scope({"conceptualSchema": {}, "physicalMapping": {"entities": {}}}) is None


def test_null_tenant_scope_report_validates_against_v1_contract() -> None:
    """Single-tenant graphs end up with ``tenantScopeReport: null``
    after Pydantic dumps the metadata block. The v1 response schema
    must accept the null and the populated-object case symmetrically,
    and reject malformed objects.

    Regression for the bug discovered in CI on PR #14: emitting a
    strict ``"type": "object"`` definition for ``tenantScopeReport``
    caused the bundled validator to reject every analyze response
    that didn't carry a Tenant collection (including the integration
    smoke test). The fix is the ``oneOf: [null, object]`` shape now
    used in ``tool_contract/v1/response.schema.json``.
    """
    from schema_analyzer.tool_contract_v1 import validate_response_v1

    base_response = {
        "contractVersion": "1",
        "operation": "analyze",
        "ok": True,
        "result": {
            "analysis": {
                "conceptualSchema": {
                    "entities": [],
                    "relationships": [],
                    "properties": [],
                },
                "physicalMapping": {"entities": {}, "relationships": {}},
                "metadata": {
                    "confidence": 0.5,
                    "timestamp": "2026-04-21T00:00:00Z",
                    "analyzedCollectionCounts": {
                        "documentCollections": 0,
                        "edgeCollections": 0,
                    },
                    "detectedPatterns": [],
                    "tenantScopeReport": None,
                },
            }
        },
    }
    assert validate_response_v1(base_response) == []

    base_response["result"]["analysis"]["metadata"]["tenantScopeReport"] = {
        "tenantEntity": "Tenant",
        "denormScopedCount": 1,
        "traversalScopedCount": 0,
        "globalCount": 0,
        "tenantFieldRegex": "^tenant[_-]?(id|key)$",
        "discovery": {
            "fromExplicitAnnotation": 0,
            "fromDenormFieldHeuristic": 1,
            "fromTraversalReachability": 0,
        },
    }
    assert validate_response_v1(base_response) == []

    # Malformed report (object missing required tenantEntity) must be
    # rejected — the schema should not silently accept anything.
    base_response["result"]["analysis"]["metadata"]["tenantScopeReport"] = {
        "denormScopedCount": 1,
    }
    assert validate_response_v1(base_response) != []


def test_per_entity_tenant_scope_blocks_validate_against_v1_contract() -> None:
    """All three role values + the optional ``tenantField`` /
    ``tenantEntity`` shapes must validate when emitted under
    ``physicalMapping.entities[*].tenantScope``."""
    from schema_analyzer.tool_contract_v1 import validate_response_v1

    response = {
        "contractVersion": "1",
        "operation": "analyze",
        "ok": True,
        "result": {
            "analysis": {
                "conceptualSchema": {
                    "entities": [],
                    "relationships": [],
                    "properties": [],
                },
                "physicalMapping": {
                    "entities": {
                        "Tenant": {
                            "style": "COLLECTION",
                            "collectionName": "Tenant",
                            "tenantScope": {"role": "tenant_root"},
                        },
                        "Device": {
                            "style": "COLLECTION",
                            "collectionName": "Device",
                            "tenantScope": {
                                "role": "tenant_scoped",
                                "tenantEntity": "Tenant",
                                "tenantField": "TENANT_ID",
                            },
                        },
                        "TraversalOnly": {
                            "style": "COLLECTION",
                            "collectionName": "TraversalOnly",
                            "tenantScope": {
                                "role": "tenant_scoped",
                                "tenantEntity": "Tenant",
                            },
                        },
                        "Cve": {
                            "style": "COLLECTION",
                            "collectionName": "Cve",
                            "tenantScope": {"role": "global"},
                        },
                    },
                    "relationships": {},
                },
                "metadata": {
                    "confidence": 0.9,
                    "timestamp": "2026-04-21T00:00:00Z",
                    "analyzedCollectionCounts": {
                        "documentCollections": 4,
                        "edgeCollections": 0,
                    },
                    "detectedPatterns": [],
                },
            }
        },
    }
    assert validate_response_v1(response) == []

    # Bogus role string must be rejected.
    response["result"]["analysis"]["physicalMapping"]["entities"]["Cve"]["tenantScope"] = {"role": "shared"}
    assert validate_response_v1(response) != []


def test_skips_entities_with_no_physical_mapping_entry() -> None:
    """If reconciliation hasn't backfilled an entity yet, the annotator
    skips it rather than crashing."""
    data = {
        "conceptualSchema": {
            "entities": [_entity("Tenant"), _entity("Orphan", properties=["TENANT_ID"])],
            "relationships": [],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {"Tenant": _pm_entity("tenant")},
            "relationships": {},
        },
    }
    summary = annotate_tenant_scope(data)
    assert summary is not None
    assert "Orphan" not in data["physicalMapping"]["entities"]
    assert data["physicalMapping"]["entities"]["Tenant"]["tenantScope"] == {"role": "tenant_root"}
